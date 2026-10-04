# Requirements: glare detector and page warnings

## Introduction

The page pipeline (`backend/src/albumproc`) merges several photos of one album
page into one composed page image. It removes glare by comparing the shots:
glare only adds light, so where shots disagree the darker one wins
(`fuse.py`). That comparison is also its only glare detector. The report's
`glare_fraction` is each shot's brightness excess over the darkest shot
covering the same spot.

On Clark's real album pages (photographed in plastic sleeves), that leaves a
blind spot exactly where it matters. Wherever only one shot covers part of the
page, which is most of a page stitched from two halves, there is nothing to
compare with. The wrinkled-sleeve glare stays in the composed page and the
report says nothing about it. The same happens where every covering shot has
glare in the same place. Uncovered page area is reported only as one number
(`coverage`), with no indication of where the gap is.

This spec adds:

1. a glare detector that works on a single photo, without needing a second
   shot to compare against,
2. a measure of the glare that remains in the composed page, with where it is
   and why it could not be removed,
3. the uncovered page area, with where it is, and
4. a metadata file written alongside every composed page, carrying warnings
   for glare and uncovered area in a form the album step and the capture app
   can act on.

The detector only reports. It does not change the composed image: glare that
no shot sees past cannot be removed, only flagged so the user can take another
shot.

### Glossary

- **Shot**: one input photo of a page.
- **Composed page**: the flat, cropped page image `process_page` returns.
- **Page frame**: pixel coordinates of the composed page. Every shot is warped
  into it before fusion.
- **Glare map**: per-pixel glare likelihood from 0 to 1 for one shot.
- **Single-image cue**: evidence of glare from one shot alone (brightness,
  washed-out colour, lost contrast, clipping).
- **Comparison cue**: the existing evidence from comparing shots, that is,
  brightness excess over the darkest shot covering the same spot.
- **Residual glare**: glare in the composed page, meaning pixels whose value
  came mostly from shots that had glare there.
- **Uncovered area**: page pixels no shot covers; they are black in the
  composed page today.
- **Region**: one connected area of residual glare or of uncovered area,
  above a minimum size.
- **Page metadata**: the JSON file written next to the composed page,
  superseding today's optional `--report` output.
- **Warning**: an entry in the page metadata with a machine-readable `code` and
  a human-readable `message`.

## Requirements

### Requirement 1: Glare in a single shot

**User story:** As someone archiving pages in plastic sleeves, I want glare
found in a shot even when no other shot covers the same spot, so that glare on
a page stitched from parts is not missed.

#### Acceptance criteria

1. THE system SHALL compute a glare map for each shot from that shot alone,
   using brightness relative to its surroundings, loss of colour saturation,
   loss of local contrast, and highlight clipping.
2. THE system SHALL compute the glare map in the page frame for every shot
   used by `process_page`, restricted to the pixels that shot covers.
3. WHERE two or more shots cover a pixel, THE system SHALL combine the
   single-image cue with the comparison cue for that pixel.
4. THE system SHALL NOT count a pixel as glare on the comparison cue alone
   unless its single-image cue is at least weakly positive, so that shadows
   and small misalignments in another shot are not reported as glare.
5. THE system SHALL NOT report uniformly bright page content as glare when
   that content keeps its texture and edges. This covers white print borders,
   white paper, overexposed skies and white text backgrounds.
6. THE system SHALL also accept a single unwarped photo and return its glare
   map in that photo's own pixels, so that a shot can be checked before the
   whole page is processed.

### Requirement 2: Glare remaining in the composed page

**User story:** As someone checking a processed page, I want to know how much
glare is left in the composed page and where, so that I know whether to
re-shoot it.

#### Acceptance criteria

1. THE system SHALL compute a residual glare map for the composed page as the
   fusion-weighted average of the covering shots' glare maps, using the same
   weights that produced each composed pixel.
2. THE system SHALL count a composed pixel as residual glare WHEN its residual
   glare value is at least the residual threshold (0.5 by default).
3. THE page metadata SHALL report the residual glare fraction, which is the
   share of covered page pixels counted as residual glare.
4. THE page metadata SHALL report, for each used shot, the share of its
   covered pixels its own glare map counts as glare, alongside the existing
   comparison-based `glare_fraction`.
5. THE system SHALL leave the composed page image unchanged: for the same
   inputs and options its pixels SHALL be identical with and without the
   glare detector.

### Requirement 3: Glare regions

**User story:** As the capture app, I want each remaining glare area located
and explained, so that I can tell the user where to aim the next shot and
whether a new angle will help.

#### Acceptance criteria

1. THE system SHALL group residual glare pixels into regions, and SHALL drop
   regions smaller than the minimum region area (0.05% of the page by
   default).
2. THE page metadata SHALL give each region its bounding box as fractions of
   the page width and height (0 to 1, origin top left), its bounding box in
   composed-page pixels, its area as a fraction of the page, its mean residual
   glare value, and a location word from a 3 × 3 grid (`top-left`, `top`,
   `top-right`, `left`, `centre`, `right`, `bottom-left`, `bottom`,
   `bottom-right`).
3. THE page metadata SHALL list, for each region, the shots that cover it.
4. WHEN a glare region is covered by only one shot over most (more than half)
   of its area, THE system SHALL give it the cause `single_shot`.
5. WHEN a glare region is covered by two or more shots over most of its area,
   THE system SHALL give it the cause `all_shots_glared`.
6. THE system SHALL list regions in descending order of area.

### Requirement 4: Uncovered page area

**User story:** As someone archiving an oversized page shot in parts, I want to
know which part of the page no photo reached, so that I can photograph just
that part.

#### Acceptance criteria

1. THE page metadata SHALL report the uncovered fraction, which is the share of
   page pixels covered by no shot, computed from the same coverage mask that
   gives `coverage`.
2. THE system SHALL group uncovered pixels into regions, and SHALL drop regions
   smaller than the minimum region area.
3. THE page metadata SHALL give each uncovered region the same bounding box,
   area and location fields as a glare region, plus the page edges it touches
   (`top`, `right`, `bottom`, `left`, or none for a hole inside the page).
4. THE system SHALL keep the existing `coverage` field, equal to one minus the
   uncovered fraction to the same rounding.

### Requirement 5: Warnings

**User story:** As someone processing a whole album, I want problems on a page
stated as warnings I can scan, so that I re-shoot only the pages that need it.

#### Acceptance criteria

1. THE system SHALL write warnings as objects with a machine-readable `code`, a
   human-readable `message`, and the fields that back the message.
2. WHEN at least one glare region remains after the minimum-area filter, THE
   system SHALL add a `glare` warning giving the residual glare fraction, the
   number of regions, the location of the largest, and how many regions have
   the cause `single_shot`.
3. WHEN at least one uncovered region remains after the minimum-area filter,
   THE system SHALL add an `incomplete_coverage` warning giving the uncovered
   fraction, the number of regions and their locations.
4. WHEN one or more shots were dropped because they matched no other shot,
   THE system SHALL add a `photos_dropped` warning listing their indexes.
5. THE `glare` message SHALL suggest another shot from a different angle WHEN
   any region has the cause `single_shot`, and SHALL suggest changing the
   light or the camera angle for every shot WHEN all regions have the cause
   `all_shots_glared`.
6. THE system SHALL use the warning codes `incomplete_coverage` and
   `photos_dropped` with the same meaning as the album spec
   (`.kiro/specs/album-pdf`), so that the album step can pass page warnings
   through unchanged.
7. WHERE the user sets the residual threshold or the minimum region area, THE
   system SHALL apply that value instead of the default and record it in the
   page metadata.

### Requirement 6: Page metadata file

**User story:** As the album step and the upload server, I want every composed
page to come with its metadata on disk, so that warnings travel with the image
without anyone asking for a report.

#### Acceptance criteria

1. WHEN `albumproc page` writes a composed page to `<name>.<ext>`, THE system
   SHALL also write the page metadata to `<name>.json` in the same folder,
   unless `--report` names another path.
2. THE page metadata SHALL contain every field of today's report unchanged in
   name and meaning, plus `schema_version` (2), `glare`, `uncovered`,
   `warnings` and the detector parameters used.
3. THE system SHALL write the page metadata after the composed page, each
   under a temporary name renamed into place, so that a metadata file never
   describes a missing or partial image.
4. THE system SHALL keep printing the page metadata to standard output as
   `albumproc page` does today.
5. THE `PageReport` returned by `process_page` SHALL carry the same glare,
   uncovered and warning data, so that Python callers get it without reading
   the file.
6. THE page metadata SHALL be valid JSON with only string keys and finite
   numbers, rounding fractions to 4 decimal places.

### Requirement 7: Mask images and debug output

**User story:** As someone tuning the detector on real pages, I want to see
what it found, so that I can judge it by eye.

#### Acceptance criteria

1. WHERE the user passes `--masks`, THE system SHALL write
   `<name>.glare.png` (residual glare, 0 to 255) and `<name>.uncovered.png`
   (255 where uncovered) at the composed page's size.
2. WHERE the user passes `--debug`, THE system SHALL also write each shot's
   glare map in the page frame and an overlay of the composed page with glare
   regions outlined in one colour and uncovered regions in another, each
   labelled with its index.
3. THE system SHALL write no mask or debug images unless asked.

### Requirement 8: Checking one photo

**User story:** As a future upload server, I want to check one photo for glare
on its own, so that the capture app can warn about a bad shot while the user is
still at the page.

#### Acceptance criteria

1. THE system SHALL provide `albumproc glare <photo>...` that prints, for each
   photo, its glare fraction and its glare regions in photo coordinates, as
   JSON.
2. WHERE `--overlay <folder>` is given, THE system SHALL write each photo with
   its glare regions outlined.
3. THE system SHALL expose `detect_glare` from the `albumproc` package, taking
   one image and an optional validity mask and returning the glare map.
4. WHEN a photo cannot be read, THE system SHALL report it by name and exit
   with status 2.

### Requirement 9: Accuracy

**User story:** As the developer, I want measurable targets for the detector,
so that changes can be judged and regressions caught.

#### Acceptance criteria

1. THE synthetic generator SHALL produce a sleeve-like glare style (long,
   wrinkled streaks with a soft veil and a clipped core) in addition to the
   existing spots, and SHALL keep its per-shot ground-truth glare mask.
2. WHEN tested on synthetic stitched pages, where most of the page is covered
   by one shot, THE detector SHALL find at least 80% of the residual glare area
   and SHALL report no more than 0.2% of glare-free page area as glare.
3. WHEN tested on synthetic full-page cases where merging removes the glare,
   THE system SHALL report no `glare` warning.
4. WHEN tested on a glare-free synthetic page that includes white print
   borders and bright print content, THE system SHALL report no `glare`
   warning.
5. THE system SHALL provide an evaluation command that scores the residual
   glare map against a hand-drawn mask image, so that real pages can be
   checked locally without committing family photos to the repository.

### Requirement 10: Cost and compatibility

**User story:** As the operator of the backend, I want the detector to add
little time and break nothing, so that it can stay on for every page.

#### Acceptance criteria

1. THE system SHALL compute glare maps at the fusion weight resolution (long
   side at most 1000 px) and SHALL add no more than 10% to `process_page`'s
   run time on the synthetic test cases.
2. THE system SHALL give identical page metadata for identical inputs and
   options.
3. THE existing tests SHALL keep passing without changes to their assertions.
4. THE system SHALL keep the `albumproc page` command's existing options and
   the composed image they produce unchanged.
