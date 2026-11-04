#!/usr/bin/env python3
"""Read-only OAuth and WorkDrive API connectivity check."""
import json
import sys
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from zoho_auth import get_access_token


def main():
    project_dir = Path(__file__).resolve().parents[1]
    try:
        config = json.loads((project_dir / "config.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        print("FAIL: Could not read config.json.", file=sys.stderr)
        return 1

    folder_id = config.get("WORKDRIVE_FOLDER_ID")
    if not isinstance(folder_id, str) or not folder_id.strip():
        print("FAIL: WORKDRIVE_FOLDER_ID is missing from config.json.", file=sys.stderr)
        return 1

    try:
        token, api_root = get_access_token()
    except SystemExit as exc:
        print("OAuth refresh failed; see the safe error above.", file=sys.stderr)
        return int(exc.code or 1)
    print("PASS: OAuth token refresh succeeded.")

    query = urlencode({"page[limit]": 1, "page[offset]": 0})
    url = f"{api_root}/workdrive/api/v1/files/{folder_id}/files?{query}"
    request = Request(url, headers={
        "Authorization": f"Zoho-oauthtoken {token}",
        "Accept": "application/vnd.api+json",
    })
    started = time.monotonic()
    try:
        with urlopen(request, timeout=30) as response:
            result = json.loads(response.read())
        elapsed = time.monotonic() - started
        items = result.get("data", [])
        print(f"PASS: WorkDrive folder listing succeeded ({len(items)} item(s) returned, {elapsed:.1f}s).")
        return 0
    except HTTPError as exc:
        print(f"FAIL: WorkDrive API returned HTTP {exc.code}.", file=sys.stderr)
    except URLError as exc:
        reason = "timeout" if isinstance(exc.reason, TimeoutError) else str(exc.reason)
        print(f"FAIL: WorkDrive API request failed ({reason}).", file=sys.stderr)
    except (json.JSONDecodeError, ValueError):
        print("FAIL: WorkDrive API returned an invalid JSON response.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
