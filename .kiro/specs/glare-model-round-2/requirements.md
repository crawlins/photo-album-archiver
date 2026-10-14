# Requirements: glare model, round 2

## Introduction

The learned single-shot glare detector (`--detector net`, PR #22) finds 10%
of the glare on real pages it was not trained on: most of the clipped
streaks and spots (62% on the page with those), almost none of the soft
veils that spread over a third or half of a page (2% on the page with one).
It was trained on 15 real pages. The single-shot glare spec
(`.kiro/specs/single-shot-glare/`, PR #28) compares finding that glare with
preventing it by a second shot, recommends the second, and sets the bar a
retrained detector must clear to become the default. This spec is the plan
for that retraining: the data to shoot, how to shoot it so that it teaches
the model, the model change that makes spread veils learnable, how the
result is measured, and what to expect.

The guarantee against glare stays the overlap check
(`.kiro/specs/overlap-coverage/`). A better detector adds value on pages the
user cannot re-shoot and lets prompts be skipped where no warning shows; it
does not replace the check.

### Glossary

Terms are as in the glare detector, overlap coverage and single-shot glare
specs. In addition:

- **Clipped glare**: a reflection of the lamp itself, washed out to white
  in the photo. Found well today.
- **Spread veil**: a reflection of a wide light (a window, a ceiling panel)
  that lifts a third or half of the page by 10 to 30 levels without
  clipping. Missed today.
- **Round**: one pass of collecting pages, generating overlap labels,
  training and measuring on held-out pages.
- **Held-out album**: an album none of whose pages are used in training,
  on which the round is measured.

## Requirements

### Requirement 1: Data from ordinary shooting

**User story:** As someone archiving albums, I want every album I shoot to
count as training data without extra work, so that the detector improves
as the archive grows.

#### Acceptance criteria

1. THE system SHALL generate overlap labels (`tools/real_glare_data.py`)
   from any page in an album on the server with two or more overlapping
   shots, with no hand labelling.
2. THE labelling tool SHALL accept a server album folder
   (`~/.local/share/albumserver/albums/<id>/photos/`) as it is, so that an
   album shot with the app is used in place without copying.
3. THE labelled pages SHALL be kept, per album, in the private repository
   `crawlins/photo-album-archiver-data` and never in the public one.
4. THE labelling tool SHALL record, per page, the share of the page with
   glare labels, with clean labels and with no label, so that the data a
   round trains on can be described.

### Requirement 2: What to shoot

**User story:** As someone shooting an album for training, I want to know
what makes a page useful, so that the time spent at the album teaches the
model.

#### Acceptance criteria

1. A page SHALL be shot with at least two shots covering its whole area,
   as the overlap check already asks for.
2. WHERE a spread veil is visible on the page, the second shot SHALL be
   taken from a clearly different angle (the phone tilted 15 to 20 degrees,
   or the photographer a step to the side), so that the veil moves and the
   comparison labels it. A sideways shift at the same angle leaves the veil
   in place and labels nothing.
3. A round SHALL include pages with no glare at all (matte pages, or shot
   under diffuse light), so that false alarms are measured on real pages.
4. A round SHALL aim for at least 300 pages in total and at least 100 pages
   showing a spread veil, because a veil is one example per page.
5. Rounds after the first SHALL add albums shot in a different place
   (another room, another kind of sleeve) before adding pages from the same
   one, because one album is one sleeve type and one lamp.
6. THE README SHALL carry this guidance in a short section the user can
   read before shooting.

### Requirement 3: A model that sees the whole page

**User story:** As the developer, I want the detector to judge brightness
against the whole page's lighting, so that a veil over half the page is
not read as the page being lit from one side.

#### Acceptance criteria

1. THE detector SHALL take, beside the full-resolution patch it sees today
   (about a fifth of the page across), a coarse view of the entire shot
   (the shot shrunk so its long side is about 128 px), fused into the
   decoder, so that each output pixel can compare its surroundings with the
   page as a whole.
2. THE detector SHALL take the shot's low-frequency brightness (a wide
   blur of its luminance) as an input channel, so that lighting fall-off
   across the page is modelled rather than mistaken for sheen.
3. THE training set SHALL include synthetic white paper and white print
   borders as hard negatives, so that the net stops flagging them and can
   be considered as the default.
4. THE exported model SHALL keep running through `cv2.dnn` with no new
   runtime dependency, at no more than 0.3 s per shot on the server.
5. THE existing net SHALL stay available as `--detector net` until the new
   one replaces it, and the hand-made `features` detector SHALL stay as it
   is.

### Requirement 4: Measurement and the decision after a round

**User story:** As the developer, I want each round measured the same way,
so that the numbers say whether more data or a different model is the next
step.

#### Acceptance criteria

1. A round SHALL be measured on a held-out album the model was not trained
   on, with recall (share of glare-labelled pixels found) and false alarms
   (share of clean-labelled pixels flagged) reported overall and per page,
   and separately for pages with clipped glare and pages with spread veils.
2. THE thresholds SHALL be chosen on validation pages from the training
   albums, never on the held-out album, and SHALL keep false alarms on at
   most 0.2% of clean area.
3. THE round's numbers SHALL be recorded in `tools/train_glare_net.py`'s
   docstring and the README, with the albums trained on and held out.
4. WHEN a round reaches the bar of the single-shot glare spec (recall at
   least 0.5 on the held-out album, false alarms at most 0.2%, no `glare`
   warning on the synthetic `clean_white` pages), THE net SHALL become the
   default detector in that round's PR.
5. WHEN a round lands at the top of the expected ranges below without
   reaching the bar, the next round SHALL add an album from a different
   place; WHEN it lands at the bottom, the model SHALL be revisited before
   more data is collected.

### Requirement 5: Expectations

These are judgements made before the data exists, recorded so that a round
can be compared with them.

1. Clipped glare: 60 to 80% found (62% today on the page that has it).
2. Spread veils: 30 to 60% found (2% today), on the first round with the
   whole-page model and at least 100 veiled pages.
3. Overall: 40 to 60% of labelled glare found, with false alarms under
   0.2%. The false-alarm limit sets the recall; the model is tuned to the
   limit and keeps whatever recall that leaves.
4. Out of reach: veils fainter than about 10 levels, which no shot can
   label; veils both shots carry in the same place, which never get a
   label; and 80% in one round.
