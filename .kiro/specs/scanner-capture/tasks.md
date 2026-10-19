# Implementation plan: capturing pages with a flatbed scanner

Each task builds on the previous ones and ends with passing tests. App paths
are under `scanner/`, page step paths under `backend/`. Requirement numbers
refer to `requirements.md`.

- [x] 1. Scan mode in the page step
  - [x] 1.1 Add `albumproc/scanmark.py`: `MARKER_PREFIX`, `is_scan(path)` and
    `scan_dpi(paths)`, reading EXIF with Pillow and treating unreadable EXIF
    as not a scan.
    - _Requirements: 8.1, 8.6_
  - [x] 1.2 Add `PageOptions.source` and the scan-mode branches in
    `process_page`: side-length aspect, curvature off, glare and shadow off,
    empty single-coverage summary and no single-coverage warnings;
    `PageReport.source` and `scan_dpi`; `SCHEMA_VERSION` 6.
    - _Requirements: 8.2, 8.3, 8.4, 8.5, 8.6_
  - [x] 1.3 Set `source` per page in the album step and `albumproc page`
    before the process key is computed.
    - _Requirements: 8.1_
  - [x] 1.4 Write tests (`tests/test_scan_mode.py`): `is_scan` cases, a
    marked flat page with its true aspect and no glare, curvature or
    single-coverage output, two overlapping marked scans stitched, a mixed
    page processed as photos, and a page's process key changing with its
    source.
    - _Requirements: 8_

- [x] 2. Scanner and shots
  - [x] 2.1 Create the `scanner/` package (`pyproject.toml`, `__init__`,
    `cli.py`) and the test fakes (`fake_scanimage.py`, `fake_server.py`).
    - _Requirements: 10.1_
  - [x] 2.2 Add `sane.py`: device listing with the marker format, `v4l`
    filtered, USB preferred; option parsing (resolution list and range,
    glass size, colour); `Scanner.scan` with progress, cancellation and
    `ScanError`.
    - _Requirements: 1.1, 1.2, 1.4, 2.1, 2.2, 5.1, 5.2, 5.3_
  - [x] 2.3 Add `shot.py`: placement, JPEG quality 95 without subsampling,
    EXIF marker, make, model, resolution and time.
    - _Requirements: 2.3, 6.1, 6.2_
  - [x] 2.4 Write `test_sane.py` and `test_shot.py`, with `scanimage -d test`
    cases skipped when it is unavailable.
    - _Requirements: 1, 2, 5.1–5.3, 6_

- [x] 3. Talking to the server
  - [x] 3.1 Add `client.py` over `urllib`: every request the app makes, the
    server's `detail` on errors, connection errors as one exception type.
    - _Requirements: 3.1, 4.1, 4.3, 9_
  - [x] 3.2 Add `outbox.py`: the SQLite table, safe file writes, coalesced
    metadata, dropping unsent shots, startup cleanup, `classify`, and the
    `Sender` thread with backoff and the blocked state.
    - _Requirements: 6.3, 7_
  - [x] 3.3 Add `settings.py` with the 0600 token file.
    - _Requirements: 2.4, 3.2, 10.3_
  - [x] 3.4 Write `test_client.py`, `test_outbox.py` and `test_settings.py`.
    - _Requirements: 3, 6.3, 7_

- [x] 4. The session
  - [x] 4.1 Add `session.py`: new and opened albums, `add_scan`, `next_page`,
    `delete_shot`, metadata changes, limits checked before scanning.
    - _Requirements: 4, 5.5, 5.6, 5.7_
  - [x] 4.2 Write `test_session.py` and the end-to-end test against a real
    `albumserver serve`.
    - _Requirements: 4, 5, 7_

- [x] 5. The window
  - [x] 5.1 Add `window.py`: scanner and server setup, album list and new
    album dialog, the scan view with Scan, Next page, cancel, previews,
    shot deletion, upload count, quit warning, page status and Open print.
    - _Requirements: 1, 2, 3, 4, 5, 7.7, 9_
  - [x] 5.2 Add `albumscan.desktop`, `make scan`, and a README section.
    - _Requirements: 10_
  - [ ] 5.3 Check the window by hand with the real scanner and record the
    result here.
    - Checked under GTK's Broadway backend against a real `albumserver`
      and `tests/fake_scanimage.py`: new album, scan with progress, Space
      while scanning ignored, Escape cancels, Enter on an empty page stays
      put, delete with confirmation, uploads held while the server is down
      and resumed on the next start, the quit warning, the previous page's
      state and warnings, and the default resolution. The option parsing
      was checked against the four SANE backends that see the network Epson
      ET-2650 (escl, epson2, epsonscan2, airscan). Still to do: a real scan
      on the CanoScan LiDE, which was not connected.
