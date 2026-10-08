# Design: album server

## Overview

One Python process serves the HTTP API; a separate worker process runs the
album step one page at a time and assembles each album's PDF. They share a SQLite index and a data folder, and nothing else.

```
 app ──HTTPS──▶ [reverse proxy or tailnet] ──▶ albumserver serve (uvicorn + FastAPI)
                                                 │  auth: bearer token → hash lookup
                                                 │  writes: photo files, index rows, page states
                                                 ▼
                                   DATA/ index.sqlite (WAL)
                                         albums/<albumId>/photos/<pageId>/<shotId>.jpg
                                         albums/<albumId>/thumbs/<shotId>.jpg
                                         albums/<albumId>/work/<pageId>/  one-page album folder
                                         albums/<albumId>/out/<pageId>/   that page's albumproc outputs
                                         albums/<albumId>/album.pdf, report.json
                                                 ▲
                                   processing worker (separate process)
                                     ready pages → process_album on one page
                                     album settled → write_album_pdf
```

The code is a new package, `backend/src/albumserver/`, in the existing
`backend/` project, so it imports `albumproc` directly and installs with it.

## Authentication recommendation

**Per-device bearer tokens, with TLS provided outside the app, preferably by
Tailscale.**

The server has one user, a handful of phones, and runs at home. What has to be
protected is the photos and the ability to delete them, against someone on the
same network or the internet. That calls for:

- **Static per-device tokens**, created on the server with
  `albumserver token create pixel-8`, shown once, stored only as a SHA-256
  hash, and revocable one by one. A token is 32 random bytes (256 bits), so
  hashing with plain SHA-256 is enough: there is nothing to brute-force, which
  is why passwords need a slow hash and these do not. This is exactly what the
  app spec already sends (`Authorization: Bearer`), it needs no login screen,
  and it keeps working offline-first uploads simple: no tokens to refresh in a
  background worker.
- **Encryption in transit from something built for it**, not from the server.
  A bearer token over plain HTTP is a password in clear text. The easiest
  correct setup is to run the server on a **Tailscale** tailnet: the phone and
  server talk over WireGuard, nothing is exposed to the internet, and
  `tailscale serve` can add HTTPS with a real certificate for the
  `*.ts.net` name. The alternative for access without Tailscale is **Caddy** in
  front of the server with an automatic Let's Encrypt certificate. The server
  itself listens on `127.0.0.1` by default and can terminate TLS directly
  (uvicorn's `ssl_certfile` / `ssl_keyfile`) for anyone who prefers that.

Rejected for the MVP:

- **Username and password with sessions or JWTs**: adds a login screen, token
  refresh and password storage for one user, with no gain over a long random
  token.
- **OAuth / OpenID Connect**: needs an identity provider and is built for many
  users and third-party clients.
- **Mutual TLS (client certificates)**: strong, but installing a client
  certificate on Android and handling it in OkHttp is fiddly, and it has to be
  re-done per phone; Tailscale gives the same device-level trust with far
  less effort.
- **No auth on a trusted LAN**: anyone on the Wi-Fi (guests, IoT devices)
  could read or delete every album.

## Key decisions

### Framework

**FastAPI on uvicorn.** The API is small, but FastAPI gives request
validation from Pydantic models (Requirement 3.2), streaming request bodies
for uploads, `FileResponse` with `Content-Length` for downloads, and a test
client, with little code. uvicorn handles TLS and graceful shutdown on
SIGTERM (Requirement 11.5). New runtime dependencies: `fastapi`,
`uvicorn[standard]`, `pydantic>=2`. Pillow is already added by the album step.

### Index in SQLite, photos as files

Photos are files, because the album step reads files and the server must
return them byte for byte. Everything else, including the page order, shot
hashes, tokens and processing state, is in one SQLite database in WAL mode,
because the limits must be checked atomically (Requirement 5.3) and listing
albums must not walk the disk. Writes go through one `Store` class using
`BEGIN IMMEDIATE` transactions, which serialise writers and make the limit
checks race-free without any locking of our own.

Ordering rules that keep files and index consistent (Requirements 4.7, 7.4,
10.3):

- **Add**: write `<shotId>.jpg.tmp`, fsync, rename, then insert the row. A
  crash leaves at most an unindexed file.
- **Delete**: delete the rows, commit, then delete files. A crash leaves at
  most unindexed files.
- **Startup**: delete `*.tmp` and any file under `photos/` or `thumbs/` with
  no row; log any row whose file is missing.

### Upload handling

The body is streamed to the temp file in 1 MB chunks while being hashed, so a
12 MP JPEG is never held in memory, and a body larger than the limit is cut
off with 413 (Requirement 2.4). After the hash matches, the file must start
with the JPEG SOI marker and open with Pillow's `verify()`; anything else is
a 400. `DateTimeOriginal` is read from EXIF with Pillow for the time taken.

Repeat uploads are cheap: the shot id is looked up before reading the body,
and when it is stored with the header's hash the server answers 200 and
discards the body unread.

A shot for an unknown album or page creates it (Requirement 4.5). The app
always sends metadata first, so this only happens after a server restore or a
metadata request lost to a bug; refusing would wedge the phone's upload queue,
because the app retries every failure other than auth errors, 409 and 422
forever.

### Page order

The album's `pages` list from the app is the order. Pages that have shots on
the server but are missing from the list are kept after the listed ones
(Requirement 3.3), because the app sends a deletion as an explicit `DELETE`,
never by omission, so an omitted page with shots means the metadata is stale,
not that the page should go. A listed page with no shots yet is kept too,
because the app sends a new page's metadata before its first shot.

### Processing

Processing is per page, because pages finish one at a time while the user
works through the album, and a page is the album step's unit of work anyway.

**When a page is ready.** Each page row carries `changed_at` (set by the API
whenever a shot is added or deleted, or the album's page size changes) and
`state`. The API sets a changed page `ready` at once when a metadata update
adds a page after it, which is the app's "Next page": the app uploads in the
order things happened, so by the time the new page's metadata arrives every
shot of the earlier page has arrived too. Otherwise the worker sets a changed
page `ready` once `changed_at` is 30 seconds old (`idle_period_s`), which
covers the last page of a session and edits to earlier pages. The 30 seconds
keeps a burst of shots of one page from starting a run after each shot.

**Running a page.** The worker takes the oldest `ready` page, marks it
`processing` with the `changed_at` it started from, and in a child process:

1. Builds `work/<pageId>/` as a one-page album folder: a `page/` subfolder of
   hard links to the page's photos (no copies), and `album.json` with the
   album's title and page size.
2. Runs `albumproc.process_album(work/<pageId>, out/<pageId>, opt)`.
3. Records `done` or `failed`, the run time and error, unless `changed_at`
   moved during the run, in which case the page goes back to `changed`
   (Requirement 8.7) and is picked up again by the rules above.

Running the album step on a one-page album reuses all of its logic, including
its reuse by content hash: a page size change finds the processed page
unchanged and only redoes the print image. Every output goes through a temp
name, so the previous print image is served until the new one is in place.

**Assembling the album.** When a page run ends, the worker checks the album:
if it has no `changed`, `ready` or `processing` pages and `pdf_stale` is set,
it builds `album.pdf` with `albumproc.pdf.write_album_pdf` from the pages'
print images in album order, and writes an album `report.json` that lists the
page reports in order. Page runs, page deletions, reorders and renames set
`pdf_stale`. A reorder or rename alone needs no page run, so the API queues
an assembly directly in that case.

**Concurrency.** `workers` (default 1) bounds simultaneous page runs; a page
run of four 12 MP shots can use 1 to 2 GB. Assemblies are cheap (no image is
decoded) and run in the worker loop itself.

**Cancellation (deferred).** With `cancel_runs = true`, the API notes the
page id when a shot for a `processing` page arrives or is deleted; the worker
terminates that child process, deletes the run's temp outputs and returns the
page to `changed`. It is off by default and is the last task, because
without it the only cost is a wasted run whose result is replaced shortly
after. Deleting an album or page always stops its runs, cancellation or not.

### Previews

Made on first request with Pillow: `ImageOps.exif_transpose`, `thumbnail
((320, 320))`, JPEG quality 80, written temp-then-rename to
`thumbs/<shotId>.jpg`. The preview's ETag is the shot's hash plus `-thumb`.

## Components

All under `backend/src/albumserver/`.

| Module | Contents |
| --- | --- |
| `config.py` | `Settings` (Pydantic settings): `data_dir`, `host` (`127.0.0.1`), `port` (8080), `tls_cert`, `tls_key`, `max_body_mb` (50), `idle_period_s` (30), `workers` (1), `cancel_runs` (false), `max_pages` (500), `max_shots` (25); TOML file plus `ALBUMSERVER_` env vars; unknown keys rejected |
| `store.py` | `Store`: SQLite schema and migrations, all reads and transactional writes, limit checks, token hashes, page states |
| `files.py` | paths under the data folder, temp-then-rename writes with fsync, streaming hash, startup cleanup, `check` |
| `auth.py` | token generation, hashing, FastAPI dependency doing a constant-time (`hmac.compare_digest`) lookup and recording last use |
| `api.py` | FastAPI app and the routes below, Pydantic request and response models |
| `thumbs.py` | preview generation and caching |
| `worker.py` | ready-page selection, one-page work folders, page runs in child processes, album assembly, cancellation |
| `cli.py` | `albumserver serve`, `token create|list|revoke`, `check` |

### Schema

```sql
CREATE TABLE album (id TEXT PRIMARY KEY, name TEXT NOT NULL, page_size TEXT,
                    created TEXT NOT NULL, updated TEXT NOT NULL,
                    pdf_stale INTEGER NOT NULL DEFAULT 0, pdf_built TEXT);
CREATE TABLE page  (id TEXT PRIMARY KEY, album_id TEXT NOT NULL REFERENCES album ON DELETE CASCADE,
                    position INTEGER NOT NULL, UNIQUE (album_id, position),
                    changed_at TEXT, state TEXT NOT NULL DEFAULT 'changed',
                    run_started_from TEXT, last_run TEXT, last_error TEXT);
CREATE TABLE shot  (id TEXT PRIMARY KEY, page_id TEXT NOT NULL REFERENCES page ON DELETE CASCADE,
                    sha256 TEXT NOT NULL, bytes INTEGER NOT NULL, taken TEXT NOT NULL,
                    received TEXT NOT NULL);
CREATE TABLE token (name TEXT PRIMARY KEY, hash TEXT NOT NULL UNIQUE,
                    created TEXT NOT NULL, last_used TEXT);
```

Positions are rewritten in full on each metadata update, inside the
transaction (`UNIQUE` is checked at statement end, so a renumber is done by
first moving positions to negative values).

### Routes

The requests from the app spec, plus three for results:

| Route | Status codes |
| --- | --- |
| `GET /api/v1/ping` | 200 |
| `GET /api/v1/albums` | 200 |
| `PUT /api/v1/albums/{albumId}` | 200, 400, 422 |
| `GET /api/v1/albums/{albumId}` | 200, 404 |
| `DELETE /api/v1/albums/{albumId}` | 204 |
| `GET /api/v1/albums/{albumId}/pages/{pageId}` | 200, 404 |
| `DELETE /api/v1/albums/{albumId}/pages/{pageId}` | 204 |
| `POST /api/v1/albums/{albumId}/pages/{pageId}/move` | 204, 400, 404, 409, 422 (shot-actions spec) |
| `PUT /api/v1/albums/{albumId}/pages/{pageId}/shots/{shotId}` | 200, 201, 400, 409, 413, 422 |
| `GET /api/v1/albums/{albumId}/pages/{pageId}/shots/{shotId}[?size=thumb]` | 200, 304, 404 |
| `DELETE /api/v1/albums/{albumId}/pages/{pageId}/shots/{shotId}` | 204 |
| `GET /api/v1/albums/{albumId}/status` | 200, 404 |
| `GET /api/v1/albums/{albumId}/pdf` | 200, 404 |
| `GET /api/v1/albums/{albumId}/pages/{pageId}/print` | 200, 404 |

Every route requires a token (401 otherwise); path ids must be UUIDs (400
otherwise). Error bodies are `{"error": "<code>", "message": "<text>"}`.
Request and response bodies are the JSON shapes in the app spec; `status`
returns:

```json
{
  "waiting_for_page_size": false,
  "pdf": "out_of_date",
  "pdf_built": "2026-10-04T02:10:00Z",
  "pages": [
    {"id": "5f0c…", "number": 1, "state": "done", "last_run": "2026-10-04T02:09:12Z", "warnings": []},
    {"id": "a91e…", "number": 2, "state": "failed", "last_run": "2026-10-04T02:09:40Z",
     "error": "no photos registered",
     "warnings": [{"code": "photos_dropped", "message": "1 of 3 photos did not match"}]},
    {"id": "07b2…", "number": 3, "state": "processing", "last_run": null, "warnings": []}
  ]
}
```

## Deployment

`deploy/albumserver.service`: `User=albumserver`, `ExecStart=albumserver serve
--config /etc/albumserver.toml`, `StateDirectory=albumserver` for the data
folder, `NoNewPrivileges`, `ProtectSystem=strict`, `ProtectHome=true`,
`PrivateTmp=true`. The README covers the two recommended TLS setups:
`tailscale serve --bg --https=443 http://127.0.0.1:8080`, or a three-line
Caddyfile with `reverse_proxy 127.0.0.1:8080`.

## Error handling

| Situation | Behaviour |
| --- | --- |
| Missing or bad token | 401, logged with client address |
| Bad JSON, unknown key, bad page size, blank name | 400 with the field |
| Body over the size limit | 413, temp file removed |
| Hash mismatch or not a JPEG | 400, temp file removed |
| Same shot id, different hash | 409 |
| Limit exceeded | 422 naming the limit |
| Disk full during upload | 507, temp file removed |
| Album step fails on a page | page `failed`, error in `status`, left out of the PDF, retried on its next change |
| Album has no page size | state `waiting_for_page_size`, not processed |
| Crash mid-write | startup cleanup removes temp and unindexed files |

## Testing strategy

- **Contract tests** with FastAPI's `TestClient` on a temporary data folder,
  one per row of the app spec's server contract, using the app spec's JSON
  examples, so the server and app cannot drift apart.
- **Auth**: no token, wrong token, revoked token, `token list` never prints a
  token, logs contain no token.
- **Uploads**: new 201, repeat 200 without reading the body, 409, bad hash,
  non-JPEG, over-size, auto-created album and page, EXIF time, limit 422s,
  and two concurrent uploads at the 25-shot boundary where exactly one wins.
- **Metadata**: create, replace, reorder, omitted page with shots kept,
  validation errors, 501 pages.
- **Retrieval**: byte-identical full shot, thumb size and orientation, ETag
  and 304.
- **Deletion and recovery**: each delete, idempotence, and a simulated crash
  between index and file steps followed by startup cleanup and `check`.
- **Processing**: with `albumproc.process_album` and a clock replaced by
  fakes: a page becomes ready at once when a later page is added and after
  30 idle seconds otherwise, not before; a shot during a run sends the page
  back to `changed` and it runs again; the PDF is built only once no page is
  pending and is rebuilt after a reorder without page runs;
  `waiting_for_page_size`; runs stop on album and page deletion; with
  `cancel_runs` the run is stopped and its output discarded; and the one-page
  work folder holds hard links and the right `album.json`. One end-to-end test on a small synthetic album from
  `albumproc.synth` checks a PDF comes back from `/pdf`.

## Dependencies on other specs

- The album step (`process_album`, `write_album_pdf`, `parse_page_size`,
  `report.json`) from the
  album-pdf spec must be implemented before task 8 here; everything before it
  can be built and tested without it.

## Out of scope

- Accounts, multiple users, sharing links.
- Syncing an album edited from two phones at once.
- Rate limiting and brute-force lockout (tokens are 256-bit random).
- Backups (the data folder is designed to be backed up with any file tool).
- A web interface.
