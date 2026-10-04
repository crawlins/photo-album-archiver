# Implementation plan: album assembly, 300 DPI print images and album PDF

Each task builds on the previous ones and ends with passing tests. Paths are
under `backend/`. Requirement numbers refer to `requirements.md`.

- [x] 1. Add dependencies and the page size parser
  - Add `Pillow>=10`, `img2pdf>=0.5` and `pikepdf>=8` to `pyproject.toml`
    dependencies; bump the version to 0.2.0.
  - Create `src/albumproc/printsize.py` with `PageSize`, `parse_page_size`
    and `parse_length` as in the design.
  - Write `tests/test_printsize.py`: `in`/`mm`/`cm`, decimals, `letter`, `a4`,
    `a3`, case and whitespace, rejection of zero, negative and malformed
    values with the text in the message, and `oriented()` for both
    orientations.
  - _Requirements: 2.2, 2.4, 2.5_

- [x] 2. Make print images in memory
  - [x] 2.1 Create `src/albumproc/printimage.py` with `PrintOptions`,
    `PageWarning`, `PrintInfo` and `make_print_image`: rotate, orient size,
    resolve `auto` fit against the tolerance, compute exact pixel size,
    resample once (area down, Lanczos up), pad or crop for `fit` / `fill`,
    mirror the bleed, convert to RGB.
    - _Requirements: 3.1, 3.3, 3.6, 4.1–4.5, 6.1, 6.2_
  - [x] 2.2 Compute capture DPI and add `upsampled` and `low_resolution`
    warnings.
    - _Requirements: 3.4, 3.5_
  - [x] 2.3 Write `tests/test_printimage.py` covering exact dimensions for each
    fit mode across DPI 150/300/600 and with bleed, the tolerance boundary,
    rotation onto a portrait size, the fill colour in padding, mirrored bleed
    pixels, and the warnings.
    - _Requirements: 3, 4, 6_

- [x] 3. Write print image files
  - Add `write_print_image` (Pillow; JPEG quality and `subsampling=0`, PNG,
    TIFF with LZW; `dpi` and the sRGB ICC profile; temp file then
    `os.replace`) and `placeholder_image`.
  - Extend `tests/test_printimage.py`: read each format back with Pillow and
    check mode, size, `info["dpi"]` and an ICC profile whose description says
    sRGB; check no `.tmp` file remains.
  - _Requirements: 5.1–5.3, 5.5, 8.4, 9.6_

- [x] 4. Add `albumproc print`
  - Add the `print` subcommand to `cli.py` (page size, DPI, format inferred
    from the extension, quality, bleed, fit, rotate, fill as `#rrggbb`).
  - Export `make_print_image` from `albumproc/__init__.py`.
  - Test via `main([...])`: output size and DPI for a synthetic page, exit 2
    on a bad page size.
  - _Requirements: 11.2, 11.3_

- [x] 5. Write the album PDF
  - Create `src/albumproc/pdf.py` with `PdfPage` and `write_album_pdf`:
    img2pdf with `get_fixed_dpi_layout_fun` so pages are sized from pixels and DPI, then pikepdf for
    `/TrimBox`, `/PageLabels`, `/Title`, dates from the epoch and
    `deterministic_id=True`; temp file then `os.replace`.
  - Write `tests/test_pdf.py`: page count and order, MediaBox =
    `px / dpi × 72`, TrimBox inset by the bleed and absent without one,
    labels, title, embedded JPEG streams byte-identical to the files,
    identical bytes for two runs with the same epoch, and PNG input accepted.
  - _Requirements: 6.3, 7.1–7.5, 12.2_

- [x] 6. Discover an album
  - Create `src/albumproc/album.py` with `PageSpec`, `AlbumSpec` and
    `discover_album`: natural sort, supported extensions, slugs, the
    `album.json` manifest (bare names or objects; unknown keys rejected;
    `rotate` and `fit` validated), size resolution (page over manifest over
    command line, else letter).
  - Write `tests/test_album_discover.py` using `tmp_path` folders with tiny
    image files: natural order, ignored files, manifest order and overrides,
    manifest page with no folder recorded as a problem, empty album, letter
    default and bad sizes, unknown manifest key.
  - _Requirements: 1.1–1.6, 2.1, 2.3, 2.4, 4.3, 4.4_

- [x] 7. Process an album
  - [x] 7.1 Add `AlbumOptions` and `process_album` running pages in order:
    read photos, `process_page`, save `pages/NNN-slug.png`,
    `make_print_image`, `write_print_image`, coverage and dropped-photo
    warnings, per-page failure capture, `--strict` and `--placeholder`, then
    `write_album_pdf` and the final report. Rewrite `report.json` atomically
    after each page.
    - _Requirements: 3.2, 5.4, 7.1, 8.1–8.5, 10.1–10.5, 12.1_
  - [x] 7.2 Add the `process_key` / `print_key` reuse logic and `force`.
    - _Requirements: 9.1–9.6_
  - [x] 7.3 Add `jobs` with a `ProcessPoolExecutor`, `cv2.setNumThreads(1)`
    in workers, and results collected in album order.
    - _Requirements: 12.1, 12.3_
  - [x] 7.4 Write `tests/test_album.py` on a three-page synthetic album built
    with `make_case` (small pages, three photos each): outputs and report
    fields, a failing page (photos of unrelated noise) with exit status 3 and
    the rest intact, `placeholder` page count, `strict` stop with no PDF,
    reuse on a second run, one changed photo reprocessing only its page, a DPI
    change reusing processed pages, and `jobs=2` matching `jobs=1`
    byte-for-byte under a fixed `SOURCE_DATE_EPOCH`.
    - _Requirements: 1, 7, 8, 9, 10, 12_

- [x] 8. Add `albumproc album`
  - Add the `album` subcommand to `cli.py` with the options in the design,
    progress lines on stderr, the summary on stdout and exit statuses 0, 1, 2
    and 3. Export `process_album` from `albumproc/__init__.py`.
  - Test argument parsing and exit codes via `main([...])`, and that
    `albumproc page` output is unchanged.
  - _Requirements: 11.1, 11.3–11.5_

- [x] 9. Update the README
  - Mark "Album assembly, 300 DPI export, PDF" as working on synthetic
    albums, document the album folder layout, `album.json`, the two new
    commands and the outputs, and add the known limits (page size must be
    given, no automatic rotation, sRGB assumed).
  - _Requirements: 11_
