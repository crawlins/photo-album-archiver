# Implementation plan: actions on a page's photos

Each task builds on the previous ones and ends with passing tests. App paths
are under `android/`, server paths under `backend/`. Requirement numbers refer
to `requirements.md`.

- [x] 1. Server move request
  - [x] 1.1 Add `Store.move_shots(album_id, page_id, shot_ids, link)`: one
    transaction that creates an unknown page at the end, skips shots already
    on the page or not in the album, checks the 25-shot and 500-page limits,
    links the files into the new page's folder, updates `page_id`, sets
    `changed_at` on the source and target pages and makes the source pages
    ready.
    - _Requirements: 10.1, 10.2, 10.3, 10.4, 10.6, 10.7, 10.8, 10.9_
  - [x] 1.2 Add `POST /api/v1/albums/{albumId}/pages/{pageId}/move` with its
    body model (1 to 25 unique UUIDs, no other keys), 204, 400, 404, 409 and
    422, unlinking the old file names after the commit.
    - _Requirements: 10.1, 10.5, 10.9_
  - [x] 1.3 Write contract tests (`tests/test_server_moves.py`): existing and
    unknown target page, repeat move, skipped shots, both 422s changing
    nothing, 400 bodies, 404 and 409, pages marked changed and the source
    ready, an emptied page reporting `empty`, previews served after a move,
    the app's order (move, then metadata), and cleanup after a crash on
    either side of the commit.
    - _Requirements: 10_
  - [x] 1.4 Add the `move` row to the album server design's route table.

- [x] 2. App data for moves
  - [x] 2.1 Add `OpKind.MOVE_SHOTS`, `PageDao.shiftUpStep1` and `atPosition`,
    `ShotDao.moveTo`, and `UploadOpDao.retargetPutShot`.
    - _Requirements: 6.1, 9.2_
  - [x] 2.2 Add `ShotStore.link` (hard link, falling back to a
    temp-then-rename copy).
    - _Requirements: 5.4_
  - [x] 2.3 Add `AlbumRepository.runOf`, `deleteRun`, `moveRunToNextPage` and
    `splitRunToNewPage`: limit checks, page insertion with renumbering, the
    link, commit, unlink sequence, retargeted `PUT_SHOT`s, and ops queued as
    `MOVE_SHOTS`, `DELETE_PAGE`, `ALBUM_META`.
    - _Requirements: 4, 5, 6, 7, 9_
  - [x] 2.4 Write Room tests (`ShotActionsRepositoryTest`) for every case in
    the design's testing strategy, including crashes before and after the
    commit followed by startup cleanup.
    - _Requirements: 4, 5, 6, 7, 9_

- [x] 3. Upload of moves
  - [x] 3.1 Add `ServerClient.moveShots` and handle `MOVE_SHOTS` in
    `UploadProcessor` with the design's response table; teach
    `FakeAlbumServer` the move request.
    - _Requirements: 9.1, 9.2_
  - [x] 3.2 Write tests: the request body, a split page and a moved
    not-yet-uploaded shot reaching the server, an emptied page deleted
    there, and the 404 and retry handling.
    - _Requirements: 9_
  - [x] 3.3 Add the `move` row and `MOVE_SHOTS` to the Android app design's
    server contract and data model, and change its out-of-scope line to
    "Reordering pages".

- [x] 4. Selection and the strip
  - [x] 4.1 Add `ShotStrip` with `combinedClickable`, the selected and run
    markings, and tap and long-press callbacks; use it for the capture
    screen's thumbnails.
    - _Requirements: 1.1, 1.2, 1.3, 1.7_
  - [x] 4.2 Add `ShotActions` (selection and menu state) to
    `CaptureViewModel`, cleared by Back, a tap on the preview, a shot, "Next
    page" (button or volume key) and a page change.
    - _Requirements: 1.4, 1.5_
  - [x] 4.3 Add the strip to `ReviewScreen`, in step with the pager, with the
    same selection state in `ReviewViewModel`.
    - _Requirements: 1.6, 1.4, 1.5_
  - [x] 4.4 Write view model tests for selecting, reselecting and the ways the
    selection clears, and instrumented Compose tests that a long press
    selects without opening review.
    - _Requirements: 1_

- [x] 5. Shot actions menu
  - [x] 5.1 Add `ShotActionsMenu` and `ShotActionsSheet`: the four actions
    with their labels for a run and for one photo, disabled actions with
    their reasons, and the two delete confirmations. Release the volume keys
    while it or a confirmation is open.
    - _Requirements: 2, 3, 4.1, 4.2, 5.5, 5.6, 5.7, 6.3, 6.4_
  - [x] 5.2 Wire the actions in both view models to the repository, with the
    snackbars, the capture screen following the current page, and the review
    screen moving to the first moved shot or behaving as after a deletion.
    - _Requirements: 3, 4, 5.1, 5.2, 5.8, 6.1, 6.2, 6.5, 7.2, 8_
  - [x] 5.3 Write tests: `ShotActionsMenuTest` for each action's label,
    availability and reason, `ShotActionsViewModelTest` for each action's
    result, snackbar and navigation, and instrumented `ShotActionsTest` that
    the menu opens on a second long press, shows disabled actions with their
    reasons, carries out moves and deletions, and leaves the volume keys
    alone while it is open.
    - _Requirements: 2, 3, 4, 5, 6, 8_

- [ ] 6. Check on a real phone
  - Against a local server: split a page shot without pressing "Next page",
    move the end of a page to the next one and to a new page, delete a run,
    and confirm the server's pages and processing match the phone, including
    with the phone offline while the actions are made.
