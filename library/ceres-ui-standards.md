# Ceres UI standard (public extract)

The full Ceres-wide UI standard is a maintainer document that is not part of the public export. This extract
is what the public repository needs from it: the rules the guard `tests/ceres/test_ui_standard.py` enforces,
written from the guard itself, and the index of every rule id the living style guide (`/styleguide`,
`ceres/static/styleguide.js`) names. Nothing here is new policy. Where the text of a rule is not in the
repository, the index says so instead of guessing; the maintainer's full document wins wherever the two differ.

Scope: desktop presentation code. Phone rules (`html.ceres-lw-le-1279 …`, `mobile-plot.css`) are a separate
design and are constrained here only by P-0.

## Rules the guard enforces

**C-1** — Colour comes from tokens: `var(--token)` or a `color-mix()` of tokens, never a raw colour. The raw
colours that predate the rule are the reviewed allowlist of `tests/ceres/test_colour_tokens.py`
(`tests/ceres/fixtures/ceres-colour-allowlist.json`).

**C-4** — One meaning per state colour (the meaning column of the style guide's colour tokens):
`--ok` possible, positive, unlocked, gain, selected filter, in cart, bought; `--warn` pending, staged, cooldown,
your listing, price rose; `--danger` blocked, negative, locked, cost, destructive, sold, close; `--info` neutral
info; `--gold` golden badges, waiting badge, top pick, splinter ring.

**T-3** — The type scale. A desktop `font-size` is one of these (a new size is added to the standard first):

| Step | Value | Use |
|---|---|---|
| fs-h-display | `2rem` / `var(--step-2)` | display: start page |
| fs-h-panel | `1.25rem` | panel heading |
| fs-body | `1rem` | running text and comfortable controls |
| fs-table | `clamp(.875rem, .75rem + .15625cqi, .9375rem)` | plot table body (frozen) |
| fs-ledger | `.9rem` | Harvest all ledger text |
| fs-control | `.8rem` (`var(--ceres-control-font-size)`) | every control: buttons, dropdowns, inputs, pills |
| fs-label | `.75rem` | pill labels, state text |
| fs-caption | `.7rem` | caption: the readable minimum (11.2px) |

`inherit` is always allowed.

**E-1** — Corner radii. A desktop radius is one of: `8px` (controls), `var(--radius)` (panels and tiles, per
Iris theme), `calc(var(--radius) - 3px)` (nested inside a panel), `5px 0 0 5px` (Iris edge chips), `999px`
(pills, scrollbar thumbs), `50%` (avatars, dots), `0` or `inherit`.

**K-1** — Every button is a `.ceres-btn` (treatments, densities and cases in `buttons.css`; the contract is
`tests/ceres/fixtures/ceres-button-contract.json`) unless it belongs to a registered special family (K-7).

**K-2** / **K-3** — A text input takes its geometry and type from the control tokens (`--ceres-control-*`; the
one shared definition is `.ceres-wide-input` in `ceres.css`). A surface may set its width, never its own text
size, weight, height, vertical padding or corner radius.

**K-7** — A button outside `.ceres-btn` is a registered special family (for example the tile remove control,
`.slot-remove`); an unregistered one is a K-1 deviation.

**M-4** — Fixed and sticky chrome (edge tabs, wallet and DEC drawer, badge rail and flag, Iris theme picker,
scroll-to-top, page header, filter row, sticky table head, and anything `position: fixed`/`sticky`) animates
only `opacity` and `transform`: no transition or animation of `box-shadow`, `filter`, `backdrop-filter` or
`all`. A glow is a pseudo-element whose opacity fades.

**M-5** — Every keyframe animation that is used has a `prefers-reduced-motion: reduce` answer; keyframes nothing
uses are deleted.

**P-0** — Phone rules live only under `html.ceres-lw-le-1279` (or a `max-width` query) and in
`mobile-plot.css`; every rule in the phone stylesheet is phone-scoped, so a phone rule can never change a desktop
value.

**W-2** — Named in the guard's list of enforced rules; its text is not in the public extract.

## Frozen surfaces

- `canonical.css` is the vendored Iris stylesheet: pull-only, never edited here, and excluded from the value
  ratchets.
- The plot table's type step (`fs-table`) is frozen.
- The phone design (P-0) is a separate design and is not constrained by the desktop rules.

## Defaults used (maintainer may overrule)

The defaults section of the full standard is not in the public extract.

## How the standard is enforced

- `tests/ceres/test_ui_standard.py` checks C-1 (beyond the colour allowlist), M-4, M-5, T-3, E-1, K-2/K-3,
  K-1/K-7 and P-0 against the dated baseline `tests/ceres/fixtures/ceres-ui-standard-baseline.json`. The baseline
  is a shrinking ratchet: a new deviation fails, and the change that fixes a deviation also removes its entry. A
  maintainer-approved exception is written into the standard and the baseline in the same commit. It also checks
  that this document carries every rule the guard enforces and the T-3 and E-1 values the guard uses.
- `tests/ceres/test_colour_tokens.py` is the C-1 raw-colour scanner and its reviewed allowlist.
- `tests/ceres/test_styleguide.py` checks that the style guide names only rule ids listed here.

## Rule index (ids the style guide names)

| Id | Where the style guide uses it | Text |
|---|---|---|
| C-1 | colour tokens | above |
| C-2 | colour tokens | not in the public extract |
| C-3 | territory plaques, terrain colours (`--terrain-*`) | not in the public extract |
| C-4 | colour tokens, market, card menu, checkout | above |
| C-6 | colour tokens | not in the public extract |
| C-7 | colour tokens | not in the public extract |
| D-2 | glass menu buttons: tint and light edge, no blur | not in the public extract |
| D-3 | Plots, Cards switcher | not in the public extract |
| E-2 | `--shadow`: shadows and backdrops | not in the public extract |
| E-3 | plot worker tiles | not in the public extract |
| I-2 | plot worker tiles | not in the public extract |
| I-4 | Plots, Cards switcher: always the last header control | not in the public extract |
| K-1 | buttons, header | above |
| K-2 | buttons, filter controls | above |
| K-3 | filter controls | above |
| K-4 | filter controls | not in the public extract |
| K-5 | filter controls | not in the public extract |
| K-6 | tabs (`.ceres-tabs`) | not in the public extract |
| K-6a | button cases: destructive, commit, commit and destructive, signal | not in the public extract |
| K-7 | buttons: tile remove | above |
| K-8 | filter controls; `--border` hairlines | not in the public extract |
| L-1 | filter controls, glass surfaces | not in the public extract |
| L-3 | plot worker tiles, My cards tiles | not in the public extract |
| L-4 | filter controls | not in the public extract |
| L-5 | plot worker tiles | not in the public extract |
| L-6 | loading spinner and skeleton tiles | not in the public extract |
| M-1 | buttons | not in the public extract |
| M-2 | plot worker tiles | not in the public extract |
| M-3 | buttons, loading | not in the public extract |
| M-4 | glass surfaces, header | above |
| Q-11 | glass surfaces | not in the public extract |
| T-1 | faces: `--font-body`, `--font-display` (panel headings only), `--font-mono` (ids, plot numbers, tx hashes only) | not in the public extract |
| T-2 | weights: 400 body; 600 dropdown value, text inputs, emphasis; 700 buttons, table header, labels; 800 display numerals | not in the public extract |
| T-3 | type scale | above |
| T-4 | numbers: right-aligned, tabular, two decimals, "—" for no value | not in the public extract |
| T-5 | typography | not in the public extract |
| W-1 | `--bg2` panel surface, `--border-bright` panel edge | not in the public extract |
| W-4 | one tooltip engine (`#ceres-tooltip`) | not in the public extract |
