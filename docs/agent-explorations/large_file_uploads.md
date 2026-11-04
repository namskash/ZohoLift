# Deferred handling of files over 250 MB

## Question

How difficult would it be to identify oversized upload candidates, queue them in `/tmp/`, and upload them after ordinary files using WorkDrive's large-file API?

## Findings

**Feasible, moderate implementation effort.** The local scan already records every regular file's path, size, and mtime. The planner already distinguishes oversized *upload candidates* (files over `MAX_NORMAL_UPLOAD`) from ordinary candidates. Those candidates can be recorded in a temporary metadata-only queue and processed after ordinary uploads.

The queue should contain only files that need uploading, not every local file over 250 MB. A large file that is already represented remotely and unchanged should not be uploaded again. Suggested queue fields: source key/root, relative path, target parent folder ID (or target folder path), size, and scanned `mtime_ns`. Do not put tokens or file contents in `/tmp/`.

Create the queue with restrictive permissions (0600), preferably via Python's secure tempfile APIs; remove it in a `finally` block. Immediately before upload, stat the file again and fail/skip safely if size or mtime changed since planning. This avoids acting on stale queue metadata. Since the source is an external drive, queueing metadata does not make the file itself available if the drive is disconnected.

## WorkDrive upload APIs

- The normal upload endpoint used by this script has a 250 MB limit.
- Zoho documents a stream upload endpoint for files over 250 MB: `POST https://upload.zoho.in/workdrive-api/v1/stream/upload` for this project's India data center. It streams the file as the raw request body and uses headers including `x-filename`, `x-parent_id`, a unique `upload-id`, and `x-streammode: 1`. Required scope: `WorkDrive.files.CREATE`.
- Zoho's docs say the stream API splits the content internally. For files above 1 GB, Zoho recommends its chunk/session upload API instead; maximum size depends on the WorkDrive edition.
- The stream-upload response may not be sufficient to confirm success. Zoho documents an Upload Progress API, which should be used with the `upload-id` to check completion/failure, followed by targeted metadata verification as needed.

References:

- [Upload large files (>250 MB)](https://www.zoho.com/workdrive/developer/docs/api/v1/upload-large-file-250mb.html)
- [Upload progress](https://www.zoho.com/workdrive/developer/docs/api/v1/upload-progress.html)
- [Chunk upload/session flow](https://www.zoho.com/workdrive/developer/docs/api/v1/large-file-upload-as-chunks.html)

## “Batch” means deferred processing, not one bulk request

The documented large-file endpoint accepts a file body per upload; there is no documented multi-file upload request. Queueing the candidates and processing the queue at the end is straightforward, but it still means one upload operation per file. Keep these uploads serial initially: large transfers consume bandwidth and memory/network resources, and parallel large uploads could worsen throttling or destabilize the external drive.

## Integration work required

1. Extend oversized candidate records to include source root, target parent, relative path, size, and scanned mtime; serialize those records to a private `/tmp/` queue (or use a secure temporary JSONL file).
2. Defer those candidates instead of adding them to the current “skipped” manifest-preservation set.
3. After ordinary uploads, read the queue and revalidate each local file's size/mtime.
4. Derive the regional upload host from the configured API domain; stream each file using the documented headers while keeping the OAuth token out of command-line arguments and logs.
5. Use a unique upload ID per file, poll/check the upload-progress endpoint, and verify the resulting remote ID/size. Handle failed or ambiguous uploads without incorrectly updating the manifest or blindly retrying (which could duplicate/overwrite files).
6. Add successfully verified large files to the manifest and activity report. Retain current safe behavior for files above the account/API maximum until chunked upload is implemented and tested.

## Assessment

Deferring candidate metadata to a protected temporary queue is easy. The main work/risk lies in implementing and testing the large-file streaming/progress protocol, mapping each file to its destination parent, handling retries/ambiguous outcomes, and integrating successful results into verification and the manifest. This is **not a bulk upload API**, and support above 1 GB needs a separate chunk/session implementation.
