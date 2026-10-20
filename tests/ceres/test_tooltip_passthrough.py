""" (finishing it): with a mouse a tooltip never blocks what lies under it; touch keeps its dismiss-by-tap box."""
from pathlib import Path

CSS = (Path(__file__).parents[2] / "ceres" / "static" / "ceres.css").read_text(encoding="utf-8")


def test_a_tooltip_never_catches_the_pointer_except_when_a_touch_long_press_raised_it():
    """ (maintainer: a tooltip blocked the login box; every tooltip must end the moment the pointer leaves the area that raised it, Ceres-wide)."""
    js = (Path(__file__).parents[2] / "ceres" / "static" / "tooltip.js").read_text(encoding="utf-8")
    assert "#ceres-tooltip { pointer-events: none; }" in CSS and "#ceres-tooltip.is-touch { pointer-events: auto; }" in CSS
    assert "@media (hover: hover) and (pointer: fine) { #ceres-tooltip" not in CSS      # no device guess: a hybrid laptop must not get a catchable tooltip
    assert "(TOUCH_OPENED && TOOLTIP_EL.contains(event.relatedTarget))" in js            # moving onto the tooltip keeps it open only for touch
    assert "event.pointerType === 'mouse' && !TOOLTIP_EL.hidden) hideTooltip()" in js     # a mouse press ends it
    assert "TOUCH_OPENED = true;" in js and js.count("TOUCH_OPENED = false;") >= 3
