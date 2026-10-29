#!/usr/bin/env python3
"""Plan an incremental WorkDrive API backup without uploading or deleting.

Reads local directory entries and metadata only (relative paths, sizes, mtimes).
Does not open local file contents and does not print local file names.
Requires exported ZOHO_CLIENT_ID/ZOHO_CLIENT_SECRET and initialized refresh-token credentials.
"""
import json
import os
import sys
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from zoho_auth import get_access_token
from manifest_store import MANIFEST, read_manifest, source_key_for

PAGE_SIZE = 50


def fail(message):
    print(f"ERROR: {message}", file=sys.stderr)
    raise SystemExit(1)


def http_json(request):
    try:
        with urlopen(request, timeout=60) as response:
            return json.load(response)
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


def scan_local(root):
    files = {}
    symlinks = 0
    for current, dirs, names in os.walk(root, followlinks=False):
        kept_dirs = []
        for name in dirs:
            path = Path(current) / name
            if path.is_symlink():
                symlinks += 1
            else:
                kept_dirs.append(name)
        dirs[:] = kept_dirs

        for name in names:
            path = Path(current) / name
            try:
                info = path.lstat()
            except OSError:
                fail("Could not read local file metadata; no contents were opened.")
            if path.is_symlink():
                symlinks += 1
                continue
            if not path.is_file():
                continue
            relative = path.relative_to(root).as_posix()
            files[relative] = {"size": info.st_size, "mtime_ns": info.st_mtime_ns}
    return files, symlinks


def list_remote(api_root, token, root_id):
    files = {}
    folders_seen = 0
    request_count = 0

    def visit(folder_id, prefix):
        nonlocal folders_seen, request_count
        folders_seen += 1
        offset = 0
        while True:
            result = api_get(
                api_root,
                token,
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
                    visit(item["id"], rel)
                else:
                    size = attrs.get("storage_info", {}).get("size_in_bytes")
                    files[rel] = int(size) if size is not None else None
            if len(items) < PAGE_SIZE:
                break
            offset += len(items)

    visit(root_id, "")
    return files, folders_seen, request_count


def main():
    source_text = input("Local directory [~/NK/Docs]: ").strip() or "~/NK/Docs"
    source = Path(source_text).expanduser().resolve()
    if not source.is_dir():
        fail("Local directory is unavailable.")

    remote_id = input("WorkDrive destination folder ID: ").strip()
    if not remote_id:
        fail("A WorkDrive folder ID is required.")

    print("Refreshing a temporary WorkDrive access token...")
    source_key = source_key_for(source)
    token, api_root = get_access_token()

    started = time.monotonic()
    print("Listing WorkDrive metadata...")
    remote, folder_count, api_requests = list_remote(api_root, token, remote_id)
    print("Scanning local metadata only; file contents are not opened...")
    local, symlink_count = scan_local(source)

    try:
        _, manifest = read_manifest(source_key)
    except ValueError as exc:
        fail(str(exc))
    manifest_exists = manifest is not None
    manifest = manifest or {}

    candidates = 0
    assumed_remote_matches = 0
    unchanged_by_manifest = 0
    changed_by_manifest = 0
    missing_remote = 0
    size_mismatch = 0
    untracked = 0

    for rel, local_meta in local.items():
        remote_size = remote.get(rel, "__missing__")
        if remote_size == "__missing__":
            missing_remote += 1
            candidates += 1
            continue
        if remote_size is None or remote_size != local_meta["size"]:
            size_mismatch += 1
            candidates += 1
            continue

        if manifest_exists:
            previous = manifest.get(rel)
            if previous is None:
                untracked += 1
                candidates += 1
            elif (previous.get("size") == local_meta["size"]
                  and previous.get("mtime_ns") == local_meta["mtime_ns"]):
                unchanged_by_manifest += 1
            else:
                changed_by_manifest += 1
                candidates += 1
        else:
            # Initial bootstrap assumption: same remote path and size means present.
            assumed_remote_matches += 1

    remote_only = sum(rel not in local for rel in remote)

    print("\nDRY RUN ONLY — no uploads, deletions, or manifest changes performed:")
    print(f"  Local regular files: {len(local)}")
    print(f"  Remote files: {len(remote)}")
    print(f"  Candidate uploads: {candidates}")
    print(f"  Missing remotely: {missing_remote}")
    print(f"  Size differs/unavailable: {size_mismatch}")
    print(f"  Remote-only (not deleted): {remote_only}")
    print(f"  Local symlinks skipped: {symlink_count}")
    print(f"  Source key: {source_key}")
    print(f"  Manifest path: {MANIFEST}")
    if manifest_exists:
        print(f"  Unchanged by local manifest: {unchanged_by_manifest}")
        print(f"  Changed by local manifest: {changed_by_manifest}")
        print(f"  Not in manifest: {untracked}")
    else:
        print(f"  Same-size remote files assumed present for initial baseline: {assumed_remote_matches}")
        print("  No manifest entry exists for this source yet.")
    print(f"  Remote folders scanned: {folder_count}")
    print(f"  API list requests: {api_requests}")
    print(f"  Elapsed: {time.monotonic() - started:.2f}s")
    print("\nLimit: same-size edits that preserve local mtime are not detected without hashing contents.")
    print("Remote-only files are reported, never deleted by this dry run.")


if __name__ == "__main__":
    main()
