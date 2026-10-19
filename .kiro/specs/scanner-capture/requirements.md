# Requirements: capturing pages with a flatbed scanner

## Introduction

The Android app photographs each album page several times, and the pipeline
undoes the perspective, bend and glare of those photos. Some albums are better
served by a flatbed scanner: loose pages, pages that come out of their
sleeves, and albums whose pages lie flat on the glass. A scan has no
perspective, no glare from a lamp and a known resolution, and the user has a
USB Canon CanoScan LiDE on the same Linux machine as the album server.

This feature adds `albumscan`, a desktop app for that machine. It drives any
scanner SANE can see, turns each scan into a shot, and uploads it to the
album server with the same requests the Android app uses, so scanned and
photographed albums live side by side on the server, with the same limits
(25 shots per page, 500 pages per album), the same processing and the same
PDF. A page larger than the scanner's glass is scanned in parts, as several
shots of one page, and stitched by the pipeline as photos are.

It also adds a scan mode to the page step (`.kiro/specs/album-pdf/`), chosen
by the shots themselves, so that a scanned page is not treated as a photo:
its proportions are measured rather than inferred from perspective, it is
never treated as bent, and it is not checked for the glare and shadow a lamp
and a hand cause. The album server's HTTP contract
(`.kiro/specs/android-app/design.md`, "Server contract") is unchanged.

Out of scope: document feeders and batch scanning, the scanner's own buttons,
a preview scan for choosing a scan area, editing pages other than the one
being scanned, reordering pages, scanning while offline from the server for
longer than the outbox holds (Requirement 7), and more than one scanning
machine working on the same album at the same time.

### Glossary

Terms are as in the Android app and album server specs. In addition:

- **App**: `albumscan`, specified here. The Android app is called the phone
  app where both are meant.
- **Scanner**: a SANE device, named by its SANE device name
  (`genesys:libusb:001:007`, `pixma:04A91913`, `escl:https://…`).
- **Scan**: one pass of the scanner, which becomes one shot.
- **Current page**: the page new scans go to; always the album's last page,
  as in the phone app.
- **Placement**: how the page lies on the glass relative to how it should
  read: turned 0, 90, 180 or 270 degrees clockwise.
- **Outbox**: the app's local folder of scans and requests not yet accepted
  by the server.
- **Scan marker**: the EXIF `Software` value `albumscan <version>` that the
  app writes into every shot.
- **Scan mode**: the page step's treatment of a page whose shots all carry
  the scan marker.

## Requirements

### Requirement 1: Choosing the scanner

**User story:** As someone archiving an album, I want the app to find my
scanner, so that I never type a device name.

#### Acceptance criteria

1. WHEN the app starts, THE app SHALL list the scanners SANE finds, each by
   its vendor, model and device name, and SHALL select the scanner used last
   when it is in the list, otherwise the first USB scanner, otherwise the
   first one.
2. IF SANE finds no scanner, THEN THE app SHALL say so, suggest checking the
   cable and running `scanimage -L`, and offer to look again, and SHALL keep
   the album and its pages usable for everything except scanning.
3. THE app SHALL let the user look for scanners again at any time without
   restarting.
4. WHEN a scanner is selected, THE app SHALL read the resolutions and the
   scan area it supports, and SHALL offer only those.

### Requirement 2: Scan settings

**User story:** As someone archiving an album, I want scans at a resolution
fit for reprinting, so that small prints can be enlarged later.

#### Acceptance criteria

1. THE app SHALL scan in colour at the highest resolution the scanner
   offers in colour by default, and SHALL let the user choose any offered
   resolution from 300 dpi up, leaving out those whose whole-glass scan
   would exceed the page step's limit of 1 billion pixels.
2. THE app SHALL scan the scanner's whole scan area.
3. THE app SHALL let the user set the placement for the album, 0 degrees by
   default, and SHALL apply it to every later scan of that album.
4. THE app SHALL remember the scanner, the resolution and each album's
   placement between runs.

### Requirement 3: Connecting to the server

**User story:** As the owner of the server, I want the app to use its own
token, so that it can be revoked like a phone's.

#### Acceptance criteria

1. THE app SHALL connect to the server URL and token the user enters, both
   defaulting to empty, and SHALL test them with `GET /api/v1/ping` before
   saving them.
2. THE app SHALL store the token in a file readable only by the user, and
   SHALL NOT log it or show it again after it is saved.
3. IF the server answers 401, THEN THE app SHALL say the token was refused
   and ask for a new one, and SHALL keep the outbox.

### Requirement 4: Albums

**User story:** As someone archiving an album, I want to start a new album or
carry on with one already on the server, so that an album can be part
photographed and part scanned.

#### Acceptance criteria

1. THE app SHALL list the server's albums with their name, page size, page
   count and last change, newest change first.
2. WHEN the user creates an album, THE app SHALL ask for its name (defaulting
   to "Album" and today's date) and its page size (`letter`, `a4`, `a3` or a
   custom size accepted by `albumproc`), choose a new UUID for it and one for
   its first page, and send its metadata.
3. WHEN the user opens an existing album, THE app SHALL fetch its metadata
   and make its last page the current page, showing that page's shots as
   previews from the server.
4. THE app SHALL let the user change the album's name and page size, and
   SHALL send the change.

### Requirement 5: Scanning a page

**User story:** As someone archiving an album, I want one key to scan and one
to move to the next page, so that I can work through a whole album with my
hands on the pages.

#### Acceptance criteria

1. WHEN the user presses Scan or the space bar, THE app SHALL scan with the
   current settings, show the progress, and add the scan as a new shot at the
   end of the current page.
2. WHILE a scan is running, THE app SHALL ignore further Scan presses and
   SHALL let the user cancel the scan, which adds no shot.
3. IF a scan fails, THEN THE app SHALL show the scanner's message, add no
   shot, and leave the current page as it was.
4. THE app SHALL show the current page's number and its shots as a strip of
   previews in the order they were scanned, and the latest scan larger.
5. WHEN the user presses Next page or the Enter key, THE app SHALL add a new
   page at the end of the album and make it the current page; WHEN the
   current page has no shots, THE app SHALL stay on it instead.
6. IF a scan would give the current page more than 25 shots, or Next page
   would give the album more than 500 pages, THEN THE app SHALL refuse it and
   say which limit was reached, before scanning.
7. THE app SHALL let the user delete any shot of the current page, after
   confirming.
8. THE app SHALL say, next to the Scan button, that a page larger than the
   glass is scanned in overlapping parts as shots of the same page.

### Requirement 6: Shots from scans

**User story:** As the album server, I want a scan to arrive as an ordinary
shot that says it is a scan, so that nothing on the server changes and the
page step can treat it as one.

#### Acceptance criteria

1. THE app SHALL turn each scan into a baseline JPEG at quality 95 with no
   chroma subsampling, after turning its pixels by the album's placement, so
   that the shot reads the right way up with no EXIF orientation.
2. THE app SHALL write into each shot's EXIF the scan marker as `Software`,
   the scanner's vendor and model as `Make` and `Model`, the scan resolution
   as `XResolution` and `YResolution` in dots per inch, and the time the scan
   ended as `DateTimeOriginal`.
3. IF a scan exceeds 1 billion pixels, or its shot the server's request
   limit (1 GB by default), THEN THE app SHALL not keep it and SHALL say to
   scan at a lower resolution.
4. THE album server SHALL accept request bodies up to 1 GB by default, and
   THE server and the page step SHALL read images up to 1 billion pixels,
   so that a whole-glass colour scan at 2400 dpi (about 570 million pixels,
   a few hundred MB as a JPEG) goes through.

### Requirement 7: Reliable upload

**User story:** As someone archiving an album, I want every scan kept until
the server has it, so that a server restart or a dropped connection never
loses a page.

#### Acceptance criteria

1. THE app SHALL write each shot to the outbox, flushed and renamed into
   place, before it shows the shot as taken.
2. THE app SHALL send the outbox's requests in the order they were made:
   album metadata, shot uploads and shot deletions, with each shot's
   `X-Content-SHA256`.
3. WHEN the server answers 200, 201 or 204, or 200 or 201 to a repeated
   upload, THE app SHALL remove the request and, for an upload, the shot's
   file from the outbox.
4. IF the server cannot be reached, or answers 5xx or 429, THEN THE app
   SHALL retry the request with growing delays up to one minute, without
   reordering the outbox.
5. IF the server answers 409 or 422 to an upload, or any other 4xx, THEN THE
   app SHALL stop sending, keep the request, and show the server's message,
   because retrying cannot help.
6. WHEN the app starts, THE app SHALL resume sending what the outbox holds.
7. THE app SHALL show how many shots are waiting to upload, and SHALL warn
   before quitting while any are.
8. WHEN a shot still in the outbox is deleted, THE app SHALL drop its upload
   instead of sending it and then deleting it.

### Requirement 8: Scan mode in the page step

**User story:** As someone archiving an album, I want scanned pages
processed as scans, so that their proportions are exact and their warnings
make sense.

#### Acceptance criteria

1. WHEN every photo of a page carries the scan marker, THE page step SHALL
   process the page in scan mode; otherwise it SHALL process it as before.
2. IN scan mode, THE page step SHALL take the page's proportions from its
   corners as they are, without a perspective model.
3. IN scan mode, THE page step SHALL NOT treat the page as bent, whatever the
   curvature setting.
4. IN scan mode, THE page step SHALL NOT look for residual glare or shadow,
   and SHALL NOT report areas covered by a single shot, because those exist
   to ask for another photo from a different angle.
5. IN scan mode, THE page step SHALL still stitch several shots of one page
   and report areas no shot covers and shots it could not place, as for
   photos.
6. THE page step SHALL record in the page metadata whether the page was
   processed in scan mode and, in scan mode, the shots' scan resolution.

### Requirement 9: Processing feedback

**User story:** As someone archiving an album, I want to see whether each
scanned page came out right, so that I can rescan it while it is still on
the glass.

#### Acceptance criteria

1. THE app SHALL show, for the current page and the one before it, the
   processing state from `GET /api/v1/albums/{albumId}/status` and each
   warning's message, refreshing it while either page is changed, ready or
   processing.
2. THE app SHALL let the user open a page's print image from the server.

### Requirement 10: Installing and running

**User story:** As the owner of the machine, I want to install the app like
the server, so that there is one way to set things up.

#### Acceptance criteria

1. THE repository SHALL hold the app in its own Python package, started with
   `albumscan`, that needs only the distribution's GTK 4 bindings, Pillow and
   SANE's `scanimage`, and not the backend's image libraries.
2. THE repository SHALL include a desktop entry for the app and a `make scan`
   target that starts it.
3. THE app SHALL keep its settings under `~/.config/albumscan/` and its
   outbox under `~/.local/share/albumscan/`.
