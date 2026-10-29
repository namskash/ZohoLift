# Zoho WorkDrive Backup — Decision Log

**Status:** Direct API uploader in use. Review dry-run plans before applying.

## Goal and constraints

Back up the external SSD to Zoho WorkDrive while keeping files online-only. Never inspect `~/NK` from this agent. Never record OAuth secrets or tokens.

## Decisions

- Use the WorkDrive API, not TrueSync/`rsync`: bulk TrueSync runs were slow, stalled on FUSE, and grew local cache substantially.
- Use a per-source metadata manifest (`state/manifest.json`) to skip known unchanged files. It tracks relative paths, size, mtime, and remote IDs—not contents. Initial same-path/same-size matches may be assumed present; this is not content verification.
- Upload only after reviewing a dry run and typing `APPLY`. Remote-only files are preserved by default.
- Batch-only `--delete-remote` is opt-in. It moves a missing-local file to WorkDrive Trash only if the manifest records it and its remote ID still matches. It requires separate `APPLY` and `DELETE` confirmations and OAuth scope `WorkDrive.files.UPDATE`.
- Files over 250 MB are reported and skipped; large-file upload is not implemented. No deletion of directories.

## Current configuration and operation

- All 13 source directories are active, including `Media`. Manifest-matched unchanged files should be skipped; new/changed candidates anywhere in the list may upload.
- `state/manifest.json` remains visible during development; move it to `~/.local/state/zoho-wd-backup/manifest.json` when development is complete.
- The WorkDrive NK folder ID is in project `.env` (mode `0600`, ignored by `.gitignore`). OAuth refresh credentials remain outside the project with restricted permissions.
- Batch command: `python scripts/zoho-wd-api-sync.py --all` (dry run). Apply with `--all --apply` only after reviewing the plan. Add `--delete-remote` only when intentionally syncing deletions.

## Results and performance

- Docs: four missing files uploaded and verified; unchanged rerun had zero candidates.
- eBooks: 81 files (~210 MB) uploaded and verified; unchanged rerun had zero candidates.
- Audio: initial run hit transient DNS failure; resumed run completed successfully.
- Preflight recursively lists the remote tree serially (50 entries per page) and scans local metadata; it does not read file contents. A 165-request listing took about 34 seconds; Audio preflight took about 39 seconds total. Next optimization: measure remote-list and local-scan time separately, then consider targeted traversal or bounded concurrency. A C rewrite is unlikely to help unless local scanning proves dominant.

## References

- [WorkDrive API: upload file](https://www.zoho.com/workdrive/developer/docs/api/v1/upload-file.html)
- [WorkDrive API: list folder contents](https://www.zoho.com/workdrive/developer/docs/api/v1/list-files-folders-inside-a-folder.html)
- [WorkDrive API: create folder](https://www.zoho.com/workdrive/developer/docs/api/v1/create-folder.html)
- [WorkDrive API OAuth scopes](https://www.zoho.com/workdrive/developer/docs/api/v1/oauth-authentication-scopes.html)
- [TrueSync setup](https://help.zoho.com/portal/en/kb/workdrive/desktop-apps/workdrive-truesync/articles/install-and-set-up-workdrive-truesync-for-linux)
