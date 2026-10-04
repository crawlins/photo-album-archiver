# Implementation plan: Android capture app (MVP)

Each task builds on the previous ones and ends with passing tests. Paths are
under `android/`. Requirement numbers refer to `requirements.md`.

- [ ] 1. Create the Android project
  - Gradle Kotlin DSL project with one `app` module, package `org.bit63.albumarchiver`,
    minSdk 26, targetSdk the latest stable API, Compose and Material 3, and a
    version catalog with CameraX, Room, DataStore, androidx.security,
    WorkManager, OkHttp, Coil, and the test libraries (JUnit, Turbine,
    MockWebServer, Robolectric, Compose UI test).
  - `MainActivity` showing an empty Compose screen, locked to portrait.
  - Add `android/` build outputs to `.gitignore`; add a CI job that runs
    `./gradlew lint testDebugUnitTest assembleDebug`.
  - _Requirements: 10.2_

- [ ] 2. Store albums, pages and shots
  - [ ] 2.1 Add the Room entities, DAOs and database from the design, with a
    page UUIDs and a unique index on `(albumId, position)`.
    - _Requirements: 7.1_
  - [ ] 2.2 Add `ShotStore`: shot paths, temp-then-rename writes with fsync,
    SHA-256, free-space check, and startup cleanup of `.tmp` files and
    orphaned `.jpg` files.
    - _Requirements: 4.8, 7.4, 10.3, 10.4_
  - [ ] 2.3 Add `AlbumRepository` with `createAlbum`, `updateAlbum`,
    `deleteAlbum` (with an option to queue `DELETE_ALBUM` and drop the
    album's pending ops), `addShot` (creating page 1 when needed), `deleteShot`,
    `startNextPage` (refusing on an empty page) and `undoNextPage`, each one
    transaction that also writes its `UploadOp`. Add `Limits` and refuse a
    26th shot on a page and a 501st page in the same transactions.
    - _Requirements: 2.3, 2.7, 4.2, 4.9, 5.1, 5.2, 5.4, 5.5, 7.2, 7.3, 8.1, 8.6, 13.7_
  - [ ] 2.4 Add `SettingsStore` (DataStore and EncryptedSharedPreferences).
    - _Requirements: 1.4, 9.1, 9.3_
  - [ ] 2.5 Write tests: repository transactions and their upload ops, page
    numbering, empty-page refusal and undo, the shot and page limits, album deletion removing files and
    rows, `ShotStore` cleanup and atomic writes.
    - _Requirements: 4, 5, 7, 10_

- [ ] 3. Camera capture
  - [ ] 3.1 Add `CameraController`: preview and `ImageCapture` bound to the
    lifecycle, maximum quality, flash off by default, `capture(target)`,
    `setFlash`, and `targetRotation` from an `OrientationEventListener`.
    - _Requirements: 3.1, 4.1, 4.3, 4.4, 10.2_
  - [ ] 3.2 Add `CaptureViewModel.takeShot()`: mutex so presses during a
    capture are dropped, capture to a temp file, rename, then
    `AlbumRepository.addShot`; errors surface as a one-shot UI event.
    - _Requirements: 4.1, 4.2, 4.6, 4.7, 10.3_
  - [ ] 3.3 Add `CaptureViewModel.nextPage()` and `undoNextPage()`.
    - _Requirements: 5.1, 5.2, 5.4_
  - [ ] 3.4 Write ViewModel tests with a fake camera and in-memory repository,
    and an instrumented test that a captured JPEG carries EXIF orientation and
    focal length.
    - _Requirements: 4, 5_

- [ ] 4. Capture screen
  - Build `CaptureScreen`: full-screen `PreviewView`, shutter and "Next page"
    buttons, flash toggle, header with album name, page number and shot count,
    thumbnail strip, pending-upload count, the new-page number flash and undo
    snackbar, capture feedback (flash animation and haptic), keep-screen-on,
    camera permission rationale, and the empty state with "New album".
  - Compose UI tests for the header, empty state, refused next page hint and
    undo.
  - Disable the shutter at 25 shots and "Next page" at page 500, with the
    "Page full" and "Album full" hints.
  - _Requirements: 1.3, 3.1–3.7, 4.5, 4.10, 5.2, 5.3, 5.4, 5.6, 8.7_

- [ ] 5. Volume keys
  - Add `VolumeKeyRouter` and wire `MainActivity.onKeyDown` / `onKeyUp` to it;
    the capture screen registers its handler when resumed with an album open
    and no drawer or dialog showing, and unregisters otherwise.
  - Unit tests for consume and pass-through and for ignoring auto-repeat; an
    instrumented test that volume up adds a shot, volume down starts a page,
    neither does anything while the drawer is open, and both are consumed
    without effect at the limits.
  - _Requirements: 6.1–6.5_

- [ ] 6. Navigation drawer and albums
  - Add `AlbumDrawer` and `AlbumDialog` for creating and editing an album
    (name, defaulting to "Album" plus the date when creating; page size,
    defaulting to 8.5 × 11 in, or A4, A3 or custom width, height and unit;
    both validated, and the size stored as the backend's size string), album
    switching, edit, and delete with confirmation.
  - Open the last album on launch, falling back to the empty state when it no
    longer exists; record the current album on every change.
  - Tests: page size formatting and validation, blank name rejected, the
    8.5 × 11 in default, editing name and size queues album metadata and
    leaves shots alone, launch into the last album, empty state when it was
    deleted, create and switch from the drawer.
  - _Requirements: 1.1–1.4, 2.1–2.9, 10.1_

- [ ] 7. Review and delete shots and pages
  - [ ] 7.1 Add `AlbumRepository.deletePage` and extend `deleteShot`:
    renumber later pages, delete an inner page with its only shot, make the
    previous page current when the last page goes, queue `DELETE_SHOT`,
    `DELETE_PAGE` and `ALBUM_META` ops, delete files after commit.
    - _Requirements: 7.3, 12.3–12.5, 12.7_
  - [ ] 7.2 Add `PageOverview` and `ReviewScreen` with their ViewModels:
    page grid, shot pager with pinch-zoom and position label, page arrows,
    delete actions with confirmations, and where to land after a deletion.
    Open them from the capture screen's thumbnails and page number and from
    the drawer.
    - _Requirements: 3.5, 11.1–11.7, 12.1, 12.2, 12.6_
  - [ ] 7.3 Write tests: repository deletion cases from the design, and
    Compose tests for swiping, page arrows at the ends, and each deletion
    path.
    - _Requirements: 11, 12_

- [ ] 8. Upload
  - [ ] 8.1 Add `ServerClient` with the ten requests from the design, the
    `X-Content-SHA256` header, and the response handling table from the
    design.
    - _Requirements: 8.5, 8.8, 9.2_
  - [ ] 8.2 Add `UploadWorker` draining `UploadOp` rows in order, and
    `UploadScheduler` enqueueing unique work with the network constraint from
    settings and exponential backoff; enqueue on app start so pending uploads
    resume after a reboot.
    - _Requirements: 8.1–8.4, 8.6, 8.9, 8.10, 9.5_
  - [ ] 8.3 Show per-album upload state in the drawer and the auth and
    no-server banners on the capture screen.
    - _Requirements: 8.7, 8.8, 9.5_
  - [ ] 8.4 Write `MockWebServer` tests: order of metadata and shots
    (metadata before a new album's first shot, and a new page's metadata only
    after every shot of the page before it), retried upload sends the same id
    and hash, 5xx and 507 retried, 401 pauses the queue, 409 and 422 dropped,
    400 retried once then dropped, 413 dropped, shot, page and album deletions sent, an album deletion drops
    that album's pending uploads.
    - _Requirements: 8_

- [ ] 9. Settings
  - Add `SettingsScreen`: server URL, token, network choice, warning on
    `http`, and "Test connection" via `GET /api/v1/ping`.
  - Tests for URL validation, the http warning and each connection result
    against `MockWebServer`.
  - _Requirements: 9.1–9.4_

- [ ] 10. Server albums
  - Add the "Also delete from the server" option to album deletion in the
    drawer.
  - Add `ServerAlbumsScreen` and its ViewModel: fetch on open and on
    pull-to-refresh, mark albums on this phone, "Delete from server" with
    confirmation for the others only, and the error states.
  - Tests against `MockWebServer`: list parsing and the on-this-phone mark,
    deletion offered only for server-only albums, a successful and a failed
    delete, and each error state.
  - _Requirements: 2.2, 2.7, 13.1–13.7_

- [ ] 11. Open server albums on the phone
  - [ ] 11.1 Add `bytes` and `state` to `Shot` and `shotCount` and
    `shotsLoaded` to `Page`, and placeholder and "Couldn't load" rendering on
    the capture, review and overview screens.
    - _Requirements: 10.3, 14.5, 14.9_
  - [ ] 11.2 Add `AlbumImporter` and `LastPageLoadWorker`, and tap-to-open and
    "Open on this phone" in the server albums list.
    - _Requirements: 13.5, 14.1, 14.2, 14.10_
  - [ ] 11.3 Add `PageLoader`, `ShotFetcher` and `ThumbFetcher`, called from
    the page overview and review screen as pages and shots are shown.
    - _Requirements: 14.3–14.9_
  - [ ] 11.4 Write `MockWebServer` tests: opening fetches only the album and
    the last page; the overview shows server shot counts without fetching
    pages; viewing a page fetches its list, thumbnails use `?size=thumb`, and
    review fetches the full shot and marks it present; a corrupted shot is
    refetched; deleting an unfetched shot or page fetches nothing and queues
    the server deletion; fetched shots are never uploaded; shooting on an
    opened album uploads only the new shots.
    - _Requirements: 14_

- [ ] 12. Update the README
  - Mark the Android capture app as working, document building and installing
    it, the volume-key controls, server settings, server albums and the HTTP
    contract the upload server must implement.
  - _Requirements: 8, 9_
