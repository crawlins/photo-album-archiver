# Implementation plan: album server

Each task builds on the previous ones and ends with passing tests. Paths are
under `backend/`. Requirement numbers refer to `requirements.md`.

- [ ] 1. Package and configuration
  - Add `fastapi`, `uvicorn[standard]` and `pydantic>=2` to the dependencies,
    `httpx` to the test extras, and the `albumserver` script entry point.
  - Create `src/albumserver/config.py` with `Settings`, loading a TOML file
    and `ALBUMSERVER_` environment variables and rejecting unknown or invalid
    settings by name.
  - Tests for defaults, file and environment precedence, and rejection.
  - _Requirements: 2.1, 11.2_

- [ ] 2. Index and files
  - [ ] 2.1 Create `store.py` with the schema, WAL mode, `BEGIN IMMEDIATE`
    write transactions, and the album, page, shot and token operations,
    including position renumbering and the limit checks.
    - _Requirements: 3.3, 5.1–5.3, 10.2_
  - [ ] 2.2 Create `files.py` with data-folder paths, streaming
    hash-while-writing to a temp file with fsync and rename, startup cleanup,
    and the `check` report.
    - _Requirements: 4.7, 7.4, 10.1, 10.3, 10.4_
  - [ ] 2.3 Tests: limits under concurrent writers, renumbering, cascade
    deletes, cleanup after a simulated crash at each step, and `check` on a
    folder with a missing file, an extra file and a changed photo.
    - _Requirements: 5, 7, 10_

- [ ] 3. Tokens
  - Create `auth.py` (generation, SHA-256 hashing, constant-time lookup,
    last-use update, FastAPI dependency) and the `token create|list|revoke`
    commands in `cli.py`.
  - Tests: create prints once and stores only the hash, list never shows a
    token, revoke takes effect, missing and wrong tokens get 401 and are
    logged without the token.
  - _Requirements: 1.1–1.6_

- [ ] 4. Album metadata routes
  - Create `api.py` with error bodies, UUID path validation, the body size
    limit, request logging, `ping`, and the album `PUT`, `GET` (one and all)
    and page `GET` routes.
  - Contract tests using the app spec's JSON examples: create, replace,
    reorder, omitted page with shots kept, validation errors, the 500-page
    limit, list order, 404s.
  - _Requirements: 2.4, 3.1–3.6, 5.2, 11.3_

- [ ] 5. Shot upload and retrieval
  - [ ] 5.1 Add the shot `PUT`: early repeat check, streaming hash, JPEG
    check, EXIF time, auto-created album and page, 201/200/400/409/413/422/507.
    - _Requirements: 4.1–4.7, 5.1_
  - [ ] 5.2 Add `thumbs.py` and the shot `GET` with `?size=thumb`, ETags and
    304.
    - _Requirements: 6.1–6.4_
  - [ ] 5.3 Tests for every upload and retrieval case in the design, including
    two concurrent uploads at the 25-shot boundary.
    - _Requirements: 4, 5, 6_

- [ ] 6. Deletion routes
  - Add the shot, page and album `DELETE` routes: index first, then files and
    previews, 204 when absent, marking the page changed or the PDF stale.
  - Tests for each, idempotence, and that previews go with their shots.
  - _Requirements: 7.1–7.4_

- [ ] 7. Serve command
  - Add `albumserver serve` (uvicorn with optional TLS, startup cleanup,
    plain-HTTP warning on non-loopback addresses, graceful SIGTERM) and
    `albumserver check`.
  - Add `deploy/albumserver.service` and an example `albumserver.toml`.
  - Test that the warning is logged and that startup cleanup runs.
  - _Requirements: 2.2, 2.3, 10.3, 10.4, 11.1, 11.4, 11.5_

- [ ] 8. Processing worker (needs the album-pdf spec implemented)
  - [ ] 8.1 Add page `changed_at` and `state` updates to the API: set on shot
    add or delete and page size change, set earlier pages `ready` when a
    metadata update adds a later page, set `pdf_stale` on reorder, rename and
    page deletion.
    - _Requirements: 8.1, 8.2, 8.5_
  - [ ] 8.2 Create `worker.py`: idle-period promotion to `ready`, oldest-ready
    selection under the worker limit, the one-page work folder, `process_album`
    in a child process, result recording or return to `changed` when the page
    changed during the run, stopping runs for deleted pages and albums, and
    startup recovery. Start it from `serve`.
    - _Requirements: 7.2, 8.3, 8.4, 8.6, 8.7, 8.9–8.11_
  - [ ] 8.3 Add album assembly: `write_album_pdf` over the pages' print images
    in order and the album report, when no page is pending and the PDF is
    stale.
    - _Requirements: 8.5_
  - [ ] 8.4 Tests with a fake `process_album` and clock for every processing
    case in the design, and one end-to-end run on a small synthetic album.
    - _Requirements: 8_
  - [ ] 8.5 (Later) Add `cancel_runs`: stop a page's run when its shots change
    mid-run, discard the run's output and return the page to `changed`, with
    tests.
    - _Requirements: 8.8_

- [ ] 9. Result routes
  - Add `status`, `pdf` and page `print` routes, reading the latest
    `report.json` and outputs.
  - Tests: each page state and PDF state, warnings carried from page
    reports, 404 before the first run, the previous print image and PDF served
    during a run.
  - _Requirements: 9.1–9.4_

- [ ] 10. Update the README
  - Mark the upload server as working; document installing, creating a
    token, configuration, the systemd unit, and the Tailscale and Caddy TLS
    setups.
  - _Requirements: 1, 2, 11_
