# Requirements: glare only one shot sees

## Introduction

Fusion removes glare by comparing shots: where two shots cover the same
spot, the darker one shows the print (`fuse.py`). Where only one shot covers
part of a page, there is nothing to compare with, and whatever glare that
shot has goes into the composed page. This spec is about that glare.

Two things exist today:

- **Single-shot detectors.** The glare detector spec
  (`.kiro/specs/glare-detector/`) built a hand-made detector (`features`,
  the default) and PR #22 added a small learned one (`--detector net`). On
  nine held-out real sleeved pages they find 0.2% and 10% of the glare the
  overlap comparison finds; the spec asked for 80%. The net's recall varies
  from 0.62 on one page to 0.02 on another, and what both miss is mostly a
  soft veil of sheen over a third or half of a page, which one photo cannot
  tell from the page being lit unevenly. The net is trained on 15 pages of a
  single album.
- **Second-shot prompts.** The overlap coverage spec
  (`.kiro/specs/overlap-coverage/`, merged) made "two shots over every part
  of a page" the guarantee: the pipeline reports single coverage as a
  warning with regions and shot boxes, the server passes it to the app, and
  the app marks the page with a red dot and lets the user add shots to it.

The open question is which of the two to build on. The design
(`design.md`) lays out both and recommends relying on the second-shot
prompts now and revisiting training once a full album has been shot, since
every page shot under the guarantee yields new overlap labels for free.
These requirements follow that recommendation. Clark has not yet decided;
`design.md` and `tasks.md` say what changes if training is chosen instead.

What the prompts still leave open, and this spec closes:

1. Nobody has measured, on real pages, whether following the prompt
   actually removes the glare, or how much glare lies outside any warned
   region (the seam band below the 1% single-coverage minimum, for one).
2. A single-coverage warning says only that one photo covers an area, not
   whether glare was seen there, so a stitched page with a clean half and a
   glared half reads the same as one with two clean halves.
3. The capture screen marks only the page being shot. Feedback for the page
   before arrives 30 to 70 seconds after its last shot, by which time the
   user is shooting the next page and sees nothing unless they open the
   page overview.
4. Nothing stops the user from switching to another album, or putting the
   album away, while pages still need another shot or are still being
   checked.

### Glossary

Terms are as in the glare detector and overlap coverage specs. In addition:

- **Single-shot glare**: glare in the composed page at pixels covered by
  exactly one used shot.
- **Prompt**: a `single_coverage` or `glare` warning shown in the app, with
  its regions.
- **Prompted shot**: a shot taken because of a prompt, aimed at the region it
  outlines.
- **Replay**: re-running a real page that has many shots with only some of
  them, as if the user had taken fewer, then adding the shot a prompt would
  ask for from the shots left over.
- **Reference composition**: the page composed from all of its shots, used
  as the glare-free truth in a replay.
- **Glare seen**: the share of a single-coverage region that the glare
  detector in use counts as residual glare.
- **Pending page**: a page with shots whose feedback has not arrived yet:
  shots still queued for upload, or uploaded since the last completed run.
- **Other warned page**: a warned page that is not the target page.

## Requirements

### Requirement 1: Replay on real pages

**User story:** As the developer, I want to measure on Clark's real pages
whether a second shot taken where the app asks removes the glare, so that the
choice between prompts and training rests on numbers rather than on a guess.

#### Acceptance criteria

1. THE project SHALL provide a replay tool (`backend/tools/replay_glare.py`,
   not installed with the package) that takes page folders from the private
   data repository and writes only numbers (JSON and a printed table), never
   images, masks or photos.
2. FOR each page with at least four shots, THE tool SHALL build the
   reference composition from all shots and, from it, a truth mask of glare
   in any composition of a subset: pixels brighter than the reference by the
   label threshold of `tools/real_glare_data.py`, after the same smooth gain
   and few-pixel shift allowance that tool uses.
3. THE tool SHALL start each replay from a subset of two shots that together
   cover the page and overlap by less than half of it, as a user stitching a
   page would, and SHALL try every such pair up to a limit per page.
4. THE tool SHALL then follow the prompts: from the subset's page metadata
   take the largest `single_coverage` region, add the unused shot whose
   coverage of that region's page box is largest, re-run, and repeat up to
   two prompted shots or until no `single_coverage` warning remains.
5. THE tool SHALL report, per page and over all pages: the single-shot glare
   area before any prompted shot; the share of it removed after one and
   after two prompted shots; the share of all glare in the composition that
   lies inside a warned region (`single_coverage` or `glare`); the share that
   lies in single coverage outside every warned region; and the same with
   the detector set to `features` and to `net`.
6. THE replay results SHALL be recorded in this spec's `tasks.md` notes, in
   the glare section of the README, and in the TODO entry for revisiting
   training (Requirement 6), with the date and the commit they were run at.
7. WHEN the replay shows that one or two prompted shots remove less than 80%
   of the single-shot glare, OR that more than 10% of all glare lies in
   single coverage outside every warned region, THE remaining tasks of this
   spec SHALL stop until the design is revisited, because the prompts would
   then not be a sufficient guarantee.
8. AFTER the revisit of 2026-10-18 (Clark: rest the guarantee on stitched
   pages; design, "Revisit: stitched pages"), Requirement 1.7 SHALL be read
   on the replays that start from a stitch pair; the one-photo starts SHALL
   still be reported, as the case the guarantee does not cover.

### Requirement 2: Glare seen in single coverage

**User story:** As someone shooting a sleeved page in two halves, I want to
know which half had glare, so that I take the extra shot where it matters
first.

#### Acceptance criteria

1. THE page metadata SHALL give every single-coverage region a `glare_seen`
   field: the share of the region's pixels whose residual glare is at least
   the residual threshold, rounded to 4 decimal places, using the detector
   in use.
2. WHEN the glare detector is disabled, THE system SHALL omit `glare_seen`
   rather than report 0, so that "not looked" is not read as "clean".
3. THE `single_coverage` warning SHALL give `glare_seen_regions`, the number
   of its regions whose `glare_seen` is at least the glare-seen threshold
   (2% by default, `--glare-seen` on `albumproc page`, recorded in the
   detector parameters).
4. WHEN at least one region meets the glare-seen threshold, THE warning's
   message SHALL name the largest such region first and say that glare was
   seen there; otherwise the message SHALL be as the overlap coverage spec
   gives it.
5. THE system SHALL keep listing single-coverage regions in descending order
   of area, and SHALL keep raising the `single_coverage` warning whether or
   not glare was seen, because the detectors miss most of the glare (a
   region with no glare seen is not shown to be clean).
6. THE page metadata's `schema_version` SHALL increase by one.
7. THE album server SHALL pass `glare_seen` and `glare_seen_regions` through
   in the album status unchanged, and THE app SHALL parse them, tolerating
   their absence.
8. WHILE the app draws a page's `single_coverage` regions, THE app SHALL
   draw a region whose `glare_seen` meets the threshold with a solid
   outline and the others with a dashed one, in the same colour.

### Requirement 3: Other pages flagged while shooting

**User story:** As someone photographing an album, I want to be told on the
capture screen when a page I have already left needs another shot, so that I
go back while the album is still open in front of me.

#### Acceptance criteria

1. WHILE the capture screen is open AND at least one other warned page has a
   `single_coverage` or `glare` warning, THE app SHALL show a notice on the
   capture screen naming the lowest-numbered such page ("Page 3 needs
   another shot") and how many more there are ("and 2 more").
2. WHEN the user taps the notice, THE app SHALL open the review screen at
   the first photo of the named page that has a shot box for one of its
   warnings, or its first photo when none has, as a page tile tap does
   (overlap coverage, Requirement 4.4).
3. WHEN a page becomes an other warned page while the capture screen is
   open, THE app SHALL vibrate briefly once, so that a user looking at the
   album rather than the screen notices; it SHALL NOT vibrate again for that
   page until its warnings have cleared and returned.
4. THE notice SHALL NOT cover the shutter button, the "Next page" button or
   the camera preview's centre, and SHALL leave the capture screen fully
   usable while shown.
5. THE notice SHALL disappear WHEN no other warned page has such a warning.
6. THE app SHALL show no notice for `incomplete_coverage`-only pages, which
   the page overview already marks, nor while no server is configured.

### Requirement 4: Leaving an album with work left

**User story:** As someone who will put the album back on the shelf, I want
to be told before I leave it which pages still need a shot, so that I never
find out after it is gone.

#### Acceptance criteria

1. THE album drawer SHALL show, for each album with at least one warned page,
   the number of its warned pages.
2. WHEN the user switches to another album, creates an album, or opens the
   server album list, AND the current album has a warned page or a pending
   page, THE app SHALL ask once before leaving, stating the number of warned
   pages and of pending pages, with "Stay" and "Leave anyway".
3. WHEN the user chooses "Stay", THE app SHALL open the page overview of the
   current album; WHEN the user chooses "Leave anyway", THE app SHALL leave
   as it would have without asking.
4. THE app SHALL NOT ask WHILE no server is configured, nor when every page
   of the album has feedback without a warning.
5. WHILE the question is open, THE app SHALL keep polling the album's
   status and update the two numbers, so that a user who waits sees pending
   pages resolve.

### Requirement 5: The single-shot detectors

**User story:** As the developer, I want the detectors kept useful and
honest while the prompts carry the guarantee.

#### Acceptance criteria

1. THE `features` detector SHALL stay the default and the `net` detector
   SHALL stay opt-in.
2. THE replay (Requirement 1) SHALL report which detector gives the more
   useful `glare_seen`: the share of single-shot glare area inside regions
   that meet the glare-seen threshold, and the share of regions meeting it
   that have no truth glare.
3. WHERE the replay shows the `net` detector's regions meeting the
   glare-seen threshold have no truth glare in at most 10% of cases AND it
   flags more single-shot glare than `features`, THE albumserver SHALL use
   `net` for its runs by default through its configuration file; the
   `albumproc` command's default SHALL stay `features`.
4. THE existing detector tests (no false warning on glare-free synthetic
   pages, recall floors) SHALL keep passing unchanged.

### Requirement 6: Revisit training later

**User story:** As Clark, I want training the glare model revisited once a
full album has been shot, since that gives many more real shots to learn
from.

#### Acceptance criteria

1. `TODO.md` SHALL carry an entry to revisit training the learned glare
   detector WHEN a full album (10 or more pages) has been shot under the
   overlap coverage guarantee, naming the data it would use (every page's
   overlap labels from `tools/real_glare_data.py`), the replay results to
   compare against, and the bar a retrained net must clear to become the
   default (design, "Option A").
2. THE entry SHALL say that real photos and labels stay in the private data
   repository and that only the exported model and numbers enter this
   repository.

### Requirement 7: Compatibility

**User story:** As the operator, I want this to break nothing that works.

#### Acceptance criteria

1. THE composed page image SHALL be unchanged for the same inputs and
   options.
2. THE existing tests SHALL keep passing without changes to their
   assertions, except those that assert the schema version or the exact set
   of metadata keys.
3. THE app SHALL keep working against a server whose pages were processed
   under an earlier schema version, showing no glare-seen styling for them.
4. THE public repository SHALL contain no real album photos, crops of them,
   masks drawn on them or per-page labels; the replay tool SHALL write its
   output outside the repository unless told otherwise, and the replay test
   SHALL run on synthetic pages only.
