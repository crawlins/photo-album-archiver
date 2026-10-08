# Design: actions on a page's photos

## Overview

Selection is UI state in the screen's ViewModel. The four actions are new
`AlbumRepository` methods, each one Room transaction that changes the rows,
checks the limits and queues the upload ops, like every existing repository
method. A move reaches the server as one new `MOVE_SHOTS` op and one new
server request, so photos never travel twice.

```
 long-press ─▶ ShotSelection (ViewModel) ─▶ long-press again ─▶ ShotActionsSheet
                                                                    │
           ┌────────────────────────┬────────────────────────┬──────┘
           ▼                        ▼                        ▼
 deleteShot / deleteRun     moveRunToNextPage        splitRunToNewPage
           │                        │                        │
           └──────── one Room transaction: rows, limits, UploadOps ──▶ UploadWorker
                                                                          │
                       POST /api/v1/albums/{a}/pages/{p}/move ◀───────────┘
```

## Key decisions

### Two long presses

The user asked for long press to select and long press again for the menu.
The first press only highlights, so a long press made by accident while
holding the phone over an album changes nothing; the second is on a thumbnail
that is now visibly marked, so it is deliberate. Deletions still confirm,
because they cannot be undone; moves do not, because nothing is lost and the
snackbar says where the shots went.

The run is "this shot and every later one on the page", so the strip marks
the later thumbnails as well as the selected one. That is what makes the menu
labels ("and the 3 after it") unambiguous before the user opens it.

### Where the strip appears

The capture screen already has a thumbnail strip, but it only ever shows the
current (last) page, so "move to the next page" there always creates a new
page. To act on any page, the review screen gets the same strip for the page
being reviewed. Both use one `ShotStrip` composable with the selection
behaviour built in. The page overview shows pages, not shots, and keeps its
existing long-press "Delete page".

On the last page, "Move … to a new page" and "Create a new page with …" do the
same thing. Both stay in the menu so that it always has the same four entries
in the same places.

### Shot order and "after"

A page's shots are already ordered by `takenAt` (then `rowid`) in the
database and by time taken on the server, and "after" uses the same order. A
move keeps each shot's `takenAt`, so moved shots slot into the target page in
time order. In the case this feature is for (shots taken for the next page
before "Next page" was pressed) they were taken before that page's own shots
and come first, as Requirement 5.3 asks, with no separate ordering field.

### Inserting a page

"Create a new page" and "Move to a new page" insert a page at
`source.position + 1`. Later pages are renumbered the way deletion does it in
reverse: positions after the source are first moved to negative values and
then set to their new values, which keeps the unique `(albumId, position)`
index valid inside the transaction (`PageDao.shiftUpStep1/2`, mirroring
`shiftDownStep1/2`). Ids, files and server paths of the other pages do not
change.

When the source page was the last page, the new page is now the last, and the
current page is always the last page, so the capture screen follows to it
without any extra state.

### Emptied pages

A move that takes every shot of an inner page ("move the whole page into the
next one") leaves it empty; it is removed with the existing
`removePageInTransaction`, which renumbers and queues `DELETE_PAGE` and the
metadata, the same rule as deleting a page's only shot. The two moves that
would leave the album unchanged (the whole last page to a new page, or the
whole page to a new page) are refused instead, so the code never creates a
page only to delete its source.

### Files on the phone

Shot files live under `shots/<albumId>/<pageId>/`, and deleting a page deletes
its folder, so a moved shot's file has to move with it or it would be deleted
with an emptied source page. To stay crash-safe without a journal, a move of
present shots is:

1. Before the transaction, hard-link each file into the target page's folder
   (`Os.link`); both names now point at the same data.
2. In the transaction, update each shot's `pageId` and `path`.
3. After commit, unlink the old names, then delete an emptied source page's
   folder.

A crash before the commit leaves the rows pointing at the old names and the
new links as unreferenced files; a crash after it leaves the old names
unreferenced. Either way startup cleanup (Android app Requirement 10.4)
deletes only a name the database does not use, and the data survives under
the other. If linking fails (it should not, on one filesystem), the files are
copied with the existing temp-then-rename write instead.

Shots that have not been fetched have no file; only their `path` changes.

### Upload ops

A new op kind carries a move:

```kotlin
enum class OpKind { ALBUM_META, PUT_SHOT, DELETE_SHOT, DELETE_PAGE, DELETE_ALBUM, MOVE_SHOTS }
// MOVE_SHOTS: pageId = target page, shots = comma-separated shot ids (new nullable column)
```

The transaction for a move queues, in this order:

1. Rewrites `pageId` on every pending `PUT_SHOT` of a moved shot to the target
   page, so its upload goes straight to where it now belongs. Without this,
   removing an emptied source page would also drop those uploads
   (`deletePutShotsForPage`).
2. `MOVE_SHOTS` for the target page with every moved shot, including those
   whose upload is still pending. An upload may have reached the server
   without the phone seeing the response; the rewritten `PUT_SHOT` would
   then get 200 for a shot still filed under the old page, and the move puts
   it right. For shots the server does not have yet, the move is a no-op.
3. `DELETE_PAGE` for an emptied source page (from `removePageInTransaction`).
4. `ALBUM_META` with the new page order.

Sending the move before the metadata matters to processing: when the server
sees the metadata with a page after the source, it marks the source page
ready at once (album server Requirement 8.2), and by then the move has already
taken the shots off it. The other order would process the source page with
shots that are about to leave. A target page that is new reaches the server
first through the move (or a rewritten `PUT_SHOT`), which creates it at the
end of the album, and the metadata then puts it in its place, which the
server already handles for shots that arrive before their metadata.

A rewritten `PUT_SHOT` can sit in the queue ahead of the `ALBUM_META` that
first lists its new page, which is the same case: the server creates the page
and the metadata places it.

Deleting the run reuses `deleteShot`'s per-shot steps for every shot in one
transaction (drop pending `PUT_SHOT`, queue `DELETE_SHOT`), then removes an
emptied inner page as `deleteShot` already does. Queuing one `DELETE_SHOT` per
shot, rather than a new bulk delete, keeps the server unchanged for deletion;
a run is at most 25 shots.

`MOVE_SHOTS` responses are handled like the other ops: 2xx done, 401/403
pause the queue, 422 and 400 are logged and dropped (the app enforces the
same limits and sends only UUIDs, so either is a bug), 404 is dropped (the
album is gone from the server), and 5xx or network errors retry with backoff.

### Server request

```
POST /api/v1/albums/{albumId}/pages/{pageId}/move
{"shots": ["b7e1…", "c3d0…"]}
```

"Move these shots of the album to this page." It is idempotent: a shot
already on the page, or not in the album at all, is skipped, so a repeated or
late move succeeds. It creates an unknown page at the end of the album, as a
shot upload does, so a move never wedges the phone's queue because metadata
has not arrived. In one SQLite transaction the server:

1. Responds 404 if the album is unknown.
2. Creates the page if needed, refusing with 422 if that makes 501 pages.
3. Selects the listed shots that are in the album and not on the page.
4. Refuses with 422 if the page's shots plus those would exceed 25.
5. Updates their `page_id`, and sets `changed_at` on every page they left and
   on the target page.

The shot files are stored by shot id, and previews are keyed by shot id
(`thumbs/<shotId>.jpg`), so nothing moves on disk. A source page left with no
shots goes to the `empty` state as when its last shot is deleted; the app
deletes it right after in the usual case.

New route row for the album server:

| Route | Status codes |
| --- | --- |
| `POST /api/v1/albums/{albumId}/pages/{pageId}/move` | 204, 400, 404, 422 |

## Components

Paths are under `android/app/src/main/java/org/bit63/albumarchiver/` and
`backend/src/albumserver/`.

### App data

- `Entities.kt`: `OpKind.MOVE_SHOTS`; `UploadOp.shots: String?`. A Room
  auto-migration from version 2 to 3 adds the column.
- `AppDatabase.kt`: `PageDao.shiftUpStep1/2(albumId, afterPosition)`;
  `ShotDao.moveToPage(ids, pageId)` and `setPath(id, path)`;
  `UploadOpDao.retargetPutShots(shotIds, pageId)`.
- `ShotStore`: `linkInto(albumId, pageId, shotId, from)` with the copy
  fallback, and `unlink(path)`.
- `AlbumRepository`:
  - `deleteRun(shotId): RunDeletion?` deletes the shot and every later shot
    of its page in one transaction.
  - `moveRunToNextPage(shotId): MoveResult` moves the run to the next page,
    or to a new page after the last one.
  - `splitRunToNewPage(shotId): MoveResult` moves the run to a new page
    inserted after the source.
  - `MoveResult` is `Moved(targetPage, count, sourceDeleted)`, `PageFull`,
    `AlbumFull`, `NothingToMove` or `NotFound`. Both moves share one private
    `moveInTransaction(run, target)` that does the row, path and op steps
    above; the caller creates the target page first when needed.
  - `runOf(shotId)` returns the selected shot and the later shots of its page
    in `takenAt, rowid` order. The UI uses it too, to label the menu and
    decide which actions are available.

### App UI

- `ui/ShotStrip.kt`: the shared strip. It takes the page's shots, the shown
  shot (review screen only) and a `ShotSelection` state, and reports taps
  and long presses. It uses `combinedClickable`. The selected thumbnail gets
  a thick accent border and a check mark, later thumbnails of the run a thin
  accent border.
- `ui/ShotActionsSheet.kt`: a `ModalBottomSheet` with the four actions, each
  enabled or disabled with its reason from a `ShotActionsState` computed by
  the ViewModel from `runOf`, the next page's shot count and the album's page
  count. It also holds the two delete confirmations.
- `CaptureViewModel` and `ReviewViewModel`: `selection: StateFlow<String?>`
  (the selected shot id), `select`, `clearSelection`, `openActions`, and one
  function per action that calls the repository and emits the snackbar or
  navigation event. `CaptureViewModel.takeShot()` and `nextPage()` clear the
  selection first. The capture screen's `VolumeKeyRouter` handler stays active
  while only a selection is showing and is released while the sheet or a
  dialog is open, as for any other dialog.
- `ReviewScreen`: adds the strip along the bottom, kept in step with the
  pager; after a move it navigates to the target page at the first moved
  shot.

### Upload

- `ServerClient.moveShots(albumId, pageId, shotIds)`.
- `UploadProcessor`: handles `MOVE_SHOTS` with the response table above.

### Server

- `api.py`: the `move` route and its Pydantic body
  (`shots: list[UUID]`, 1 to 25 items, unique, `extra="forbid"`).
- `store.py`: `Store.move_shots(album_id, page_id, shot_ids)` doing the steps
  above in one transaction, reusing the page-creation and limit helpers that
  shot upload uses.

## Error handling

| Situation | Behaviour |
| --- | --- |
| Next page would exceed 25 shots | move disabled in the menu with the reason; refused again in the transaction if it changed |
| Album has 500 pages | new-page actions disabled with the reason; refused again in the transaction |
| Whole page to a new page, or whole last page to the next | action disabled; nothing to change |
| Selected shot deleted meanwhile (e.g. by the review screen) | `NotFound`; selection cleared, nothing changed |
| Linking a file fails | file copied instead; if that fails too, the action fails with an error and nothing changes |
| Crash during a move | startup cleanup removes whichever file names the database does not use |
| Move reaches the server for an unknown page | page created at the end; the next metadata places it |
| Move reaches the server for a shot it does not have | skipped; the shot's pending upload goes to the new page |
| 422, 400 or 404 on a move | logged and dropped |

## Testing strategy

Each implementation task lands with the tests covering it.

- **Repository (Room, in-memory)**: each action's rows, positions and queued
  ops in order; moving to an existing next page and to a new page after the
  last; splitting the last page and an inner page with renumbering; moving a
  whole inner page deletes it and renumbers; the refused cases (25 shots,
  500 pages, nothing to move); pending `PUT_SHOT`s are retargeted and survive
  the source page's deletion; not-fetched shots move without a file; deleting
  a run on the last page keeps an empty current page, on an inner page
  removes it; `takenAt` order on the target page.
- **File moves**: the link, commit, unlink sequence, and simulated crashes
  before and after the commit followed by startup cleanup, losing no file.
- **ViewModels**: select, reselect, clear on Back, shot, next page and page
  change; menu availability and reasons for each case; navigation and
  snackbar after each action.
- **Upload**: `UploadProcessor` against MockWebServer for `MOVE_SHOTS` and its
  response handling; a queue of rewritten `PUT_SHOT`, `MOVE_SHOTS`,
  `DELETE_PAGE` and `ALBUM_META` sent in that order.
- **Compose UI tests**: long press selects without opening review; the run
  is marked; long press on the selected thumbnail opens the sheet; disabled
  actions show their reasons; volume keys pass through while the sheet is
  open.
- **Server contract tests**: move to an existing page and to an unknown one;
  repeated move; unknown and already-moved shots skipped; 422 at 25 shots and
  at 501 pages with nothing changed; 400 bodies; 404 album; both pages marked
  changed; a source page left empty reports `empty`; previews still served
  after a move. Plus one end-to-end test with the app's op order: shots, a
  move to a new page, then metadata, after which the source page becomes
  ready at once and the server's page order matches.

## Changes to other specs

When this is implemented, the Android app spec's server contract table and
the album server's route table gain the `move` row, the app's `OpKind` list
gains `MOVE_SHOTS`, and the app spec's out-of-scope line "Inserting or
reordering pages" becomes "Reordering pages".

## Out of scope

- Moving shots to the previous page or to any page other than the next.
- Reordering shots within a page or pages within an album.
- Undoing a move. A move to the next page can be undone by hand only partly,
  since there is no move to the previous page; this can be added later as a
  fifth action if it turns out to be needed.
- Multi-select of shots that are not a run to the end of the page.
