# Design: shadow detection and removal

## Overview

Shadows are handled in two places, matching where they can be fixed:

- **Where shots overlap**, a comparison cue finds the shot that is shadowed,
  and fusion stops preferring it. This changes the composed image, and is
  the part that fixes most real shadows: Clark takes two or more shots of
  each page, and a hand or phone shadow rarely falls in the same place twice.
- **Where only one shot covers the page**, or every shot has the same shadow,
  a single-image cue finds the shadow and the page metadata reports it. The
  image is left as it is.

```
warped shots ─▶ fuse()
                 │ tone match (as today)
                 │      │
                 │ comparison shadow cue per shot ──┐  (shadow.py)
                 │      │                           │
                 │ re-fit tone without shadow px    │  only if any shadow
                 │      │                           │
                 │ glare excess vs darkest UNSHADOWED shot
                 │ weights = feather · glare_w · shadow_w
                 ▼                                  │
         composed page                              │
                 │                                  ▼
       _assess(): single-image shadow cue, grow comparison shadows into
                  single-shot area, residual = Σ wᵢ·sᵢ / Σ wᵢ
                 │
       shadow regions ─▶ `shadow` warning ─▶ page metadata
```

Everything runs on the fusion grid (long side ≤ 1000 px), like the glare
detector. A new module `shadow.py` holds the cues; `fuse.py` and
`pipeline._assess` call it.

## Key decisions

### Shadow and glare look different when you compare them

Both make one shot differ from another, so the darker-wins rule cannot tell
them apart. Physically they differ:

- A **shadow** removes part of the light reaching the page. The print's
  reflectance is unchanged, so in linear light every channel and every
  pixel in the shadow is scaled by the same factor `k < 1`. Colour
  (chromaticity) and the relative texture are kept.
- **Glare** adds roughly white light on top of the print. In linear light it
  adds about the same amount `c` to every channel, which raises dark channels
  proportionally more, washes colour out and lowers relative contrast.

So within a small window, with shot *i* and the reference *R* (the other shot,
or the per-pixel median of the others) both tone-matched and converted to
linear light, fit `R ≈ a·I + b` over the window's pixels and channels:

| case                         | a        | b      |
|------------------------------|----------|--------|
| same light                   | ≈ 1      | ≈ 0    |
| shot *i* shadowed            | ≈ 1/k > 1| ≈ 0    |
| reference has glare          | ≈ 1 or less | > 0 |
| shot *i* has glare           | ≈ 1 or less | < 0 |

This is a local linear regression, done with box filters like a guided
filter: `a = cov(I, R) / var(I)` and `b = mean(R) − a·mean(I)`, which costs a
handful of box filters per shot.

On flat content `var(I)` is too small for the fit. There the cue falls back to
the per-channel ratios `k_c = mean(R_c) / mean(I_c)`: a shadow gives the same
ratio in every channel, glare in *R* gives larger ratios in *I*'s darker
channels. On flat grey or white content the two are indistinguishable, so
there the cue also requires the reference's single-image glare score (veil
and desaturation from `glare.glare_features`) to be below `weak`.

The comparison is done in linear light (inverse sRGB after tone matching),
since the camera's tone curve bends additive glare towards looking
multiplicative. The tone curves are close to power laws, which keep a shadow
multiplicative, so the shadow side of the test is not sensitive to them.

### The comparison cue

For shot *i* at grid pixel *p*, with the window of `window_frac` (1.5%) of
the long side:

```
depth   = 1 − mean(I) / mean(R)                 # in luminance, linear light
fit     = clip(1 − |b| / (b_tol · mean(R)), 0, 1)        where var(I) is enough
        = clip(1 − spread(log k_c) / chroma_tol, 0, 1)   elsewhere
s_raw   = clip((depth − min_depth) / depth_ramp, 0, 1) · fit
s       = s_raw · (1 − glare_ref)               # glare_ref: reference's glare cue
```

`s` then goes through the same hysteresis the glare detector uses: seeds at
`seed` (0.7), grown into connected pixels at `weak` (0.3), seeds narrower than
`min_seed_px` dropped, regions smaller than `min_area` dropped. Growth uses a
lower depth threshold (`min_depth / 3`), which follows the penumbra out to
where the shadow fades, so no dim rim is left (Requirement 1.4).

Penumbrae of a hand or phone held 30-50 cm above the page under room lights
are wide, 1-5% of the page. The window is smaller than that, so the fit sees
a slowly varying `k` and stays close to multiplicative within each window.

### Fusion

Today, at each grid pixel, `lum_min` is the darkest covering shot and each
shot's glare weight falls with its excess over `lum_min`. With shadow maps
`Sᵢ` (after hysteresis):

```
lum_eff_i = lum_i  where Sᵢ < 0.5, else +inf
lum_min   = min_i lum_eff_i,  falling back to min_i lum_i where every covering shot is shadowed
glare_w   = as today, from the excess over this lum_min
shadow_w  = 1 − Sᵢ_dilated   (1 where every covering shot is shadowed)
w         = (feather · glare_w · shadow_w) ** sharpness + 1e-8
```

`Sᵢ_dilated` uses the same halo as glare (`halo_px` plus the blur reach),
so that smoothing the weights does not let the shadow back in at its rim.

Where shot *i* is shadowed and the reference has glare at the same spot (the
conflict in Requirement 2.4), `Sᵢ` is 0 because of the glare veto, so fusion
keeps darker-wins. `s_raw` is still high there, and the residual shadow is
computed from `s_raw`, so the spot is reported.

When every `Sᵢ` is empty, `fuse` takes today's code path unchanged, which
gives the byte-identical output required by Requirement 2.6 and the cheap
test of Requirement 6.4. `FuseParams.shadow.enabled = False` does the same
everywhere.

### Tone matching

`_estimate_gains` takes the mode of per-pixel ratios, so a shadow covering
less than about a third of the overlap barely moves it. `_fit_curve` fits on
pixels that agree within 8%, and a shadow of depth ≥ 0.12 is mostly outside
that already. So the order is: tone as today, shadow maps, then, only when
any shadow was found, re-fit the tone with shadowed pixels removed from the
fitting masks and recompute the shadow maps once. Shadows are not excluded on
a first pass because finding them needs tone-matched shots.

### The single-image cue

From one shot there is no clean reference, so the cue looks for what a
shadow does to the image on its own. This is the classic intrinsic-image
approach: split the image's log-luminance gradients into illumination and
reflectance, rebuild the illumination from its gradients, and look for dips
in it.

A gradient counts as illumination when it is

- **soft**: its magnitude at the penumbra scale (Gaussian σ of 1% of the long
  side) is at least `soft_ratio` of its magnitude at the fine scale, which
  sharp print edges fail, and
- **achromatic in log space**: the log-gradients of the three channels agree
  within `chroma_tol`, which edges between differently coloured content
  fail, and
- **texture-preserving**: the local contrast of log-luminance (standard
  deviation in a small window) is about the same on both sides.

The illumination log-image is rebuilt from the gradients that pass, with the
others set to zero, by a Poisson solve on the grid (DCT, with the shot's mask
as Neumann boundary). The shadow cue is the depth of this illumination below
its own smooth upper envelope (a low-order surface fitted to its top
percentiles, which absorbs vignetting and the lamp's falloff), mapped to
0..1 like the comparison cue.

It is weighted cautiously, as the glare detector is. A false `shadow` warning
sends the user back to re-shoot a good page, and from one shot a dark mat
with a soft vignette looks a lot like a shadow. It reports on its own only
from strong seeds (≥ 0.7). Its main use is growth: a shadow found by
comparison is grown into the part of the same shot no other shot covers,
following this cue at `weak`, so a shadow that crosses out of the overlap is
reported in full (Requirement 3.2).

Spine shading on a curved page is a soft, achromatic dip too, lying along
the binding edge in every shot. The cue ignores dips whose region runs along
most of one page edge and whose depth falls away from that edge. Once page
curvature lands (PR #19), this uses its fitted binding side instead.

### Residual shadow and the report

Residual shadow follows the actual blend, as residual glare does:
`residual = Σ wᵢ · max(s_rawᵢ, singleᵢ_grown) / Σ wᵢ`. For a shot fusion
pushed out of a shadow, `wᵢ` is near 0 there and the shadow does not count.

`regions.glare_regions` becomes a general `cause_regions(residual, …,
causes=("single_shot", "all_shots_glared"))`, called once for glare and once
with `("single_shot", "all_shots_shadowed")`. `page_warnings` gains a
`shadow` warning built like the glare one:

- `single_shot`: "A shadow remains on 3.1% of the page (bottom-right). Only
  one photo covers it; add a shot of that area without the shadow."
- `all_shots_shadowed`: "A shadow remains on 3.1% of the page (bottom-right).
  Every photo has it; hold the phone so that you and it are not between the
  light and the page."

`PageWarning.code` gets `shadow`. The album step and the server already pass
page warnings through by code and message, so they need no change beyond the
docs.

### Module layout

- `shadow.py` (new): `ShadowParams`, `comparison_shadow(toned, masks, glare_ref,
  p)`, `single_shadow(toned, mask, p)`, `grow(seeds, cue, p)`.
- `fuse.py`: `FuseParams.shadow: ShadowParams`; `FuseResult` gains
  `shadow_small` (the maps used for weights) and `shadow_raw_small`.
- `WEIGHT_MAX_SIDE` moves from `fuse.py` to a small `grid.py`, since `fuse`
  now needs `glare.glare_features` and `glare` imports `fuse` for that
  constant. `fuse` re-exports it so existing imports keep working.
- `pipeline._assess`: the single-image cue, growth, residual, regions and the
  `shadow` section of the report. `detector` gains `shadow: asdict(params)`.
- `regions.py`: `cause_regions`, the `shadow` warning.
- `synth.py`: `_add_shadow`, `shadow=` on `make_shots` and `make_case`.
- `cli.py`: `--no-shadow` on `albumproc page`, and the shadow maps in
  `--debug` output (`shadow_00.png`, …, `shadow_residual.png`).

### Parameters (defaults)

| name           | default | meaning |
|----------------|---------|---------|
| `enabled`      | True    | off gives today's output exactly |
| `window_frac`  | 0.015   | comparison window, fraction of the long side |
| `min_depth`    | 0.12    | shallower differences are not shadow |
| `depth_ramp`   | 0.10    | depth above `min_depth` for a full cue |
| `b_tol`        | 0.06    | allowed offset in the linear fit, fraction of `mean(R)` |
| `chroma_tol`   | 0.08    | allowed spread of per-channel log ratios |
| `seed`, `weak` | 0.7, 0.3| hysteresis, as glare |
| `min_seed_px`  | 5       | narrower seeds are dropped |
| `soft_ratio`   | 0.6     | single-image: coarse/fine gradient ratio for a soft edge |
| `single_weight`| 0.5     | single-image cue scale, fitted by `tools/fit_shadow.py` |

The values above are starting points; `tools/fit_shadow.py` sets
`depth_ramp`, `b_tol`, `chroma_tol` and `single_weight` from the synthetic
cases, and the real pages check them.

## Testing

Synthetic shadows (`synth._add_shadow`): a silhouette made of a capsule (arm)
and an ellipse with finger capsules (hand), or a rounded rectangle (phone),
entering from a page edge, blurred to a penumbra of 0.5-3% of the long side,
multiplied in linear light by `1 − depth` with depth 0.2-0.6 and a slight
colour tint (±3%) for ambient light of another colour. Ground truth is the
silhouette mask at half depth. `make_case(shadow=...)` takes `none` (default,
so existing tests are unchanged), `one` (one shot shadowed), `all` (same
shadow in every shot) and `part` (shadow in a part only one shot covers, on
`kind="stitch"`).

Unit tests (`tests/test_shadow.py`):

- the linear fit on constructed windows: a pure scale gives `b ≈ 0`, a white
  offset gives `a ≈ 1, b > 0`, and the flat-content fallback,
- the glare veto: a darker shot opposite a glared shot is not shadowed,
- byte-identical output on every existing synthetic case with shadows on and
  off, and no `shadow` warning,
- recall ≥ 0.9 and false positives ≤ 0.5% in overlaps on `shadow="one"`,
  and the composed page within 3 grey levels of the shadow-free fusion inside
  the shadow mask,
- glare cases with an added shadow in another shot: residual glare no worse,
- `shadow="part"`: a `single_shot` shadow warning at the right location;
  `shadow="all"`: `all_shots_shadowed`,
- `clean_white` and pages with black mats and dark prints: no warning from
  the single-image cue.

Real pages: the 19 glare-training pages and 5 older pages in
`crawlins/photo-album-archiver-data` are run through `albumproc page` with
shadows on and off. Pages without a visible shadow must give no `shadow`
warning and an unchanged image. A few pages shot on purpose with a hand or
phone shadow in one of two shots check the fusion change. Results go in a
short notes file in the data repository; no real photo or crop goes into the
public repository.
