# Photo Album Archiver

Turns several phone photos of each photo-album page into clean, printable page
images, and eventually one PDF per album.

| Part | Status |
| --- | --- |
| `backend/` page processing pipeline (Python + OpenCV) | working on synthetic photos |
| Album assembly, 300 DPI export, PDF | not started |
| Upload API and storage server | not started |
| Android capture app (Kotlin) | not started |

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
3. **True proportions** from the perspective geometry of the corners
   (Zhang & He), using the phone's focal length (26 mm equivalent by default,
   or the real one from EXIF via `--focal-35mm`).
4. **Warp** every full-resolution photo straight into the flat page rectangle
   (one resampling step), at roughly the resolution the photos captured.
5. **Fuse.** Tone curves match every shot's exposure and colour to one of
   them. Where shots disagree, the darker one is favoured, because glare only
   ever adds light. The weights hand over gradually between shots instead of
   switching per pixel, and edges of partial shots are feathered, so seams
   blend.

The report (JSON) gives which photos were used or dropped, how much of the page
was covered, the glare found in each shot and the colour correction applied.
The capture app can use coverage and glare to ask for another shot.

### Use

```sh
cd backend
python3 -m pip install -e '.[test]'
albumproc page shot1.jpg shot2.jpg shot3.jpg -o page.png --report page.json --debug debug/
albumproc synth samples/ --kind glare -n 4      # synthetic test photos (glare, stitch or pair)
albumproc synth samples/ --kind pair --gap 0    # pages touching, as in an open album
python3 -m pytest
```

### Known limits

- Pages are assumed flat. A page bulging near the album's spine will be
  slightly distorted; that needs a curved-surface model.
- Glare can only be removed where at least one shot sees that spot without it,
  so for oversized pages each region needs two shots from different angles.
- Tested on synthetic photos and on five real sleeved album pages (two shots
  each, on the `real-test-photos` branch; `tests/test_real.py` runs them when
  `real/` is checked out).
