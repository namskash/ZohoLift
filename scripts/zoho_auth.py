#!/usr/bin/env python3
"""Securely bootstrap and use a Zoho WorkDrive OAuth refresh token.

Client ID and secret remain in exported environment variables. The refresh token
is stored in ~/.local/state/zoho-wd-backup/credentials.json with mode 0600.
Never prints token values.
"""
import json
import os
import stat
import sys
import tempfile
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

TOKEN_URL = "https://accounts.zoho.in/oauth/v2/token"
STATE_DIR = Path.home() / ".local/state/zoho-wd-backup"
CREDENTIALS = STATE_DIR / "credentials.json"
DEFAULT_API = "https://www.zohoapis.in"


def fail(message):
    print(f"ERROR: {message}", file=sys.stderr)
    raise SystemExit(1)


def env_credentials():
    names = ("ZOHO_CLIENT_ID", "ZOHO_CLIENT_SECRET")
    missing = [name for name in names if not os.environ.get(name)]
    if missing:
        fail(f"Export {', '.join(missing)} in this terminal. Do not paste their values here.")
    return os.environ["ZOHO_CLIENT_ID"], os.environ["ZOHO_CLIENT_SECRET"]


def token_request(data):
    request = Request(
        TOKEN_URL,
        data=urlencode(data).encode(),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urlopen(request, timeout=30) as response:
            result = json.load(response)
    except HTTPError as exc:
        # Do not echo request data or token-bearing material.
        fail(f"Zoho OAuth returned HTTP {exc.code}; check the grant/refresh token and client configuration.")
    except URLError as exc:
        fail(f"Could not reach Zoho OAuth: {exc.reason}")
    if result.get("error"):
        fail(f"Zoho OAuth error: {result['error']}")
    return result


def save_credentials(refresh_token, api_domain):
    STATE_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(STATE_DIR, 0o700)
    payload = {"refresh_token": refresh_token, "api_domain": api_domain}
    fd, temp_name = tempfile.mkstemp(prefix="credentials.", dir=STATE_DIR)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            json.dump(payload, out)
            out.write("\n")
            out.flush()
            os.fsync(out.fileno())
        os.replace(temp_name, CREDENTIALS)
        os.chmod(CREDENTIALS, 0o600)
    except Exception:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


def bootstrap():
    client_id, client_secret = env_credentials()
    code = os.environ.get("ZOHO_CODE_TEMP")
    if not code:
        fail("Export a fresh, unused ZOHO_CODE_TEMP grant code before setup.")
    result = token_request({
        "grant_type": "authorization_code",
        "client_id": client_id,
        "client_secret": client_secret,
        "code": code,
    })
    refresh_token = result.get("refresh_token")
    if not refresh_token:
        fail("Zoho did not return a refresh token; credentials were not saved.")
    api_domain = (result.get("api_domain") or DEFAULT_API).rstrip("/")
    save_credentials(refresh_token, api_domain)
    print(f"Refresh token saved securely to {CREDENTIALS} (permissions 0600).")
    print("The grant code is now consumed. Future runs use the refresh token.")


def get_access_token():
    client_id, client_secret = env_credentials()
    if not CREDENTIALS.is_file():
        fail(f"OAuth is not initialized. Run: python {Path(__file__).name} setup")
    mode = stat.S_IMODE(CREDENTIALS.stat().st_mode)
    if mode & 0o077:
        fail(f"Credential file permissions are too open. Run: chmod 600 '{CREDENTIALS}'")
    try:
        saved = json.loads(CREDENTIALS.read_text(encoding="utf-8"))
        refresh_token = saved["refresh_token"]
    except (OSError, ValueError, KeyError) as exc:
        fail(f"Could not read OAuth credentials: {exc}")

    result = token_request({
        "grant_type": "refresh_token",
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token,
    })
    access_token = result.get("access_token")
    if not access_token:
        fail("Zoho did not return an access token; refresh may be revoked or expired.")
    api_domain = (result.get("api_domain") or saved.get("api_domain") or DEFAULT_API).rstrip("/")
    return access_token, api_domain


def main():
    if len(sys.argv) != 2 or sys.argv[1] != "setup":
        print(f"Usage: {Path(sys.argv[0]).name} setup", file=sys.stderr)
        raise SystemExit(2)
    bootstrap()


if __name__ == "__main__":
    main()
