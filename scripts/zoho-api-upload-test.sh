#!/usr/bin/env bash
set -euo pipefail
umask 077

# Upload a generated test file to the supplied WorkDrive folder.
# Requires exported ZOHO_CLIENT_ID, ZOHO_CLIENT_SECRET, and ZOHO_CODE_TEMP.
# This script does not read files from ~/NK.

: "${ZOHO_CLIENT_ID:?ZOHO_CLIENT_ID is not set in this shell}"
: "${ZOHO_CLIENT_SECRET:?ZOHO_CLIENT_SECRET is not set in this shell}"
: "${ZOHO_CODE_TEMP:?ZOHO_CODE_TEMP is not set in this shell}"

folder_id='lhmjs6745ad3288ed4a0db391a74cf2801ce0'

if ! command -v curl >/dev/null || ! command -v jq >/dev/null; then
  printf 'ERROR: Install curl and jq, then run this script again.\n' >&2
  exit 1
fi

tmpdir=$(mktemp -d)
trap 'rm -rf "$tmpdir"' EXIT

# Exchange the short-lived grant code. Keep token data in a private temp file.
curl -fsS 'https://accounts.zoho.in/oauth/v2/token' \
  --data-urlencode 'grant_type=authorization_code' \
  --data-urlencode "client_id=$ZOHO_CLIENT_ID" \
  --data-urlencode "client_secret=$ZOHO_CLIENT_SECRET" \
  --data-urlencode "code=$ZOHO_CODE_TEMP" \
  -o "$tmpdir/token.json"

access_token=$(jq -er '.access_token' "$tmpdir/token.json")
api_domain=$(jq -r '.api_domain // "https://www.zohoapis.in"' "$tmpdir/token.json")
api_domain=${api_domain%/}

# Generate a unique 10 MiB file. The file is removed when this script exits.
filename="api-speed-test-$(date +%s).bin"
head -c 10M /dev/urandom > "$tmpdir/$filename"
printf 'Uploading generated 10 MiB test file: %s\n' "$filename"

# Upload directly to WorkDrive and report elapsed time and throughput.
curl -sS -o "$tmpdir/upload-response.json" \
  -w 'HTTP %{http_code}; upload time %{time_total}s; speed %{speed_upload} bytes/s\n' \
  "$api_domain/workdrive/api/v1/upload" \
  -H "Authorization: Zoho-oauthtoken $access_token" \
  -F "parent_id=$folder_id" \
  -F "filename=$filename" \
  -F 'override-name-exist=false' \
  -F "content=@$tmpdir/$filename;filename=$filename"

printf 'WorkDrive response:\n'
jq . "$tmpdir/upload-response.json"
printf '\nThe uploaded test file remains in the WorkDrive API Test folder.\n'
