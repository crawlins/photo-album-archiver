# Implementation plan: actions on a page's photos

Each task builds on the previous ones and ends with passing tests. App paths
are under `android/`, server paths under `backend/`. Requirement numbers refer
to `requirements.md`.

- [ ] 1. Server move request
  - [ ] 1.1 Add `Store.move_shots(album_id, page_id, shot_ids)`: one
    transaction that creates an unknown page at the end, skips shots already
    on the page or not in the album, checks the 25-shot and 500-page limits,
    updates `page_id`, and sets `changed_at` on the source and target pages.
    - _Requirements: 10.1, 10.2, 10.3, 10.4, 10.6, 10.7_
  - [ ] 1.2 Add `POST /api/v1/albums/{albumId}/pages/{pageId}/move` with its
    body model (1 to 25 unique UUIDs, no other keys), 204, 400, 404 and 422.
    - _Requirements: 10.1, 10.5_
  - [ ] 1.3 Write contract tests: existing and unknown target page, repeat
    move, skipped shots, both 422s changing nothing, 400 bodies, 404 album,
    pages marked changed, an emptied page reporting `empty`, previews served
    after a move, and the end-to-end order (shots, move, metadata) making the
    source page ready at once.
    - _Requirements: 9.4, 10_
  - [ ] 1.4 Add the `move` row to the album server design's route table.

- [ ] 2. App data for moves
  - [ ] 2.1 Add `OpKind.MOVE_SHOTS` and `UploadOp.shots` with a Room
    auto-migration from 2 to 3; add `PageDao.shiftUpStep1/2`,
    `ShotDao.moveToPage` and `setPath`, and `UploadOpDao.retargetPutShots`.
    - _Requirements: 6.1, 9.2_
  - [ ] 2.2 Add `ShotStore.linkInto` (hard link, falling back to a
    temp-then-rename copy) and `unlink`.
    - _Requirements: 5.4_
  - [ ] 2.3 Add `AlbumRepository.runOf`, `deleteRun`, `moveRunToNextPage` and
    `splitRunToNewPage` with the shared `moveInTransaction`: limit checks,
    page insertion with renumbering, the link, commit, unlink sequence,
    retargeted `PUT_SHOT`s, and ops queued as `MOVE_SHOTS`, `DELETE_PAGE`,
    `ALBUM_META`.
    - _Requirements: 4, 5, 6, 7, 9_
  - [ ] 2.4 Write Room tests for every case in the design's testing strategy,
    including crashes before and after the commit followed by startup
    cleanup.
    - _Requirements: 4, 5, 6, 7, 9, 10.3 of the Android app spec_

- [ ] 3. Upload of moves
  - [ ] 3.1 Add `ServerClient.moveShots` and handle `MOVE_SHOTS` in
    `UploadProcessor` with the design's response table.
    - _Requirements: 9.1, 9.2_
  - [ ] 3.2 Write MockWebServer tests: the request body, each response, and a
    queue sent in the order rewritten `PUT_SHOT`, `MOVE_SHOTS`, `DELETE_PAGE`,
    `ALBUM_META`.
    - _Requirements: 9_
  - [ ] 3.3 Add the `move` row and `MOVE_SHOTS` to the Android app design's
    server contract and data model, and change its out-of-scope line to
    "Reordering pages".

- [ ] 4. Selection and the strip
  - [ ] 4.1 Add `ShotStrip` with `combinedClickable`, the selected and run
    markings, and tap and long-press callbacks; use it for the capture
    screen's thumbnails.
    - _Requirements: 1.1, 1.2, 1.3, 1.7_
  - [ ] 4.2 Add selection state to `CaptureViewModel`, cleared by Back, a tap
    outside the strip, a shot, "Next page" (button or volume key) and a page
    change.
    - _Requirements: 1.4, 1.5_
  - [ ] 4.3 Add the strip to `ReviewScreen`, in step with the pager, with the
    same selection state in `ReviewViewModel`.
    - _Requirements: 1.6, 1.4, 1.5_
  - [ ] 4.4 Write ViewModel tests for selecting, reselecting and every way the
    selection clears, and Compose tests that a long press selects without
    opening review and marks the run.
    - _Requirements: 1_

- [ ] 5. Shot actions menu
  - [ ] 5.1 Add `ShotActionsSheet` and `ShotActionsState`: the four actions
    with their labels for a run and for one photo, disabled actions with
    their reasons, and the two delete confirmations. Release the volume keys
    while it or a confirmation is open.
    - _Requirements: 2, 3, 4.1, 4.2, 5.5, 5.6, 5.7, 6.3, 6.4_
  - [ ] 5.2 Wire the actions in both ViewModels to the repository, with the
    snackbars, the capture screen following the current page, and the review
    screen moving to the first moved shot or behaving as after a deletion.
    - _Requirements: 3, 4, 5.1, 5.2, 5.8, 6.1, 6.2, 6.5, 7.2, 8_
  - [ ] 5.3 Write ViewModel tests for each action's availability, result,
    snackbar and navigation, and Compose tests that a long press on the
    selected thumbnail opens the sheet, disabled actions show their reasons,
    and volume keys pass through while it is open.
    - _Requirements: 2, 3, 4, 5, 6, 8_

- [ ] 6. Check on a real phone
  - Against a local server: split a page shot without pressing "Next page",
    move the end of a page to the next one and to a new page, delete a run,
    and confirm the server's pages and processing match the phone, including
    with the phone offline while the actions are made.
