# Optimising `list_remote()`

## Current bottleneck

`scripts/zoho-wd-api-sync.py:list_remote()` recursively lists each WorkDrive folder with `GET /workdrive/api/v1/files/{folder_id}/files`. It requests pages with a limit of 50, then lists each discovered child folder. There is no documented single request that recursively returns the entire folder tree, so a complete inventory still requires traversing folders and fetching additional pages where needed.

## Options

### 1. Bounded concurrency — best first optimisation

Folder listings become independent as soon as their IDs are discovered. The current recursive implementation waits for a child folder’s full listing before moving to the next sibling. A bounded worker pool could list multiple sibling folders concurrently, reducing serial network latency.

- Keep pagination for each folder and preserve the same path-to-ID/file metadata results.
- Bound concurrency and handle throttling/retries; do not issue unbounded requests.
- Measure request counts and elapsed time before and after.
- The first remote listing remains necessary for planning and remote-only/deletion checks.

### 2. Recent Changes API — promising, but needs validation

Zoho documents a Changes API that issues a token for a resource and returns subsequent file/folder changes. In principle, a persisted change cursor plus a local remote-inventory cache could avoid a full tree traversal on most runs.

Relevant docs:

- [Get start token for recent changes](https://www.zoho.com/workdrive/developer/docs/api/v1/get-start-token.html)
- [Get list of recent changes](https://www.zoho.com/workdrive/developer/docs/api/v1/get-list-of-recent-changes.html)

Caveats to resolve before relying on it:

- The docs do not clearly guarantee that observing a root folder covers all descendants recursively; test nested folders.
- Change events may not directly provide the resource ID/path details needed to update the cache. In the documented example, `data[].id` is an event ID, not necessarily the changed file/folder ID.
- Tokens expire and the feed can be unavailable for a resource. The implementation needs recovery and full-reconciliation behavior.
- A reliable cache must handle create, update, move, trash/delete, and folder changes, including path changes after moves.

Suggested design if validated: do a full baseline listing, persist a remote inventory and Changes API cursor outside the source data, apply each change page transactionally, and periodically reconcile with a full listing. If the token expires or events cannot be interpreted safely, fall back to a full listing.

### 3. Pagination

The documented list-folder endpoint returns at most 50 results per page. Follow its pagination mechanism for additional results; increasing `PAGE_SIZE` beyond the API maximum will not help. Cursor pagination may be preferable to manually constructing offsets if the endpoint provides a next cursor/link, but it does not remove the per-page request requirement.

## Recommendation

Implement and benchmark bounded concurrency first: it is a contained change that preserves the current full-inventory semantics. Separately prototype the Changes API against a nested test tree before designing persistent incremental inventory. Do not replace full traversal with a cache until change coverage and recovery behavior are confirmed.
