# Design: glare model, round 2

## Overview

Round 1 (PR #22) showed what overlap labels can and cannot teach a small
local model: clipped glare, yes; spread veils, no, because a patch a fifth of
the page across cannot tell a veil over half the page from the page's own
lighting. Round 2 changes the model so that it can, and sets out what to
shoot so that the labels contain the veils in the first place. Everything
else (label generation from overlapping shots, training outside the package,
ONNX export, running through `cv2.dnn`) stays as it is.

```
album on the server ──▶ real_glare_data.py (overlap labels per shot)
                                   │
          train_glare_net.py: local branch (as now) + whole-page branch + low-frequency brightness
                                   │
          thresholds on validation pages ──▶ held-out album: recall, false alarms, by glare kind
                                   │
                 numbers recorded ──▶ default detector if the bar is met (single-shot glare spec)
```

## Key decisions

### The data comes from shooting, not labelling

Every page shot under the overlap guarantee is labelled by the comparison
between its shots. The only thing the user does differently for training is
the angle of the second shot where a veil is visible (Requirement 2.2),
because a wide reflection only moves with the angle. This keeps the cost of
data at zero beyond the archive the user wants anyway, and it is why the
plan asks for albums rather than photographs of glare.

### The whole page as a second input

A veil is a low-frequency change; the page's lighting is one too. The two
differ in what they do to the print under them (a veil washes colour out
and flattens texture, lighting does not) and in where they sit on the page
(a veil follows the sleeve's wrinkles, lighting falls off smoothly). A local
patch sees the first, not the second. The whole-page branch, a small
encoder on the shot at about 128 px fused into the decoder at its coarsest
level, gives every output pixel the page's layout; the low-frequency
brightness channel gives it the lighting explicitly, so the net learns
"brighter than the lighting here" rather than "brighter than nearby".

### Hard negatives from synthetic pages

The net is not the default because it flags white paper and print borders
on synthetic pages. Real albums rarely have pure white paper, so the real
labels do not teach that; the synthetic `clean_white` generator does, cheaply,
and training on it alongside the real pages is what lets the net be judged
for the default.

### Held-out by album, thresholds by validation

Pages of one album share a sleeve, a lamp and a print style. Holding out
pages of the training album flatters the numbers; holding out an album does
not. Thresholds are chosen on validation pages from the training albums so
the held-out album is read once.

### Expectations are written down first

Requirement 5 is a forecast. Recording it before the round makes the
decision after the round (Requirement 4.5) a comparison rather than a
reinterpretation.

## Components

### `tools/real_glare_data.py` (extended)

- Accepts an album folder: `--album <dir>` labels every page folder under
  `photos/` and names each output by the page's number from the album's
  `report.json` when present, else by folder name.
- Prints and writes `labels.json` beside the `.npz` files: per page, the
  share of glare, clean and unlabelled pixels and the number of shots.

### `tools/train_glare_net.py` (extended)

- `Net2`: the current `Net` plus a whole-page branch (the input shrunk to a
  long side of 128 px, three strided convolutions, upsampled and
  concatenated at `e3`) and a fourth input channel, the luminance blurred
  with a sigma of about a tenth of the long side.
- `--synthetic DIR`: adds synthetic `clean_white` shots as negatives.
- `--holdout-album NAME` and `--val-album NAME`; the evaluation reports by
  page and by glare kind (a page counts as veiled when its glare labels
  cover more than 10% of it in one connected area).
- `export` carries the extra input through ONNX; `albumproc.glare.net_score`
  builds the channel the same way (same blur on the same grid).

### `albumproc.glare` (small change)

- `net_score` computes the low-frequency channel and the 128 px view when
  the model asks for them (an ONNX model with two inputs), so a round-1
  model keeps working.

### README

- A short "Shooting for training" section: two shots everywhere, a real
  angle change where a veil shows, some glare-free pages, albums from
  different places.

## Testing strategy

- Label tool: an album folder with `report.json` names outputs by page
  number; `labels.json` sums to one per page.
- Net2 shapes: a random input of any grid size gives an output of that
  size; export and `cv2.dnn` agree with torch to 1e-4.
- `net_score` with a two-input model builds the extra inputs and matches
  the training code's on a known image.
- Synthetic `clean_white` pages raise no `glare` warning with the new net
  before it can be made the default (existing test, run with
  `--detector net`).
- The held-out measurement itself is not a test; its numbers go in the
  docstring and the README.

## Out of scope

- Collecting the album: that is the user's shooting, guided by the README.
- Changing the overlap check, the prompts or the app.
- Removing glare the detector finds; it is reported, as before.
