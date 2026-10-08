# Requirements: actions on a page's photos

## Introduction

While shooting an album it is easy to get a page boundary wrong: the user
forgets to press "Next page" and the first shots of the next page land on the
previous one, presses it too late, or takes a run of bad shots at the end of a
page. Today the only fix is deleting shots one at a time from the review
screen and taking them again.

This feature lets the user long-press a photo's thumbnail to select it, then
long-press it again for a menu of actions on that photo and the photos after
it on the same page:

- Delete this photo.
- Delete this photo and the others after it to the end of the page.
- Move this photo and the ones after it to the next page, creating a new page
  when this is the last page.
- Create a new page with this photo and the ones after it.

It extends the Android app spec (`.kiro/specs/android-app/`) and adds one
request to the album server (`.kiro/specs/album-server/`), because the server
has no way to move a shot from one page to another. Requirement numbers below
refer to this spec unless they name another one.

This feature changes one rule of the MVP: pages can now be inserted in the
middle of an album (by "Create a new page"), not only added at the end. Adding
shots to an earlier page with the camera stays out of scope; the current page
is still always the album's last page.

Out of scope: moving photos to the previous page or to an arbitrary page,
reordering photos within a page, selecting several photos that are not a run
to the end of the page, and undoing a move.

### Glossary

Terms are as in the Android app spec. In addition:

- **Photo**: what the app's labels call a shot. The menu uses "photo" because
  that is the word the user sees; this spec uses "shot" elsewhere, as the
  other specs do.
- **Thumbnail strip**: a row of thumbnails of one page's shots in the order
  they were taken: the capture screen's strip (Android app Requirement 3.4)
  and the review screen's strip added here.
- **Selected shot**: the shot whose thumbnail the user has long-pressed once.
- **Run**: the selected shot and every shot after it on the same page.
- **Source page**: the page holding the selected shot.
- **Next page**: the page numbered one more than the source page.
- **Shot actions menu**: the menu of the four actions.

## Requirements

### Requirement 1: Selecting a shot

**User story:** As someone who has just noticed a page boundary in the wrong
place, I want to pick the photo where the problem starts, so that I can act on
it and the photos after it.

#### Acceptance criteria

1. WHEN the user long-presses a thumbnail in a thumbnail strip AND no shot is
   selected, THE app SHALL select that shot, give brief haptic feedback, and
   SHALL NOT open the review screen.
2. WHILE a shot is selected, THE thumbnail strip SHALL mark the selected
   thumbnail clearly and mark every later thumbnail of the run less strongly,
   so that the user can see which shots "the ones after it" means.
3. WHILE a shot is selected, WHEN the user taps or long-presses another
   thumbnail in the same strip, THE app SHALL select that shot instead; a tap
   SHALL NOT open the review screen while a shot is selected.
4. WHILE a shot is selected, WHEN the user presses Back or taps outside the
   strip, THE app SHALL clear the selection and do nothing else; WHEN the user
   takes a shot or starts the next page (by button or volume key), or the
   strip comes to show a different page, THE app SHALL clear the selection
   and carry on as normal.
5. THE app SHALL NOT keep the selection when the screen is left or the app is
   restarted.
6. THE review screen SHALL show a thumbnail strip of the page being reviewed
   along its bottom edge, scrolled to and highlighting the shot shown, and
   WHEN the user taps a thumbnail with no shot selected, THE review screen
   SHALL show that shot.
7. A shot that has not been fetched (Android app Requirement 14) SHALL be
   selectable like any other, showing its placeholder or preview.

### Requirement 2: Opening the shot actions menu

**User story:** As someone fixing a page boundary, I want a deliberate second
gesture to open the actions, so that a single slip never deletes or moves
photos.

#### Acceptance criteria

1. WHEN the user long-presses the selected thumbnail, THE app SHALL open the
   shot actions menu for the selected shot.
2. THE menu SHALL name the shot and its run, for example "Page 12, photo 3 of
   5", and SHALL offer, in this order:
   1. "Delete this photo"
   2. "Delete this photo and the N after it" (N being the number of later
      shots in the run)
   3. "Move this photo and the N after it to page P", where P is the next
      page's number, or "… to a new page P" when the source page is the
      album's last page
   4. "Create a new page with this photo and the N after it"
3. WHEN the selected shot is the last shot of its page, THE menu SHALL word
   actions 3 and 4 for the one photo, and SHALL show action 2 disabled with
   the reason "No photos after this one".
4. THE menu SHALL show an action that cannot be done disabled, with the
   reason underneath, as given here and in Requirements 5 and 6, rather than
   hiding it, so that the menu always has the same layout.
5. WHEN the user dismisses the menu without choosing, THE app SHALL close it
   and keep the selection.
6. WHILE the menu or one of its confirmations is showing, THE volume keys
   SHALL keep their normal system behaviour (Android app Requirement 6.5).
7. WHEN an action has been carried out, THE app SHALL clear the selection.

### Requirement 3: Deleting this shot

**User story:** As someone with one bad photo, I want to delete it from the
thumbnail strip, so that I don't have to open it first.

#### Acceptance criteria

1. WHEN the user chooses "Delete this photo", THE app SHALL ask for
   confirmation naming the page, and SHALL then delete the shot exactly as the
   review screen's "Delete shot" does (Android app Requirements 7.3, 12.2 and
   12.4), including deleting an inner page left with no shots.

### Requirement 4: Deleting the run

**User story:** As someone who took several bad photos at the end of a page,
I want to delete them together, so that I don't delete them one by one.

#### Acceptance criteria

1. WHEN the user chooses "Delete this photo and the N after it", THE app SHALL
   ask for confirmation naming the page and the number of photos, and SHALL
   then delete every shot of the run in one step.
2. WHEN the run is every shot of the page AND the page is not the album's last
   page, THE app SHALL delete the page as well and SHALL say so in the
   confirmation, as for the page's only shot (Android app Requirement 12.4).
3. WHEN the run is every shot of the album's last page, THE app SHALL keep the
   page as an empty current page, the same state as just after "Next page".
4. THE app SHALL remove every shot of the run from the phone and queue its
   removal on the server, as for any other deletion (Android app
   Requirement 7.3).

### Requirement 5: Moving the run to the next page

**User story:** As someone who pressed "Next page" too late, I want to move the
photos that belong to the next page onto it, so that each page gets its own
photos without retaking them.

#### Acceptance criteria

1. WHEN the user chooses "Move … to page P" AND the source page is not the
   album's last page, THE app SHALL move every shot of the run to the next
   page.
2. WHEN the user chooses "Move … to a new page P" AND the source page is the
   album's last page, THE app SHALL create a new page after it, move every
   shot of the run to that page, and make it the current page, so that the
   next shot is added to it.
3. THE app SHALL keep each page's shots in the order they were taken, so that
   moved shots, which were taken before the next page's own shots, come first
   on it.
4. WHEN the run is every shot of the source page, THE app SHALL delete the
   source page after the move and renumber the pages after it, so that no
   empty page is left inside the album.
5. THE move SHALL be unavailable, with the reason "Already the whole last
   page", WHEN the run is every shot of the album's last page, because it
   would change nothing.
6. THE move SHALL be unavailable, with the reason "Page P would have more than
   25 photos", WHEN the next page's shots plus the run would exceed 25.
7. THE move to a new page SHALL be unavailable, with the reason "Album is full
   (500 pages)", WHEN the album already has 500 pages.
8. THE app SHALL carry out a move without asking for confirmation, because it
   deletes nothing, and SHALL then show for about four seconds which page the
   shots went to, for example "Moved 3 photos to page 13".
9. THE app SHALL move shots that have not been fetched without fetching them.

### Requirement 6: Creating a new page from the run

**User story:** As someone who forgot to press "Next page" between two pages, I
want the photos of the second page split off into a page of their own, so that
the two pages are separated without retaking anything.

#### Acceptance criteria

1. WHEN the user chooses "Create a new page with this photo and the N after
   it", THE app SHALL insert a new page directly after the source page, move
   every shot of the run to it, and renumber the pages after it, so that page
   numbers stay contiguous from 1.
2. WHEN the source page is the album's last page, THE new page SHALL become
   the album's last page and therefore the current page.
3. THE action SHALL be unavailable, with the reason "Already the whole page",
   WHEN the run is every shot of the source page, because it would change
   nothing.
4. THE action SHALL be unavailable, with the reason "Album is full (500
   pages)", WHEN the album already has 500 pages.
5. THE app SHALL carry out the action without asking for confirmation and
   SHALL then show for about four seconds the new page's number, for example
   "Made page 13 from 3 photos".
6. THE app SHALL move shots that have not been fetched without fetching them.

### Requirement 7: Limits

**User story:** As the owner of the backend, I want the shot and page limits
to hold through every move, so that no album the app makes can be refused by
the server.

#### Acceptance criteria

1. THE app SHALL check the 25-shot and 500-page limits for a move or new page
   in the same transaction that makes the change, so that nothing can get
   around them between the menu showing and the action running.
2. IF a limit check fails when the action runs, THEN THE app SHALL change
   nothing and show the same reason the menu would have shown.
3. THE app SHALL NOT limit deletions.

### Requirement 8: Where the user ends up

**User story:** As someone fixing an album, I want to see the result of an
action straight away, so that I can tell it did what I meant.

#### Acceptance criteria

1. WHEN an action is carried out on the capture screen, THE capture screen
   SHALL show the current page afterwards, which after a move from the last
   page is the new page holding the moved shots.
2. WHEN a deletion is carried out on the review screen, THE review screen
   SHALL behave as after any deletion (Android app Requirement 12.6).
3. WHEN a move is carried out on the review screen, THE review screen SHALL
   show the first moved shot on the page it went to, so that the user sees
   the shots where they are now.
4. THE page overview, the capture screen's header and the drawer's page
   counts SHALL reflect the change at once.

### Requirement 9: Keeping the server in step

**User story:** As someone archiving albums over a poor network, I want
moves and deletions to reach the server like every other change, so that the
server's pages match the phone's however the upload queue is going.

#### Acceptance criteria

1. THE app SHALL make each action locally at once and queue the matching
   server changes in the same transaction, in the order they happened, as for
   every other change (Android app Requirement 8.9).
2. THE app SHALL send a move to the server as a move of the shots, not as a
   deletion and a new upload, so that no photo is uploaded twice and shots
   that are not on the phone can be moved.
3. WHEN a moved shot has not finished uploading, THE app SHALL upload it to
   the page it was moved to and still send the move, so that the shot ends
   up on that page whether or not an earlier upload attempt reached the
   server.
4. THE app SHALL send the move before the album metadata that gives the new
   page order, so that the server knows the source page is complete when it
   sees the page after it, and processes it at once (album server
   Requirement 8.2).
5. WHEN the source page is deleted after a move, THE app SHALL queue its
   deletion after the move.

### Requirement 10: Moving shots on the server

**User story:** As the app, I want to move shots from one page to another on
the server safely and repeatably, so that a retried move never loses or
duplicates a photo.

#### Acceptance criteria

1. WHEN the server receives `POST /api/v1/albums/{albumId}/pages/{pageId}/move`
   with a list of shot ids, THE server SHALL move every listed shot of the
   album to that page, keeping its stored file, hash and time taken, and
   respond 204.
2. THE server SHALL skip a listed shot that is already on the page or is not
   in the album, so that repeating a move, or moving a shot whose upload never
   arrived or was deleted, succeeds and changes nothing more.
3. WHEN the page does not exist yet, THE server SHALL create it at the end of
   the album, as for a shot upload (album server Requirement 4.5); the
   metadata that follows gives it its place.
4. IF the move would give the page more than 25 shots, or creating the page
   would give the album more than 500 pages, THEN THE server SHALL respond 422
   and change nothing.
5. IF the album does not exist, THEN THE server SHALL respond 404; IF the
   body is not a JSON object with a `shots` list of 1 to 25 distinct UUIDs and
   nothing else, THEN THE server SHALL respond 400 and change nothing.
6. THE server SHALL make the whole move, its limit checks and the page
   creation in one transaction.
7. THE server SHALL treat the page the shots came from and the page they went
   to as changed (album server Requirement 8.1), and a page left with no shots
   SHALL be handled as when its last shot is deleted.
8. THE server SHALL keep each moved shot's preview, which does not depend on
   the page.
