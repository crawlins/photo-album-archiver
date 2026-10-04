# Requirements: page curvature correction

## Introduction

The page pipeline (`backend/src/albumproc`) assumes every album page is flat.
It maps each photo onto the page with one homography (`register.py`), finds
the page as a four-cornered outline (`page.py`), and warps the photos into a
rectangle (`pipeline.py`). The README already lists the limit: "A page
bulging near the album's spine will be slightly distorted; that needs a
curved-surface model."

Clark's real pages show it. They are 12 × 12 in pages in a post-bound album,
in plastic sleeves, photographed while the album lies open. Near the binding
the page lifts off the table and bends down into the gutter. In the photos
the page's top and bottom edges bow by up to about 1% of the page width
(30 to 40 px at full resolution) over the strip nearest the binding, and the
rest of the page is close to flat. That was measured roughly from the edge
traces of four of the five real pages; it is not a precise figure.

On a flat-page model this causes four problems:

1. **Bent lines.** Print borders, mats and titles that cross the curved strip
   come out bent in the composed page.
2. **Squeezed content.** The strip that bends away from the camera is seen
   foreshortened, so it is narrower in the output than on the real page, and
   the page's measured proportions (`aspect`) come out too narrow.
3. **Misaligned shots.** Two shots from different angles see the lifted strip
   with different parallax. One homography per shot cannot align both the flat
   part and the strip, so fusion blurs or doubles content there.
4. **Wrong page outline.** The corners are fitted to straight sides, so a
   bowed side either leaves background in the crop or cuts off page content.

This spec adds a curved-page model that detects when a page is bent near its
binding, estimates the shape of the bend, and flattens the page so that
straight lines on the real page are straight in the output and distances
along the page are true. Flat pages keep going through today's path
unchanged.

### Glossary

- **Shot**: one input photo of a page.
- **Composed page**: the flat, cropped page image `process_page` returns.
- **Binding side**: the page edge nearest the album's spine or posts (`left`,
  `right`, `top` or `bottom` of the composed page).
- **Curved strip**: the part of the page next to the binding side that is not
  flat.
- **Page surface**: the page's shape in 3D. Here it is a *cylindrical
  surface*: it bends only across the binding, so every line parallel to the
  binding side stays straight in 3D. A sheet of paper bent without creasing or
  stretching takes this shape.
- **Profile**: the page surface's cross-section, perpendicular to the binding
  side.
- **Lift**: the profile's height above the plane of the flat part of the page,
  as a fraction of the page width across the binding.
- **Flattening map**: for every pixel of the composed page, the point in a shot
  that shows it.
- **Planar path**: today's pipeline, with one homography per shot.
- **Page metadata**: the JSON report written alongside the composed page (see
  `.kiro/specs/glare-detector`).

## Requirements

### Requirement 1: Detect a curved page

**User story:** As someone archiving pages from a bound album, I want the
pipeline to notice when a page is bent near its binding, so that I do not have
to tell it for every page.

#### Acceptance criteria

1. THE system SHALL trace the page outline as curves rather than four straight
   sides, and SHALL measure how far each side bows from a straight line.
2. THE system SHALL fit both a flat page and a curved page to the same
   evidence, and SHALL treat the page as curved only WHEN the curved fit
   reduces the fit error by at least the minimum improvement (30% by default)
   AND its maximum lift is at least the minimum lift (0.3% of the page width by
   default).
3. THE system SHALL choose the binding side automatically as the side next to
   which the fitted lift is greatest, and SHALL prefer the side facing a
   neighbouring page WHEN one was found next to the page.
4. WHERE the user names the binding side, THE system SHALL use it and SHALL
   NOT try the other sides.
5. WHERE the user turns curvature correction off, THE system SHALL use the
   planar path without fitting a curved model.
6. WHERE the user forces curvature correction, THE system SHALL apply the
   curved model even WHEN it does not meet the thresholds in 1.2, unless the
   fit fails (Requirement 7).

### Requirement 2: Flatten the page

**User story:** As someone printing the archived pages, I want lines that are
straight on the page to be straight in the print, and the area near the
binding at its true size, so that photos near the spine are not bent or
squeezed.

#### Acceptance criteria

1. WHEN a page is treated as curved, THE system SHALL map every composed-page
   pixel to the page surface by distance along the surface, so that equal
   distances on the real page are equal distances in the composed page.
2. THE system SHALL produce each shot's contribution to the composed page with
   a single resampling of the full-resolution shot, as the planar path does.
3. THE system SHALL set the composed page's aspect ratio from the page
   surface's true width and height, and SHALL report it as `aspect`.
4. THE system SHALL keep the page's four corners at the corners of the
   composed page, with the same inset trim as the planar path.
5. THE system SHALL keep the composed page's resolution at roughly what the
   shots captured on the flat part of the page, capped by the existing
   `max_side`.

### Requirement 3: Align shots on a curved page

**User story:** As someone shooting each page from more than one angle to
remove glare, I want the shots to line up near the binding too, so that
merging them does not blur or double the content there.

#### Acceptance criteria

1. WHEN a page is treated as curved, THE system SHALL correct each
   non-reference shot's remaining misalignment in the composed page with a
   smooth displacement field estimated from matched features.
2. THE system SHALL estimate a shot's displacement against shots already
   aligned where they overlap, so that a stitched shot that does not overlap
   the reference is still aligned.
3. THE system SHALL limit each displacement to the maximum correction (1% of
   the page's long side by default), and SHALL fade the correction to zero
   where a shot has no matches nearby.
4. THE system SHALL fold the displacement into the shot's flattening map, so
   that each shot is still resampled once (Requirement 2.2).
5. THE system SHALL report, for each shot, the median feature misalignment
   against the aligned shots before and after the correction, in composed-page
   pixels.

### Requirement 4: Pages that stay flat

**User story:** As someone archiving loose pages and album covers, I want flat
pages processed exactly as they are today, so that the new model cannot make
them worse.

#### Acceptance criteria

1. WHEN a page is not treated as curved, THE system SHALL produce the same
   composed image, pixel for pixel, as the planar path, for the same inputs
   and options.
2. WHEN a page is not treated as curved, THE system SHALL keep every existing
   page-metadata field equal to what the planar path reports.
3. THE existing tests SHALL keep passing without changes to their assertions.

### Requirement 5: Report the curvature

**User story:** As someone checking a processed page, I want to know whether
the page was flattened and how much it was bent, so that I can judge the
result and spot pages to re-shoot.

#### Acceptance criteria

1. THE page metadata SHALL include a `curvature` object for every page with:
   whether the curved model was applied, the mode used (`auto`, `off` or
   `force`), the binding side (or `null`), the maximum lift, the width of the
   curved strip as a fraction of the page width, the change in page width
   compared with the planar estimate, and the outline fit error of the flat
   and the curved fit in pixels.
2. WHEN the curved model was applied, THE `curvature` object SHALL include the
   profile as a list of (position, lift) samples across the page, as
   fractions of the page width, for plotting.
3. WHEN the curved model was considered but not applied, THE `curvature`
   object SHALL say why (`flat`, `small_gain`, `fit_failed` or `off`).
4. THE system SHALL write warnings in the format of the glare-detector spec,
   with a machine-readable `code`, a human-readable `message`, and the fields
   that back the message.
5. WHEN any part of the curved strip was seen at more than the steep angle
   (60° from straight on by default) in every shot covering it, THE system
   SHALL add a `steep_binding` warning giving the share of the page affected
   and the resolution there compared with the flat part, and suggesting a shot
   aimed more squarely at the binding.
6. WHEN the page outline bows by more than the minimum lift but the curved fit
   failed, THE system SHALL add a `curvature_uncorrected` warning.
7. THE system SHALL give identical page metadata for identical inputs and
   options.

### Requirement 6: Command line and debug output

**User story:** As someone tuning the model on real pages, I want to control
it and to see what it fitted, so that I can judge it by eye.

#### Acceptance criteria

1. THE `albumproc page` command SHALL accept `--curvature auto|off|force`
   (default `auto`) and `--binding auto|left|right|top|bottom` (default
   `auto`).
2. THE `albumproc album` command SHALL accept the same two options for all
   pages, and `album.json` SHALL accept `curvature` and `binding` per page,
   overriding them.
3. WHERE the user passes `--debug`, THE system SHALL write the traced outline
   and both fitted outlines over the preview mosaic, the fitted profile as a
   plot, a grid of page lines drawn through the flattening map onto the
   reference shot, and each shot's displacement field as a colour image.
4. THE system SHALL keep every existing option of both commands and its
   meaning.

### Requirement 7: Failure handling

**User story:** As someone processing a whole album unattended, I want a page
whose curvature cannot be fitted to still come out, so that one odd page does
not stop the album.

#### Acceptance criteria

1. WHEN the curved fit does not converge, gives a profile that folds back on
   itself, or gives a page whose corners are more than 2% of the page diagonal
   from the traced outline, THE system SHALL fall back to the planar path for
   that page.
2. WHEN the system falls back, THE composed image SHALL be the one the planar
   path produces, and the metadata SHALL record the reason (Requirement 5.3).
3. WHEN a shot's displacement field cannot be estimated (too few matches), THE
   system SHALL use that shot without the correction and SHALL record that in
   its metadata entry.

### Requirement 8: Accuracy

**User story:** As the developer, I want measurable targets for the
correction, so that changes can be judged and regressions caught.

#### Acceptance criteria

1. THE synthetic generator SHALL render pages bent near a binding side with a
   given lift and curved-strip width, from a profile family different from the
   one the model fits, and SHALL keep the flat page image as ground truth.
2. WHEN tested on synthetic curved pages with a lift of up to 5% of the page
   width and a binding on any side, THE composed page SHALL be within 0.3% of
   the page width of the ground truth everywhere after the best single
   homography, which is the remaining bend and squeeze that a flat model
   cannot explain.
3. WHEN tested on the same pages, THE planar path SHALL exceed that 0.3% for
   lifts of 3% and above, so that the test shows the correction is needed.
4. WHEN tested on the same pages, THE reported `aspect` SHALL be within 0.5%
   of the ground truth.
5. WHEN tested on synthetic two-shot curved pages, THE fused page's mean
   absolute error against the ground truth in the curved strip SHALL be no
   more than 1.2 times that on the flat part.
6. WHEN tested on every existing synthetic case (all flat), THE system SHALL
   not treat any page as curved.
7. THE system SHALL provide a command that measures the straightness of long
   lines in a composed page, so that real pages can be compared before and
   after correction locally without committing family photos to the
   repository.

### Requirement 9: Cost and compatibility

**User story:** As the operator of the backend, I want the correction to cost
little on flat pages and a bounded amount on curved ones, so that it can stay
on for every page.

#### Acceptance criteria

1. THE detection step SHALL add no more than 10% to `process_page`'s run time
   on the existing synthetic flat cases.
2. THE system SHALL add no more than 60% to `process_page`'s run time on the
   synthetic curved cases.
3. THE system SHALL keep peak memory for a page under twice that of the planar
   path for the same page, by building flattening maps in horizontal bands.
4. THE system SHALL add at most one new runtime dependency, and only a widely
   packaged one.
