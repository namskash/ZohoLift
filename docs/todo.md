# Ideas and TO-DO

- [ ] Calculate time taken and other stats (speed of upload), ... - show at the end
- [ ] add colours
- [ ] no need for "APPLY" check. start by default (after all files are uploaded once)
- [ ] --dry-run should be an optional mode. not --apply. Apply should be the default
- [ ] at the start run du on each dirs, show which ones are the heaviest using some UNICODE / ASCII art.
- [ ] No asking for dir, store folder ID in an env var. (have example env var)
- [ ] put dirs in a dedicated .txt file - easy edits. also easier to NOT backup to git
- [ ] config file: --delete-remote, --apply, --dry-run — another place to configure the answers for these. If a user wants to update this, they can just modify the file values of each config. This can be done via a Go script too.
