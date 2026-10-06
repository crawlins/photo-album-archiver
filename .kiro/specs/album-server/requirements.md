# Requirements: album server

## Introduction

Step 3 of the plan: the server on the user's Linux machine that the Android app
uploads to. It stores albums, pages and shots, serves them back to the app, and
runs the album step (`albumproc album`, specified in `.kiro/specs/album-pdf/`)
on each album so that its print images and PDF stay current as shots arrive.

The HTTP contract the app needs is defined in the Android app spec
(`.kiro/specs/android-app/design.md`, "Server contract"). This spec implements
that contract unchanged and adds what the app does not use yet: processing
status and downloading results.

There is one user. Accounts, sharing and multi-tenant isolation are out of
scope. So is any image processing beyond what `albumproc` already does and the
small previews the app asks for.

### Glossary

- **Server**: the program specified here, `albumserver`.
- **App**: the Android capture app.
- **Album, page, shot**: as in the Android app spec; ids are UUIDs chosen by
  the app.
- **Data folder**: the one folder holding everything the server stores.
- **Token**: a secret string the app sends as `Authorization: Bearer <token>`.
- **Album step**: `albumproc.process_album`, which turns an album folder into
  print images, a PDF and `report.json`.
- **Outputs**: the album step's files for one page, and the PDF and album
  report built from them.
- **Changed page**: a page whose shots or page size changed since its last
  run.

## Requirements

### Requirement 1: Access tokens

**User story:** As the owner of the server, I want each phone to use its own
revocable token, so that a lost phone can be cut off without touching the
others.

#### Acceptance criteria

1. THE server SHALL reject every API request without a valid token with 401,
   including `GET /api/v1/ping`.
2. WHEN the owner runs `albumserver token create <name>`, THE server SHALL
   generate a random token of at least 256 bits, print it once, and store only
   its SHA-256 hash with the name and creation time.
3. THE server SHALL provide `albumserver token list`, showing each token's
   name, creation time and last use but never the token, and
   `albumserver token revoke <name>`, after which that token gets 401.
4. THE server SHALL compare token hashes in constant time.
5. THE server SHALL NOT write tokens or `Authorization` headers to its logs.
6. WHEN a request is rejected for a bad token, THE server SHALL log the
   client address and the time, so that repeated failures are visible.

### Requirement 2: Network exposure

**User story:** As the owner of the server, I want it safe by default on my
network, so that photos and tokens are not exposed by accident.

#### Acceptance criteria

1. THE server SHALL listen on `127.0.0.1:8080` unless configured otherwise.
2. THE server SHALL serve HTTPS directly WHERE a certificate and key are
   configured, and plain HTTP otherwise.
3. WHEN the server listens on a non-loopback address over plain HTTP, THE
   server SHALL log a warning at startup that tokens and photos travel
   unencrypted.
4. THE server SHALL reject request bodies larger than the configured maximum
   (50 MB by default) with 413 before reading them in full.

### Requirement 3: Album metadata

**User story:** As the app, I want to create, update, list, read and delete
albums, so that the server's albums match the phone's.

#### Acceptance criteria

1. WHEN the server receives `PUT /api/v1/albums/{albumId}` with a name, page
   size, page order and creation time, THE server SHALL create the album or
   replace its metadata, and SHALL respond 200.
2. IF the name is blank, the page size cannot be parsed by `albumproc`, a page
   id is repeated or not a UUID, or the body has unknown keys, THEN THE server
   SHALL respond 400 naming the problem and change nothing.
3. WHEN the page order omits a page that has shots on the server, THE server
   SHALL keep that page after the listed ones in the order its first shot
   arrived, so that a metadata update can never lose shots.
4. THE server SHALL answer `GET /api/v1/albums` with every album's id, name,
   page size, page count, shot count and last change, newest change first.
5. THE server SHALL answer `GET /api/v1/albums/{albumId}` with the album's
   metadata and its pages in order with their shot counts, and with 404 for an
   unknown album.
6. THE server SHALL answer `GET /api/v1/albums/{albumId}/pages/{pageId}` with
   the page's shots in the order they were taken, each with its id, SHA-256,
   size in bytes and time taken, and with 404 for an unknown album or page.

### Requirement 4: Shot upload

**User story:** As the app, I want to upload a shot safely and repeatably, so
that retries never lose or duplicate a photo.

#### Acceptance criteria

1. WHEN the server receives
   `PUT /api/v1/albums/{albumId}/pages/{pageId}/shots/{shotId}` with a JPEG
   body and an `X-Content-SHA256` header, THE server SHALL store the body
   byte for byte and respond 201.
2. IF the body's SHA-256 differs from the header, or the body is not a JPEG,
   THEN THE server SHALL respond 400 and store nothing.
3. WHEN the shot is already stored with the same hash, THE server SHALL respond
   200 and change nothing.
4. WHEN the shot id is already stored with a different hash, THE server SHALL
   respond 409 and change nothing.
5. WHEN the album or page does not exist yet, THE server SHALL create it, an
   unknown album with the name "Untitled" and no page size, and a new page at
   the end of the album, so that a shot is never refused because its metadata
   has not arrived.
6. THE server SHALL record the shot's time taken from its EXIF
   `DateTimeOriginal`, or the time it was received when that is missing.
7. THE server SHALL write the shot under a temporary name, flush it to disk
   and rename it into place before recording it, so that a crash never leaves
   a recorded shot without its complete file.

### Requirement 5: Limits

**User story:** As the owner of the server, I want the same limits as the app
enforced here too, so that a buggy or old client cannot exceed them.

#### Acceptance criteria

1. IF a shot upload would give a page more than 25 shots, THEN THE server
   SHALL respond 422 and store nothing.
2. IF a shot upload or a metadata update would give an album more than 500
   pages, THEN THE server SHALL respond 422 and change nothing.
3. THE server SHALL check each limit in the same transaction that records the
   change, so that concurrent requests cannot exceed it together.

### Requirement 6: Retrieving shots

**User story:** As the app opening an album that is only on the server, I want
to fetch individual shots and small previews, so that I never need to download
a whole album.

#### Acceptance criteria

1. THE server SHALL answer
   `GET /api/v1/albums/{albumId}/pages/{pageId}/shots/{shotId}` with the
   stored JPEG byte for byte, `Content-Type: image/jpeg`, `Content-Length` and
   `X-Content-SHA256`, and with 404 when it is not stored.
2. WHEN the request has `?size=thumb`, THE server SHALL answer with a JPEG
   preview at most 320 px on its long side, the right way up according to the
   shot's EXIF orientation.
3. THE server SHALL make each preview on first request and reuse it after
   that.
4. THE server SHALL send an `ETag` equal to the hash for full shots and
   previews, and SHALL answer 304 to a matching `If-None-Match`.

### Requirement 7: Deleting

**User story:** As the app, I want to delete shots, pages and albums on the
server, so that deletions on the phone carry through.

#### Acceptance criteria

1. WHEN the server receives a `DELETE` for a shot, a page or an album, THE
   server SHALL remove it, and everything under it, and respond 204, also when
   it does not exist.
2. WHEN an album is deleted, THE server SHALL also delete its previews and its
   outputs, and SHALL cancel any processing of it.
3. WHEN a shot is deleted, THE server SHALL delete its preview and treat its
   page as changed; WHEN a page is deleted, THE server SHALL delete its
   previews and mark the album's PDF out of date (Requirement 8).
4. THE server SHALL remove a deleted item from its index before deleting its
   files, so that a crash in between leaves only unindexed files, which
   startup cleanup removes.

### Requirement 8: Processing

**User story:** As someone archiving an album, I want each page processed as
soon as I have finished shooting it, so that results are ready while I am still
working through the album and I never run anything by hand.

#### Acceptance criteria

1. THE server SHALL process each page on its own, and SHALL treat a page as
   changed when a shot is added to it or deleted from it, or the album's page
   size changes.
2. WHEN the album's page order gains a page after a changed page, THE server
   SHALL make the changed page ready for processing at once, because the user
   has moved on to the next page and the app has already uploaded the earlier
   page's shots.
3. WHEN a changed page has had no further change for the idle period (30
   seconds by default), THE server SHALL make it ready for processing.
4. WHEN a page is processed, THE server SHALL run the album step on a one-page
   album folder holding that page's photos and an `album.json` with the
   album's title and page size, producing the page's processed page, print
   image and report.
5. WHEN a page run ends AND no page of the album is changed, ready or running
   AND the album's PDF is out of date, THE server SHALL build the album's PDF
   and album report from every page's latest print image and report, in album
   order. THE PDF SHALL be out of date after any page run, page deletion,
   reorder or change of album name.
6. THE server SHALL run at most the configured number of page runs at a time
   (1 by default), oldest ready first, in a separate process from the one
   serving requests, so that processing never slows uploads.
7. WHEN a shot arrives for or is deleted from a page while that page is being
   processed, THE server SHALL let the run finish and treat the page as changed
   again, so that it is processed again under criteria 2 and 3.
8. WHERE run cancellation is enabled, WHEN a shot arrives for or is deleted
   from a page while that page is being processed, THE server SHALL stop the
   run, discard its output and treat the page as changed. Cancellation is off
   by default and may be implemented after the rest of this requirement.
9. WHILE an album has no page size, THE server SHALL NOT process its pages and
   SHALL report it as waiting for a page size.
10. WHEN the server starts, THE server SHALL treat every page that changed
    since its last successful run as changed, and every interrupted run as not
    run.
11. WHEN a page is deleted, THE server SHALL delete its outputs; WHEN an album
    is deleted, THE server SHALL stop its runs.

### Requirement 9: Results

**User story:** As someone archiving an album, I want to see whether an album
has been processed and download its PDF and print images, so that I can check
and print the results.

#### Acceptance criteria

1. THE server SHALL answer `GET /api/v1/albums/{albumId}/status` with whether
   the album is waiting for a page size, the state of its PDF (`none`,
   `out_of_date` or `current`) and, for every page in order, its processing
   state (`changed`, `ready`, `processing`, `done` or `failed`), the time of
   its last run, and from that run's report its warnings, each with a code and
   message, or its error.
2. THE server SHALL answer `GET /api/v1/albums/{albumId}/pdf` with the album's
   latest PDF, and with 404 when none has been made.
3. THE server SHALL answer `GET /api/v1/albums/{albumId}/pages/{pageId}/print`
   with the page's latest print image, and with 404 when none has been made.
4. WHILE a page is being processed or a PDF is being built, THE server SHALL
   keep serving the previous print image and PDF.

### Requirement 10: Storage

**User story:** As the owner of the server, I want everything in one folder I
can back up, so that restoring the server is copying a folder back.

#### Acceptance criteria

1. THE server SHALL keep its index, photos, previews and outputs under one
   configured data folder, and nothing elsewhere.
2. THE server SHALL use SQLite in write-ahead-log mode for its index, so that
   a crash never corrupts it.
3. WHEN the server starts, THE server SHALL delete temporary files and files
   not in its index, and SHALL log each photo in its index whose file is
   missing.
4. THE server SHALL provide `albumserver check`, which reports index entries
   without files, files without index entries and photos whose hash no longer
   matches, without changing anything.

### Requirement 11: Running the server

**User story:** As the owner of a Linux machine, I want to install and run the
server like any other service, so that it starts on boot and logs to the
journal.

#### Acceptance criteria

1. THE server SHALL be installed with the backend's Python package and started
   with `albumserver serve`.
2. THE server SHALL read its settings from a TOML file given with `--config`,
   with every setting also settable by an `ALBUMSERVER_` environment variable,
   and SHALL refuse to start on an unknown or invalid setting, naming it.
3. THE server SHALL log one line per request (method, path, status, size and
   duration) and each processing run to standard error.
4. THE repository SHALL include a systemd unit file for running the server as
   an unprivileged user.
5. WHEN the server receives SIGTERM, THE server SHALL stop accepting requests,
   finish requests in progress, stop processing workers, and exit.
