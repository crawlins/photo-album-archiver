# Requirements: shadow detection and removal

## Introduction

The page pipeline (`backend/src/albumproc`) merges several photos of one album
page into one composed page. Fusion assumes that where shots disagree, the
darker one is the truthful one, because glare only ever adds light
(`fuse.py`). Each shot's weight falls as its brightness rises above the
darkest shot covering the same spot.

A shadow breaks that assumption. When the photographer's hand, arm, phone or
head shades part of the page in one shot, that shot is the darkest there, so
it wins and the shadow is copied into the composed page. The clean shots that
would have fixed it are pushed out as if they had glare, and the report's
comparison `glare_fraction` counts them as glare. The glare detector already
declines to call those pixels glare (`glare.combine`, glare-detector
Requirement 1.4), but fusion does not use the detector, so the composed image
is unaffected and nothing reports the shadow.

This spec adds:

1. a shadow detector that compares shots where they overlap and tells a
   shadow (light taken away, multiplicatively) from glare (light added on
   top),
2. a change to fusion so that a shot found shadowed loses its darker-wins
   advantage there and the clean shots fill the area,
3. a cautious single-image cue for shadows where only one shot covers the
   page, or where every shot has the same shadow, and
4. shadow regions and a `shadow` warning in the page metadata, in the same
   form as the existing glare and coverage warnings.

Where a shadow is seen by only one shot, the page is reported, not repaired,
as glare is today: brightening a shadow from one photo would guess at the
print underneath.

### Glossary

- **Shot**, **composed page**, **page frame**, **region**, **warning**, **page
  metadata**: as in the glare-detector spec.
- **Shadow**: part of a shot where the light reaching the page was partly
  blocked by something between the light and the page. The print's colour and
  texture are kept but scaled down by a roughly constant factor.
- **Depth**: how much light a shadow removes, as `1 − (shadowed / unshadowed)`
  brightness. 0 is no shadow; 0.5 means half the light is gone.
- **Penumbra**: the soft edge of a shadow, where depth ramps from 0 to its full
  value.
- **Shadow map**: per-pixel shadow likelihood from 0 to 1 for one shot, on the
  fusion grid.
- **Comparison cue (shadow)**: evidence of a shadow in one shot from comparing
  it with the other shots covering the same spot.
- **Single-image cue (shadow)**: evidence of a shadow from one shot alone.
- **Residual shadow**: shadow in the composed page, meaning pixels whose value
  came mostly from shots that were shadowed there.
- **Spine shading**: the darkening of a curved page near the binding. It
  appears in every shot in the same place and is out of scope (see the
  page-curvature spec).

## Requirements

### Requirement 1: Shadows where shots overlap

**User story:** As someone photographing a page twice to get rid of glare, I
want a shadow my hand or phone cast in one shot to be recognised, so that the
other shot is used there instead.

#### Acceptance criteria

1. WHERE two or more shots cover a pixel, THE system SHALL compute for each of
   them a comparison shadow cue against the other covering shots, after tone
   matching.
2. THE system SHALL count a shot as shadowed at a pixel only WHEN it is darker
   than the other covering shots by at least the minimum depth (0.12 by
   default) AND its colour and local texture match theirs scaled by a single
   factor, so that a darker shot is not called shadowed when the other shot
   is the one with glare.
3. THE system SHALL NOT count as shadow a difference explained by glare in
   another shot: light added equally to all channels, raising dark channels
   proportionally more and lowering saturation and local contrast.
4. THE system SHALL extend each shadow by its penumbra, so that its soft edge
   does not leave a dim rim in the composed page.
5. THE system SHALL ignore shadow-like differences smaller than the minimum
   shadow area (0.05% of the page by default).
6. WHERE three or more shots cover a pixel, THE system SHALL compare each shot
   with a robust combination of the others (the per-pixel median), so that a
   shadow in one other shot does not make a clean shot look bright.

### Requirement 2: Fusion around shadows

**User story:** As someone archiving a page, I want the composed page free of
shadows wherever another shot saw the page unshadowed.

#### Acceptance criteria

1. WHERE a shot is shadowed at a pixel and another covering shot is not, THE
   fusion SHALL take that pixel from the unshadowed shots, giving the shadowed
   shot a weight that falls with its shadow map value.
2. THE fusion SHALL compute each shot's brightness excess (used for glare
   weights and the comparison `glare_fraction`) against the darkest
   *unshadowed* covering shot, so that a clean shot is not pushed out, or
   counted as glare, because another shot was shadowed.
3. WHERE every covering shot is shadowed at a pixel, THE fusion SHALL fall back
   to today's darker-wins weights there.
4. WHERE a shot is shadowed AND the only other covering shot has glare at the
   same pixel, THE fusion SHALL keep today's darker-wins behaviour there and
   the pixel SHALL count as residual shadow.
5. THE system SHALL exclude pixels found shadowed from the tone-curve fit, so
   that a large shadow does not darken a shot's tone curve.
6. WHEN no shot is found shadowed anywhere, THE composed page SHALL be
   byte-identical to the output without shadow handling.
7. THE system SHALL be able to turn shadow handling off by an option, which
   SHALL give byte-identical output to today's pipeline.

### Requirement 3: Shadows only one shot sees

**User story:** As someone stitching an oversized page from parts, I want to
be told when a shadow falls on a part only one photo covers, so that I can
re-shoot that part.

#### Acceptance criteria

1. THE system SHALL compute a single-image shadow cue for each shot from that
   shot alone, from a drop in the page's illumination with a soft, smooth
   edge that keeps the colour and texture of the content inside it.
2. THE system SHALL grow shadows found by comparison (Requirement 1) into the
   parts of the same shot no other shot covers, following the single-image
   cue, so that a shadow crossing the edge of an overlap is reported in full.
3. THE system SHALL report a shadow from the single-image cue alone only WHEN
   its cue is strong (above the seed threshold, 0.7 by default).
4. THE system SHALL NOT report dark page content as shadow when it has sharp
   edges or a different colour from its surroundings. This covers dark
   prints, black mats, black album paper, dark print borders and the gap
   between prints.
5. THE single-image cue SHALL only report. It SHALL NOT change the composed
   image.

### Requirement 4: Residual shadow and regions

**User story:** As the capture app, I want each remaining shadow located and
explained, so that I can tell the user where to re-shoot and what to change.

#### Acceptance criteria

1. THE system SHALL compute a residual shadow map for the composed page as the
   fusion-weighted average of the covering shots' shadow maps, using the
   weights that produced each composed pixel.
2. THE system SHALL group residual shadow at or above the residual threshold
   (0.5 by default) into regions, with the same fields, ordering and minimum
   area as glare regions.
3. WHEN a shadow region is covered by only one shot over most of its area,
   THE system SHALL give it the cause `single_shot`.
4. WHEN a shadow region is covered by two or more shots over most of its
   area, THE system SHALL give it the cause `all_shots_shadowed`.
5. THE page metadata SHALL report under `shadow` the residual shadow
   fraction, the regions, and for each used shot the share of its covered
   pixels found shadowed (whether or not fusion removed it).
6. THE page metadata SHALL report the shadow detector's parameters under
   `detector`, as it does for glare.

### Requirement 5: The `shadow` warning

**User story:** As someone checking an album, I want a warning on pages where
a shadow is left, saying where it is and what to do.

#### Acceptance criteria

1. WHEN the page has at least one shadow region, THE page metadata SHALL carry
   a warning with code `shadow`, the residual shadow fraction, the number of
   regions, the number with cause `single_shot`, the location of the largest,
   and a message.
2. THE message SHALL say where the largest shadow is, and SHALL advise adding
   a shot of that area without the shadow (for `single_shot`) or moving so
   that nothing is between the light and the page (for
   `all_shots_shadowed`).
3. THE album step and the upload server SHALL pass the warning through
   unchanged, as they do other page warnings.

### Requirement 6: Test data and checks

**User story:** As a developer, I want synthetic shadows with known ground
truth and real pages to check the detector against.

#### Acceptance criteria

1. THE synthetic generator SHALL be able to add a shadow to a shot: a smooth
   silhouette (hand, arm or phone shape) with a depth between 0.2 and 0.6 and
   a penumbra between 0.5% and 3% of the page's long side, returning its
   ground-truth mask.
2. ON synthetic two- and three-shot pages with a shadow in one shot, the
   comparison cue SHALL find at least 90% of the shadow in overlap areas with
   at most 0.5% false positives, and the composed page SHALL be within 3 grey
   levels (mean absolute error) of the same page fused without the shadow,
   inside the shadow's mask.
3. ON synthetic pages with glare in one shot and a shadow in another at
   different places, glare removal SHALL be no worse than without shadow
   handling.
4. ON every existing synthetic case without shadows (including
   `clean_white` and the glare cases), the composed page SHALL be
   byte-identical with shadow handling on and off, and no `shadow` warning
   SHALL be raised.
5. ON synthetic single-shot shadows with depth at least 0.35, the
   single-image cue SHALL find at least half of the shadows, with no `shadow`
   warning on the clean synthetic pages.
6. ON Clark's real pages in the private data repository, no `shadow` warning
   SHALL be raised on pages without a visible shadow, and the results on
   pages with one SHALL be recorded in the design notes. Real photos SHALL NOT
   be added to the public repository.

## Out of scope

- Brightening a shadow that only one shot sees (relighting). The content is
  there but darker, so this is possible later, but it guesses at the print
  and the warning comes first.
- Spine shading on curved pages (see the page-curvature spec).
- Shadows inside the print itself, which are part of the photograph.
- Live shadow feedback in the capture app.
