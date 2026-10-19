# Photo Album Archiver

Turns several phone photos of each photo-album page into clean, printable page
images at 300 DPI, and one PDF per album.

| Part | Status |
| --- | --- |
| `backend/` page processing pipeline (Python + OpenCV) | working on synthetic photos |
| Album assembly, 300 DPI export, PDF | working on synthetic albums |
| `albumserver` upload API, storage and processing server | working; tested against the app's contract |
| `android/` capture app (Kotlin) | working against a fake server and an emulator |
| `scanner/` flatbed scanner app (Python, GTK 4) | working against SANE's test scanner and `albumserver` |

## Page pipeline

Input: 1 or more photos of one page, from different angles. They can each show
the whole page (to get rid of glare) or each show part of an oversized page
(to stitch it), or a mix.

1. **Register.** SIFT features and a RANSAC homography for every overlapping
   pair; the best-connected photo becomes the reference and the others are
   chained to it. Photos that don't match anything are dropped and reported.
2. **Find the page** in a low-res mosaic and in each photo: candidate
   outlines from closed edges (nested ones too), from colour contrast with
   the background, and from four long straight lines (for outlines that never
   close, e.g. a page only whole in the mosaic). Pages of an open album touch
   with no background between them, so each outline is also cut along
   straight lines running right across it (the join between pages, the
   binding, the edge of a page stack). A candidate is marked down when such
   a line runs just inside its left or right side (it takes in a neighbour's
   edge or a page stack), down its middle (two pages as one), or a wide strip
   in from its top or bottom (the table's edge; a sleeve's thin crimped
   edges belong to the page), and when it takes in background. Photos are
   assumed upright, so neighbours lie left and right. Pages cut off by the
   edge of what was photographed are discounted.
   **Inside a sleeve,** the outline found may lie on the sleeve's crimped
   seam, its binding strip or the paper, differently on each side. A strip
   around each side is searched for edges running the side's length, the
   bands between them are told apart (crimp: ridged across; strip: plain,
   about as light as the paper but not like it; clear plastic; paper), and
   the paper's four edges are chosen together, each traced within a narrow
   band so it cannot jump to the sleeve's. The page is then cropped to the
   paper (`--crop sleeve` keeps the sleeve's bands around it), the bent-page
   fit below uses the paper's edges, and the strip's side counts as the
   binding's. A page with no crimp or strip found, or whose paper is found
   on fewer than three sides, is processed exactly as before (`sleeve` in
   the page metadata says why; `--sleeve off` skips the step).
3. **True proportions** from the perspective geometry of the corners
   (Zhang & He), using the phone's focal length (26 mm equivalent by default,
   or the real one from EXIF via `--focal-35mm`).
4. **Bent pages.** A page held at its binding lifts off the table and bends
   near it, so its edges bow there. The page outline is traced as curves,
   long straight lines in the page (print borders, mats, titles) are found,
   and a page bent across one side (a cylinder: the profile's angle as a
   spline over the distance along the page) is fitted to them with the known
   focal length, at each side in turn, and compared with a flat page fitted
   to the same evidence. The page counts as bent when that clearly explains
   the outline better (30% lower cost) and lifts at least 0.3% of its width;
   the side facing a neighbouring page is preferred.
5. **Warp** every full-resolution photo straight into the page rectangle (one
   resampling step), at roughly the resolution the photos captured. A flat
   page goes through one homography per photo, exactly as before; a bent one
   through a flattening map (output pixel, page surface, reference photo,
   photo) that keeps distances along the page true, with a smooth correction
   per photo, from matched features, for the parallax a homography cannot
   model in the bent strip.
6. **Fuse.** Tone curves match every shot's exposure and colour to one of
   them. Where shots disagree, the darker one is favoured, because glare only
   ever adds light. A shadow (the phone, a hand) takes light away; with
   `--shadow`, shots are first compared for shadows (a shadow scales the
   light and keeps the print's colour, where glare adds white light and
   washes colour out), and where a shot is shadowed and another is not, the
   unshadowed one is used there. The weights hand over gradually between shots instead of switching per
   pixel, and edges of partial shots are feathered, so seams blend.

7. **Check what is left.** A glare detector looks at each shot on its own
   (whiter than its surroundings, washed-out colour, flattened texture,
   clipped highlights), combines that with the comparison between shots, and
   weights it by each shot's share of every composed pixel. What remains is
   grouped into glare regions, each with where it is, which shots saw it and
   why it stayed (`single_shot`: another shot from a different angle fixes
   it; `all_shots_glared`: change the light). With `--shadow`, shadows left
   in the page (where only one photo covers it, or every photo has the same
   shadow) are found and grouped the same way (`single_shot`,
   `all_shots_shadowed`). Page area
   no photo reached is grouped and located the same way. The composed image
   is not changed by this step.

Every page comes with its metadata, `PAGE.json` next to `PAGE.png` (or at
`--report`): which photos were used or dropped, coverage, the colour
correction, the glare, shadow and uncovered regions, whether the page was
flattened (`curvature`: binding side, lift, profile, each photo's alignment,
or why not), what was taken for sleeve and paper (`sleeve`: whether the page
was treated as sleeved or why not, the crop, the strip's side, and per side
the sleeve's bands from the outside in with their widths), and warnings
(`glare`, `shadow`, `incomplete_coverage`, `single_coverage`,
`photos_dropped`, `steep_binding`, `curvature_uncorrected`,
`paper_edge_uncertain`), each with a code and a message:

```json
{"code": "glare", "fraction": 0.0412, "regions": 1, "single_shot": 1, "location": "bottom-left",
 "message": "Glare remains on 4.1% of the page in 1 area (bottom-left). It was seen by only one photo; add a shot of that area from a different angle."}
```

Glare is removed only where two shots overlap, so the page metadata also
reports the part of the page only one photo reached, as a `single_coverage`
warning with regions (the seam between two shots is ignored; the minimum is
1% of the page, `--min-single-area`). A page shot once always gets it. Every
warning about an area carries its regions, each with `shot_boxes`: the
region's box in each photo's own pixels, so the capture app can outline on
the photo where the next shot should go. That, not single-photo detection,
is what the project relies on against glare. Each single-coverage region
also says how much of it the glare detector saw as glare (`glare_seen`,
left out with the detector off, since "not looked" is not "clean"), and the
warning names the largest region with at least 2% seen first (`--glare-seen`)
and counts them (`glare_seen_regions`). The warning is raised either way:
the detectors miss most single-shot glare.

Whether following that prompt removes the glare was measured on the 19
real glare-album pages (`tools/replay_glare.py`, run by hand on the private
photos on 2026-10-17 at `62e9eb1` with the tool as added after it): each
page is replayed from fewer shots, and the shot that covers the outlined
area is added, as a user following the prompt would. Started from a stitch
of two shots, two prompted shots removed 93% of the single-shot glare (3
replays, one page). Started from one whole-page photo, they removed 54%
(9 replays, 8 pages), or 66% with the shot that covers the least of the
area while still covering half of it. What stays looks like glare the
added shot has in the same place: the shot covering most of the area is
often the one taken next, from about the same angle, and does worse than
one taken from elsewhere. With the area then covered twice the prompt goes
away, and no detector reports what is left.
No glare was found in single coverage outside every warned area. So two
shots over every part of a page do not by themselves remove the glare:
they do when the page is stitched from shots aimed at different parts of
it, and that is what the project relies on. **Aim each shot at a different
part of the page**, and take a prompted shot aimed at the outlined part; a
second whole-page shot from the same place clears the warning but not the
glare. The stitch result rests on 3 replays of one page, since the glare
album was mostly shot whole; the first album shot with the app is the
check (`.kiro/specs/single-shot-glare/tasks.md`).

Known limit: the single-photo detector is cautious. Faint sleeve sheen looks
much like pale print content, white borders and white paper in one photo, so
it reports glare it is sure of and misses much of the faint kind (on synthetic
stitched pages it finds roughly 5 to 10% of the leftover glare, with false
alarms under 0.2% of the page; on real sleeved pages it finds almost none).
Glare no shot sees past is reported, never painted over.

Shadow handling (`--shadow`, off by default) works the same way: a shadow is
removed where another photo saw that part of the page without it, and
reported where none did (`shadow` warning). With only two photos of a flat,
pale area (white paper), a shadow in one and glare in the other look alike,
so there the darker photo is kept as before; a third photo settles it. It is
off by default because on real pages where most shots carry the phone's
shadow somewhere, pushing out the shots found shadowed can let in one whose
shadow went unseen, and on five of 19 real glare-album pages that made the
page worse.

A learned detector (`--detector net`, a small CNN run through OpenCV, about
0.1 s per photo) was trained on 15 real album pages, labelled by comparing
each page's overlapping shots (`tools/real_glare_data.py`,
`tools/train_glare_net.py`). On 9 real pages it had never seen it finds
about 10% of the glare the overlap comparison finds, with false alarms on
0.01% of the clean area, where the default detector finds 0.2%. It is not
the default because it flags white paper and print borders on synthetic
pages, and because most of what it misses, a soft veil over much of a page,
cannot be told from the page's own lighting in one photo. The glare spec's
original targets (80% found, 0.2% false alarms) are out of reach for either
detector from a single photo, and were replaced by the overlap coverage spec
(`.kiro/specs/overlap-coverage/`): two shots over every part of a page,
checked by the server and shown by the app.

### Use

```sh
cd backend
python3 -m pip install -e '.[test]'
albumproc page shot1.jpg shot2.jpg shot3.jpg -o page.png --debug debug/   # also writes page.json; debug/ gets sleeve.jpg, curvature_outline.jpg, _profile.png, _grid.jpg, align_NN.png
albumproc page shot*.jpg -o page.png --masks     # also page.glare.png, page.uncovered.png and page.single.png
albumproc glare shot1.jpg --overlay checked/     # glare in single photos, before processing a page
albumproc glare-eval page.json --truth drawn.png # score page.glare.png against a hand-drawn mask
albumproc synth samples/ --kind glare -n 4      # synthetic test photos (glare, stitch, pair or clean_white)
albumproc synth samples/ --kind stitch -n 2 --glare-style sleeve   # long sleeve streaks instead of spots
albumproc synth samples/ --kind pair --gap 0    # pages touching, as in an open album
albumproc synth samples/ --kind curved --lift 0.03 --binding left -n 2   # a page bent near its binding
albumproc page shot*.jpg -o page.png --curvature force --binding left   # always flatten, bent at the left (default: auto)
albumproc page shot*.jpg -o page.png --crop sleeve   # a sleeved page with its sleeve; --sleeve off takes the page outline as found
albumproc synth samples/ --kind glare -n 2 --sleeve --binding left   # a page in an album sleeve, binding strip on the left
albumproc straightness page.png --overlay lines.jpg   # how straight long lines are, to compare before and after
python3 -m pytest
```

## Album: print images and PDF

Input: an album folder with one subfolder of photos per page (`.jpg`, `.png`
or `.tif`), taken in natural order (`page-2` before `page-10`).

```sh
albumproc album my-album/ -o out/ --page-size 10x12in
albumproc print out/pages/001-cover.png -o cover.tif --page-size 10x12in --bleed 0.125in
```

Each page goes through the page pipeline, then is resampled once to exactly
its physical size at 300 DPI (`--dpi`) and written with that resolution and an
sRGB profile. The photos carry no scale, so the page size comes from
`album.json` (the capture app writes it when an album is created), else
`--page-size`, else 8.5x11in. Sizes are written `10x12in`, `254x305mm`,
`letter`, `a4` or `a3`, either way round; the long side goes with the page's
long side. A page whose measured shape is more than 2% off the size is fitted
inside it with a white border and flagged rather than distorted.

An optional `album.json` in the album folder sets the title, the size and the
page order, with per-page overrides:

```json
{
  "title": "Family album 1962",
  "page_size": "10x12in",
  "pages": [
    {"folder": "cover", "name": "Front cover", "page_size": "10.5x12.5in"},
    "page-01",
    {"folder": "page-02", "rotate": 90, "fit": "fit"},
    {"folder": "page-03", "curvature": "force", "binding": "left"},
    {"folder": "page-04", "crop": "sleeve"}
  ]
}
```

`curvature` (`auto`, `off`, `force`) and `binding` (`auto`, `left`, `right`,
`top`, `bottom`) override `--curvature` and `--binding` for one page, and
`sleeve` (`auto`, `off`) and `crop` (`paper`, `sleeve`) override `--sleeve`
and `--crop`; changing them reprocesses that page.

Output:

```
out/report.json            every page: status, pipeline report, size, DPI, warnings
out/album.pdf              one PDF page per album page, at its physical size
out/pages/001-cover.png    processed page (lossless), reused on re-runs
out/print/001-cover.jpg    print image (JPEG quality 95; --format png or tiff)
```

A page that fails is reported and left out, and the command exits with 3
(`--placeholder` puts a blank "missing" page in its place; `--strict` stops).
Re-runs only reprocess pages whose photos changed, and only re-print when just
the print options changed (`--force` redoes everything). `--jobs N` processes
pages in parallel. With `SOURCE_DATE_EPOCH` set, the PDF is byte-for-byte
reproducible. Warnings in the report (`low_resolution`, `upsampled`,
`aspect_mismatch`, `incomplete_coverage`, `photos_dropped`, and each page's
own, such as `glare`, `shadow` and `single_coverage`, passed through as they
are) say which pages are worth shooting again.

## Android capture app

`android/` is the phone side: it photographs each album page several times
and uploads the shots to the server. All image work happens on the backend.

- **Albums.** The hamburger menu creates albums (a name and a page size:
  8.5 × 11 in, A4, A3 or custom), switches between them, and edits or deletes
  them. The app reopens the album you were last shooting, at its last page.
- **Shooting.** The camera preview fills the screen. **Volume up** (or the
  shutter) takes a shot of the current page; **volume down** (or "Next page")
  starts the next page, which is refused while the current page has no shots.
  "Undo" removes a page started by mistake. Limits: 25 shots per page, 500
  pages per album. Flash is off by default because it puts a glare spot in the
  same place on every shot.
- **Reviewing.** Tap a thumbnail or the page number to review shots and pages:
  swipe through a page's shots, pinch to zoom, use the arrows to change page,
  and delete shots or pages (later pages are renumbered).
- **Uploading.** Every shot is kept on the phone and uploaded in the
  background in the order things happened, on Wi-Fi only unless Settings say
  otherwise, retrying until the server confirms it. Uploads survive the app
  being closed and the phone rebooting.
- **Server albums** lists what is on the server. An album that is not on this
  phone can be deleted there, or opened to keep shooting it; opening fetches
  only the album's page list and its last page, and other pages load as you
  look at them.
- **Settings**: the server URL and access token (stored encrypted), "Test
  connection", and the upload network.
- **Pages that need another shot.** With a server set, a page whose last run
  found glare or part of the page in one photo only gets a red dot, and its
  photos show the area outlined: solid where glare was seen there, dashed
  where none was (which is not proof there is none). While you shoot, a line
  above the thumbnails names the lowest-numbered other page that needs a
  shot ("Page 3 needs another shot, and 2 more"), with one short vibration
  when a page first needs one; tap it to open that page's photo with the
  area. The album list shows how many pages of each album need a shot, and
  switching album, starting a new one or opening the server albums asks
  first ("Stay" opens the page overview, "Leave anyway" goes on) while the
  album has such pages or pages still being checked.

### Build and install

Every build of `main` publishes the app on the
[android-latest](https://github.com/crawlins/photo-album-archiver/releases/tag/android-latest)
pre-release: open that page on the phone and tap `album-archiver.apk` (allow
installs from the browser when asked). Each pull request's build also keeps
the APK as the `album-archiver-apk` workflow artifact.

To build it yourself you need JDK 17 or later (a full JDK, with `jlink`) and
the Android SDK (platform 37). From the repository root:

```sh
make apk                                # android/app/build/outputs/apk/debug/app-debug.apk
make install-apk                        # build, install over USB (keeps the app's data) and start it
```

`install-apk` needs `adb` (the SDK's platform-tools) on `PATH` and the phone
plugged in with USB debugging on (Settings > About phone, tap Build number
seven times, then Developer options > USB debugging). Debug builds are all
signed with `android/app/debug.keystore`, so a newer APK, from CI or a local
build, installs over an older one.

The Gradle tasks directly:

```sh
cd android
./gradlew assembleDebug                 # app/build/outputs/apk/debug/app-debug.apk
./gradlew installDebug                  # onto a connected phone or emulator
./gradlew lint testDebugUnitTest        # lint and the JVM tests
./gradlew connectedDebugAndroidTest     # the emulator tests (needs a running emulator)
```

The JVM tests use Robolectric and an in-memory implementation of the server
contract below (`app/src/sharedTest/.../FakeAlbumServer.kt`); the emulator
tests drive the real UI with the same fake server running on the device,
plus one test that takes a real CameraX shot and checks its EXIF orientation
and focal length.

### Server contract

The upload server implements these calls (`.kiro/specs/android-app/design.md`
has the bodies). Every request carries `Authorization: Bearer <token>`; album,
page and shot ids are UUIDs the app chooses, so retried uploads are
idempotent.

| Request | Meaning |
| --- | --- |
| `GET /api/v1/ping` | 200 when the token is valid |
| `GET /api/v1/albums` | every album with name, page size, page and shot counts, last change |
| `GET /api/v1/albums/{album}` | one album's metadata and its pages in order with shot counts |
| `PUT /api/v1/albums/{album}` | create or replace `{name, page_size, pages, created}` |
| `DELETE /api/v1/albums/{album}` | delete the album and everything made from it |
| `GET /api/v1/albums/{album}/pages/{page}` | one page's shots: id, sha256, bytes, taken |
| `DELETE /api/v1/albums/{album}/pages/{page}` | delete a page and its shots |
| `PUT /api/v1/albums/{album}/pages/{page}/shots/{shot}` | store a JPEG; `X-Content-SHA256` header; 201 new, 200 same, 400 bad hash, 409 different hash, 422 over a limit |
| `GET /api/v1/albums/{album}/pages/{page}/shots/{shot}` | the JPEG as uploaded with `X-Content-SHA256`; `?size=thumb` for a preview of at most 320 px |
| `DELETE /api/v1/albums/{album}/pages/{page}/shots/{shot}` | delete a shot |

The app answers 401 and 403 by pausing uploads until Settings change, retries
5xx and network errors with exponential backoff, retries a 400 on a shot once,
and drops 409, 413 and 422 (keeping the shot on the phone).

## Server

`albumserver` is the server the capture app uploads to. It stores albums,
pages and shots in one data folder, serves them back to the app, and runs the
album step on each page as soon as it has been shot, so that every album's
print images and PDF stay current while you work through it.

```sh
cd backend
python3 -m pip install -e .
albumserver token create pixel-8        # prints the phone's token, once
albumserver serve                       # http://127.0.0.1:8080, data in ~/.local/share/albumserver
make serve                              # the same on every interface, for a phone on the Wi-Fi (PORT=8080)
```

Enter the server's URL and the token in the app's settings. Each phone gets
its own token; `albumserver token list` shows them (never the tokens) with
when each was last used, and `albumserver token revoke pixel-8` cuts one off.

Settings come from a TOML file (`--config`, see
[`deploy/albumserver.toml`](backend/deploy/albumserver.toml)) and from
`ALBUMSERVER_<NAME>` environment variables, which win. An unknown or invalid
setting stops the server, naming it. The main ones: `data_dir`, `host`
(`127.0.0.1`), `port` (8080), `tls_cert`/`tls_key`, `max_body_mb` (1024,
for high-resolution scans), `idle_period_s` (10),
`workers` (1 page run at a time; each can use 1 to 2 GB), `cancel_runs`
(false), `glare_detector` (`features` by default, `net` in the deployed
file; it changes only the page warnings, never the composed page).

**Limits.** A page holds at most 25 shots and an album at most 500 pages, the
same as the app. A shot beyond either gets 422, as does album metadata
listing more than 500 pages; the checks are made in the same database
transaction as the change, so concurrent uploads cannot exceed them.

**Processing.** A page is processed when the app moves on to a later page, or
after 10 seconds without a new or deleted shot. The page runs through the
album step on its own, in a separate process; once no page of the album is
waiting, the album's PDF is rebuilt from every page's latest print image.
`GET /api/v1/albums/{id}/status` shows each page's state, warnings and
errors; `/pdf` and `/pages/{pageId}/print` download the results. A page whose
shots change mid-run is run again afterwards (with `cancel_runs = true` the
run is stopped instead).

**Data folder.** `index.sqlite` (SQLite, WAL) plus
`albums/<albumId>/photos/<pageId>/<shotId>.jpg`, previews, outputs and the
album's `album.pdf` and `report.json`. Back up the folder to back up the
server. Every file is written under a temporary name and renamed into place
before it is recorded, and removed from the index before it is deleted, so
after a crash startup cleanup only has to delete files the index does not
know. `albumserver check` reports missing photos, unindexed files and photos
whose hash no longer matches, without changing anything.

### Running it as a service

[`deploy/albumserver.service`](backend/deploy/albumserver.service) runs the
server as an unprivileged `albumserver` user with its data in
`/var/lib/albumserver`; the comment at its top has the install steps. It
logs one line per request and per page run to the journal; tokens and
`Authorization` headers are never logged.

### TLS

The token is a password: never send it over plain HTTP outside your own
machine. The server listens on loopback by default and warns at startup when
it listens on another address without TLS. Either:

- **Tailscale** (recommended): the phone and server talk over the tailnet and
  nothing is exposed to the internet. `tailscale serve --bg --https=443
  http://127.0.0.1:8080`, then use `https://<machine>.<tailnet>.ts.net` in the
  app.
- **Caddy** with an automatic certificate, for access without Tailscale:

  ```
  albums.example.com {
      reverse_proxy 127.0.0.1:8080
  }
  ```

- Or set `tls_cert` and `tls_key` to serve HTTPS directly.

## Scanner app

`albumscan` (in `scanner/`) captures pages with a flatbed scanner instead of
the phone: any scanner SANE finds (`scanimage -L`), such as a CanoScan LiDE
on USB. Each scan becomes a shot uploaded to `albumserver` with the phone's
requests, so a scanned album gets the same limits, processing and PDF, and an
album can be part photographed, part scanned.

```sh
sudo apt install sane-utils python3-gi gir1.2-gtk-4.0 python3-pil
albumserver token create scanner        # the app's own token
make scan                               # or: cd scanner && python3 -m pip install --user -e . && albumscan
```

It runs on the system Python, because GTK 4's bindings come from the
distribution; it does not need the backend or OpenCV. On first start it asks
for the server's address and the token, then for an album: a new one, or one
already on the server, which carries on at its last page.

Space scans, Enter starts the next page, Escape cancels a scan; click a scan's
thumbnail to delete it. Scans are colour, of the whole glass, at the
scanner's highest colour resolution by default; any offered resolution from
300 dpi up can be chosen. The server takes requests up to 1 GB
(`max_body_mb`) and images up to 1 billion pixels, which covers a whole glass
at 2400 dpi; higher resolutions are left out of the list. "Page turned" says how the page lies on the glass,
per album, and turns every scan back. A page larger than the glass is scanned
in overlapping parts, each a scan of the same page, and stitched. Every scan
is kept in `~/.local/share/albumscan/outbox/` until the server has it, and
uploads resume when the app starts again; settings are in
`~/.config/albumscan/`. Below the scans, the previous and current pages show
their processing state and warnings, with a button to open the print image.

Scans carry `Software: albumscan <version>` in their EXIF. A page whose shots
all carry it is processed in **scan mode**: its proportions are taken from
its outline as they are, it is never treated as bent, and glare, shadow and
single-coverage checks are skipped, since a scanner's light moves with its
sensor. Stitching, uncovered areas, dropped shots and the sleeve step work as
for photos. `source` and `scan_dpi` in the page metadata say which mode ran;
`albumproc page --source photo|scan` overrides it.

## Known limits

- The page size cannot be measured from the photos; a wrong size prints the
  page at the wrong size.
- Pages are never rotated automatically; use `rotate` in `album.json`.
- Photos are assumed to be sRGB, which is what phones produce in practice.
- Bent pages: the model handles a page bent across one side (the binding),
  lifted towards or away from the camera, with or without its own outline
  seen at the binding. It does not handle wavy or cockled paper, dog-eared
  corners, sleeve wrinkles, content hidden in the gutter (a `steep_binding`
  warning asks for another shot), or the darker shading near the binding.
  On the five real sleeved pages, with the paper's own edges traced inside
  the sleeve, `auto` flattens page-three and page-five and keeps the other
  three flat; see `curvature` in the page metadata and `--debug`'s
  `curvature_*.jpg`.
- Sleeves: the paper's edge is found on four of the five real sleeved pages
  (not on page-four, which keeps the outline found before), and within the
  spec's tolerance on two; on the side away from the binding the edge is only
  taken within 1% of the page detection's, since the next pages under the
  sleeve end where the paper does. See `sleeve` in the page metadata and
  `--debug`'s `sleeve.jpg`.
- Glare can only be removed where at least one shot sees that spot without it,
  so for oversized pages each region needs two shots from different angles.
- Tested on synthetic photos and on real sleeved album pages, kept in the
  private crawlins/photo-album-archiver-data repository (family photos);
  `tests/test_real.py` runs them when its `real/` is linked into the root.
- The Android app has only been tested against a fake server and an emulator
  camera, not yet against a running `albumserver`.
- Android ignores the capture screen's portrait lock on large screens
  (tablets, foldables unfolded) from Android 16 on.
