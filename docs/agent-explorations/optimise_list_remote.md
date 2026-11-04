# Optimising `list_remote()`

## Current bottleneck

`scripts/zoho-wd-api-sync.py:list_remote()` recursively lists each WorkDrive folder with `GET /workdrive/api/v1/files/{folder_id}/files`. It requests pages with a limit of 50, then lists each discovered child folder. There is no documented single request that recursively returns the entire folder tree, so a complete inventory still requires traversing folders and fetching additional pages where needed.

## Options

### 1. Bounded concurrency — abandoned

An implementation that listed sibling folders concurrently with a maximum of three workers was tried and then abandoned: concurrency of three triggered Zoho rate limiting. Concurrent listing does not reduce request count, and the rate-limit cost outweighs the latency benefit. Do not use or recommend this approach without new evidence that the API limits or request pacing have changed.

The safe baseline is sequential folder traversal with bounded backoff for retryable HTTP/network errors. Any future optimization should first be tested with conservative request pacing and observed rate-limit behavior. The initial remote listing remains necessary for planning and remote-only/deletion checks.

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

Do not pursue the three-worker concurrency approach; it caused rate limiting. Prefer sequential traversal with retry/backoff unless a rate-limit-safe alternative is demonstrated. The Changes API remains a possible way to reduce repeated full traversals, but should be prototyped against a nested test tree before designing persistent incremental inventory. Do not replace full traversal with a cache until change coverage and recovery behavior are confirmed.
