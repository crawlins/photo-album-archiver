# TODO

Gaps between the specs in `.kiro/specs/` and the code, found by reading the
code rather than the `tasks.md` checkboxes. Reviewed on 2026-10-04 against
`main` at e31e582, PR #9 (album server) at 6cacc42, PR #1 (two pages) at
86784b9 and PR #8 (page curvature spec) at 9f0641a. Line numbers refer to
those commits.

Suggested order: fix the Android upload-order bug and the crashed-run PDF state
first, then merge PR #1 so the glare detector and page curvature work can
start.

## Album PDF (`album-pdf`, merged)

All twelve requirements are implemented. The remaining gaps are small.

- [ ] **Test that `albumproc page` output is unchanged** (task 8). No test
  calls `main(["page", ...])`. The code path itself was not changed.
- [ ] **Test a page whose processing fails** (task 7.4, R8.1). The failing-page
  test uses an unreadable JPEG (`backend/tests/test_album.py:136-147`), so no
  album-level test covers `process_page` itself raising.
- [ ] **Remove the temp PDF on failure** (R9.6). `album.pdf.img2pdf.tmp` is
  left behind if pikepdf raises (`backend/src/albumproc/pdf.py:48-76`).
- [ ] **Range-check the JPEG quality** (R5.1). `PrintOptions.validate`
  (`backend/src/albumproc/printimage.py:39-49`) does not look at it.
- [ ] **`--strict` with `--jobs` above 1** (R8.3). After a page fails, only
  queued work is cancelled (`backend/src/albumproc/album.py:367-370`). Pages
  already running still write their outputs. No PDF is written, as required.
- [ ] **Decide on the report schema** (R10.1, R10.3). The target DPI is stored
  once for the album (`album.py:338`), not per page, and the report stores
  two combined keys rather than a hash per photo (`album.py:250-251`). This
  matches the design but not the requirement wording; either change the code
  or the requirement.
- [ ] **Placeholder orientation** (R8.4). A failed page's placeholder follows
  the size string's first side (`album.py:301`), not the page's real
  orientation, which is unknown at that point. Probably acceptable; note it
  in the spec.
- [ ] **Stale spec wording.** Task 9 still mentions "page size must be given"
  (superseded by the letter default in R2.3). The album-pdf `design.md:152`
  says the album step is "not yet implemented".

## Android app (`android-app`, merged in PR #7)

Every task has matching code and tests. One real bug, a few partial items.

- [ ] **Bug: album metadata can be sent out of order** (R8.9). `ALBUM_META`
  ops carry no snapshot; `UploadProcessor.metadata()`
  (`android/app/src/main/java/org/bit63/albumarchiver/upload/UploadProcessor.kt:114-124`)
  reads the album's current pages when the op is sent. An older queued
  `ALBUM_META` (create, first shot, edit, delete) therefore announces the next
  page before the previous page's shots arrive.
  - Offline example: shoot s1, edit the album, shoot s2, press Next page. The
    queue sends META (already listing p2), s1, s2, META. The server marks p1
    ready before s2 arrives, processes it incomplete, then again.
  - In the plain create, shoot, Next page case, p2 is in the very first PUT, so
    "a new page appears" never triggers and p1 waits for the 30 s idle
    fallback.
  - The test "a new page's metadata is sent only after every shot…"
    (`UploadProcessorTest.kt:53`) only checks the last META, so it misses this.
  - Fix: snapshot the page list into the op, or drop or coalesce older pending
    META ops for the album when a new one is queued.
- [ ] **Retry loads when the network returns** (R14.9). Nothing watches
  connectivity. A failed thumbnail (`ui/ShotImage.kt:271-278`), page or shot
  load (`ui/review/ReviewModels.kt:61-78`) only retries when recomposed.
- [ ] **Re-check camera permission on resume** (R3.7). `granted` is remembered
  once (`ui/capture/CaptureScreen.kt:343-347`), so the rationale stays up after
  the user grants permission in system Settings.
- [ ] **Refresh the server album list on return** (R13.3). It refreshes only
  when its ViewModel is created (`ui/server/ServerAlbumsViewModel.kt:64-66`);
  coming back from Settings still shows the old error.
- [ ] **Queue metadata for server albums with no page size.** `AlbumImporter`
  imports them as `letter` (`upload/Fetchers.kt:153`) but queues nothing, so
  the server keeps `page_size` NULL and never processes the album (PR #9
  `store.py:455`), while the phone shows a size.
- [ ] **Free-space check before opening a server album.** The design says
  "after the space check"; `AlbumImporter.open` (`upload/Fetchers.kt:147`) has
  none.
- [ ] **Preview fills the screen** (R3.1, minor). It uses `FIT_CENTER`
  (`CaptureScreen.kt:384`) and is letterboxed.
- [ ] **Volume keys with no album open** (R6.3, minor). They are consumed only
  while an album is open (`CaptureScreen.kt:156-159`). The design agrees; the
  requirement does not. Align one with the other.
- [ ] **Run the app against the real server.** Its API calls match PR #9's
  `api.py` (paths, methods, payloads, status codes), but it has only been run
  against `FakeAlbumServer`.

## Album server (`album-server`, PR #9 open)

Nearly complete; 116 server tests pass locally. CI had not finished when
checked.

- [ ] **Crashed page runs leave the PDF marked current** (R8.5, R9.1). When the
  child process raises or dies, `finish_run(claim, None, error)` returns
  `"discarded"` (`backend/src/albumserver/store.py:498-499`) before
  `pdf_stale = 1` is set (line 504). The page's old outputs are kept, so
  `/status` reports the PDF as current and `/print` serves the old print for a
  page now marked failed. Extend
  `test_crashed_run_is_failed_and_retried_on_the_next_change`
  (`test_server_worker.py:287`) to check both.
- [ ] **README still says the server does not exist** (`README.md:260-261`),
  contradicting its own status table. The status table on `main` also says
  "not started".
- [ ] **Promote a page that changed during its run** (R8.2, minor). Next-page
  promotion (`store.py:283-287`) only moves pages in `changed`. A page that
  was `processing` and got a new shot returns to `changed` at run end
  (`store.py:496`) and waits the 30 s idle period.
- [ ] **Pages with no shots show `changed` forever** in `/status` (schema
  default, `store.py:44`). Cosmetic.
- [ ] **Test the 507 response** (task 5.1). Implemented at `api.py:326-328`,
  not tested.
- [ ] **Preview ETag wording** (R6.4). The ETag is `"<sha>-thumb"`
  (`api.py:347`), as in the design, not "equal to the hash" as the
  requirement says. Fix the requirement.

## Glare detector (`glare-detector`, spec only)

Nothing is implemented; every task (1.1 to 7) is open. Blocked on PR #1.

- [ ] **Merge PR #1 first.** It changes `pipeline.py`, `fuse.py`, `synth.py`
  and `cli.py`, the same files tasks 1, 3, 5 and 6 edit.
- [ ] **Task 7 changes code, not just a note.** It says to note the change in
  the album-pdf PR, but `album.py:223` (`_page_warnings`) already computes
  page warnings, so the album step must be changed to copy the page's
  `warnings` instead.
- [ ] **Real-page check** (task 7) needs five real pages and hand-drawn truth
  masks, done locally.
- Server and app support is future work: PR #9 already passes page warnings
  through from `report.json` (`worker.py:227-238`), and the app shows none.

## Page curvature (`page-curvature`, implemented)

Tasks 1 to 7 are implemented. The neighbouring page's side is found from the
page candidates (`pipeline._neighbour_side`). Open:

- [ ] **Real pages are not flattened.** On the five real sleeved pages the
  traced outline jumps between the sleeve's and the paper's edges, so `auto`
  treats all five as flat (`small_gain` on four, `fit_failed` on one), and
  `force` picks a wrong binding side on two. Tracing the paper's own edge
  inside the sleeve is the next step.
- [ ] **Strip MAE on two-shot pages** (R8.5): 1.4 times the rest of the page
  on the synthetic two-shot case, not 1.2.
- [ ] **Lift accuracy at 0.5%** (design test plan): within 30% on the
  rendered outlines, not 10%; 2% and 5% are within 10%.
- [ ] **Flat path error on top-binding synthetic pages** (R8.3) stays under
  0.3% of the width at 3% and 5% lifts; the test checks left bindings.
