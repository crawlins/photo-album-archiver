# Photo Album Archiver

Turns several phone photos of each photo-album page into clean, printable page
images at 300 DPI, and one PDF per album.

| Part | Status |
| --- | --- |
| `backend/` page processing pipeline (Python + OpenCV) | working on synthetic photos |
| Album assembly, 300 DPI export, PDF | working on synthetic albums |
| `albumserver` upload API, storage and processing server | working; tested against the app's contract |
| `android/` capture app (Kotlin) | working against a fake server and an emulator |

## Page pipeline

Input: 1 or more photos of one page, from different angles. They can each show
the whole page (to get rid of glare) or each show part of an oversized page
(to stitch it), or a mix.

1. **Register.** SIFT features and a RANSAC homography for every overlapping
   pair; the best-connected photo becomes the reference and the others are
   chained to it. Photos that don't match anything are dropped and reported.
2. **Find the page** in a low-res mosaic and in each photo: candidate
   outlines from closed edges (nested ones too), from colour contrast with
   the background, and from four long straight lines (for outlines that never
   close, e.g. a page only whole in the mosaic). Pages of an open album touch
   with no background between them, so each outline is also cut along
   straight lines running right across it (the join between pages, the
   binding, the edge of a page stack). A candidate is marked down when such
   a line runs just inside its left or right side (it takes in a neighbour's
   edge or a page stack), down its middle (two pages as one), or a wide strip
   in from its top or bottom (the table's edge; a sleeve's thin crimped
   edges belong to the page), and when it takes in background. Photos are
   assumed upright, so neighbours lie left and right. Pages cut off by the
   edge of what was photographed are discounted.
3. **True proportions** from the perspective geometry of the corners
   (Zhang & He), using the phone's focal length (26 mm equivalent by default,
   or the real one from EXIF via `--focal-35mm`).
4. **Warp** every full-resolution photo straight into the flat page rectangle
   (one resampling step), at roughly the resolution the photos captured.
5. **Fuse.** Tone curves match every shot's exposure and colour to one of
   them. Where shots disagree, the darker one is favoured, because glare only
   ever adds light. The weights hand over gradually between shots instead of
   switching per pixel, and edges of partial shots are feathered, so seams
   blend.

The report (JSON) gives which photos were used or dropped, how much of the page
was covered, the glare found in each shot and the colour correction applied.
The capture app can use coverage and glare to ask for another shot.

### Use

```sh
cd backend
python3 -m pip install -e '.[test]'
albumproc page shot1.jpg shot2.jpg shot3.jpg -o page.png --report page.json --debug debug/
albumproc synth samples/ --kind glare -n 4      # synthetic test photos (glare, stitch or pair)
albumproc synth samples/ --kind pair --gap 0    # pages touching, as in an open album
python3 -m pytest
```

## Album: print images and PDF

Input: an album folder with one subfolder of photos per page (`.jpg`, `.png`
or `.tif`), taken in natural order (`page-2` before `page-10`).

```sh
albumproc album my-album/ -o out/ --page-size 10x12in
albumproc print out/pages/001-cover.png -o cover.tif --page-size 10x12in --bleed 0.125in
```

Each page goes through the page pipeline, then is resampled once to exactly
its physical size at 300 DPI (`--dpi`) and written with that resolution and an
sRGB profile. The photos carry no scale, so the page size comes from
`album.json` (the capture app writes it when an album is created), else
`--page-size`, else 8.5x11in. Sizes are written `10x12in`, `254x305mm`,
`letter`, `a4` or `a3`, either way round; the long side goes with the page's
long side. A page whose measured shape is more than 2% off the size is fitted
inside it with a white border and flagged rather than distorted.

An optional `album.json` in the album folder sets the title, the size and the
page order, with per-page overrides:

```json
{
  "title": "Family album 1962",
  "page_size": "10x12in",
  "pages": [
    {"folder": "cover", "name": "Front cover", "page_size": "10.5x12.5in"},
    "page-01",
    {"folder": "page-02", "rotate": 90, "fit": "fit"}
  ]
}
```

Output:

```
out/report.json            every page: status, pipeline report, size, DPI, warnings
out/album.pdf              one PDF page per album page, at its physical size
out/pages/001-cover.png    processed page (lossless), reused on re-runs
out/print/001-cover.jpg    print image (JPEG quality 95; --format png or tiff)
```

A page that fails is reported and left out, and the command exits with 3
(`--placeholder` puts a blank "missing" page in its place; `--strict` stops).
Re-runs only reprocess pages whose photos changed, and only re-print when just
the print options changed (`--force` redoes everything). `--jobs N` processes
pages in parallel. With `SOURCE_DATE_EPOCH` set, the PDF is byte-for-byte
reproducible. Warnings in the report (`low_resolution`, `upsampled`,
`aspect_mismatch`, `incomplete_coverage`, `photos_dropped`) say which pages are
worth shooting again.

## Android capture app

`android/` is the phone side: it photographs each album page several times
and uploads the shots to the server. All image work happens on the backend.

- **Albums.** The hamburger menu creates albums (a name and a page size:
  8.5 × 11 in, A4, A3 or custom), switches between them, and edits or deletes
  them. The app reopens the album you were last shooting, at its last page.
- **Shooting.** The camera preview fills the screen. **Volume up** (or the
  shutter) takes a shot of the current page; **volume down** (or "Next page")
  starts the next page, which is refused while the current page has no shots.
  "Undo" removes a page started by mistake. Limits: 25 shots per page, 500
  pages per album. Flash is off by default because it puts a glare spot in the
  same place on every shot.
- **Reviewing.** Tap a thumbnail or the page number to review shots and pages:
  swipe through a page's shots, pinch to zoom, use the arrows to change page,
  and delete shots or pages (later pages are renumbered).
- **Uploading.** Every shot is kept on the phone and uploaded in the
  background in the order things happened, on Wi-Fi only unless Settings say
  otherwise, retrying until the server confirms it. Uploads survive the app
  being closed and the phone rebooting.
- **Server albums** lists what is on the server. An album that is not on this
  phone can be deleted there, or opened to keep shooting it; opening fetches
  only the album's page list and its last page, and other pages load as you
  look at them.
- **Settings**: the server URL and access token (stored encrypted), "Test
  connection", and the upload network.

### Build and install

Needs JDK 17 or later (a full JDK, with `jlink`) and the Android SDK
(platform 37).

```sh
cd android
./gradlew assembleDebug                 # app/build/outputs/apk/debug/app-debug.apk
./gradlew installDebug                  # onto a connected phone or emulator
./gradlew lint testDebugUnitTest        # lint and the JVM tests
./gradlew connectedDebugAndroidTest     # the emulator tests (needs a running emulator)
```

The JVM tests use Robolectric and an in-memory implementation of the server
contract below (`app/src/sharedTest/.../FakeAlbumServer.kt`); the emulator
tests drive the real UI with the same fake server running on the device,
plus one test that takes a real CameraX shot and checks its EXIF orientation
and focal length.

### Server contract

The upload server implements these calls (`.kiro/specs/android-app/design.md`
has the bodies). Every request carries `Authorization: Bearer <token>`; album,
page and shot ids are UUIDs the app chooses, so retried uploads are
idempotent.

| Request | Meaning |
| --- | --- |
| `GET /api/v1/ping` | 200 when the token is valid |
| `GET /api/v1/albums` | every album with name, page size, page and shot counts, last change |
| `GET /api/v1/albums/{album}` | one album's metadata and its pages in order with shot counts |
| `PUT /api/v1/albums/{album}` | create or replace `{name, page_size, pages, created}` |
| `DELETE /api/v1/albums/{album}` | delete the album and everything made from it |
| `GET /api/v1/albums/{album}/pages/{page}` | one page's shots: id, sha256, bytes, taken |
| `DELETE /api/v1/albums/{album}/pages/{page}` | delete a page and its shots |
| `PUT /api/v1/albums/{album}/pages/{page}/shots/{shot}` | store a JPEG; `X-Content-SHA256` header; 201 new, 200 same, 400 bad hash, 409 different hash, 422 over a limit |
| `GET /api/v1/albums/{album}/pages/{page}/shots/{shot}` | the JPEG as uploaded with `X-Content-SHA256`; `?size=thumb` for a preview of at most 320 px |
| `DELETE /api/v1/albums/{album}/pages/{page}/shots/{shot}` | delete a shot |

The app answers 401 and 403 by pausing uploads until Settings change, retries
5xx and network errors with exponential backoff, retries a 400 on a shot once,
and drops 409, 413 and 422 (keeping the shot on the phone).

## Server

`albumserver` is the server the capture app uploads to. It stores albums,
pages and shots in one data folder, serves them back to the app, and runs the
album step on each page as soon as it has been shot, so that every album's
print images and PDF stay current while you work through it.

```sh
cd backend
python3 -m pip install -e .
albumserver token create pixel-8        # prints the phone's token, once
albumserver serve                       # http://127.0.0.1:8080, data in ~/.local/share/albumserver
```

Enter the server's URL and the token in the app's settings. Each phone gets
its own token; `albumserver token list` shows them (never the tokens) with
when each was last used, and `albumserver token revoke pixel-8` cuts one off.

Settings come from a TOML file (`--config`, see
[`deploy/albumserver.toml`](backend/deploy/albumserver.toml)) and from
`ALBUMSERVER_<NAME>` environment variables, which win. An unknown or invalid
setting stops the server, naming it. The main ones: `data_dir`, `host`
(`127.0.0.1`), `port` (8080), `tls_cert`/`tls_key`, `idle_period_s` (30),
`workers` (1 page run at a time; each can use 1 to 2 GB), `cancel_runs`
(false).

**Limits.** A page holds at most 25 shots and an album at most 500 pages, the
same as the app. A shot beyond either gets 422, as does album metadata
listing more than 500 pages; the checks are made in the same database
transaction as the change, so concurrent uploads cannot exceed them.

**Processing.** A page is processed when the app moves on to a later page, or
after 30 seconds without a new or deleted shot. The page runs through the
album step on its own, in a separate process; once no page of the album is
waiting, the album's PDF is rebuilt from every page's latest print image.
`GET /api/v1/albums/{id}/status` shows each page's state, warnings and
errors; `/pdf` and `/pages/{pageId}/print` download the results. A page whose
shots change mid-run is run again afterwards (with `cancel_runs = true` the
run is stopped instead).

**Data folder.** `index.sqlite` (SQLite, WAL) plus
`albums/<albumId>/photos/<pageId>/<shotId>.jpg`, previews, outputs and the
album's `album.pdf` and `report.json`. Back up the folder to back up the
server. Every file is written under a temporary name and renamed into place
before it is recorded, and removed from the index before it is deleted, so
after a crash startup cleanup only has to delete files the index does not
know. `albumserver check` reports missing photos, unindexed files and photos
whose hash no longer matches, without changing anything.

### Running it as a service

[`deploy/albumserver.service`](backend/deploy/albumserver.service) runs the
server as an unprivileged `albumserver` user with its data in
`/var/lib/albumserver`; the comment at its top has the install steps. It
logs one line per request and per page run to the journal; tokens and
`Authorization` headers are never logged.

### TLS

The token is a password: never send it over plain HTTP outside your own
machine. The server listens on loopback by default and warns at startup when
it listens on another address without TLS. Either:

- **Tailscale** (recommended): the phone and server talk over the tailnet and
  nothing is exposed to the internet. `tailscale serve --bg --https=443
  http://127.0.0.1:8080`, then use `https://<machine>.<tailnet>.ts.net` in the
  app.
- **Caddy** with an automatic certificate, for access without Tailscale:

  ```
  albums.example.com {
      reverse_proxy 127.0.0.1:8080
  }
  ```

- Or set `tls_cert` and `tls_key` to serve HTTPS directly.

## Known limits

- The page size cannot be measured from the photos; a wrong size prints the
  page at the wrong size.
- Pages are never rotated automatically; use `rotate` in `album.json`.
- Photos are assumed to be sRGB, which is what phones produce in practice.
- Pages are assumed flat. A page bulging near the album's spine will be
  slightly distorted; that needs a curved-surface model.
- Glare can only be removed where at least one shot sees that spot without it,
  so for oversized pages each region needs two shots from different angles.
- Tested on synthetic photos and on five real sleeved album pages (two shots
  each, on the `real-test-photos` branch; `tests/test_real.py` runs them when
  `real/` is checked out).
- The Android app has only been tested against a fake server and an emulator
  camera; the real upload server does not exist yet.
- Android ignores the capture screen's portrait lock on large screens
  (tablets, foldables unfolded) from Android 16 on.
