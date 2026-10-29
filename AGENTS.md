# Zoho WorkDrive Uploader

This project backs up directories from an external SSD to Zoho WorkDrive through its API. The goal is a reliable online-only backup without TrueSync/FUSE bulk-sync behavior.

## Safety rules

- Never inspect, read, or scan source data under `~/NK` or the external SSD’s `NK` directory from the agent, including during debugging. Do not run backup commands on the user's behalf.
- Never expose or record OAuth secrets, grant codes, access tokens, or refresh tokens. Keep OAuth credentials outside this project.
- Do not upload or delete remotely without the user’s explicit authorization. Review dry-run output before any apply run.
- Remote deletion is off by default. `--delete-remote` is batch-only, moves only manifest-tracked files with a matching remote ID to WorkDrive Trash, and requires separate `APPLY` and `DELETE` confirmations. Do not weaken these safeguards.
- Never use Git commands unless the user explicitly asks.

## Project layout and behavior

- `scripts/zoho-wd-api-sync.py` — API uploader; defaults to dry-run. `--all` processes the configured batch directories; `--apply` performs uploads after confirmation.
- `scripts/manifest_store.py` — metadata-only per-source manifest handling.
- `scripts/zoho_auth.py` — OAuth refresh-token helper.
- `state/manifest.json` — visible development manifest; move it to `~/.local/state/zoho-wd-backup/` only when development is complete.
- `.env` — WorkDrive destination folder ID; keep private and ignored by Git. Do not print its contents.
- `docs/decision-log.md` — concise record of decisions and progress.

The manifest compares paths, sizes, and local modification times; it does not hash file contents. A first-run same-path/same-size remote match may be assumed present. Remote-only files are preserved by default. Files over 250 MB are reported and skipped; large-file upload is not implemented.

Keep project scripts and documentation in this directory. Run syntax checks that do not access source data; ask the user to run dry-runs and applies in their credentialed terminal.
