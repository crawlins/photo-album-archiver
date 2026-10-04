# Requirements: album assembly, 300 DPI print images and album PDF

## Introduction

Step 1 of the plan (`backend/src/albumproc`) turns several phone photos of one
album page into one flat, cropped, glare-free page image. That image is at
"roughly the resolution the photos captured" (`PageOptions.max_side`, 8000 px
cap) and has the page's true proportions (`PageReport.aspect`), but it carries
no physical scale: nothing in a photo says how many inches the page is.

Step 2, specified here, takes a whole album, a folder of pages each with its
photos, and produces:

1. one print-ready image per page at exactly 300 pixels per inch of the real
   page, tagged so printers and layout tools read it at the right size, and
2. one PDF for the album with the pages in order, each PDF page the physical
   size of the album page.

Uploads, storage and the Android app are later steps and out of scope. So are
layout, captions and decoration: the album is reproduced, not redesigned.

### Glossary

- **Page pipeline**: `albumproc.process_page`, the existing step 1.
- **Processed page**: the image and `PageReport` the page pipeline returns.
- **Page size**: the physical width and height of an album page, given by the
  user (for example `10x12in` or `254x305mm`).
- **Print image**: a processed page resampled to the page size at the target
  resolution, written to disk with resolution and colour metadata.
- **Target DPI**: pixels per inch of the print image, 300 by default.
- **Capture DPI**: the processed page's own pixels per inch of the real page,
  i.e. its long side in pixels divided by the page size's long side in inches.
- **Bleed**: extra margin added beyond the page edge so a print shop can trim
  without leaving a white sliver.
- **Album folder**: a folder holding one subfolder of photos per page and an
  optional `album.json` manifest.

## Requirements

### Requirement 1: Read an album

**User story:** As someone archiving an album, I want to point the tool at one
folder holding every page's photos, so that I don't have to run it page by
page.

#### Acceptance criteria

1. WHEN the album command is given an album folder, THE system SHALL treat
   each immediate subfolder that contains at least one supported photo as one
   page.
2. THE system SHALL order pages by natural sort of their subfolder names, so
   that `page-2` comes before `page-10`.
3. THE system SHALL accept `.jpg`, `.jpeg`, `.png`, `.tif` and `.tiff` photos,
   case-insensitively, and SHALL ignore every other file.
4. WHERE the album folder contains `album.json`, THE system SHALL take the page
   order, titles, page size and per-page overrides from it instead of from
   folder names and command-line defaults.
5. IF a page named in `album.json` has no folder or no supported photos, THEN
   THE system SHALL report that page as failed with the reason and continue
   with the others.
6. IF the album folder contains no pages, THEN THE system SHALL exit with a
   non-zero status and an error naming the folder, writing no outputs.

### Requirement 2: Physical page size

**User story:** As someone archiving an album, I want to state the album's page
size once, so that every printed page comes out the same size as the original.

#### Acceptance criteria

1. THE system SHALL take each page's size from, in order of precedence, the
   page's entry in `album.json`, the album-wide `page_size` in `album.json`
   (which the capture app writes when an album is created), and the command
   line.
2. THE system SHALL accept page sizes as `<w>x<h><unit>` where unit is `in`,
   `mm` or `cm`, and SHALL accept the named sizes `letter`, `a4` and `a3`.
3. IF no page size is given anywhere, THEN THE system SHALL use 8.5 x 11
   inches (US letter), the capture app's default for a new album.
4. IF a page size is not positive or cannot be parsed, THEN THE system SHALL
   fail before processing any page, quoting the bad value.
5. THE system SHALL treat a page size as an unordered pair of side lengths,
   matching the longer side to the processed page's longer side, so that the
   same size string works for portrait and landscape pages.

### Requirement 3: 300 DPI print image

**User story:** As someone printing an archived album, I want each page as an
image at exactly 300 DPI of the real page size, so that it prints at the
original size without the print service rescaling it.

#### Acceptance criteria

1. WHEN a page is processed, THE system SHALL resample it to
   `round(width_in × DPI)` by `round(height_in × DPI)` pixels, where DPI is the
   target DPI.
2. THE system SHALL use 300 as the target DPI unless the user sets another
   value between 72 and 1200.
3. WHEN the capture DPI is above the target DPI, THE system SHALL downsample
   with area averaging; WHEN it is below, THE system SHALL upsample with a
   Lanczos filter.
4. WHEN the capture DPI is below the target DPI, THE system SHALL record the
   page as upsampled in the album report with its capture DPI.
5. WHEN the capture DPI is below the low-resolution threshold (150 by
   default), THE system SHALL add a "low resolution" warning for that page,
   so the capture app can ask for closer shots.
6. THE system SHALL resample each page in a single step from the processed
   page, with no intermediate resize.

### Requirement 4: Aspect and orientation mismatch

**User story:** As someone archiving an album, I want pages whose measured
shape disagrees with the stated size handled visibly rather than silently
distorted, so that a misdetected page or a wrong size doesn't go unnoticed.

#### Acceptance criteria

1. WHEN the processed page's aspect ratio is within the aspect tolerance (2%
   by default) of the page size's aspect ratio, THE system SHALL scale the page
   to fill the page size exactly.
2. WHEN the aspect ratios differ by more than the tolerance, THE system SHALL
   scale the page uniformly to fit inside the page size, centre it, fill the
   remainder with the fill colour (white by default), and add an "aspect
   mismatch" warning giving both ratios.
3. WHERE `album.json` sets a page's fit mode to `stretch`, `fit` or `fill`,
   THE system SHALL use that mode for the page regardless of the tolerance,
   where `fill` scales uniformly to cover the page size and crops the excess
   equally from both sides.
4. WHERE `album.json` sets a page's rotation to 90, 180 or 270 degrees, THE
   system SHALL rotate the processed page clockwise by that amount before
   matching it to the page size.
5. THE system SHALL NOT rotate a page automatically.

### Requirement 5: Print image files

**User story:** As someone sending pages to a print service, I want image files
that carry their resolution and colour space, so that the service prints them
at the right size and colour.

#### Acceptance criteria

1. THE system SHALL write each print image as JPEG at quality 95 by default,
   or as PNG or TIFF when the user selects that format.
2. THE system SHALL record the target DPI in every print image's resolution
   metadata (JFIF density for JPEG, pHYs for PNG, resolution tags for TIFF).
3. THE system SHALL embed an sRGB ICC profile in every print image.
4. THE system SHALL name print images `NNN-<page name>.<ext>`, where `NNN` is
   the page's 1-based position in the album, zero-padded to three digits.
5. THE system SHALL write print images in RGB with 8 bits per channel.

### Requirement 6: Bleed

**User story:** As someone ordering prints that will be trimmed, I want an
optional bleed margin, so that trimming doesn't leave white edges.

#### Acceptance criteria

1. WHERE the user sets a bleed, THE system SHALL extend each print image by
   the bleed on all four sides, filling the margin by mirroring the page
   content at its edge.
2. THE system SHALL use no bleed by default.
3. WHERE a bleed is set, THE system SHALL make each PDF page's media box the
   page size plus bleed and its trim box the page size.

### Requirement 7: Album PDF

**User story:** As someone archiving an album, I want one PDF of the whole
album, so that I can keep, share or print the album as a single file.

#### Acceptance criteria

1. WHEN at least one page has a print image, THE system SHALL write one PDF
   containing every successful page's print image, one per PDF page, in album
   order.
2. THE system SHALL size each PDF page to its page size (plus bleed, if any)
   in points, so that the page prints at 100% scale at its physical size.
3. WHEN print images are JPEG, THE system SHALL embed them in the PDF without
   re-encoding.
4. THE system SHALL set the PDF's title metadata to the album title (from
   `album.json`, else the album folder's name) and each page's label to its
   page name.
5. WHEN the `SOURCE_DATE_EPOCH` environment variable is set, THE system SHALL
   use it for the PDF's creation and modification dates, so that identical
   inputs give a byte-identical PDF.

### Requirement 8: Failed pages

**User story:** As someone archiving a long album, I want one bad page not to
stop the rest, so that I only need to re-shoot the pages that failed.

#### Acceptance criteria

1. IF the page pipeline raises an error for a page, THEN THE system SHALL
   record the page as failed with the error message, leave it out of the PDF,
   and continue with the next page.
2. WHEN any page failed, THE system SHALL exit with status 3 after writing the
   outputs for the pages that succeeded.
3. WHERE the user passes `--strict`, THE system SHALL stop at the first failed
   page, exit with a non-zero status and write no PDF.
4. WHERE the user passes `--placeholder`, THE system SHALL put a blank page of
   the page size, labelled with the page name and "missing", into the PDF in
   place of each failed page, so that facing pages stay paired.
5. WHEN a processed page's coverage is below the coverage threshold (0.99 by
   default), THE system SHALL keep the page and add an "incomplete coverage"
   warning with the coverage value.

### Requirement 9: Re-running an album

**User story:** As someone who re-shoots a few pages after checking the
results, I want a re-run to redo only what changed, so that fixing one page
doesn't reprocess the whole album.

#### Acceptance criteria

1. WHEN a page's photos (by content hash) and page pipeline options are the
   same as in the previous run's report, AND that run's processed page image
   still exists, THE system SHALL reuse the processed page instead of running
   the page pipeline again.
2. WHEN, in addition, the page's print options and page size are unchanged
   AND the previous print image still exists, THE system SHALL reuse the print
   image as well.
3. WHEN a page's photos or page pipeline options changed, THE system SHALL
   reprocess that page from its photos.
4. WHERE the user passes `--force`, THE system SHALL reprocess every page.
5. THE system SHALL rebuild the PDF on every run from the current print
   images.
6. THE system SHALL write every output file under a temporary name and rename
   it into place once complete, so that an interrupted run never leaves a
   partial file that a later run would reuse.

### Requirement 10: Album report

**User story:** As the capture app, I want a machine-readable account of the
whole album, so that I can show which pages need another shot.

#### Acceptance criteria

1. THE system SHALL write `report.json` to the output folder listing, for every
   page in album order: its name, status (`ok`, `reused` or `failed`), the
   input photos, the page pipeline's report, the page size, target and capture
   DPI, fit mode used, output file paths and warnings.
2. THE album report SHALL include totals: pages found, pages succeeded, pages
   failed, and the PDF's path and page count.
3. THE album report SHALL record the hashes and options needed for
   Requirement 9.
4. THE system SHALL write the album report even when every page failed.
5. THE system SHALL write each warning as an object with a machine-readable
   `code` (`low_resolution`, `upsampled`, `aspect_mismatch`,
   `incomplete_coverage`, `photos_dropped`) and a human-readable `message`.

### Requirement 11: Command line

**User story:** As a developer of the upload server, I want the album step
callable from the command line and from Python, so that the server can run it
either way.

#### Acceptance criteria

1. THE system SHALL provide `albumproc album <album folder> -o <output folder>`
   with options for page size, DPI, image format, JPEG quality, bleed, fill
   colour, focal length, `--strict`, `--placeholder`, `--force` and
   `--jobs`.
2. THE system SHALL provide `albumproc print <page image> -o <file>` that
   turns one already-processed page image into a print image, with the same
   page size, DPI, format, bleed, fit and rotation options.
3. THE system SHALL expose `process_album` and `make_print_image` from the
   `albumproc` package, returning the album report and print image without
   requiring the command line.
4. WHILE pages are being processed, THE system SHALL print one progress line
   per page to standard error with its position, name and status.
5. THE system SHALL keep the existing `albumproc page` command and its output
   unchanged.

### Requirement 12: Resources

**User story:** As the operator of the Linux backend, I want an album of a
hundred pages to run on a modest machine, so that memory doesn't grow with the
album.

#### Acceptance criteria

1. THE system SHALL hold the full-resolution data of at most one page per
   worker in memory at a time, writing each page's outputs before starting
   the next.
2. THE system SHALL build the PDF from the print image files on disk, without
   decoding them.
3. WHERE `--jobs N` is given, THE system SHALL process up to N pages in
   parallel worker processes, with 1 as the default, and SHALL produce the same
   outputs as a run with one worker.
