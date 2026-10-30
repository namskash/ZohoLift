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

## First-time setup

1. **Create OAuth credentials in Zoho.** In the Zoho API Console, create a Self Client, then generate a fresh, one-time grant token with the WorkDrive read/upload scopes. The deletion option additionally requires `WorkDrive.files.UPDATE`. Use the data center for your account; this project currently targets Zoho India (`.zoho.in`). See [the OAuth setup guide](docs/oauth-first-time-setup.md) for scope details.
2. **Provide the client credentials and grant token to the setup shell.** Export `ZOHO_CLIENT_ID`, `ZOHO_CLIENT_SECRET`, and `ZOHO_CODE_TEMP`. If you add these exports to `.zshrc` or `.bashrc`, reload that shell configuration or open a new shell so the variables are available. Then, from the project directory, run:
   ```sh
   python scripts/zoho_auth.py setup
   ```
   This consumes the grant token and saves the refresh token and API domain to `~/.local/state/zoho-wd-backup/credentials.json` with private permissions. Unset `ZOHO_CODE_TEMP` afterward; keep the client ID and secret available in the shell for future syncs.
3. **Configure the uploader.** Run:
   ```sh
   python setup.py
   ```
   It asks for the WorkDrive destination folder ID, whether uploads and remote deletion are enabled by default, and the full path to the local backup root. Get the folder ID from the destination folder URL (the value after `/folders/`). It creates `config.json` and, if absent, `backup-dirs.txt` with example entries.
4. **Choose source directories.** Edit `backup-dirs.txt`: add one directory per line, each relative to the `localDir` backup root. Blank lines and lines beginning with `#` are ignored. For example:
   ```text
   Documents
   Photos/Family
   ```
5. **Preview, then sync.** Run a dry run first to review the plan:
   ```sh
   python scripts/zoho-wd-api-sync.py --dry-run
   ```
   When ready, run `python scripts/zoho-wd-api-sync.py` to apply the configured behavior. Run it again whenever you want to back up changes. Keep `ZOHO_CLIENT_ID` and `ZOHO_CLIENT_SECRET` available in that shell for each run.

`config.json` and `backup-dirs.txt` are private and gitignored. `config_sample.json` shows the configuration shape; never commit real configuration values or OAuth credentials.

## Run behavior

Uploads are enabled by default unless `apply` is false in `config.json` or `--dry-run` is passed. `--apply` and `--dry-run` override the config. Remote deletion is controlled by `deleteRemote` in `config.json`, overridden by `--delete-remote` or `--no-delete-remote`; there is no additional confirmation prompt. Deletion only applies to manifest-tracked files whose remote IDs still match.

At startup, the script reports source directory sizes, then recursively lists the remote tree and scans local metadata. These steps can take several minutes for large trees. It uploads new or changed files, verifies the remote results, and updates the manifest only after verification succeeds.

## Backup behavior

- The per-source manifest is `state/manifest.json`. It tracks paths, file sizes, modification times, and remote IDs; it does not hash file contents.
- On an initial run, a same-path, same-size remote file may be assumed present; this does not verify its contents.
- Remote-only files are preserved unless `--delete-remote` is supplied. Deletion applies only to manifest-tracked files whose remote IDs still match; directories are not deleted.
- Candidate files over 250 MB are reported and skipped.
- Only uploaded files are verified after upload, using upload response metadata and a targeted metadata lookup if size is missing. If reported size disagrees, the script streams a bounded download to count bytes (under 250 MB); it does not save the download. A successful fallback is recorded in the manifest.
- Missing tracked files are moved to Trash in batches of up to 200; the script does not relist the full remote tree after uploads or deletions.
- Logs are written to timestamped files under `logs/`; a separate `.activity.json` report records uploaded and trashed paths, sizes, and local modification times. These files can contain sensitive file paths.

## Project files

- `scripts/zoho-wd-api-sync.py` — primary uploader and dry-run command
- `scripts/zoho_auth.py` — OAuth refresh-token setup and access-token helper
- `scripts/manifest_store.py` — metadata-only manifest handling
- `docs/oauth-first-time-setup.md` — OAuth bootstrap instructions
- `utils/zoho-api-upload-test.sh` — isolated upload test utility (not a backup command)
