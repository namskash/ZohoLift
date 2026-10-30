<p align="center">
  <img src="assets/zoho-lift-ascii-pagga-font.png" alt="Zoho Lift" />
</p>

# Zoho WorkDrive Uploader

Back up configured local directories to Zoho WorkDrive through its API. Files remain online-only; this project does not use TrueSync or FUSE.

## Requirements

- Python 3
- `curl` for file uploads
- `du` with GNU `--bytes` support for the startup directory-size summary
- A Zoho WorkDrive OAuth client and the required read/upload permissions

For first-time OAuth setup, follow [docs/oauth-first-time-setup.md](docs/oauth-first-time-setup.md). OAuth client credentials are supplied through `ZOHO_CLIENT_ID` and `ZOHO_CLIENT_SECRET`; the refresh token is kept outside this repository under `~/.local/state/zoho-wd-backup/`.

## Configuration

- `config.json` is private and gitignored. Set `localDir` to the local backup root and `WORKDRIVE_FOLDER_ID` to the destination folder ID. The uploader reads these values from `config.json`; `.env` is not used.
- `backup-dirs.txt` is gitignored. Put one directory path per line, relative to `localDir`; blank lines and lines beginning with `#` are ignored.
- `config_sample.json` shows the configuration shape. Do not commit real configuration values.

## Run

Preview first:

```sh
python scripts/zoho-wd-api-sync.py --dry-run
```

Run the backup:

```sh
python scripts/zoho-wd-api-sync.py
```

**Uploads are the default.** The script has no upload confirmation prompt. `--dry-run` scans local and remote metadata and prints the plan without uploading or changing the manifest. Add `--delete-remote` only when intentionally moving missing, manifest-tracked remote files to WorkDrive Trash; the script asks for a separate `DELETE` confirmation.

At startup, the script reports source directory sizes, then recursively lists the remote tree and scans local metadata. These steps can take several minutes for large trees. It uploads new or changed files, verifies the remote results, and updates the manifest only after verification succeeds.

## Backup behavior

- The per-source manifest is `state/manifest.json`. It tracks paths, file sizes, modification times, and remote IDs; it does not hash file contents.
- On an initial run, a same-path, same-size remote file may be assumed present; this does not verify its contents.
- Remote-only files are preserved unless `--delete-remote` is supplied. Deletion applies only to manifest-tracked files whose remote IDs still match; directories are not deleted.
- Candidate files over 250 MB are reported and skipped.
- Verification compares WorkDrive-reported sizes with local metadata. If they disagree, the script streams a bounded download of that file to count bytes; it does not save the download to disk. This fallback is limited to files under 250 MB. A successful fallback is recorded in the manifest to avoid repeated uploads when WorkDrive's listed size remains inaccurate.
- Logs are written to timestamped files under `logs/` with restricted permissions. The directory is gitignored; logs can include file paths and verification metadata.

## Project files

- `scripts/zoho-wd-api-sync.py` — primary uploader and dry-run command
- `scripts/zoho_auth.py` — OAuth refresh-token setup and access-token helper
- `scripts/manifest_store.py` — metadata-only manifest handling
- `docs/oauth-first-time-setup.md` — OAuth bootstrap instructions
- `utils/zoho-api-upload-test.sh` — isolated upload test utility (not a backup command)
