"""the "N Changes" badge uses the standard badge design: coloured text and border on a clear background, normal weight.
It used to be a solid gold fill with dark text and extra-heavy weight, the reverse of every other badge."""
from pathlib import Path

STATIC = Path(__file__).parents[2] / "ceres" / "static"


def test_no_special_fill_or_weight_for_the_changes_badge():
    colours = (STATIC / "badge-colours.css").read_text(encoding="utf-8")
    css = (STATIC / "ceres.css").read_text(encoding="utf-8")
    assert ".badge.badge-staged-changes.badge-group-special" not in colours
    assert ".badge.badge-staged-changes {" not in css
    assert "badge-staged-changes" not in colours


def test_the_special_group_is_the_outline_style_every_badge_shares():
    colours = (STATIC / "badge-colours.css").read_text(encoding="utf-8")
    block = colours[colours.index(".badge.badge-group-special {"):][:120]
    assert "color: var(--gold)" in block and "border-color: var(--gold)" in block and "background" not in block


def test_no_pending_badge_background_inside_an_open_slide_out():
    """ fixed the table strip only; an open slide-out still tinted these three badges gold."""
    css = (STATIC / "ceres.css").read_text(encoding="utf-8")
    assert ".badge-overlay-row.is-open :is(.badge-staged-changes" not in css
