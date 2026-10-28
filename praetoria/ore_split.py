""" (Land 2.0 readiness, Phase 0): expected ore mix of one Ore Mine harvest by the plot's base PP.

Pure math, wired to no screen. Status: **announced, not released**. Source: the Phase 2 whitepaper page
"Mining Ores" (revision 2026-09-14, re-read 2026-10-28; `library/land-phase-2-praetoria.md`, block of
2026-10-27). One Ore Mine keeps the live Iron rate (0.0005 ore per PP per hour) for its total; each harvest
splits that total at random across five ores, least rare first, summing to 100%:

    Ore        baseline (0 base PP)   at 100k base PP   base-PP threshold
    Iron       70-90%                 50-70%            0
    Tin        10-25%                 20-35%            0
    Copper      0-10%                  5-15%            0
    Rhodium     0%                     3-10%            25,000
    Vanadium    0%                     1-3%             50,000

"As your base PP increases from 0, the minimum and maximum of the range interpolates linearly towards the
upper ranges defined by the 100k base PP min & max. Note that interpolation from the base range does not
begin until a certain base PP threshold is reached for each ore." Two readings ( plan §B.4):

- : the ramp runs from the threshold to 100k (no jump).
- : the ramp runs from 0 but only switches on above the threshold (a jump at the threshold).

Threshold modes:
- "official": the 25k / 50k thresholds of the page above (default; still on the page 2026-10-28).
- "none": maintainer-reported  (Discord weekly land update, 2026-10-27): thresholds removed, chances still
  rise with base PP. With no threshold  and  are the same line from 0 to 100k.

The output is the published RANGE per ore (min, max) and its midpoint, in percent. It is not a probability
model: the page publishes neither the pick distribution nor how "must sum to 100%" narrows the later picks
(the 100k midpoints add up to 106%), so a real Rhodium/Vanadium share is probably below its midpoint.
"""

from __future__ import annotations

import math
from typing import Any

ORES: tuple[str, ...] = ("IRON", "TIN", "COPPER", "RHODIUM", "VANADIUM")  # least rare -> most rare
TOP_BASE_PP = 100_000.0  # the 100k column; also the worksite's base-PP cap, so nothing goes past it
# ore: (baseline min, baseline max, 100k min, 100k max, official threshold), percent and base PP
TABLE: dict[str, tuple[float, float, float, float, float]] = {
    "IRON": (70.0, 90.0, 50.0, 70.0, 0.0),
    "TIN": (10.0, 25.0, 20.0, 35.0, 0.0),
    "COPPER": (0.0, 10.0, 5.0, 15.0, 0.0),
    "RHODIUM": (0.0, 0.0, 3.0, 10.0, 25_000.0),
    "VANADIUM": (0.0, 0.0, 1.0, 3.0, 50_000.0),
}
THRESHOLD_MODES = ("official", "none")
READINGS = ("R1", "R2")


def _fraction(base_pp: float, threshold: float, reading: str) -> float:
    """How far along the baseline -> 100k ramp a plot is, 0..1."""
    if base_pp <= threshold:
        return 0.0
    if reading == "R1":
        return (base_pp - threshold) / (TOP_BASE_PP - threshold)
    return base_pp / TOP_BASE_PP


def ore_split_expectation(base_pp: Any, thresholds_mode: str = "official", reading: str = "R1") -> dict[str, dict[str, float]]:
    """Expected share range per ore for a plot's base PP: {ore: {"min", "max", "mid"}} in percent.

    `base_pp` below 0 counts as 0 and above 100k as 100k (the cap). Raises ValueError for a non-numeric
    base PP or an unknown mode/reading."""
    if thresholds_mode not in THRESHOLD_MODES:
        raise ValueError(f"thresholds_mode must be one of {THRESHOLD_MODES}")
    if reading not in READINGS:
        raise ValueError(f"reading must be one of {READINGS}")
    if isinstance(base_pp, bool):
        raise ValueError("base_pp must be a number")
    try:
        pp = float(base_pp)
    except (TypeError, ValueError):
        raise ValueError("base_pp must be a number") from None
    if not math.isfinite(pp):
        raise ValueError("base_pp must be finite")
    pp = min(max(pp, 0.0), TOP_BASE_PP)
    out: dict[str, dict[str, float]] = {}
    for ore in ORES:
        base_min, base_max, top_min, top_max, threshold = TABLE[ore]
        f = _fraction(pp, threshold if thresholds_mode == "official" else 0.0, reading)
        low = base_min + (top_min - base_min) * f
        high = base_max + (top_max - base_max) * f
        out[ore] = {"min": low, "max": high, "mid": (low + high) / 2}
    return out
