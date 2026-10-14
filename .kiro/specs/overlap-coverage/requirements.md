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
- **Page feedback**: the warnings of a page's last processing run, as the
  server's album status reports them.
- **Target page**: the page new shots are added to. Today this is always the
  album's last page.

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
2. THE server's album status SHALL keep reporting each page's warnings with
   their fields, so that the app can read the `single_coverage` warning's
   locations and fraction, not only its message.
3. WHEN a shot is added to a page that already has a completed run, THE
   server SHALL treat the page as changed and process it again, as the album
   server spec already requires.

### Requirement 4: Page feedback in the app

**User story:** As someone photographing an album, I want to be told, while
I am still at the album, which part of a page needs one more shot, so that I
never find out after the album has gone back on the shelf.

#### Acceptance criteria

1. WHILE a server is configured AND the capture screen or the page overview
   is open, THE app SHALL fetch the album's status from the server when the
   screen opens, after each of the album's uploads completes, and then every
   10 seconds, and SHALL stop when the screen closes.
2. THE app SHALL keep the latest page feedback per page in its local store,
   so that it survives a screen change and shows at once when a screen
   reopens.
3. WHEN the target page or the page before it has a `single_coverage`,
   `incomplete_coverage` or `glare` warning in its latest feedback, THE
   capture screen SHALL show a feedback banner with the page number and the
   warning's message, the most recent page first, `single_coverage` before
   the other codes on the same page.
4. WHEN the banner's page is the target page, THE banner SHALL say so and
   the user adds shots as usual; WHEN it is the page before, THE banner SHALL
   offer "Add shots to page N".
5. WHEN the user chooses "Add shots to page N", THE app SHALL make page N the
   target page: the capture screen SHALL show its number and its thumbnail
   strip, new shots SHALL be stored and uploaded as shots of page N, and THE
   app SHALL offer "Back to page M" (the album's last page) in place of "Next
   page".
6. WHEN the user chooses "Back to page M" or starts a new page from an
   earlier target page, THE app SHALL make the album's last page the target
   page again without creating a page.
7. THE app SHALL restore the target page after the app is restarted, and
   SHALL reset it to the album's last page when that page is deleted.
8. WHEN a page's latest feedback no longer has a warning, THE app SHALL
   remove its banner.
9. THE page overview SHALL show on each page its processing state and, when
   its latest feedback has warnings, a warning mark with the messages, so
   that the user can find every page that needs another shot before leaving
   the album.
10. WHILE no server is configured, OR the status request fails, THE app
    SHALL show no feedback and no error on the capture screen; the drawer's
    existing upload state is enough.
11. THE app SHALL show feedback only for pages whose every shot has been
    uploaded, since the server's run did not see the others.

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
   `single_coverage` warnings.
