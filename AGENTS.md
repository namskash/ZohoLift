# Zoho WorkDrive Uploader

This project backs up directories from an external SSD to Zoho WorkDrive through its API. The goal is a reliable online-only backup without TrueSync/FUSE bulk-sync behavior.

## Safety rules

- Never inspect, read, or scan the configured source data directories from the agent, including during debugging. Do not run backup commands on the user's behalf.
- Never expose or record OAuth secrets, grant codes, access tokens, or refresh tokens. Keep OAuth credentials outside this project.
- Never use Git commands unless the user explicitly asks.

## Project layout and behavior

- `README.md` — user-facing end-to-end setup and sync guide; keep it aligned with the actual scripts.
- `scripts/zoho-wd-api-sync.py` — API uploader; reads sources from the gitignored `backup-dirs.txt`. Upload behavior defaults from `apply` in `config.json` (currently enabled unless configured otherwise); `--dry-run` overrides it. There is no confirmation prompt before uploads.
- `setup.py` — interactively creates/updates private `config.json` and creates `backup-dirs.txt` with example entries if absent.
- `scripts/manifest_store.py` — metadata-only per-source manifest handling.
- `scripts/zoho_auth.py` — OAuth refresh-token helper; credentials are stored outside the project in `~/.local/state/zoho-wd-backup/credentials.json`.
- `config.json` — private, gitignored settings including local backup root and WorkDrive destination folder ID. `.env` is not used by the uploader.
- `backup-dirs.txt` — private, gitignored source list; entries are paths relative to `localDir` in `config.json`.
- `state/manifest.json` — visible development manifest; move it to `~/.local/state/zoho-wd-backup/` only when development is complete.
- `docs/decision-log.md` — concise record of decisions and progress.

The manifest compares paths, sizes, and local modification times; it does not hash file contents. A first-run same-path/same-size remote match may be assumed present. Remote-only files are preserved unless deletion is explicitly enabled; deletion has no extra confirmation prompt. Files over 250 MB are reported and skipped; large-file upload is not implemented. Logs and activity reports are written under `logs/` and may include sensitive file paths.

Keep project scripts and documentation in this directory. Run syntax checks that do not access source data; the user runs backups in their own credentialed terminal.
