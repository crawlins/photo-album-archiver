# Implementation plan: glare model, round 2

Tasks 1 and 2 are code and can be done before the album exists; task 3
needs the album on the server; task 4 is the round itself. Paths are under
`backend/`. Requirement numbers refer to `requirements.md`.

- [ ] 1. Data tooling and guidance
  - [ ] 1.1 `tools/real_glare_data.py --album DIR`: label every page of a
    server album in place, named by page number from `report.json`;
    write `labels.json` with the per-page shares.
    - _Requirements: 1.1, 1.2, 1.4_
  - [ ] 1.2 README section "Shooting for training" (two shots everywhere,
    angle change where a veil shows, glare-free pages, different places).
    - _Requirements: 2.1 to 2.6_
  - [ ] 1.3 Tests: album naming, `labels.json` sums.
    - _Requirements: 1.2, 1.4_

- [ ] 2. The whole-page model
  - [ ] 2.1 `Net2` in `tools/train_glare_net.py`: whole-page branch and the
    low-frequency brightness channel; `--synthetic` negatives;
    `--holdout-album`, `--val-album`; evaluation by glare kind.
    - _Requirements: 3.1, 3.2, 3.3, 4.1, 4.2_
  - [ ] 2.2 Export with two inputs; `albumproc.glare.net_score` builds them
    for a two-input model and keeps the round-1 model working.
    - _Requirements: 3.4, 3.5_
  - [ ] 2.3 Tests: shapes, export agreement, `net_score` input building,
    run time under 0.3 s per shot on the fusion grid.
    - _Requirements: 3.4_

- [ ] 3. The album
  - [ ] 3.1 Clark shoots an album under the guidance (300 pages, 100 with
    a veil, some glare-free) and a smaller second album elsewhere for the
    hold-out, or says which existing album is held out.
    - _Requirements: 2.1 to 2.5_
  - [ ] 3.2 Label both with `--album`, store the labels in the private
    repository, note the shares.
    - _Requirements: 1.3, 1.4_

- [ ] 4. The round
  - [ ] 4.1 Train `Net2` on the training album(s) plus synthetic
    negatives; choose thresholds on validation pages; measure on the
    held-out album by page and by glare kind.
    - _Requirements: 4.1, 4.2_
  - [ ] 4.2 Record the numbers in the docstring and the README against
    Requirement 5; apply Requirement 4.4 or 4.5.
    - _Requirements: 4.3, 4.4, 4.5_
