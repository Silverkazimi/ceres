"""/: a staged, unsigned lock change shows its target padlock in warn orange, pulsing like the badge clock, in the board cell."""
from pathlib import Path

STATIC = Path(__file__).parents[2] / "ceres" / "static"


def test_clock_is_warn_orange_pulses_and_is_on_the_shared_timeline():
    css = (STATIC / "ceres.css").read_text(encoding="utf-8")
    assert "td.lock-cell .plot-lock-icon .plot-lock-staged { color: var(--warn); animation: lock-clock-pulse 1.3s ease-in-out infinite; }" in css
    keyframes = css[css.index("@keyframes lock-clock-pulse"):][:330]
    assert "var(--warn)" in keyframes and "var(--gold)" not in keyframes and "#" not in keyframes
    reduced = css[css.index("@media (prefers-reduced-motion: reduce)", css.index("@keyframes lock-clock-pulse")):][:300]
    assert "plot-lock-staged" in reduced and "animation: none" in reduced
    assert "'lock-clock-pulse'" in (STATIC / "pulse-sync.js").read_text(encoding="utf-8")


def test_the_pulse_has_the_same_rhythm_as_the_badge_clock():
    css = (STATIC / "ceres.css").read_text(encoding="utf-8")
    assert "animation: badge-clock-pulse 1.3s ease-in-out infinite" in css
    assert "animation: lock-clock-pulse 1.3s ease-in-out infinite" in css
