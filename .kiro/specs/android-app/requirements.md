# Requirements: Android capture app (MVP)

## Introduction

This is the minimum viable product (MVP) of the Android app, step 4 of the
plan: a Kotlin app for photographing the pages of a photo album, several shots
per page, and getting those shots to the Linux backend. The phone only captures and uploads. All image work
(registration, glare removal, stitching, flattening, print images and the PDF)
happens on the backend, so the app never processes a photo.

Several shots per page are the point of the app. The backend removes glare by
combining shots of the same page taken from different angles, and stitches
pages too big for one frame from shots that each cover part of the page. The
app therefore makes "one more shot of this page" the cheapest action there is,
and "move on to the next page" the second cheapest, both reachable without
looking away from the album: the volume buttons do each.

The app is organised around the same structure the backend's album step reads:
an album is an ordered list of pages, and each page is a set of photos. What the
app uploads maps directly onto an album folder (one subfolder per page plus an
`album.json`) for `albumproc album`.

The upload server (step 3) does not exist yet. This spec states the small HTTP
contract the app expects from it; step 3 implements that side.

Out of scope: on-device processing or preview of the processed page, OCR,
captions, editing the PDF, sharing, accounts and multiple users.

### Glossary

- **Album**: one physical photo album being archived, called a "document" or
  "project" in some places; the app calls it an album throughout.
- **Page**: one album page, identified by its 1-based position in the album.
- **Shot**: one photo of a page. A page has one or more shots.
- **Current album**: the album the capture screen is adding shots to.
- **Current page**: the page the next shot is added to, always the last page of
  the current album.
- **Capture screen**: the app's main view, a full-screen camera preview with
  the capture controls.
- **Navigation drawer**: the menu opened from the hamburger button.
- **Server**: the upload server on the user's Linux machine.
- **Review screen**: a full-screen view of one shot, from which the user can
  move through the shots of a page and through the pages of the album.
- **Page overview**: a grid of every page of the current album.
- **Upload queue**: shots and album metadata saved on the phone but not yet
  confirmed by the server.

## Requirements

### Requirement 1: Launch into the last album

**User story:** As someone archiving an album over several sittings, I want the
app to open straight onto the album I was last shooting, so that I can pick up
where I left off without navigating.

#### Acceptance criteria

1. WHEN the app is launched AND an album was open when the app was last used
   AND that album still exists, THE app SHALL open the capture screen with that
   album as the current album.
2. WHEN the app opens an album, THE app SHALL make its last page the current
   page, so that the next shot is added to the page that was being shot.
3. WHEN the app is launched AND there is no last album, or it no longer exists,
   THE app SHALL show an empty state on the capture screen with a "New album"
   action and SHALL NOT take any shot until an album is open.
4. THE app SHALL record the current album as the last album every time the
   current album changes.

### Requirement 2: Navigation drawer

**User story:** As someone archiving several albums, I want a hamburger menu to
create a new album and to switch between albums, so that each album's pages
stay separate.

#### Acceptance criteria

1. THE capture screen SHALL show a hamburger button that opens the navigation
   drawer.
2. THE navigation drawer SHALL contain a "New album" action, the list of
   albums on this phone with each one's name, page count and upload state, a
   "Server albums" entry and a "Settings" entry.
3. WHEN the user chooses "New album", THE app SHALL ask for an album name
   (defaulting to "Album" plus today's date) and the album's page size
   (defaulting to 8.5 × 11 inches), create the album with no pages, make it
   the current album and close the drawer.
4. THE app SHALL accept a page size as one of `letter`, `a4`, `a3` or a custom
   width and height in inches or millimetres, SHALL require every album to
   have one, and SHALL store it in the form the backend accepts (for example
   `8.5x11in`).
5. IF the album name is blank, or a custom width or height is missing, not a
   number or not positive, THEN THE app SHALL show the problem next to the
   field and SHALL NOT save the album.
6. WHEN the user selects an album in the drawer, THE app SHALL make it the
   current album and return to the capture screen.
7. WHEN the user long-presses an album in the drawer, THE app SHALL offer
   "Edit" and "Delete". Deleting SHALL ask for confirmation with an "Also
   delete from the server" option, off by default, and SHALL then remove the
   album and its shots from the phone, and from the server only when that
   option was chosen (Requirement 13).
8. WHEN the user chooses "Edit", THE app SHALL show the album's name and page
   size in the same form used to create it, validated the same way, and WHEN
   the user saves, THE app SHALL update both and send them to the server.
9. WHEN an album's page size changes, THE app SHALL keep all of its pages and
   shots unchanged.

### Requirement 3: Capture screen

**User story:** As someone photographing an album page, I want the camera to be
the main view with large controls, so that I can frame and shoot quickly.

#### Acceptance criteria

1. THE capture screen SHALL show the rear camera's live preview as large as
   the screen allows without cropping it, so that everything in the preview
   is in the shot and nothing in the shot is hidden from the user.
2. THE capture screen SHALL show a shutter button that takes a shot and a
   "Next page" button that starts a new page.
3. THE capture screen SHALL show the current album's name, the current page's
   number and the number of shots on the current page.
4. THE capture screen SHALL show thumbnails of the current page's shots, most
   recent last.
5. WHEN the user taps a thumbnail, THE app SHALL open the review screen at
   that shot (Requirement 11).
6. THE capture screen SHALL keep the screen on while it is in the foreground.
7. WHILE the camera permission has not been granted, THE capture screen SHALL
   show why the permission is needed and a button to request it, in place of
   the preview.

### Requirement 4: Taking a shot

**User story:** As someone photographing an album page, I want each shot to be
saved at full quality against the right page, so that the backend has the best
material to work with.

#### Acceptance criteria

1. WHEN the user presses the shutter button, THE app SHALL take a photo at the
   camera's full resolution and add it as a shot of the current page.
2. WHEN the current album has no pages AND the user takes a shot, THE app SHALL
   create page 1 and add the shot to it.
3. THE app SHALL save each shot as a JPEG with its EXIF metadata, including
   orientation and focal length, so that the backend can use the real focal
   length.
4. THE app SHALL turn the flash off by default, and SHALL let the user switch
   it on for the current session from the capture screen.
5. WHEN a shot has been saved, THE app SHALL give brief visual and haptic
   feedback and add its thumbnail to the capture screen.
6. WHILE a shot is being taken, THE app SHALL ignore further shutter presses,
   so that one press never produces two shots.
7. IF a shot cannot be taken or saved, THEN THE app SHALL show an error message
   and SHALL NOT add a shot to the page.
8. THE app SHALL write each shot to storage under a temporary name and rename
   it into place once complete, so that an interrupted save never leaves a
   partial shot.
9. THE app SHALL allow at most 25 shots per page.
10. WHEN the current page has 25 shots, THE app SHALL disable the shutter
    button, ignore volume up, and show that the page is full and that "Next
    page" moves on.

### Requirement 5: Starting the next page

**User story:** As someone photographing an album, I want one action to move on
to the next page, so that the shots of each page are grouped without any
typing.

#### Acceptance criteria

1. WHEN the user presses "Next page" AND the current page has at least one
   shot, THE app SHALL create a new page numbered one more than the current
   page and make it the current page.
2. WHEN the user presses "Next page" AND the current page has no shots, THE app
   SHALL keep the current page and show a hint that the page has no shots yet,
   so that empty pages are never created.
3. WHEN a new page is started, THE app SHALL show the new page number
   prominently for about one second, so that the user can confirm the action
   without looking closely.
4. THE app SHALL provide "Undo" for about five seconds after a new page is
   started, which removes the new page while it still has no shots.
5. THE app SHALL allow at most 500 pages per album.
6. WHEN the current page is page 500, THE app SHALL disable the "Next page"
   button, ignore volume down, and show that the album is full, so that the
   user starts a new album for the remaining pages.

### Requirement 6: Volume buttons

**User story:** As someone holding the phone over an album, I want the volume
buttons to shoot and to move to the next page, so that I can work without
reaching for the touchscreen.

#### Acceptance criteria

1. WHILE the capture screen is in the foreground with an album open, WHEN the
   user presses volume up, THE app SHALL take a shot exactly as the shutter
   button does, including the shot limit.
2. WHILE the capture screen is in the foreground with an album open, WHEN the
   user presses volume down, THE app SHALL start the next page exactly as the
   "Next page" button does, including the page limit.
3. WHILE the capture screen is in the foreground with an album open, THE app
   SHALL consume volume key presses, so that they do not change the volume.
   With no album open the keys do nothing on this screen, so they keep
   changing the volume.
4. WHEN a volume key is held down, THE app SHALL act once per press and SHALL
   ignore the key's auto-repeat.
5. WHILE any other screen, dialog or the navigation drawer is showing, or the
   app is in the background, THE volume keys SHALL keep their normal system
   behaviour.

### Requirement 7: Local storage

**User story:** As someone archiving albums where the network may be poor, I
want every shot kept safely on the phone until the server has it, so that no
shot is ever lost.

#### Acceptance criteria

1. THE app SHALL store albums, pages and shots in app-specific storage and
   SHALL keep them across app restarts and phone reboots.
2. THE app SHALL keep each shot on the phone after it has been uploaded, until
   the user deletes the shot or its album.
3. WHEN the user deletes a shot or a page, THE app SHALL remove it from the
   phone and queue its removal on the server.
4. WHEN free storage drops below 500 MB, THE app SHALL warn the user on the
   capture screen and SHALL still allow shots while there is room for them.

### Requirement 8: Upload

**User story:** As someone archiving albums, I want shots to reach the server
by themselves in the background, so that the backend can process pages while I
keep shooting.

#### Acceptance criteria

1. WHEN a shot has been saved AND a server is configured, THE app SHALL add the
   shot to the upload queue.
2. THE app SHALL upload queued shots in the background, in the order they were
   taken, while the phone has a network connection that meets the user's
   upload setting (any network or unmetered only, unmetered by default).
3. THE app SHALL keep uploading after the app is closed, and SHALL resume
   pending uploads after a reboot.
4. IF an upload fails, THEN THE app SHALL retry with exponential backoff and
   SHALL keep the shot in the upload queue until the server confirms it.
5. THE app SHALL make every upload idempotent, so that a retried or repeated
   upload never creates a duplicate shot on the server.
6. THE app SHALL send the album's name, page size and page order to the server
   whenever any of them changes.
7. THE capture screen SHALL show the number of shots waiting to upload, and the
   drawer SHALL show each album as "uploaded", "uploading" or "waiting".
8. IF the server rejects the app's credentials, THEN THE app SHALL stop
   uploading and show a message pointing to Settings.
9. THE app SHALL send shots, deletions and album metadata to the server in the
   order they happened, and SHALL send the album's metadata when a page is
   started, so that when the server sees a new page every shot of the
   previous page has already arrived and the server can start processing it.
10. THE app SHALL send the album's metadata before the first shot of a new
    album.

### Requirement 9: Settings

**User story:** As the owner of the Linux backend, I want to point the app at my
server once, so that uploads go to the right place.

#### Acceptance criteria

1. THE settings screen SHALL let the user enter the server URL and an access
   token, and SHALL store the token in encrypted storage.
2. THE settings screen SHALL offer "Test connection", which calls the server
   and reports success, an unreachable server or rejected credentials.
3. THE settings screen SHALL let the user choose whether uploads use any
   network or only unmetered networks.
4. WHEN the server URL uses `http` rather than `https`, THE settings screen
   SHALL warn that the token and photos travel unencrypted.
5. WHILE no server is configured, THE app SHALL still capture and store shots,
   and SHALL queue them for upload once a server is configured.

### Requirement 10: Lifecycle and reliability

**User story:** As someone shooting a long album, I want interruptions such as
calls, rotation or the app being killed not to lose work or my place, so that I
can trust the app over a long session.

#### Acceptance criteria

1. WHEN the app returns to the foreground, THE app SHALL show the same album
   and current page as before it left.
2. THE app SHALL lock the capture screen to portrait and SHALL record the
   device's physical orientation in each shot's EXIF, so that landscape shots
   still come out the right way up.
3. THE app SHALL commit each shot it takes to the local database only after
   its file is fully written, so that a crash never leaves a taken shot without
   a file or a file without a shot. Shots on the server that have not been
   fetched (Requirement 14) are the only shots without a file.
4. WHEN the app starts, THE app SHALL delete leftover temporary shot files.

### Requirement 11: Reviewing shots and pages

**User story:** As someone archiving an album, I want to move through the shots
of each page and through the pages, so that I can check what I have before the
album goes to the backend.

#### Acceptance criteria

1. THE app SHALL open the review screen from a thumbnail on the capture screen,
   and SHALL open the page overview from the page number on the capture screen
   and from the album's entry in the navigation drawer.
2. THE review screen SHALL show one shot full screen with its page number and
   its position on the page, for example "Page 12, shot 3 of 5".
3. WHEN the user swipes left or right on the review screen, THE app SHALL show
   the next or previous shot of the same page.
4. THE review screen SHALL show previous-page and next-page arrows, which SHALL
   show the first shot of the adjacent page, and SHALL hide each arrow at the
   first and last page.
5. THE review screen SHALL let the user pinch to zoom into a shot, so that
   focus and glare can be checked.
6. THE page overview SHALL show every page of the album in order with its
   number, a thumbnail of its first shot and its shot count, and WHEN the user
   taps a page, THE app SHALL open the review screen at that page's first shot.
7. WHEN the user returns to the capture screen, THE app SHALL keep the album's
   last page as the current page, so that reviewing never changes where new
   shots go.

### Requirement 12: Deleting shots and pages

**User story:** As someone archiving an album, I want to delete bad shots and
whole pages, so that only the shots I want reach the backend.

#### Acceptance criteria

1. THE review screen SHALL offer "Delete shot" and "Delete page", and THE page
   overview SHALL offer "Delete page" when a page is long-pressed.
2. WHEN the user chooses "Delete shot" or "Delete page", THE app SHALL ask for
   confirmation naming the page and, for a page, the number of shots it holds.
3. WHEN a page is deleted, THE app SHALL delete its shots and renumber the
   following pages, so that page numbers stay contiguous from 1.
4. WHEN the user deletes the only shot of a page that is not the album's last
   page, THE app SHALL delete the page as well and SHALL say so in the
   confirmation, so that no empty page is left inside the album.
5. WHEN the album's last page is deleted, THE app SHALL make the page before
   it the current page; WHEN no pages are left, THE app SHALL show the album
   with no pages, and the next shot SHALL create page 1.
6. WHEN a shot or page has been deleted, THE review screen SHALL show the next
   shot, or the previous one when there is no next, or return to the page
   overview when the album is empty.
7. WHEN shots or pages are deleted, THE app SHALL count only the remaining
   ones against the limits of 25 shots per page and 500 pages per album.

### Requirement 13: Albums on the server

**User story:** As the owner of the backend, I want to see which albums are on
the server and delete ones I no longer need, so that the server holds only the
albums I want.

#### Acceptance criteria

1. WHEN the user opens "Server albums" from the navigation drawer, THE app
   SHALL fetch and list every album on the server with its name, page size,
   page count, shot count and when it was last changed.
2. THE server albums list SHALL mark each album that is also on this phone.
3. THE server albums list SHALL refresh when opened, when the user returns to
   it from another screen such as Settings, and when the user pulls down on
   it.
4. IF no server is configured, the server cannot be reached or the
   credentials are rejected, THEN THE server albums list SHALL say which, and
   offer "Retry" or a link to Settings.
5. WHEN the user long-presses an album that is not on this phone, THE app
   SHALL offer "Open on this phone" (Requirement 14) and "Delete from server";
   for deletion THE app SHALL ask for confirmation naming the album and
   its page count, and then delete it from the server and remove it from the
   list.
6. THE server albums list SHALL NOT offer deletion of an album that is also on
   this phone, which is deleted from the drawer instead (Requirement 2.7), so
   that the phone never re-uploads into an album just deleted from the server.
7. WHEN the user deletes an album from the phone with "Also delete from the
   server" chosen, THE app SHALL discard that album's pending uploads and
   queue the album's deletion on the server, so that it is deleted even if the
   server is unreachable at that moment.
8. THE server SHALL delete an album's photos, processed pages, print images
   and PDF when the album is deleted.

### Requirement 14: Opening a server album on the phone

**User story:** As someone who archived an album from another phone, or deleted
it from this one, I want to open an album that is only on the server and carry
on shooting it straight away, without first downloading every photo in it.

#### Acceptance criteria

1. WHEN the user taps an album in the server albums list that is not on this
   phone, or chooses "Open on this phone", THE app SHALL fetch only the album's
   name, page size and pages in order with each page's shot count, create the
   album on the phone, make it the current album and return to the capture
   screen.
2. WHEN a server album has been opened, THE app SHALL fetch the shot list and
   the shots of its last page, so that the capture screen shows that page's
   thumbnails and enforces its shot limit, and SHALL fetch nothing else until
   it is needed.
3. WHEN the page overview or the review screen shows a page whose shot list
   has not been fetched, THE app SHALL fetch that page's shot list.
4. WHEN a shot that has not been fetched is shown as a thumbnail, THE app SHALL
   fetch a small preview of it; WHEN it is shown on the review screen, THE app
   SHALL fetch the full shot.
5. WHILE a page's shot list or a shot has not been fetched, THE app SHALL show
   a placeholder in its place, and the page overview SHALL show the page's shot
   count from the album's metadata.
6. THE app SHALL check each fetched full shot against the SHA-256 the server
   gave for it, and IF they differ, THEN THE app SHALL discard the file and
   fetch it again.
7. THE app SHALL keep fetched full shots on the phone like shots it took,
   SHALL keep previews in a cache the system may clear, and SHALL NOT upload
   fetched shots back to the server.
8. WHEN the user deletes a shot or page that has not been fetched, THE app
   SHALL delete it without fetching it and queue its deletion on the server as
   for any other shot or page.
9. IF a fetch fails, THEN THE app SHALL keep the placeholder, show that the
   shot or page could not be loaded, and retry when it is next shown or when
   the network returns.
10. WHEN the album has been opened, THE server albums list SHALL mark it as on
    this phone, and from then on it SHALL behave as any other album on the
    phone.
