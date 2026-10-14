# Requirements: the paper's edge inside an album sleeve

## Introduction

Clark's album pages sit in clear plastic sleeves, and the page pipeline
(`backend/src/albumproc`) does not know that. `detect_page` (`page.py`)
returns one quadrilateral around "the page", and `trace_outline`
(`curvature.py`) then follows the strongest edge within ±3% of the page
diagonal of each side. Around a sleeved page there are several strong edges
in that band, and which one wins changes along a side.

What a sleeve adds around the paper, measured on the five two-shot real pages
in the private data repository (`real/grandma-wilson`, `real/page-two` to
`real/page-five`) and seen on the 19 `real/glare-*` pages of the same album:

- **Crimped seams** on the two sides across the binding (top and bottom when
  the binding is on the left or right): a grey, heat-sealed band about 1.5%
  of the page width wide, with ridges running across it. On
  `grandma-wilson` the ridges repeat every 10 preview-mosaic pixels (0.55% of
  the page width), and the band's along-side autocorrelation at that lag is
  0.75, against at most 0.25 on paper and on the cloth beside the album.
- **A gap** between the crimp and the paper's edge, 0 to 1% of the width,
  where clear plastic shows whatever is behind it.
- **A binding strip** on the binding side: an opaque cream band about 2.5%
  of the width, between the sleeve's edge and the paper, next to the posts.
- **A clear margin** on the side away from the binding, where the sleeve runs
  past the paper by up to about 5.5% of the width and shows the next pages
  or the table through the plastic.
- On some sleeves, rounded outer corners (`page-two`).

Today's outline mixes these. On `grandma-wilson` the quad's top and bottom
are the crimps' outer edges, its right side is the paper's edge and its left
side is the binding strip's outer edge; the traced bottom side steps from the
crimp to the sleeve's edge below it partway along. As a result (TODO.md, page
curvature):

1. **Curvature correction cannot run on real pages.** With the outline
   jumping by 1 to 3% of the width between edges, a 1% bend near the binding
   is lost in the noise: `auto` keeps all five real pages flat
   (`small_gain` on four, `fit_failed` on `page-two`), and `force` picks the
   wrong binding side on two.
2. **The crop is inconsistent.** Some sides of the composed page include
   sleeve (crimp, strip, clear plastic over the table) and some do not, and
   which ones varies from page to page, so pages of one album come out at
   different sizes and proportions.

This spec adds a sleeve model: it finds the sleeve's bands around the page,
the paper's own edge inside them, and traces that edge as one continuous
curve per side. The composed page is cropped to the paper by default, the
curvature fit uses the paper's traced edges, and a binding strip, where one
is found, tells the curvature fit which side the binding is on. Pages with no
sleeve go through today's path unchanged.

### Glossary

- **Sleeve**: the clear plastic pocket the paper sits in.
- **Paper**: the album page inside the sleeve, with its prints and lettering.
  Its outline is the **paper edge**.
- **Outer outline**: the quadrilateral `detect_page` returns today. It may lie
  on the sleeve's edge, the paper's edge or a band edge in between, and may
  differ from side to side.
- **Corridor**: the strip searched around each side of the outer outline, from
  4% of the page width outside it to 8% inside.
- **Band**: the stretch of a corridor between two adjacent edges that run the
  length of the side. Each is classified as one of:
  - **crimp**: the heat-sealed seam, ridged across;
  - **strip**: the opaque binding strip;
  - **clear**: clear plastic over whatever lies behind the sleeve;
  - **paper**: the paper itself (margin, print or lettering);
  - **outside**: beyond the sleeve (table, cloth, a neighbouring page).
- **Sleeve outline**: the sleeve's outer edge, where it can be seen.
- **Binding side**, **page metadata**, **composed page**, **planar path**: as
  in the page-curvature spec (`.kiro/specs/page-curvature/requirements.md`).
- **Labelled pages**: the five two-shot real pages, with the paper's corners,
  points along each paper edge and the binding side marked by hand, stored in
  the private data repository.

## Requirements

### Requirement 1: Find the sleeve and the paper

**User story:** As someone archiving pages kept in plastic sleeves, I want the
pipeline to tell the sleeve from the paper, so that the page it composes is
the paper and not a mix of paper and plastic.

#### Acceptance criteria

1. THE system SHALL search the corridor of each side of the outer outline for
   edges that run the length of the side, allowing each to bow by up to 1.5%
   of the page width, so that a page bent near its binding is still followed.
2. THE system SHALL classify every band between those edges as `crimp`,
   `strip`, `clear`, `paper` or `outside`.
3. THE system SHALL choose the four paper edges together, as the outermost
   edges that have a band other than a sleeve band (`crimp`, `strip`,
   `clear`) on their inner side, run the full length between the adjacent
   chosen paper edges, and meet them at corners.
4. THE system SHALL NOT take a print's or a mat's border inside the paper for
   the paper edge, however close to the paper edge it lies.
5. THE system SHALL find the sleeve outline on each side where the outermost
   band is seen, and SHALL record a side's sleeve outline as unknown where it
   is not.
6. THE system SHALL treat the page as sleeved only WHEN a crimp band is found
   on at least one side or a strip band on one side, AND the paper edge is
   found on at least three sides.
7. WHERE the user turns sleeve detection off, THE system SHALL use the outer
   outline exactly as today.

### Requirement 2: One continuous edge per side

**User story:** As someone relying on the page outline for cropping and for
flattening a bent page, I want each side of the paper traced along the same
physical edge from end to end, so that the outline does not jump between the
sleeve's edges and the paper's.

#### Acceptance criteria

1. THE system SHALL trace each found paper edge as a curve, to a fraction of a
   pixel, within ±0.3% of the page width of the edge chosen in 1.3, so that the
   trace cannot move to another edge of the corridor.
2. THE system SHALL bridge a stretch of up to 20% of a side where the paper
   edge is not visible (glare, a shadow, a print touching it) by the curve
   fitted to the rest of that side, and SHALL NOT follow another edge across
   it.
3. THE system SHALL give the paper's corners as the intersections of the
   traced edges' end tangents, as `trace_outline`'s corners are found today.

### Requirement 3: Crop to the paper or to the sleeve

**User story:** As someone printing an album, I want every page cropped to the
same thing, the paper by default, so that pages from one album come out at
one size and proportion.

#### Acceptance criteria

1. WHEN a page is sleeved, THE system SHALL by default compose the page from
   the paper's outline, so that no sleeve band is in the composed page beyond
   the existing inset trim.
2. WHERE the user asks for the sleeve crop, THE system SHALL compose the page
   from the paper's outline extended on each side by that side's measured
   band width, in page coordinates, so that the sleeve crop follows the same
   page model (flat or bent) as the paper crop. Where a side's sleeve outline
   is unknown, the extension SHALL be the width of the opposite side's bands
   when that side is of the same kind (crimp opposite crimp), and zero
   otherwise.
3. THE system SHALL compute the page's `aspect` from the cropped outline, so
   that a paper crop reports the paper's proportions.
4. WHEN a page is not sleeved, THE crop option SHALL have no effect.

### Requirement 4: Unsleeved pages are unchanged

**User story:** As someone archiving loose pages and album covers, I want pages
without a sleeve processed exactly as today, so that the sleeve model cannot
make them worse.

#### Acceptance criteria

1. WHEN a page is not sleeved, or sleeve detection is off, THE system SHALL
   produce the same composed image, pixel for pixel, and the same values for
   every existing page-metadata field as the pipeline without this change.
2. THE existing synthetic tests SHALL keep passing without changes to their
   assertions.
3. THE real-page test's expected aspect ratios
   (`backend/tests/test_real.py`) SHALL be replaced by the paper aspect ratios
   from the labelled pages, since those pages are now cropped to the paper.

### Requirement 5: Curvature from the paper's edges

**User story:** As someone archiving pages bent near the binding, I want the
curvature correction to work on sleeved pages, so that the work in the
page-curvature spec reaches real pages.

#### Acceptance criteria

1. WHEN a page is sleeved, THE curvature fit SHALL use the traced paper edges
   (Requirement 2) as its outline evidence instead of tracing the outer
   outline.
2. WHEN a strip band is found on one side, THE curvature fit SHALL prefer
   that side as the binding side, in the same way and with the same factor as
   it prefers a side facing a neighbouring page, applying the factor once
   WHEN both cues name the same side.
3. WHERE the user names the binding side, THE named side SHALL win over the
   strip cue.
4. THE page-curvature spec's requirements SHALL keep holding on its synthetic
   cases, which have no sleeve.

### Requirement 6: Report the sleeve

**User story:** As someone checking a processed page, I want to see what the
pipeline took for sleeve and paper, so that I can spot a wrong crop and know
why.

#### Acceptance criteria

1. THE page metadata SHALL include a `sleeve` object for every page with:
   whether the page was treated as sleeved, the mode (`auto` or `off`), the
   crop used (`paper` or `sleeve`), and per side (`top`, `right`, `bottom`,
   `left` of the composed page) the bands found from the outside in, each with
   its kind and its mean width as a fraction of the page width, whether the
   paper edge was found, and whether the sleeve outline was seen.
2. THE `sleeve` object SHALL give the side holding a strip band, or `null`.
3. WHEN a page is not treated as sleeved but bands were found, THE `sleeve`
   object SHALL say why (`few_sides`, `no_sleeve_band` or `off`).
4. WHEN a sleeved page's paper edge was not found on one side, THE system SHALL
   use the outer outline's side there and SHALL add a `paper_edge_uncertain`
   warning in the glare-detector spec's format, naming the side.
5. THE page metadata schema version SHALL be raised by one.
6. THE system SHALL give identical page metadata for identical inputs and
   options.

### Requirement 7: Command line, album options and debug output

**User story:** As someone tuning the model on real pages, I want to control it
and to see what it found, so that I can judge it by eye.

#### Acceptance criteria

1. THE `albumproc page` and `albumproc album` commands SHALL accept
   `--sleeve auto|off` (default `auto`) and `--crop paper|sleeve` (default
   `paper`), and `album.json` SHALL accept `sleeve` and `crop` per page,
   overriding them and becoming part of the page's cache key.
2. WHERE the user passes `--debug`, THE system SHALL write an image of the
   preview mosaic with each side's candidate edges, the bands coloured by
   kind, the chosen paper outline and the sleeve outline drawn on it.
3. THE system SHALL keep every existing option of both commands and its
   meaning.

### Requirement 8: Accuracy

**User story:** As the developer, I want measurable targets on synthetic and
real pages, so that changes can be judged and regressions caught.

#### Acceptance criteria

1. THE synthetic generator SHALL render a page inside a sleeve with crimp
   bands on the two sides across the binding, a gap, a binding strip, a clear
   margin over the background, optionally rounded sleeve corners, the paper
   shifted and turned slightly inside the sleeve, and a print whose border
   lies 1 to 3% of the width inside the paper edge, and SHALL keep the
   paper's and the sleeve's true corners and band kinds as ground truth.
2. WHEN tested on synthetic sleeved pages, flat and bent by up to 3% lift,
   THE paper corners SHALL be within 0.25% of the page diagonal of the truth,
   and 98% of traced paper-edge points within 0.2% of the page width of the
   true edge.
3. WHEN tested on the same pages, THE band kinds SHALL be right on at least
   95% of the sides.
4. WHEN tested on synthetic sleeved pages bent by 1%, 3% and 5% lift, THE
   curvature correction SHALL be applied with the right binding side and meet
   the page-curvature spec's Requirement 8.2 (0.3% of the width after the
   best homography).
5. WHEN tested on the labelled real pages, THE paper corners SHALL be within
   0.5% of the page diagonal of the labels, and at least 90% of the labelled
   edge points within 0.4% of the page width of the traced edge, on every
   page.
6. WHEN tested on the labelled real pages with curvature `force`, THE system
   SHALL pick the labelled binding side on all five, and with curvature
   `auto` none SHALL be `fit_failed`.
7. WHEN run on the 19 `glare-*` pages, THE system SHALL treat every page as
   sleeved and complete without error.
8. THE real-page labels SHALL stay in the private data repository, and tests
   that use them SHALL be skipped WHEN it is not checked out, as the existing
   real-page test is.

### Requirement 9: Cost

**User story:** As the operator of the backend, I want the sleeve step to cost
little, so that it can stay on for every page.

#### Acceptance criteria

1. THE sleeve step SHALL add no more than 10% to `process_page`'s run time on
   the labelled real pages, and no more than 5% on the existing synthetic
   cases.
2. THE system SHALL add no new runtime dependency.
