# Design: Android capture app (MVP)

## Overview

A single-activity Kotlin app with three screens (capture, shot viewer,
settings) and a navigation drawer. Shots are written to app storage and
recorded in a local database first; a background worker uploads them to the
server. The capture path never waits on the network.

```
 Volume keys ─┐
 Buttons ─────┼─▶ CaptureViewModel ──takeShot──▶ CameraX ImageCapture
              │        │                            │ JPEG + EXIF
              │        │                            ▼
              │        ├──────────────▶ ShotStore (files) ── tmp, rename
              │        ▼
              │   AlbumRepository ──▶ Room (albums, pages, shots, uploads)
              │        │                       ▲
              │        └─ enqueue ──▶ WorkManager UploadWorker ──HTTP──▶ server
              │
 DataStore: last album id, settings      EncryptedSharedPreferences: token
```

The code lives in a new top-level `android/` folder next to `backend/`, as one
Gradle project with a single `app` module.

## Key decisions

### Platform and libraries

- **Kotlin, Jetpack Compose, Material 3**, single activity. Compose's
  `ModalNavigationDrawer` gives the hamburger menu directly.
- **minSdk 26** (Android 8.0), **targetSdk** the latest stable API at
  implementation time. API 26 covers nearly every phone in use and gives
  adaptive icons, `java.time` and a modern `NotificationChannel` without
  desugaring.
- **CameraX** (`camera-core`, `camera-camera2`, `camera-lifecycle`,
  `camera-view`) for preview and capture. It handles device quirks and
  writes EXIF, including orientation and the lens focal length, which the
  backend's `--focal-35mm` can use instead of its 26 mm default.
- **Room** for albums, pages, shots and upload state; **DataStore** for the
  last album and settings; **EncryptedSharedPreferences** (androidx.security)
  for the token.
- **WorkManager** for uploads and downloads, because it survives process death and reboots
  and applies network constraints and backoff (Requirement 8.2 to 8.4).
- **OkHttp** for HTTP; the API is ten calls, so Retrofit is not needed.
- Gradle Kotlin DSL with a version catalog (`gradle/libs.versions.toml`).

### The capture screen owns the volume keys

Volume keys reach the activity's `onKeyDown` / `onKeyUp` before the system
changes the volume. `MainActivity` forwards `KEYCODE_VOLUME_UP` and
`KEYCODE_VOLUME_DOWN` to a `VolumeKeyRouter`, which acts only when the capture
screen has registered itself as active (resumed, album open, no drawer or
dialog showing). When it acts, it returns `true` to consume the key; otherwise
it returns `false` and the system adjusts the volume as normal
(Requirement 6.5).

The action fires on the first `ACTION_DOWN` (`repeatCount == 0`); repeats are
consumed but ignored, and so is the matching `ACTION_UP`, so holding a key
neither changes the volume nor fires twice (Requirement 6.4).

Mapping volume up to the shutter matches the stock camera app's convention, so
it is what users expect. Volume down advances the page, which is the less
frequent action.

### Shots and pages

The current page is always the album's last page. The app never inserts pages
in the middle or reorders them in this version, which keeps "where does the
next shot go" trivially answerable and matches how an album is shot, front to
back. Reviewing earlier pages (Requirement 11) only looks; adding shots to an
earlier page is out of scope here.

Each page has a stable UUID and a `position` (its displayed page number).
Deleting a page renumbers the later pages by rewriting `position`, but their
ids, file folders and server paths stay the same, so a deletion never moves or
re-uploads a shot. Page order travels to the server in the album metadata.

Deleting the only shot of an inner page deletes the page too (Requirement
12.4). The last page is exempt because it is the current page, and an empty
current page is already allowed, the same state as just after "Next page".

"Next page" does nothing to an empty page (Requirement 5.2), so empty pages
never exist except page 1 of a new album before its first shot, and that page
is created lazily by the first shot (Requirement 4.2). Undo removes the new
empty page and is offered as a snackbar.

### Limits

An album holds at most 500 pages and a page at most 25 shots (Requirements
4.9 and 5.5). Several shots per page are encouraged, and 25 is far more than
glare removal or stitching needs, so the shot limit only stops a stuck key or
a runaway session; the page limit keeps one album's upload and processing run
bounded. Both are constants in one place (`Limits.kt`), checked in
`AlbumRepository` inside the same transaction that adds the shot or page, so
the buttons and the volume keys cannot get around them. The UI greys out the
button and the key is consumed with a short message rather than passed to the
system, so the user is never surprised by a volume change at the limit. The
server enforces the same limits.

Shot capture is serialised by a `Mutex` in the ViewModel: a press while a
capture is in flight is dropped, not queued (Requirement 4.6), because a queued
shot would fire after the user has moved the phone.

### File writes and the database

`ImageCapture.takePicture` writes to `shots/<albumId>/<pageId>/<shotId>.jpg.tmp`.
On success the file is `fsync`ed, renamed to `.jpg`, and only then is the
`Shot` row inserted (Requirement 10.3). On startup, any `.tmp` files and any
`.jpg` without a row are deleted (Requirement 10.4). Shot ids are UUIDv4s
generated on the phone, which makes uploads idempotent without a round trip.

The on-phone layout has one folder per page, like the backend's album folder;
page order lives in the database, not in folder names, so renumbering never
renames anything:

```
files/shots/<albumId>/
  <pageId>/<shotId>.jpg
```

Shots go in app-specific storage (`Context.filesDir`), not the shared gallery:
they are working material, not photos the user wants mixed into their camera
roll, and app storage needs no storage permission.

### Flash and exposure

Flash defaults off because a flash on a glossy album sleeve is a guaranteed
glare spot in the same place on every shot, which the backend cannot remove.
The toggle is for matte pages in dark rooms, and resets to off when the app
restarts. Exposure and focus are left to CameraX's continuous auto modes;
tap-to-focus on the preview is provided by `PreviewView`.

### Orientation

The capture screen is locked to portrait so the controls do not jump around as
the phone tilts over an album. CameraX's `ImageCapture.targetRotation` is
updated from an `OrientationEventListener`, so each JPEG's EXIF orientation
reflects how the phone was actually held, which the backend honours when it
reads the photo (Requirement 10.2).

### Upload

Each shot, each shot deletion and each album metadata change becomes a row in
an `upload_op` table. One unique WorkManager job (`ExistingWorkPolicy.KEEP`,
network constraint from settings, exponential backoff from 30 s) drains the
table in insertion order and deletes each row once the server returns 2xx.
Ordering by insertion means metadata reaches the server before or alongside
the shots it describes, and it is what the server's processing relies on
(Requirement 8.9): "Next page" queues `ALBUM_META` after every `PUT_SHOT` of
the page being left, and creating an album, or its page 1 on the first shot,
queues `ALBUM_META` before that shot. When the server receives metadata naming
a new page, it can process the previous page at once. Each `ALBUM_META` row
therefore records the album's page order at the moment it is queued, and that
is the order it sends; reading the current order at send time would let an
older queued row announce a page before the shots queued ahead of it. The
album's name and page size are read when the row is sent. A failed op therefore
blocks the ops behind it rather than being skipped, apart from the responses
below that are dropped.

Requests are idempotent by construction: the shot id and content SHA-256 are
in the request, so a repeated upload is a no-op on the server (Requirement
8.5). Responses are handled as follows:

| Response | Handling |
| --- | --- |
| 2xx (200, 201, 204) | done; row deleted |
| 401, 403 | queue paused; persistent "Check server settings" banner (Requirement 8.8) |
| 409, 422 | logged and dropped; the app never reuses a shot id and enforces the same limits, so either means a bug |
| 400 on a shot | retried once, since a hash mismatch can be corruption in transit; then logged and dropped with the shot kept on the phone |
| 413 | logged and dropped with the shot kept on the phone; the server's body limit (50 MB) is far above any phone JPEG |
| 507, 5xx, network errors | retried with backoff |

Shots are kept on the phone after upload (Requirement 7.2). Phones have the
room, it lets the user re-upload to a rebuilt server, and the space problem is
handled by the low-storage warning rather than by silently discarding
originals.

### Server contract (implemented in step 3)

All requests carry `Authorization: Bearer <token>`. Album, page and shot ids
are UUIDs chosen by the app.

| Request | Body | Meaning |
| --- | --- | --- |
| `GET /api/v1/ping` | none | connection test; 200 when the token is valid |
| `GET /api/v1/albums` | none | list every album on the server, below |
| `GET /api/v1/albums/{albumId}` | none | one album's metadata and its pages with shot counts, below |
| `GET /api/v1/albums/{albumId}/pages/{pageId}` | none | one page's shots, below |
| `GET /api/v1/albums/{albumId}/pages/{pageId}/shots/{shotId}` | none | the shot's JPEG exactly as uploaded, with `X-Content-SHA256` and `Content-Length`; with `?size=thumb`, a JPEG preview at most 320 px on its long side; 404 if absent |
| `DELETE /api/v1/albums/{albumId}` | none | delete an album with its photos and every output made from it; 204 even if absent |
| `PUT /api/v1/albums/{albumId}` | JSON, below | create or replace album metadata |
| `PUT /api/v1/albums/{albumId}/pages/{pageId}/shots/{shotId}` | JPEG; header `X-Content-SHA256` | store a shot; 201 when new, 200 if already stored with that hash, 400 if the body does not match the hash or is not a JPEG, 409 if stored with a different hash, 422 if the page already has 25 other shots or would be the album's 501st page |
| `DELETE /api/v1/albums/{albumId}/pages/{pageId}/shots/{shotId}` | none | remove a shot; 204 even if absent |
| `DELETE /api/v1/albums/{albumId}/pages/{pageId}` | none | remove a page and its shots; 204 even if absent |
| `POST /api/v1/albums/{albumId}/pages/{pageId}/move` | JSON `{"shots": [ids]}` | move the listed shots of the album to the page, creating it at the end if unknown; shots already there or unknown are skipped; 204, 404 for an unknown album, 422 past a limit (shot-actions spec) |

```json
{
  "name": "Rawlins family 1962-1968",
  "page_size": "8.5x11in",
  "pages": ["5f0c…", "a91e…", "07b2…"],
  "created": "2026-10-04T01:09:19Z"
}
```

`page_size` is always set, so the server can run `albumproc album` without
asking for one. `pages` lists page ids in album
order and is the only source of page numbers. The server stores each album as
an album folder, one subfolder per page id, plus an `album.json` whose `pages`
list gives each folder in this order with the name "Page N", which is exactly
what the album step reads. A metadata update with more than 500 pages gets a
422. How the app handles each response is under "Upload" above.

The server (`.kiro/specs/album-server/`) also offers processing status, the
album PDF and each page's print image. The MVP app does not use them.

`GET /api/v1/albums` returns the albums newest change first; the list is short
(one entry per physical album), so it is not paginated:

```json
{
  "albums": [
    {
      "id": "3d2a…",
      "name": "Rawlins family 1962-1968",
      "page_size": "8.5x11in",
      "pages": 42,
      "shots": 131,
      "updated": "2026-10-04T01:27:08Z"
    }
  ]
}
```

`GET /api/v1/albums/{albumId}` returns the album and its pages in order, with
shot counts but not the shots, so it stays small however big the album is:

```json
{
  "id": "3d2a…",
  "name": "Rawlins family 1962-1968",
  "page_size": "8.5x11in",
  "created": "2026-10-04T01:09:19Z",
  "pages": [
    {"id": "5f0c…", "shots": 3},
    {"id": "a91e…", "shots": 4}
  ]
}
```

`GET /api/v1/albums/{albumId}/pages/{pageId}` returns one page's shots in the
order they were taken:

```json
{
  "id": "5f0c…",
  "shots": [
    {"id": "b7e1…", "sha256": "9f86…", "bytes": 4183022, "taken": "2026-10-04T01:12:40Z"}
  ]
}
```

The server keeps each shot's original bytes, which the album step needs
anyway, so a full fetch is byte-identical to the upload and verifies against
the same hash. It makes previews on first request and caches them.

### Opening a server album

Opening fetches only the album metadata and writes the album and its pages in
one transaction, each page with its `shotCount` from the server and
`shotsLoaded = false`, and no upload ops, so the album is usable at once and
nothing is sent back. The last page is then loaded straight away (its shot list
and its full shots), because it is the current page: the capture screen shows
its thumbnails and the 25-shot limit needs its real shots. Everything else is
fetched only when it is looked at, so continuing a 500-page album costs one
small request plus one page of photos.

Loading a page's shot list writes its shots as `NOT_DOWNLOADED` rows and sets
`shotsLoaded`. Previews go through Coil with a custom fetcher for the `thumb`
URL and Coil's disk cache, so they never enter `ShotStore` and the system may
evict them. A full shot shown on the review screen is fetched by `ShotFetcher`
through `ShotStore`'s temp-then-rename write, checked against its SHA-256, and
marked `PRESENT`; from then on it is an ordinary local file. Fetches run in the
app's process, not WorkManager: they only matter while the user is looking,
and a failed one simply retries the next time the shot is shown or the network
comes back. The last page's initial load is the exception and runs as unique
WorkManager work under the upload network setting, so an album opened offline
finishes loading by itself.

Deleting a shot or page that has not been fetched removes its rows and queues
the server deletion; nothing is fetched first. New shots on an opened album
are ordinary local shots with upload ops. The page overview uses `shotCount`
until a page's shots are loaded, and `shotCount` is kept in step with the
loaded rows after that.

Only one phone is assumed. Once opened, the phone's copy is the one that
changes; a later change made to the same album from another phone is not
pulled in, which is acceptable for a single-user MVP.

### Deleting albums

An album on the phone is deleted from the drawer, with "Also delete from the
server" off by default because a server deletion takes the processed pages and
PDF with it and cannot be undone. When it is chosen, the album's pending
`UploadOp` rows are dropped and one `DELETE_ALBUM` op is queued in the same
transaction, so nothing queued for that album can recreate it on the server
after the deletion, and an offline phone still deletes it once it reconnects.

The server albums list deletes only albums that are not on this phone. If it
could delete an album the phone still holds, the next edit or shot on the phone
would upload into an album that no longer exists, recreating a partial copy;
routing those deletions through the drawer avoids that without needing any
server-side tombstones. An album deleted on the server from another device can
still be recreated by this phone's next upload; with a single user that is an
acceptable MVP limit.

## Components

All under `android/app/src/main/java/org/bit63/albumarchiver/`.

### Data (`data/`)

```kotlin
@Entity data class Album(@PrimaryKey val id: String, val name: String,
                         val pageSize: String, val createdAt: Instant)
@Entity data class Page(@PrimaryKey val id: String, val albumId: String,
                        val position: Int,        // 1-based; unique (albumId, position)
                        val shotCount: Int = 0,
                        val shotsLoaded: Boolean = true)  // false until a server page's list is fetched
@Entity data class Shot(@PrimaryKey val id: String, val albumId: String,
                        val pageId: String, val path: String,
                        val sha256: String, val bytes: Long,
                        val takenAt: Instant,
                        val state: ShotState = ShotState.PRESENT)
enum class ShotState { PRESENT, NOT_DOWNLOADED }
@Entity data class UploadOp(@PrimaryKey(autoGenerate = true) val seq: Long = 0,
                            val kind: OpKind, val albumId: String,
                            val pageId: String?, val shotId: String?,
                            val attempts: Int = 0,
                            val pages: String? = null) // ALBUM_META: page ids in order
enum class OpKind { ALBUM_META, PUT_SHOT, DELETE_SHOT, DELETE_PAGE, DELETE_ALBUM, MOVE_SHOTS }  // MOVE_SHOTS: shot-actions spec
```

- `AlbumDao`, `PageDao`, `ShotDao`, `UploadOpDao`: Room DAOs exposing `Flow`s
  for the UI.
- `AlbumRepository`: the only writer. `createAlbum`, `updateAlbum` (name and page size),
  `deleteAlbum(alsoOnServer)`, `addShot`, `deleteShot` (deleting an inner page with it when
  it was the page's only shot), `deletePage` (renumbering later pages),
  `startNextPage`, `undoNextPage`, each a single Room transaction that also
  inserts the matching `UploadOp`s (a page deletion also queues
  `ALBUM_META`) and then kicks the upload worker. Files are deleted after the
  transaction commits; a crash in between leaves orphan files that startup
  cleanup removes.
- `Limits`: `MAX_PAGES_PER_ALBUM = 500`, `MAX_SHOTS_PER_PAGE = 25`.
- `ShotStore`: file paths, temp-then-rename writes, SHA-256, startup cleanup,
  free-space check.
- `SettingsStore`: DataStore for last album id, server URL and network
  preference; EncryptedSharedPreferences for the token.

### Camera (`camera/`)

- `CameraController`: binds `Preview` and `ImageCapture`
  (`CAPTURE_MODE_MAXIMIZE_QUALITY`, flash off) to the lifecycle, exposes
  `suspend fun capture(target: File): Result<Unit>` and `setFlash(on)`, and
  updates `targetRotation` from an `OrientationEventListener`.

### UI (`ui/`)

- `MainActivity`: hosts Compose, keeps the screen on while the capture screen
  is shown, forwards volume keys to `VolumeKeyRouter`, locks portrait.
- `VolumeKeyRouter`: holds the active handler (or none) and implements the
  repeat and consume rules above.
- `CaptureScreen` + `CaptureViewModel`: preview, shutter, "Next page", flash
  toggle, album and page header, shot count, thumbnail strip, pending-upload
  count, low-storage and auth banners, empty state, permission rationale.
  The ViewModel exposes `takeShot()` and `nextPage()`, used by both buttons and
  volume keys.
- `AlbumDrawer`: "New album", album list with page counts and upload state,
  edit and delete (with the "Also delete from the server" option), "Server
  albums", "Settings".
- `AlbumDialog`: one form for creating and editing an album. Name (prefilled
  with "Album" plus the date when creating) and page size, a choice of
  8.5 × 11 in (selected by default when creating), A4, A3 or custom width and
  height with a unit, validated before "Create" or "Save" is enabled. Editing
  opens it prefilled with the album's current values; an existing custom size
  is shown as custom even when it equals a preset, so nothing changes unless
  the user changes it. A page size change touches no shots: the backend's
  album step reuses its processed pages and only redoes the print images and
  the PDF. The default is US Letter because it is the most common
  album page in the US; the backend's `letter` size is the same thing.
- `PageOverview` + `PageOverviewViewModel`: `LazyVerticalGrid` of pages
  (number, first-shot thumbnail, shot count), tap to review, long-press to
  delete.
- `ReviewScreen` + `ReviewViewModel`: a `HorizontalPager` over the shots of
  one page with pinch-zoom, "Page N, shot i of n", previous-page and next-page
  arrows, "Delete shot" and "Delete page" with confirmation dialogs. After a
  deletion it moves to the next shot, the previous one, or back to the
  overview. Thumbnails and full images are decoded downsampled (Coil) so a
  25-shot page of 12 MP photos does not exhaust memory.
- `ServerAlbumsScreen` + `ServerAlbumsViewModel`: fetches `GET /api/v1/albums`
  on open, on returning to it and on pull-to-refresh, marks albums whose id is in the local
  database, opens the others on tap or "Open on this phone" (refused while
  the phone has less than the 500 MB low-storage threshold free, since the
  last page alone can be 25 full shots) and offers "Delete from server" for them, and shows the error states
  with "Retry" or a link to Settings. Nothing is cached; the list is
  always the server's current answer.
- `SettingsScreen` + `SettingsViewModel`: URL, token, network choice, http
  warning, "Test connection".

### Upload (`upload/`)

- `ServerClient`: OkHttp calls for the requests above, mapping responses to
  `Ok`, `Retry` or `AuthFailed`; `listAlbums()`, `getAlbum()`, `getPage()`
  and `deleteAlbum()` are also called directly by the UI.
- `AlbumImporter`: turns `GET /api/v1/albums/{albumId}` into one Room
  transaction (album and pages with `shotsLoaded = false`, no upload ops) and
  enqueues `LastPageLoadWorker`.
- `PageLoader`: fetches a page's shot list on demand and writes its
  `NOT_DOWNLOADED` shot rows.
- `ShotFetcher`: fetches a full shot on demand through `ShotStore`, verifies
  the hash and marks it `PRESENT`; a mismatch deletes the file and retries
  once before reporting a failure.
- `ThumbFetcher`: a Coil `Fetcher` for `NOT_DOWNLOADED` shots that requests
  `?size=thumb` and relies on Coil's disk cache.
- `LastPageLoadWorker`: unique WorkManager work that runs `PageLoader` and
  `ShotFetcher` for an opened album's last page.
- `UploadWorker`: `CoroutineWorker` that drains `UploadOp` rows in order and
  returns `Result.retry()` on the first retryable failure.
- `UploadScheduler`: enqueues the unique work with the current network
  constraint; re-enqueues when settings change.

## Error handling

| Situation | Behaviour |
| --- | --- |
| Delete confirmed | shot or page removed locally at once; server removal queued like an upload |
| Album deleted from the phone with "Also delete from the server" | pending uploads for it dropped; `DELETE_ALBUM` queued |
| Server albums list while offline or unauthorised | message saying which, with "Retry" or a link to Settings |
| "Delete from server" fails | error shown, album left in the list |
| Server unreachable while opening an album | error shown; nothing created |
| Page list or shot fetch fails, or hash mismatch | placeholder with "Couldn't load"; retried when shown again or the network returns |
| Shot missing on the server (404) | shot row removed, logged; the page keeps its other shots |
| Page has 25 shots | shutter disabled, volume up ignored, "Page full" hint |
| Album has 500 pages | "Next page" disabled, volume down ignored, "Album full" hint |
| Camera permission denied | rationale and "Grant" button in place of the preview; "Open settings" after a permanent denial |
| Camera unavailable or capture error | snackbar with the error; no shot recorded |
| Storage below 500 MB | warning banner; shots still allowed |
| Storage full during a save | capture error as above; temp file removed |
| No server configured | shots queue locally; banner "Uploads paused: no server set" |
| Server unreachable or 5xx | retried with backoff; shown as "waiting" |
| 401 / 403 | queue paused; banner pointing to Settings |
| 409 on a shot | logged, dropped from the queue |
| App killed mid-capture | temp file deleted at next start; no shot recorded |

## Testing strategy

- **Unit tests (JVM)**: `VolumeKeyRouter` (consume when active, pass through
  when not, first press only on repeat); `CaptureViewModel` with fakes
  (shutter and volume up add a shot, next page on an empty page is refused,
  undo, first shot creates page 1, presses during a capture are dropped,
  the 26th shot and the 501st page are refused);
  page size parsing and formatting; `ServerClient` and `UploadWorker` against
  OkHttp `MockWebServer` (order, idempotent retry, backoff on 5xx, pause on
  401, 409 handling).
- **Room tests** (Robolectric or instrumented, in-memory DB): repository
  transactions insert the matching upload op; deleting a middle page
  renumbers the later pages and keeps their ids; deleting an inner page's only
  shot deletes the page, but not on the last page; deleting the last page
  makes the previous one current; deletions free room under the limits; deleting an album removes its
  rows and files; last-album lookup when the album was deleted.
- **Instrumented tests**: launch opens the last album; empty state with no
  albums; drawer creates and switches albums; review swipes through shots
  and the arrows move between pages; deleting from review and the overview; volume keys take a shot and
  start a page on the capture screen and change nothing while the drawer is
  open (`Instrumentation.sendKeyDownUpSync`); a captured file has EXIF
  orientation and focal length (on an emulator with the virtual scene camera).
- **Manual check** on a real phone over a real album: hands-free shooting with
  the volume keys, then upload to a local server stub.

## Out of scope

- Any image processing, preview of the processed page or feedback from the
  backend's report (coverage, glare, low resolution) on the phone. The report
  already has what a later version would need to ask for another shot.
- Reordering pages, and adding shots to an earlier page. (Inserting a page
  and moving shots between pages are in the shot-actions spec.)
- Front camera, video, manual exposure controls.
- Sharing, exporting or viewing the PDF on the phone.
- Multiple users, accounts or server discovery.
- Syncing changes made to the same album from another phone after it has been
  opened on this one.
