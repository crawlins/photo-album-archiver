# Requirements: overlap coverage

## Introduction

The page pipeline removes glare by comparing shots: where two shots cover the
same spot, the darker one shows the print. That works, and it is the only
glare removal the project has. The glare detector spec
(`.kiro/specs/glare-detector/`) set out to find the glare left where only one
shot covers the page, from that shot alone, and asked for 80% of it found
with false alarms on at most 0.2% of the page. Two detectors were built and
measured on Clark's real sleeved pages (PR #16, PR #22): the hand-made one
finds 0.2% of the glare the overlap comparison finds, the learned one 10%.
What both miss is mostly a soft veil of sheen over a third or half of a page,
and from one photo that looks the same as the page being lit unevenly. Only a
second shot from another angle tells them apart.

So the guarantee moves to where it can be kept. Instead of promising to find
glare in a single shot, the project promises that every part of a page is
covered by at least two shots, and tells the user, while they are still at
the album, where that is not yet so. The pipeline measures single coverage
and reports it as a warning with locations; the server already passes page
warnings to the app; the app shows them on the capture screen and lets the
user add the missing shots to the page they belong to. The single-shot
detectors stay as a last warning, with their accuracy recorded rather than
required.

This spec amends the glare detector spec's Requirement 9 (accuracy), extends
the page metadata (schema version 3), changes one default of the album server
(the idle period before a page is processed) and adds a feature to the
Android app. Requirement numbers below refer to this spec unless they name
another one.

### Glossary

Terms are as in the glare detector and Android app specs. In addition:

- **Coverage count**: for each page pixel, the number of used shots that cover
  it, on the fusion grid.
- **Single coverage**: page pixels with a coverage count of exactly one.
  Glare there cannot be removed, only detected.
- **Single-coverage region**: one connected area of single coverage above the
  minimum single-coverage area, with the same fields as an uncovered region.
- **Page feedback**: the warnings of a page's last processing run, with
  their regions, as the server's album status reports them.
- **Warned page**: a page whose latest feedback has at least one warning.
- **Shot box**: a region's bounding box in one shot's own pixels, found by
  mapping the region's page-frame box through that shot's homography.
- **Target page**: the page new shots are added to. Today this is always the
  album's last page.
- **Shot outline**: where one shot lies on the composed page, as a polygon
  in page fractions (its four corners for a flat page; points along its
  edges for a bent one).
- **Page preview**: a reduced copy of the composed page (long side 1500 px)
  the server keeps beside the print image.

## Requirements

### Requirement 1: Single coverage in the page metadata

**User story:** As someone photographing a page in parts, I want to know
which part of the page only one photo reached, so that I can take one more
shot of it from another angle and have its glare removed.

#### Acceptance criteria

1. THE system SHALL compute the single-coverage fraction, which is the share
   of covered page pixels whose coverage count is exactly one, from the same
   shot masks that give the uncovered fraction.
2. THE system SHALL group single-coverage pixels into regions, SHALL drop
   regions smaller than the minimum single-coverage area (1% of the page by
   default, larger than the glare and uncovered minimum because the seam
   between two overlapping shots always leaves a thin band of single
   coverage), and SHALL list them in descending order of area.
3. THE page metadata SHALL give each single-coverage region the same bounding
   box, area, location and edges fields as an uncovered region, plus the
   index of the one shot that covers it.
3a. THE page metadata SHALL give every region of every kind (glare,
   uncovered, single coverage) a `shot_boxes` map from used-shot index to
   the region's shot box in that shot's full-resolution pixels, clipped to
   the shot, for each shot whose frame the box falls into at all; an
   uncovered region, which no shot covers, SHALL get the box of the shot
   nearest to it when any part of the mapped box lies inside that shot.
4. THE page metadata SHALL carry this under a `single_coverage` object with
   `fraction` and `regions`, beside `glare` and `uncovered`, and
   `schema_version` SHALL become 3.
5. WHEN at least one single-coverage region remains after the minimum-area
   filter, THE system SHALL add a `single_coverage` warning giving the
   fraction, the number of regions and their locations, with a message that
   names the largest region's location and asks for another shot of that
   part from a different angle.
6. WHEN a page has exactly one used shot, THE system SHALL still add the
   warning, with the whole covered page as its one region, because a page
   shot once has had no glare removed at all.
7. WHERE the user sets the minimum single-coverage area
   (`--min-single-area` on `albumproc page`), THE system SHALL apply that
   value and record it in the page metadata's detector parameters.
8. WHERE the user passes `--masks`, THE system SHALL also write
   `<name>.single.png` (255 where single coverage) at the composed page's
   size, and WHERE the user passes `--debug`, THE overlay SHALL outline
   single-coverage regions in a third colour.
9. THE album step SHALL pass the `single_coverage` warning through to the
   album report as it does the other page warnings.
10. THE system SHALL leave the composed page image and every existing
    metadata field unchanged.
11. THE `glare` warning SHALL also give the number of regions with the cause
    `all_shots_glared`, beside `single_shot`, and every warning (`glare`,
    `incomplete_coverage`, `single_coverage`) SHALL carry its regions, so
    that a reader of the warnings alone can draw them.

### Requirement 2: Glare detector accuracy, amended

**User story:** As the developer, I want the detector's targets to be ones
the tests can hold, so that a green test suite means what it says.

This replaces Requirement 9.2 of the glare detector spec and adds to it.

#### Acceptance criteria

1. WHEN tested on synthetic pages where every page pixel is covered by at
   least two shots whose glare does not coincide, THE system SHALL report no
   `glare` warning and a residual glare fraction of at most 0.5%.
2. WHEN tested on synthetic stitched pages where most of the page is covered
   by one shot, THE system SHALL report a `single_coverage` warning whose
   regions together cover at least 90% of the single-coverage area.
3. THE tests SHALL hold that no `glare` warning is raised on glare-free
   synthetic pages, for both detectors (`features` and `net`), as the glare
   detector spec's 9.3 and 9.4 require.
4. THE tests SHALL hold, for each detector, a recall floor on synthetic
   stitched pages that catches regressions, at or below the value the
   detector reaches today, and SHALL NOT require any detector to find a set
   share of single-shot glare.
5. THE README and the glare detector spec SHALL record the measured accuracy
   of each detector on real pages (features: 0.2% of the glare found; net:
   10% found, false alarms on 0.01% of clean area, nine held-out pages) and
   state that single coverage, not single-shot detection, is what the
   project guarantees against.

### Requirement 3: Sooner page feedback from the server

**User story:** As someone shooting a page, I want the server to process it
soon after my last shot, so that its feedback reaches me while I am still at
that page.

#### Acceptance criteria

1. THE server SHALL use an idle period of 10 seconds by default before a
   changed page becomes ready for processing (album server spec, Requirement
   8.3 names 30 seconds); the setting SHALL stay configurable.
2. THE server's album status SHALL report each page's warnings with all
   their fields, including their regions, and SHALL key each region's shot
   boxes by shot id rather than by the run's input index, so that the app
   can draw a region on the photo it holds.
3. WHEN a shot is added to a page that already has a completed run, THE
   server SHALL treat the page as changed and process it again, as the album
   server spec already requires.

### Requirement 4: Page feedback in the app

**User story:** As someone photographing an album, I want to be told, while
I am still at the album, which page needs another photo and of which part,
so that I never find out after the album has gone back on the shelf.

#### Acceptance criteria

1. WHILE a server is configured AND the capture screen, the page overview or
   the review screen is open, THE app SHALL fetch the album's status from the
   server when the screen opens, after each of the album's uploads completes,
   and then every 10 seconds, and SHALL stop when the screen closes.
2. THE app SHALL keep the latest page feedback per page in its local store,
   so that it survives a screen change and shows at once when a screen
   reopens.
3. THE app SHALL mark a warned page with a red dot on the top-right corner
   of every picture of it: the page's tile in the page overview, each of its
   thumbnails in the capture screen's strip and the review screen's strip,
   and the capture screen's page number when the target page is warned.
4. WHEN the user taps a marked thumbnail, THE app SHALL open the review
   screen at that photo; WHEN the user taps a marked page tile, THE app
   SHALL open the review screen at the first photo of that page that has a
   shot box for one of its warnings, or its first photo when none has.
5. WHILE the review screen shows a photo of a warned page, THE app SHALL
   show the page's warning messages at the bottom of the screen, and SHALL
   draw a rectangle on the photo for every region of those warnings that
   has a shot box in this photo, in red for `glare` and `single_coverage`
   regions and in another colour for `incomplete_coverage`, scaled with the
   photo as the user zooms.
6. WHEN a region has no shot box in the photo shown, THE warning's message
   SHALL still be listed, with the region's location word, so that the
   user knows where to aim even when no photo shows the spot.
7. THE review screen of a warned page SHALL offer "Add shots to page N";
   WHEN the user chooses it, THE app SHALL make page N the target page and
   return to the capture screen: the capture screen SHALL show its number
   and its thumbnail strip, new shots SHALL be stored and uploaded as shots
   of page N, and THE app SHALL offer "Back to page M" (the album's last
   page) in place of "Next page".
8. WHEN the user chooses "Back to page M" or starts a new page from an
   earlier target page, THE app SHALL make the album's last page the target
   page again without creating a page.
9. THE app SHALL restore the target page after the app is restarted, and
   SHALL reset it to the album's last page when that page is deleted.
10. WHEN a page's latest feedback no longer has a warning, THE app SHALL
    remove its red dots, rectangles and messages.
11. WHILE no server is configured, OR the status request fails, THE app
    SHALL show no feedback and no error; the drawer's existing upload state
    is enough.
12. THE app SHALL show feedback only for pages whose every shot has been
    uploaded, since the server's run did not see the others.

### Requirement 6: The composed page on the phone

**User story:** As someone checking a page, I want to see the composed page
with each shot's outline on it, so that I can see at a glance what is
covered twice, what once, and which shot to retake.

#### Acceptance criteria

1. THE page metadata SHALL give each used shot's outline on the composed
   page, in page fractions, under `shots_outline`, keyed by input index (the
   server rekeys by shot id as it does shot boxes).
2. WHEN a page run ends well, THE server SHALL keep a page preview beside the
   print image and SHALL serve it at
   `GET /api/v1/albums/{albumId}/pages/{pageId}/preview`, with 404 when none
   has been made; the status SHALL say whether a preview exists.
3. THE app SHALL offer a page view from the page overview's tile and from a
   "Page" toggle on the review screen; the review screen's "Shot" toggle
   returns to the photo. The warnings bar, the rectangles' colours and "Add
   shots to page N" SHALL be the same in both.
4. THE page view SHALL show the page preview, pinch to zoom, with every used
   shot's outline drawn as a thin polygon numbered as in the thumbnail
   strip, and the page's warning regions drawn as rectangles from their page
   fractions.
5. WHEN the user taps a shot's outline, THE app SHALL select that shot (its
   polygon filled lightly, its thumbnail highlighted); WHEN the user taps a
   selected outline, or a warning rectangle, THE app SHALL open the review
   screen at that shot, or at the first shot covering the rectangle's
   region.
6. WHILE the page has no preview yet (not processed, processing or failed),
   THE page view SHALL say so and show the first shot in its place with no
   outlines.
7. THE app SHALL fetch the preview when the page view opens and again when
   the page's last run time changes, and SHALL keep the last one fetched.

### Requirement 5: Compatibility

**User story:** As the operator, I want the new metadata and app behaviour to
break nothing that works today.

#### Acceptance criteria

1. THE existing tests SHALL keep passing without changes to their
   assertions, except those that assert the schema version or the exact set
   of metadata keys.
2. THE album server SHALL keep serving pages processed under schema version
   2 and SHALL show no `single_coverage` feedback for them until they are
   processed again.
3. THE app SHALL keep working against a server that reports no
   `single_coverage` warnings and warnings without regions or shot boxes.
