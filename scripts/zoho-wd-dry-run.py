#!/usr/bin/env python3
"""Compare local file metadata with a WorkDrive folder; do not upload/delete files.

Reads directory entries and file metadata only (names, sizes, timestamps).
Does not open/read file contents or print local file names.
Requires exported ZOHO_CLIENT_ID, ZOHO_CLIENT_SECRET, and a fresh ZOHO_CODE_TEMP.
"""
import getpass
import json
import os
import sys
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

TOKEN_URL = "https://accounts.zoho.in/oauth/v2/token"
DEFAULT_API = "https://www.zohoapis.in"
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


def local_files(root):
    result = {}
    symlinks = 0
    for current, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = [d for d in dirs if not (Path(current) / d).is_symlink()]
        for name in files:
            path = Path(current) / name
            try:
                info = path.lstat()
            except OSError as exc:
                fail(f"Cannot stat a local file (path hidden): {exc}")
            if path.is_symlink():
                symlinks += 1
                continue
            if not path.is_file():
                continue
            relative = path.relative_to(root).as_posix()
            result[relative] = (info.st_size, int(info.st_mtime * 1000))
    return result, symlinks


def remote_files(api_root, token, root_id):
    files = {}
    requests = 0

    def visit(folder_id, prefix):
        nonlocal requests
        offset = 0
        entries = []
        while True:
            response = api_get(
                api_root,
                token,
                f"/workdrive/api/v1/files/{folder_id}/files",
                {"page[limit]": PAGE_SIZE, "page[offset]": offset},
            )
            requests += 1
            page = response.get("data", [])
            entries.extend(page)
            if len(page) < PAGE_SIZE:
                break
            offset += len(page)

        for item in entries:
            attrs = item.get("attributes", {})
            name = attrs.get("name")
            if not name:
                continue
            relative = f"{prefix}/{name}" if prefix else name
            if attrs.get("is_folder") is True:
                visit(item["id"], relative)
            else:
                size = attrs.get("storage_info", {}).get("size_in_bytes")
                modified_ms = attrs.get("modified_time_in_millisecond")
                files[relative] = {
                    "size": int(size) if size is not None else None,
                    "modified_ms": int(modified_ms) if modified_ms is not None else None,
                }

    visit(root_id, "")
    return files, requests


def main():
    for key in ("ZOHO_CLIENT_ID", "ZOHO_CLIENT_SECRET", "ZOHO_CODE_TEMP"):
        if not os.environ.get(key):
            fail(f"{key} is not exported in this terminal. Do not paste its value here.")

    root_input = input("Local directory to compare [~/NK/Docs]: ").strip()
    local_root = Path(root_input or "~/NK/Docs").expanduser().resolve()
    if not local_root.is_dir():
        fail("Local directory is unavailable.")

    remote_id = input("WorkDrive destination folder ID: ").strip()
    if not remote_id:
        fail("A WorkDrive folder ID is required.")

    print("Exchanging the grant code for a temporary access token...")
    token_payload = urlencode({
        "grant_type": "authorization_code",
        "client_id": os.environ["ZOHO_CLIENT_ID"],
        "client_secret": os.environ["ZOHO_CLIENT_SECRET"],
        "code": os.environ["ZOHO_CODE_TEMP"],
    }).encode()
    token_data = http_json(Request(
        TOKEN_URL,
        data=token_payload,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    ))
    token = token_data.get("access_token")
    if not token:
        fail("Zoho did not return an access token; check the grant code and scopes.")
    api_root = (token_data.get("api_domain") or DEFAULT_API).rstrip("/")

    start = time.monotonic()
    print("Listing WorkDrive folder metadata...")
    remote, request_count = remote_files(api_root, token, remote_id)
    print("Scanning local directory metadata (file contents are not opened)...")
    local, symlinks = local_files(local_root)

    missing = sum(path not in remote for path in local)
    changed_size = sum(
        path in remote
        and remote[path]["size"] is not None
        and size != remote[path]["size"]
        for path, (size, _) in local.items()
    )
    unknown_remote_size = sum(
        path in remote and remote[path]["size"] is None for path in local
    )
    same_size = sum(
        path in remote and remote[path]["size"] == size
        for path, (size, _) in local.items()
    )
    same_size_mtime_match = sum(
        path in remote
        and remote[path]["size"] == size
        and remote[path]["modified_ms"] is not None
        and abs(remote[path]["modified_ms"] - local_mtime) <= 2000
        for path, (size, local_mtime) in local.items()
    )
    same_size_mtime_diff = sum(
        path in remote
        and remote[path]["size"] == size
        and remote[path]["modified_ms"] is not None
        and abs(remote[path]["modified_ms"] - local_mtime) > 2000
        for path, (size, local_mtime) in local.items()
    )
    unknown_remote_mtime = sum(
        path in remote
        and remote[path]["size"] == size
        and remote[path]["modified_ms"] is None
        for path, (size, _) in local.items()
    )
    remote_only = sum(path not in local for path in remote)

    print("\nMetadata comparison (no uploads or deletions performed):")
    print(f"  Local regular files: {len(local)}")
    print(f"  Remote files: {len(remote)}")
    print(f"  Missing remotely: {missing}")
    print(f"  Size differs: {changed_size}")
    print(f"  Same size (contents not checked): {same_size}")
    print(f"  Same size, modification times within 2 seconds: {same_size_mtime_match}")
    print(f"  Same size, modification times differ by over 2 seconds: {same_size_mtime_diff}")
    print(f"  Same size, remote modification time unavailable: {unknown_remote_mtime}")
    print(f"  Remote size unavailable: {unknown_remote_size}")
    print(f"  Remote-only files (not deleted): {remote_only}")
    print(f"  Local symlinks skipped: {symlinks}")
    print(f"  API list requests: {request_count}")
    print(f"  Total elapsed: {time.monotonic() - start:.2f}s")
    print("\nThis is a size-only comparison. Same-size edits cannot be detected without reading contents.")


if __name__ == "__main__":
    main()
