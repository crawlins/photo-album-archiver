# Photo Album Archiver

Turns several phone photos of each photo-album page into clean, printable page
images at 300 DPI, and one PDF per album.

| Part | Status |
| --- | --- |
| `backend/` page processing pipeline (Python + OpenCV) | working on synthetic photos |
| Album assembly, 300 DPI export, PDF | working on synthetic albums |
| Upload API and storage server | not started |
| `android/` capture app (Kotlin) | working against a fake server and an emulator |

## Page pipeline

Input: 1 or more photos of one page, from different angles. They can each show
the whole page (to get rid of glare) or each show part of an oversized page
(to stitch it), or a mix.

1. **Register.** SIFT features and a RANSAC homography for every overlapping
   pair; the best-connected photo becomes the reference and the others are
   chained to it. Photos that don't match anything are dropped and reported.
2. **Find the page** in a low-res mosaic: candidate outlines from edges and
   from colour contrast with the background, fitted to a quadrilateral either
   by convex hull or by intersecting the four longest straight sides (which
   survives glare crossing the page edge).
3. **True proportions** from the perspective geometry of the corners
   (Zhang & He), using the phone's focal length (26 mm equivalent by default,
   or the real one from EXIF via `--focal-35mm`).
4. **Warp** every full-resolution photo straight into the flat page rectangle
   (one resampling step), at roughly the resolution the photos captured.
5. **Fuse.** Tone curves match every shot's exposure and colour to one of
   them. Where shots disagree, the darker one is favoured, because glare only
   ever adds light; edges of partial shots are feathered so seams blend.

The report (JSON) gives which photos were used or dropped, how much of the page
was covered, the glare found in each shot and the colour correction applied.
The capture app can use coverage and glare to ask for another shot.

### Use

```sh
cd backend
python3 -m pip install -e '.[test]'
albumproc page shot1.jpg shot2.jpg shot3.jpg -o page.png --report page.json --debug debug/
albumproc synth samples/ --kind glare -n 4      # make synthetic test photos
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

## Known limits

- The page size cannot be measured from the photos; a wrong size prints the
  page at the wrong size.
- Pages are never rotated automatically; use `rotate` in `album.json`.
- Photos are assumed to be sRGB, which is what phones produce in practice.
- Pages are assumed flat. A page bulging near the album's spine will be
  slightly distorted; that needs a curved-surface model.
- Glare can only be removed where at least one shot sees that spot without it,
  so for oversized pages each region needs two shots from different angles.
- Tested only on synthetic photos so far.
- The Android app has only been tested against a fake server and an emulator
  camera; the real upload server does not exist yet.
- Android ignores the capture screen's portrait lock on large screens
  (tablets, foldables unfolded) from Android 16 on.
