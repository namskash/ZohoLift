#!/usr/bin/env python3
"""Incremental Zoho WorkDrive uploader. Dry-run by default; --apply uploads.

Local scanning reads directory entries and metadata. File contents are opened only
for files selected for upload. Deletion is opt-in and moves only manifest-tracked
files missing locally to WorkDrive Trash.
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from zoho_auth import get_access_token
from manifest_store import MANIFEST, read_manifest, source_key_for, write_manifest as store_manifest

PAGE_SIZE = 50
MAX_NORMAL_UPLOAD = 250_000_000
# All configured source directories. The manifest skips known unchanged files.
BACKUP_SOURCE_DIRS = (
    "Audio",
    "Docs",
    "eBooks",
    "G2",
    "Linux",
    "Media",
    "Music/Me",
    "Music/My Collection",
    "Music/Smule",
    "temp",
    "The Notebook of Thoughts, Arguments, and Papers",
    "Video editing/Thumbnails, pictures and more",
    "Worksheets",
)


def fail(message):
    print(f"ERROR: {message}", file=sys.stderr)
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


def print_upload_stats(uploaded_bytes, elapsed):
    speed = uploaded_bytes / elapsed if elapsed > 0 else 0
    print(f"Upload time: {elapsed:.2f}s")
    print(f"Upload throughput: {human_bytes(speed)}/s")


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
            print(f"DNS lookup failed; retrying upload in {delay}s ({attempt}/4).")
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


def project_env_value(key):
    env_path = Path(__file__).resolve().parents[1] / ".env"
    if not env_path.is_file():
        return None
    for line in env_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        name, _, value = stripped.partition("=")
        if name.strip() == key:
            return value.strip().strip("\"'")
    return None


def run_batch(args):
    base_text = input("Local NK directory [/run/media/namskash/PortableSSD/NK]: ").strip()
    base = Path(base_text or "/run/media/namskash/PortableSSD/NK").expanduser().resolve()
    if not base.is_dir():
        fail("Local NK directory is unavailable.")
    remote_root_id = os.environ.get("WORKDRIVE_NK_FOLDER_ID") or project_env_value("WORKDRIVE_NK_FOLDER_ID")
    if not remote_root_id:
        remote_root_id = input("WorkDrive destination NK folder ID: ").strip()
    else:
        print("Using the WorkDrive NK folder ID from project configuration.")
    if not remote_root_id:
        fail("A WorkDrive destination folder ID is required.")

    token, api_root = get_access_token()
    started = time.monotonic()
    print("Listing remote metadata and scanning local directory metadata...")
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

    for relative_root in BACKUP_SOURCE_DIRS:
        source = (base / relative_root).resolve()
        if not source.is_dir():
            fail(f"A configured local source directory is missing; no changes made: {relative_root}")
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
        if args.delete_remote:
            deletions, conflicts = tracked_deletion_candidates(previous, local, remote_for_source)
            deletion_candidates.extend(
                {"remote_path": f"{relative_root}/{rel}", "remote_id": file_id}
                for rel, file_id in deletions
            )
            deletion_conflicts.extend(f"{relative_root}/{rel}" for rel in conflicts)
        candidates = []
        for rel, meta in local.items():
            remote_item = remote_for_source.get(rel)
            if remote_item is None or remote_item["size"] != meta["size"]:
                candidates.append(rel)
                continue
            if previous is None:
                total_baseline += 1
            else:
                old = previous.get(rel)
                if old is None or old.get("size") != meta["size"] or old.get("mtime_ns") != meta["mtime_ns"]:
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
            })
        plans.append({
            "source_key": source_key,
            "source_root": source,
            "relative_root": relative_root,
            "local": local,
        })

    candidate_bytes = sum(item["size"] for item in all_candidates)
    print("\nBatch plan:")
    print(f"  Source directories: {len(plans)}")
    print(f"  Local regular files: {total_local_files} ({human_bytes(total_local_bytes)})")
    print(f"  Candidate uploads: {len(all_candidates)} ({human_bytes(candidate_bytes)})")
    print(f"  Oversized candidate files skipped: {len(oversized_candidates)} ({human_bytes(sum(item[2] for item in oversized_candidates))})")
    if args.delete_remote:
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
    print(f"  Manifest: {MANIFEST}")

    if invalid_folder_names:
        fail("Batch preflight found invalid folder names; no remote changes were made.")
    if not args.apply:
        print("DRY RUN: no folders, files, or manifest were changed.")
        print("Use --all --apply only after reviewing this plan.")
        return

    confirmation = (
        f"Type APPLY to create {len(missing_dirs)} folder(s), "
        f"upload {len(all_candidates)} file(s), and update the manifest: "
    )
    if input(confirmation).strip() != "APPLY":
        print("Cancelled; no changes made.")
        return
    if args.delete_remote and deletion_candidates:
        delete_confirmation = (
            f"Type DELETE to move these {len(deletion_candidates)} manifest-tracked file(s) to WorkDrive Trash: "
        )
        if input(delete_confirmation).strip() != "DELETE":
            print("Deletion cancelled; no remote files were deleted.")
            return

    if missing_dirs:
        create_missing_folders(api_root, token, remote_folders, missing_dirs)
    upload_started = time.monotonic()
    uploaded_bytes = 0
    for index, item in enumerate(all_candidates, 1):
        parent_id = remote_folders.get(item["target_parent"])
        if not parent_id:
            fail("Destination parent folder is unavailable; stopping uploads.")
        upload_file(api_root, token, parent_id, item["local_path"], item["local_path"].name)
        uploaded_bytes += item["size"]
        print(f"Uploaded {index}/{len(all_candidates)} candidate file(s).")

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
        for rel, meta in plan["local"].items():
            if rel in skipped_paths:
                continue
            item = remote_for_source.get(rel)
            if item is None or item["size"] != meta["size"]:
                fail("Post-upload verification failed; manifest was not updated.")
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
            print(f"Moved tracked remote file to Trash {index}/{len(deletion_candidates)}.")
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

    print(f"Batch upload and verification complete: {len(all_candidates)} file(s) uploaded.")
    print_upload_stats(uploaded_bytes, time.monotonic() - upload_started)
    if deletion_candidates:
        print(f"Moved {len(deletion_candidates)} tracked remote file(s) to Trash.")
    print(f"Manifest written: {MANIFEST}")
    print(f"Verification API requests: {verify_requests}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="upload candidates; default is dry-run")
    parser.add_argument("--all", action="store_true", help="process the currently configured batch directories")
    parser.add_argument("--delete-remote", action="store_true", help="in batch mode, move missing manifest-tracked files to Trash")
    args = parser.parse_args()
    if args.delete_remote and not args.all:
        fail("--delete-remote currently requires --all batch mode.")
    if args.all:
        run_batch(args)
        return

    source_text = input("Local directory [~/NK/Docs]: ").strip() or "~/NK/Docs"
    source = Path(source_text).expanduser().resolve()
    if not source.is_dir():
        fail("Local directory is unavailable.")
    remote_id = input("WorkDrive destination folder ID: ").strip()
    if not remote_id:
        fail("A WorkDrive destination folder ID is required.")

    source_key = source_key_for(source)
    token, api_root = get_access_token()
    started = time.monotonic()
    print("Listing remote metadata and scanning local metadata...")
    remote, remote_folders, api_requests = list_remote(api_root, token, remote_id)
    local, local_dirs, symlinks = scan_local(source)
    try:
        manifest_data, previous = read_manifest(source_key)
    except ValueError as exc:
        fail(str(exc))

    candidates = []
    baseline_assumptions = 0
    remote_only = sum(rel not in local for rel in remote)
    for rel, meta in local.items():
        remote_item = remote.get(rel)
        if remote_item is None or remote_item["size"] != meta["size"]:
            candidates.append(rel)
            continue
        if previous is None:
            # Bootstrap assumption: a same-path, same-size remote file is present.
            baseline_assumptions += 1
        else:
            old = previous.get(rel)
            if old is None or old.get("size") != meta["size"] or old.get("mtime_ns") != meta["mtime_ns"]:
                candidates.append(rel)

    # Preflight candidates and the complete local directory tree before any remote writes.
    candidate_info = []
    oversized = 0
    invalid_folder_names = 0
    missing_parent_files = 0
    for directory in local_dirs:
        if len(directory.rsplit("/", 1)[-1]) > 200:
            invalid_folder_names += 1
    missing_dirs = local_dirs - set(remote_folders)

    for rel in candidates:
        local_path = source / rel
        size = local[rel]["size"]
        if size > MAX_NORMAL_UPLOAD:
            oversized += 1
            continue
        parent_rel = rel.rpartition("/")[0]
        if parent_rel and parent_rel not in remote_folders:
            missing_parent_files += 1
        candidate_info.append((rel, local_path, parent_rel))

    print("\nCandidate upload plan:")
    print(f"  Local regular files: {len(local)} ({human_bytes(sum(item['size'] for item in local.values()))})")
    print(f"  Remote files: {len(remote)}")
    print(f"  Candidate uploads: {len(candidates)} ({human_bytes(sum(local[rel]['size'] for rel in candidates))})")
    print(f"  Missing destination folders to create: {len(missing_dirs)}")
    print(f"  Candidate files with missing parent folders: {missing_parent_files}")
    print(f"  Invalid folder names (>200 characters): {invalid_folder_names}")
    print(f"  Candidates over 250 MB (not uploaded): {oversized}")
    print(f"  Same-size remote files assumed present for initial baseline: {baseline_assumptions}")
    print(f"  Remote-only files (never deleted): {remote_only}")
    print(f"  Symlinks skipped: {symlinks}")
    print(f"  API list requests: {api_requests}")
    print(f"  Preflight elapsed: {time.monotonic() - started:.2f}s")
    print(f"  Source key: {source_key}")
    print(f"  Manifest: {MANIFEST}")

    if invalid_folder_names or oversized:
        fail("Preflight found unsupported items; no remote changes were made.")
    if not args.apply:
        print("DRY RUN: no folders, files, or manifest were changed.")
        print("Use --apply only after reviewing this plan.")
        return
    confirmation = (
        f"Type APPLY to create {len(missing_dirs)} folder(s), "
        f"upload {len(candidate_info)} file(s), and update the manifest: "
    )
    if input(confirmation).strip() != "APPLY":
        print("Cancelled; no changes made.")
        return

    if missing_dirs:
        create_missing_folders(api_root, token, remote_folders, missing_dirs)

    uploaded = 0
    uploaded_bytes = 0
    upload_started = time.monotonic()
    for rel, local_path, parent_rel in candidate_info:
        parent_id = remote_folders.get(parent_rel)
        if not parent_id:
            fail("Destination parent folder is unavailable; stopping uploads.")
        upload_file(api_root, token, parent_id, local_path, local_path.name)
        uploaded += 1
        uploaded_bytes += local[rel]["size"]
        print(f"Uploaded {uploaded}/{len(candidate_info)} candidate file(s).")

    # Re-list to verify remote presence and size before recording local baselines.
    verified_remote, _, verify_requests = list_remote(api_root, token, remote_id)
    for rel in local:
        item = verified_remote.get(rel)
        if item is None or item["size"] != local[rel]["size"]:
            fail("Post-upload verification failed; manifest was not updated.")

    store_manifest(manifest_data, source_key, remote_id, local, verified_remote)
    print(f"Upload and verification complete: {uploaded} file(s) uploaded.")
    print_upload_stats(uploaded_bytes, time.monotonic() - upload_started)
    print(f"Manifest written: {MANIFEST}")
    print(f"Verification API requests: {verify_requests}")


if __name__ == "__main__":
    main()
