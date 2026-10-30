#!/usr/bin/env python3
"""Incremental Zoho WorkDrive uploader. Uploads by default; --dry-run previews without changes.

Local scanning reads directory entries and metadata. File contents are opened only
for files selected for upload. Deletion is opt-in and moves only manifest-tracked
files missing locally to WorkDrive Trash.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen

from zoho_auth import get_access_token
from manifest_store import MANIFEST, read_manifest, source_key_for, write_manifest as store_manifest

PAGE_SIZE = 50
MAX_NORMAL_UPLOAD = 250_000_000
PROJECT_DIR = Path(__file__).resolve().parents[1]
SOURCE_DIRS_FILE = PROJECT_DIR / "backup-dirs.txt"
ASCII_ART_FILE = PROJECT_DIR / "assets" / "ascii-art-patorjk-com-favourites.txt"


class Tee:
    def __init__(self, terminal, logfile):
        self.terminal = terminal
        self.logfile = logfile

    def write(self, value):
        self.terminal.write(value)
        plain_value = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", value)
        self.logfile.write(plain_value)
        self.flush()
        return len(value)

    def flush(self):
        self.terminal.flush()
        self.logfile.flush()

    def isatty(self):
        return self.terminal.isatty()


def setup_logging():
    global RUN_LOG_PATH
    log_dir = Path(__file__).resolve().parents[1] / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    log_path = log_dir / f"zoho-wd-api-sync-{stamp}-{os.getpid()}.log"
    fd = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    logfile = os.fdopen(fd, "w", encoding="utf-8", buffering=1)
    sys.stdout = Tee(sys.stdout, logfile)
    sys.stderr = Tee(sys.stderr, logfile)
    RUN_LOG_PATH = log_path
    return log_path


RUN_LOG_PATH = None


def write_activity_report(uploaded_files, deleted_files):
    if RUN_LOG_PATH is None:
        return
    report_path = RUN_LOG_PATH.with_suffix(".activity.json")
    payload = {
        "completed_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "uploaded": uploaded_files,
        "moved_to_trash": deleted_files,
    }
    fd = os.open(report_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as report:
        json.dump(payload, report, indent=2)
        report.write("\n")


def print_banner():
    banner = f"\n{ASCII_ART_FILE.read_text(encoding='utf-8').rstrip()}\n"
    terminal = getattr(sys.stdout, "terminal", sys.stdout)
    terminal.write(banner)
    terminal.flush()


def color(text, code):
    if sys.stdout.isatty() and "NO_COLOR" not in os.environ:
        return f"\033[{code}m{text}\033[0m"
    return text


def fail(message):
    print(color(f"ERROR: {message}", "31;1"), file=sys.stderr)
    raise SystemExit(1)


def human_bytes(value):
    size = float(value)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if size < 1024 or unit == "TiB":
            return f"{size:.1f} {unit}"
        size /= 1024


def http_json(request):
    try:
        with urlopen(request, timeout=90) as response:
            body = response.read()
            return json.loads(body) if body else {}
    except HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        fail(f"HTTP {exc.code}: {body[:1000]}")
    except URLError as exc:
        fail(f"Network/API request failed: {exc.reason}")


def api_get(api_root, token, path, params=None):
    query = "?" + urlencode(params) if params else ""
    request = Request(
        api_root + path + query,
        headers={
            "Authorization": f"Zoho-oauthtoken {token}",
            "Accept": "application/vnd.api+json",
        },
    )
    return http_json(request)


def api_post_json(api_root, token, path, payload):
    request = Request(
        api_root + path,
        data=json.dumps(payload).encode(),
        headers={
            "Authorization": f"Zoho-oauthtoken {token}",
            "Accept": "application/vnd.api+json",
            "Content-Type": "application/vnd.api+json",
        },
    )
    return http_json(request)


def api_patch_json(api_root, token, path, payload):
    request = Request(
        api_root + path,
        data=json.dumps(payload).encode(),
        method="PATCH",
        headers={
            "Authorization": f"Zoho-oauthtoken {token}",
            "Accept": "application/vnd.api+json",
            "Content-Type": "application/vnd.api+json",
        },
    )
    return http_json(request)


def list_remote(api_root, token, root_id):
    files = {}
    folders = {"": root_id}
    request_count = 0

    def visit(folder_id, prefix):
        nonlocal request_count
        offset = 0
        while True:
            result = api_get(
                api_root, token,
                f"/workdrive/api/v1/files/{folder_id}/files",
                {"page[limit]": PAGE_SIZE, "page[offset]": offset},
            )
            request_count += 1
            items = result.get("data", [])
            for item in items:
                attrs = item.get("attributes", {})
                name = attrs.get("name")
                if not name:
                    continue
                rel = f"{prefix}/{name}" if prefix else name
                if attrs.get("is_folder") is True:
                    folders[rel] = item["id"]
                    visit(item["id"], rel)
                else:
                    size = attrs.get("storage_info", {}).get("size_in_bytes")
                    files[rel] = {
                        "id": item.get("id"),
                        "size": int(size) if size is not None else None,
                    }
            if len(items) < PAGE_SIZE:
                break
            offset += len(items)

    visit(root_id, "")
    return files, folders, request_count


def scan_local(root):
    files = {}
    directories = set()
    symlinks = 0
    for current, dirs, names in os.walk(root, followlinks=False):
        kept_dirs = []
        for name in dirs:
            path = Path(current) / name
            if path.is_symlink():
                symlinks += 1
            else:
                kept_dirs.append(name)
                directories.add(path.relative_to(root).as_posix())
        dirs[:] = kept_dirs
        for name in names:
            path = Path(current) / name
            try:
                info = path.lstat()
            except OSError:
                fail("Could not read local file metadata.")
            if path.is_symlink():
                symlinks += 1
                continue
            if not path.is_file():
                continue
            rel = path.relative_to(root).as_posix()
            files[rel] = {"size": info.st_size, "mtime_ns": info.st_mtime_ns}
    return files, directories, symlinks


def create_remote_folder(api_root, token, parent_id, name):
    result = api_post_json(
        api_root,
        token,
        "/workdrive/api/v1/files",
        {
            "data": {
                "attributes": {"parent_id": parent_id, "name": name},
                "type": "files",
            }
        },
    )
    data = result.get("data")
    if isinstance(data, list):
        data = data[0] if data else None
    folder_id = data.get("id") if isinstance(data, dict) else None
    if not folder_id:
        fail("Folder creation returned no folder ID; stopping before uploads.")
    return folder_id


def create_missing_folders(api_root, token, remote_folders, missing_dirs):
    ordered = sorted(missing_dirs, key=lambda path: (path.count("/"), path))
    for index, rel in enumerate(ordered, 1):
        parent_rel, _, name = rel.rpartition("/")
        parent_id = remote_folders.get(parent_rel)
        if not parent_id:
            fail("A parent folder is still missing; stopping before file uploads.")
        remote_folders[rel] = create_remote_folder(api_root, token, parent_id, name)
        print(f"Created destination folder {index}/{len(ordered)}.")


def move_remote_file_to_trash(api_root, token, file_id):
    response = api_patch_json(
        api_root,
        token,
        f"/workdrive/api/v1/files/{quote(str(file_id), safe='')}",
        {"data": {"attributes": {"status": "51"}, "type": "files"}},
    )
    if response.get("errors") or response.get("status") == "failure":
        fail("WorkDrive reported a trash operation error.")


def tracked_deletion_candidates(previous, local_files, remote_files):
    candidates = []
    conflicts = []
    for rel, old in (previous or {}).items():
        if rel in local_files:
            continue
        remote_item = remote_files.get(rel)
        if remote_item is None:
            continue
        old_id = old.get("remote_id")
        if old_id and remote_item.get("id") == old_id:
            candidates.append((rel, old_id))
        else:
            conflicts.append(rel)
    return candidates, conflicts


def print_stat_line(label, value):
    print(f"  {color(label + ':', '35')} {color(str(value), '37')}")


def print_upload_stats(uploaded_bytes, elapsed):
    speed = uploaded_bytes / elapsed if elapsed > 0 else 0
    print_stat_line("Upload time", f"{elapsed:.2f}s")
    print_stat_line("Upload throughput", f"{human_bytes(speed)}/s")


def upload_file(api_root, token, parent_id, path, filename):
    # Use curl with a private config sent over stdin. This streams file contents
    # and keeps the OAuth token out of curl's command-line arguments.
    import subprocess

    encoded_name = quote(filename, safe="")
    url = f"{api_root}/workdrive/api/v1/upload"

    def config_value(key, value):
        return f"{key} = {json.dumps(value)}"

    with open(path, "rb") as source_file:
        fd = source_file.fileno()
        form_file = f"content=@/proc/self/fd/{fd};filename={encoded_name}"
        config_lines = [
            config_value("url", url),
            config_value("request", "POST"),
            config_value("header", f"Authorization: Zoho-oauthtoken {token}"),
            "silent",
            "show-error",
            config_value("write-out", "\\n__HTTP_STATUS__:%{http_code}"),
            config_value("form", f"parent_id={parent_id}"),
            config_value("form", f"filename={encoded_name}"),
            config_value("form", "override-name-exist=true"),
            config_value("form", form_file),
        ]
        config_input = "\n".join(config_lines).encode()
        for attempt in range(1, 6):
            result = subprocess.run(
                ["curl", "--config", "-"],
                input=config_input,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                pass_fds=(fd,),
                check=False,
            )
            if result.returncode != 6 or attempt == 5:
                break
            delay = min(2 ** attempt, 30)
            print(color(f"DNS lookup failed; retrying upload in {delay}s ({attempt}/4).", "33"))
            time.sleep(delay)
    output = result.stdout.decode("utf-8", "replace")
    marker = "\n__HTTP_STATUS__:"
    if result.returncode != 0 or marker not in output:
        detail = result.stderr.decode("utf-8", "replace")[:500]
        fail(f"Upload command failed (curl {result.returncode}): {detail}")
    body, status_text = output.rsplit(marker, 1)
    if not status_text.strip().startswith("2"):
        fail(f"Upload failed with HTTP {status_text.strip()}: {body[:500]}")
    try:
        response = json.loads(body)
    except json.JSONDecodeError:
        fail("Upload returned success HTTP status but invalid JSON; manifest not updated.")
    if response.get("errors") or response.get("status") == "failure":
        fail("WorkDrive reported an upload error; manifest not updated.")
    response["_http_status"] = status_text.strip()
    return response


def upload_response_summary(response):
    data = response.get("data")
    if isinstance(data, list):
        data = data[0] if data else None
    attrs = data.get("attributes", {}) if isinstance(data, dict) else {}
    if not isinstance(attrs, dict):
        attrs = {}
    summary = {"http_status": response.get("_http_status")}
    for key in ("name", "size", "size_in_bytes", "storage_info", "status"):
        if key in attrs:
            summary[key] = attrs[key]
    if response.get("status") is not None:
        summary["api_status"] = response["status"]
    if isinstance(data, dict) and data.get("id") is not None:
        summary["id"] = data["id"]
    return summary


def safe_url_label(url):
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}{parts.path}"


def http_error_context(exc, requested_url):
    body = exc.read(8192)
    details = {}
    try:
        payload = json.loads(body.decode("utf-8", "replace"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        payload = None
    if isinstance(payload, dict):
        for key in ("error", "code", "message", "description", "error_description", "status"):
            value = payload.get(key)
            if isinstance(value, (str, int, float)):
                value = str(value)
                value = re.sub(
                    r"(?i)(access[_ -]?token|refresh[_ -]?token|client[_ -]?secret|grant[_ -]?code)\s*[:=]\s*[^,\s\"']+",
                    r"\1=<redacted>", value,
                )
                value = re.sub(r"(?i)Zoho-oauthtoken\s+\S+", "Zoho-oauthtoken <redacted>", value)
                details[key] = value[:300]
    result = (
        f"HTTP {exc.code}; requested {safe_url_label(requested_url)}; "
        f"responded from {safe_url_label(exc.geturl())}"
    )
    if details:
        result += f"; response details: {json.dumps(details, sort_keys=True)}"
    else:
        result += f"; response body omitted ({len(body)} bytes, non-JSON or no recognized fields)"
    auth_challenge = exc.headers.get("WWW-Authenticate") if exc.headers else None
    if auth_challenge:
        result += f"; WWW-Authenticate: {auth_challenge[:300]}"
    return result


def download_size_for_verification(api_root, token, file_id, expected_size):
    if expected_size >= MAX_NORMAL_UPLOAD:
        fail("Cannot download-verify a mismatched file at or above the 250 MB limit.")
    api_parts = urlsplit(api_root)
    host = api_parts.netloc
    if host.startswith("www.zohoapis."):
        download_host = host.replace("www.zohoapis.", "download.zoho.", 1)
    else:
        fail("Cannot determine the WorkDrive download host for this API region.")
    path = f"/v1/workdrive/download/{quote(str(file_id), safe='')}"
    url = urlunsplit((api_parts.scheme, download_host, path, "", ""))
    request = Request(url, headers={"Authorization": f"Zoho-oauthtoken {token}"})
    try:
        with urlopen(request, timeout=90) as response:
            total = 0
            limit = expected_size + 1
            while total < limit:
                chunk = response.read(min(64 * 1024, limit - total))
                if not chunk:
                    break
                total += len(chunk)
            return total
    except HTTPError as exc:
        fail(f"WorkDrive download verification failed: {http_error_context(exc, url)}")
    except URLError as exc:
        fail(f"WorkDrive download verification failed: {exc.reason}")


def load_backup_source_dirs():
    try:
        lines = SOURCE_DIRS_FILE.read_text(encoding="utf-8").splitlines()
    except OSError:
        fail(f"Could not read source directory list: {SOURCE_DIRS_FILE}")
    directories = [
        line.strip() for line in lines
        if line.strip() and not line.lstrip().startswith("#")
    ]
    if not directories:
        fail(f"Source directory list is empty: {SOURCE_DIRS_FILE}")
    for directory in directories:
        path = Path(directory)
        if path.is_absolute() or ".." in path.parts:
            fail("Source directory entries must be relative paths without '..'.")
    return directories


def project_config():
    config_path = Path(__file__).resolve().parent.parent / "config.json"
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        fail(f"Could not read project configuration: {config_path}")
    if not isinstance(config, dict):
        fail("Project configuration must be a JSON object.")
    return config


def local_backup_root(config):
    value = config.get("localDir")
    if not isinstance(value, str) or not value.strip():
        fail("Set localDir in config.json to the NK backup root.")
    root = Path(value).expanduser().resolve()
    if not root.is_dir():
        fail("Configured local backup root is unavailable.")
    return root


def print_source_sizes(sources):
    sizes = []
    for relative_root, source in sources:
        result = subprocess.run(
            ["du", "-s", "--bytes", "--", str(source)],
            check=False, capture_output=True, text=True,
        )
        if result.returncode != 0:
            fail(f"Could not calculate source directory size: {relative_root}")
        try:
            size = int(result.stdout.split(None, 1)[0])
        except (IndexError, ValueError):
            fail(f"Could not parse source directory size: {relative_root}")
        sizes.append((relative_root, size))

    max_size = max((size for _, size in sizes), default=0)
    print("\nSource directory sizes (largest first):")
    for name, size in sorted(sizes, key=lambda item: item[1], reverse=True):
        bar_size = round(30 * size / max_size) if max_size else 0
        bar = "█" * bar_size
        if size >= 1_000_000_000:
            size_color = "31"
        elif size > 250_000_000:
            size_color = "38;5;208"
        else:
            size_color = "92"
        size_text = color(f"{human_bytes(size):>10}", size_color)
        print(f"  {size_text}  {bar:<30}  {name}")


def run_batch(args):
    config = project_config()
    apply_changes = args.apply if args.apply is not None else config.get("apply", True)
    delete_remote = args.delete_remote if args.delete_remote is not None else config.get("deleteRemote", False)
    if not isinstance(apply_changes, bool) or not isinstance(delete_remote, bool):
        fail("Config values 'apply' and 'deleteRemote' must be true or false.")
    dry_run = not apply_changes
    base = local_backup_root(config)
    source_roots = [
        (relative_root, (base / relative_root).resolve())
        for relative_root in load_backup_source_dirs()
    ]
    for relative_root, source in source_roots:
        if not source.is_dir():
            fail(f"A configured local source directory is missing; no changes made: {relative_root}")
    print_source_sizes(source_roots)

    remote_root_id = config.get("WORKDRIVE_FOLDER_ID")
    if not isinstance(remote_root_id, str) or not remote_root_id.strip():
        remote_root_id = input("WorkDrive destination NK folder ID: ").strip()
    else:
        remote_root_id = remote_root_id.strip()
        print(
            f"\nUsing the {color('WorkDrive NK folder ID', '34')} from the "
            f"{color('project configuration', '35')}."
        )
    if not remote_root_id:
        fail("A WorkDrive destination folder ID is required.")

    token, api_root = get_access_token()
    started = time.monotonic()
    print("Listing remote metadata and scanning local directory metadata... this may take a while; please wait.\n")
    remote, remote_folders, api_requests = list_remote(api_root, token, remote_root_id)

    plans = []
    missing_dirs = set()
    all_candidates = []
    oversized_candidates = []
    skipped_by_source = {}
    deletion_candidates = []
    deletion_conflicts = []
    invalid_folder_names = 0
    total_local_files = 0
    total_local_bytes = 0
    total_remote_only = 0
    total_symlinks = 0
    total_baseline = 0
    manifest_data = None

    for relative_root, source in source_roots:
        source_key = source_key_for(source)
        local, local_dirs, symlinks = scan_local(source)
        total_local_files += len(local)
        total_local_bytes += sum(meta["size"] for meta in local.values())
        total_symlinks += symlinks

        try:
            source_manifest, previous = read_manifest(source_key)
        except ValueError as exc:
            fail(str(exc))
        if manifest_data is None:
            manifest_data = source_manifest

        prefix = relative_root
        remote_for_source = {
            path[len(prefix) + 1:]: item
            for path, item in remote.items()
            if path.startswith(prefix + "/")
        }
        if delete_remote:
            deletions, conflicts = tracked_deletion_candidates(previous, local, remote_for_source)
            deletion_candidates.extend(
                {
                    "remote_path": f"{relative_root}/{rel}",
                    "remote_id": file_id,
                    "size_bytes": previous[rel].get("size"),
                    "modified_time": time.strftime(
                        "%Y-%m-%dT%H:%M:%S%z",
                        time.localtime(previous[rel]["mtime_ns"] / 1_000_000_000),
                    ) if isinstance(previous[rel].get("mtime_ns"), int) else None,
                }
                for rel, file_id in deletions
            )
            deletion_conflicts.extend(f"{relative_root}/{rel}" for rel in conflicts)
        candidates = []
        for rel, meta in local.items():
            remote_item = remote_for_source.get(rel)
            if remote_item is None:
                candidates.append(rel)
                continue
            old = previous.get(rel) if previous is not None else None
            if remote_item["size"] != meta["size"]:
                previously_download_verified = (
                    old is not None
                    and old.get("download_verified") is True
                    and old.get("remote_id") == remote_item.get("id")
                    and old.get("size") == meta["size"]
                    and old.get("mtime_ns") == meta["mtime_ns"]
                )
                if previously_download_verified:
                    remote_item["size"] = meta["size"]
                    remote_item["_download_verified"] = True
                else:
                    candidates.append(rel)
                    continue
            if previous is None:
                total_baseline += 1
            elif old is None or old.get("size") != meta["size"] or old.get("mtime_ns") != meta["mtime_ns"]:
                candidates.append(rel)

        total_remote_only += sum(rel not in local for rel in remote_for_source)
        root_parts = relative_root.split("/")
        for depth in range(1, len(root_parts) + 1):
            root_prefix = "/".join(root_parts[:depth])
            if root_prefix not in remote_folders:
                missing_dirs.add(root_prefix)
        for local_dir in local_dirs:
            target_dir = f"{relative_root}/{local_dir}"
            if len(target_dir.rsplit("/", 1)[-1]) > 200:
                invalid_folder_names += 1
            if target_dir not in remote_folders:
                missing_dirs.add(target_dir)
        if len(relative_root.rsplit("/", 1)[-1]) > 200:
            invalid_folder_names += 1

        for rel in candidates:
            local_path = source / rel
            if local[rel]["size"] > MAX_NORMAL_UPLOAD:
                oversized_candidates.append((relative_root, rel, local[rel]["size"]))
                skipped_by_source.setdefault(source_key, set()).add(rel)
                continue
            parent_rel = rel.rpartition("/")[0]
            target_parent = f"{relative_root}/{parent_rel}" if parent_rel else relative_root
            all_candidates.append({
                "source_key": source_key,
                "source_root": source,
                "relative_root": relative_root,
                "relative_path": rel,
                "local_path": local_path,
                "target_parent": target_parent,
                "size": local[rel]["size"],
                "mtime_ns": local[rel]["mtime_ns"],
            })
        plans.append({
            "source_key": source_key,
            "source_root": source,
            "relative_root": relative_root,
            "local": local,
        })

    candidate_bytes = sum(item["size"] for item in all_candidates)
    print(color("Batch plan:", "1;36"))
    print(f"  Source directories: {len(plans)}")
    print(f"  Local regular files: {total_local_files} ({human_bytes(total_local_bytes)})")
    print(f"  Candidate uploads: {len(all_candidates)} ({human_bytes(candidate_bytes)})")
    print(f"  Oversized candidate files skipped: {len(oversized_candidates)} ({human_bytes(sum(item[2] for item in oversized_candidates))})")
    if delete_remote:
        print(f"  Manifest-tracked remote files to move to Trash: {len(deletion_candidates)}")
        for item in deletion_candidates:
            print(f"    Trash candidate: {item['remote_path']}")
        print(f"  Remote ID/path conflicts protected from deletion: {len(deletion_conflicts)}")
        for path in deletion_conflicts:
            print(f"    Protected conflict: {path}")
    print(f"  Missing destination folders to create: {len(missing_dirs)}")
    print(f"  Invalid folder names (>200 characters): {invalid_folder_names}")
    print(f"  Candidates over 250 MB: {len(oversized_candidates)} ({human_bytes(sum(item[2] for item in oversized_candidates))})")
    print(f"    250 MB–1 GB: {sum(MAX_NORMAL_UPLOAD < item[2] <= 1_000_000_000 for item in oversized_candidates)}")
    print(f"    Over 1 GB: {sum(item[2] > 1_000_000_000 for item in oversized_candidates)}")
    for relative_root, rel, size in oversized_candidates:
        print(f"    Oversized: {relative_root}/{rel} ({human_bytes(size)})")
    print(f"  Same-size remote files assumed present: {total_baseline}")
    print(f"  Remote-only files found (tracked deletions listed separately): {total_remote_only}")
    print(f"  Symlinks skipped: {total_symlinks}")
    print(f"  API list requests: {api_requests}")
    print(f"  Preflight elapsed: {time.monotonic() - started:.2f}s")
    print(f"  Manifest: {MANIFEST}\n")

    if invalid_folder_names:
        fail("Batch preflight found invalid folder names; no remote changes were made.")
    if dry_run:
        print(color("DRY RUN: no folders, files, or manifest were changed.", "33;1"))
        print("Run again without --dry-run to upload and update the manifest.")
        return

    if missing_dirs:
        create_missing_folders(api_root, token, remote_folders, missing_dirs)
    upload_started = time.monotonic()
    uploaded_bytes = 0
    upload_summaries = {}
    uploaded_files = []
    for index, item in enumerate(all_candidates, 1):
        parent_id = remote_folders.get(item["target_parent"])
        if not parent_id:
            fail("Destination parent folder is unavailable; stopping uploads.")
        response = upload_file(api_root, token, parent_id, item["local_path"], item["local_path"].name)
        key = f"{item['relative_root']}/{item['relative_path']}"
        upload_summaries[key] = upload_response_summary(response)
        uploaded_files.append({
            "path": key,
            "modified_time": time.strftime(
                "%Y-%m-%dT%H:%M:%S%z",
                time.localtime(item["mtime_ns"] / 1_000_000_000),
            ),
            "size_bytes": item["size"],
        })
        uploaded_bytes += item["size"]
        print(
            f"{color(f'Uploaded {index}/{len(all_candidates)} candidate file(s).', '32')} "
            f"{color(item['relative_path'], '90')}"
        )

    print(
        "\nNext: refreshing the full WorkDrive listing and verifying file sizes "
        "before updating the manifest. This can take a few minutes; please wait."
    )
    verified_remote, verified_folders, verify_requests = list_remote(api_root, token, remote_root_id)
    manifest_updates = []
    for plan in plans:
        prefix = plan["relative_root"]
        remote_for_source = {
            path[len(prefix) + 1:]: item
            for path, item in verified_remote.items()
            if path.startswith(prefix + "/")
        }
        skipped_paths = skipped_by_source.get(plan["source_key"], set())
        missing_remote = []
        size_mismatches = []
        for rel, meta in plan["local"].items():
            if rel in skipped_paths:
                continue
            item = remote_for_source.get(rel)
            if item is None:
                missing_remote.append((rel, meta["size"], meta["mtime_ns"]))
            elif item["size"] != meta["size"]:
                key = f"{prefix}/{rel}"
                print(
                    f"Download verification attempt: {key}; local {meta['size']} B, "
                    f"WorkDrive listed {item['size']} B, remote ID {item.get('id')}; "
                    f"upload response {json.dumps(upload_summaries.get(key, {}), sort_keys=True)}"
                )
                downloaded_size = download_size_for_verification(
                    api_root, token, item.get("id"), meta["size"]
                )
                if downloaded_size == meta["size"]:
                    print(
                        f"WorkDrive listed {item['size']} B for {rel}, but a streamed "
                        f"download matched the local size ({downloaded_size} B); verified."
                    )
                    item["size"] = meta["size"]
                    item["_download_verified"] = True
                else:
                    size_mismatches.append((rel, meta["size"], item["size"], downloaded_size))
        if missing_remote or size_mismatches:
            for rel, local_size, local_mtime in missing_remote:
                key = f"{prefix}/{rel}"
                print(
                    f"  Verification details: {key}\n"
                    f"    scanned local size: {local_size} B; scanned mtime_ns: {local_mtime}\n"
                    f"    WorkDrive listing: file missing\n"
                    f"    upload response metadata: {json.dumps(upload_summaries.get(key, {}), sort_keys=True)}",
                    file=sys.stderr,
                )
            for rel, local_size, remote_size, downloaded_size in size_mismatches:
                local_path = plan["source_root"] / rel
                try:
                    current = local_path.stat()
                    current_size = current.st_size
                    current_mtime = current.st_mtime_ns
                except OSError:
                    current_size = current_mtime = None
                key = f"{prefix}/{rel}"
                print(
                    f"  Verification details: {key}\n"
                    f"    scanned local size: {local_size} B; current local size: {current_size} B\n"
                    f"    scanned mtime_ns: {plan['local'][rel]['mtime_ns']}; current mtime_ns: {current_mtime}\n"
                    f"    WorkDrive listed size: {remote_size} B; streamed download size: {downloaded_size} B\n"
                    f"    remote ID: {remote_for_source[rel].get('id')}\n"
                    f"    upload response metadata: {json.dumps(upload_summaries.get(key, {}), sort_keys=True)}",
                    file=sys.stderr,
                )
            fail(
                "Post-upload verification failed; manifest was not updated "
                f"({len(missing_remote)} remote file(s) missing, "
                f"{len(size_mismatches)} size mismatch(es) after download verification)."
            )
        nested_root_id = verified_folders.get(prefix)
        if not nested_root_id:
            fail("Post-upload folder verification failed; manifest was not updated.")
        manifest_local = {
            rel: meta for rel, meta in plan["local"].items() if rel not in skipped_paths
        }
        manifest_updates.append((plan, nested_root_id, manifest_local, remote_for_source, skipped_paths))

    if deletion_candidates:
        for item in deletion_candidates:
            current = verified_remote.get(item["remote_path"])
            if not current or current.get("id") != item["remote_id"]:
                fail("A deletion target changed since preflight; no further deletions attempted.")
        for index, item in enumerate(deletion_candidates, 1):
            move_remote_file_to_trash(api_root, token, item["remote_id"])
            print(
                f"{color(f'Moved tracked remote file {index}/{len(deletion_candidates)} to Trash:', '31')} "
                f"{color(item['remote_path'], '90')}\t{item['size_bytes']} B\t{item['modified_time']}"
            )
        after_delete, _, delete_verify_requests = list_remote(api_root, token, remote_root_id)
        still_present = [item["remote_path"] for item in deletion_candidates if item["remote_path"] in after_delete]
        if still_present:
            fail("Trash verification failed; manifest was not updated.")
        verify_requests += delete_verify_requests

    for plan, nested_root_id, manifest_local, remote_for_source, skipped_paths in manifest_updates:
        store_manifest(
            manifest_data,
            plan["source_key"],
            nested_root_id,
            manifest_local,
            remote_for_source,
            preserve_paths=skipped_paths,
        )

    print(color(f"Batch upload and verification complete: {len(all_candidates)} file(s) uploaded.", "32;1"))
    deleted_files = [
        {key: item[key] for key in ("remote_path", "size_bytes", "modified_time")}
        for item in deletion_candidates
    ]
    write_activity_report(uploaded_files, deleted_files)
    print(f"Activity details: {RUN_LOG_PATH.with_suffix('.activity.json')}")
    print_upload_stats(uploaded_bytes, time.monotonic() - upload_started)
    if deletion_candidates:
        print(color(f"Moved tracked remote files to Trash: {len(deletion_candidates)}", "31"))
    print_stat_line("Manifest written", MANIFEST)
    print_stat_line("Verification API requests", verify_requests)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", dest="apply", action="store_true", default=None, help="apply changes, overriding config.json")
    parser.add_argument("--dry-run", dest="apply", action="store_false", help="preview changes, overriding config.json")
    parser.add_argument("--delete-remote", dest="delete_remote", action="store_true", default=None, help="move missing manifest-tracked files to Trash, overriding config.json")
    parser.add_argument("--no-delete-remote", dest="delete_remote", action="store_false", default=None, help="preserve remote-only files, overriding config.json")
    args = parser.parse_args()
    log_path = setup_logging()
    print_banner()
    print(f"Log file: {log_path}")
    run_batch(args)


if __name__ == "__main__":
    main()
