# Zoho WorkDrive Backup — Decision Log

**Status:** Direct API uploader in use. Uploads are the default; use `--dry-run` to preview.

## Goal and constraints

Back up the configured external source directories to Zoho WorkDrive while keeping files online-only. Never inspect source data from this agent. Never record OAuth secrets or tokens.

## Decisions

- Use the WorkDrive API, not TrueSync/`rsync`: bulk TrueSync runs were slow, stalled on FUSE, and grew local cache substantially.
- Use a per-source metadata manifest (`state/manifest.json`) to skip known unchanged files. It tracks relative paths, size, mtime, and remote IDs—not contents. Initial same-path/same-size matches may be assumed present; this is not content verification.
- Uploads are the default; `--dry-run` previews without changes. There is no upload confirmation prompt. Remote-only files are preserved by default.
- Batch-only `--delete-remote` is opt-in. It moves a missing-local file to WorkDrive Trash only if the manifest records it and its remote ID still matches. It requires a separate `DELETE` confirmation and OAuth scope `WorkDrive.files.UPDATE`.
- Files over 250 MB are reported and skipped; large-file upload is not implemented. No deletion of directories.

## Configuration and operation

- User-specific source paths, local roots, destination folder IDs, credentials, and run history belong in private, ignored configuration or external state—not in tracked documentation.
- `state/manifest.json` is development state; move it to `~/.local/state/zoho-wd-backup/manifest.json` when development is complete.
- Command: `python scripts/zoho-wd-api-sync.py` uploads by default; use `--dry-run` to preview. Sources are configured in the gitignored `backup-dirs.txt`; the local root is configured in `config.json` (`localDir`). Add `--delete-remote` only when intentionally syncing deletions.

## Performance

- Preflight recursively lists the remote tree and scans local metadata; it does not read file contents. Measure remote-list and local-scan time separately before choosing further optimizations.

## References

- [WorkDrive API: upload file](https://www.zoho.com/workdrive/developer/docs/api/v1/upload-file.html)
- [WorkDrive API: list folder contents](https://www.zoho.com/workdrive/developer/docs/api/v1/list-files-folders-inside-a-folder.html)
- [WorkDrive API: create folder](https://www.zoho.com/workdrive/developer/docs/api/v1/create-folder.html)
- [WorkDrive API OAuth scopes](https://www.zoho.com/workdrive/developer/docs/api/v1/oauth-authentication-scopes.html)
- [TrueSync setup](https://help.zoho.com/portal/en/kb/workdrive/desktop-apps/workdrive-truesync/articles/install-and-set-up-workdrive-truesync-for-linux)
