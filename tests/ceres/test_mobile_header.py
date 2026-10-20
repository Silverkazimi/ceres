"""mobile header polish (maintainer ruling 2026-09-19) -- narrower player
field pushed right, square left corners fully covered by the avatar, and a
larger board logo. Mobile tier only (html.ceres-lw-le-1919.ceres-lw-le-1279);
desktop rules are untouched.

mobile-plot.css loads AFTER ceres.css (index.html) and wins the cascade for
any selector both files declare at this tier -- the logo, avatar sizing, and
player-input narrowing/flex fix all live there; ceres.css must not carry a
competing declaration for those same properties."""
from pathlib import Path

STATIC = Path(__file__).parents[2] / "ceres" / "static"
SELECTOR = "html.ceres-lw-le-1919.ceres-lw-le-1279"


def _ceres_css() -> str:
    return (STATIC / "ceres.css").read_text()


def _mobile_plot_css() -> str:
    return (STATIC / "mobile-plot.css").read_text()


def test_mobile_logo_is_larger_in_the_file_that_actually_wins():
    mobile = _mobile_plot_css()
    assert f"{SELECTOR} .board-logo-img {{ block-size: 4.6875rem; }}" in mobile
    # ceres.css must not carry a competing (and therefore dead) rule for this
    # selector -- mobile-plot.css loads later and would silently shadow it.
    assert f"{SELECTOR} .board-logo-img" not in _ceres_css()
    # Desktop logo sizing (non-mobile rule) is untouched.
    assert ".board-logo-img { display: block; height: 75px; width: auto; }" in _ceres_css()


def test_mobile_player_avatar_sizing_lives_in_mobile_plot_css():
    mobile = _mobile_plot_css()
    assert f"{SELECTOR} .board-header .player-avatar {{" in mobile
    block = mobile[mobile.index(f"{SELECTOR} .board-header .player-avatar {{"):]
    block = block[: block.index("}") + 1]
    assert "inline-size: 3.25rem;" in block
    assert "block-size: 3.25rem;" in block
    # ceres.css keeps only the overlap margin (not shadowed, since
    # mobile-plot.css never sets margin-inline-end on this selector) -- it
    # must not re-declare inline-size/block-size, which would be dead.
    ceres = _ceres_css()
    assert f"{SELECTOR} .board-header .player-avatar {{ margin-inline-end: -1.15rem; }}" in ceres
    assert "2.35rem" not in ceres


def test_mobile_player_field_is_narrower_pushed_right_and_wins_the_cascade():
    ceres = _ceres_css()
    mobile = _mobile_plot_css()
    # The switch is pushed to the row's right edge in ceres.css; that rule
    # still declares flex: 0 0 auto there, but mobile-plot.css (below) loads
    # after it and overrides the flex value for this selector, so ceres.css's
    # own text is unchanged even though it no longer wins the cascade.
    assert f"{SELECTOR} .board-header .player-switch {{ flex: 0 0 auto; min-inline-size: 0; margin-inline-start: auto; }}" in ceres
    # (2026-09-20): mobile-plot.css overrides the switch's own flex
    # so it can shrink -- .board-header-row is pinned to inline-size: 100%
    # at this tier, and at 360px CSS px the switch previously refused to
    # give up any width (flex-shrink: 0), overflowing the header by ~11px.
    assert f"{SELECTOR} .board-header .player-switch {{" in mobile
    switch_block = mobile[mobile.index(f"{SELECTOR} .board-header .player-switch {{"):]
    switch_block = switch_block[: switch_block.index("}") + 1]
    assert "flex: 0 1 auto;" in switch_block
    assert "min-inline-size: 0;" in switch_block
    # The narrowing + flex fix live in mobile-plot.css, the file that actually
    # renders for this selector.
    assert f"{SELECTOR} .board-header .player-input {{" in mobile
    block = mobile[mobile.index(f"{SELECTOR} .board-header .player-input {{"):]
    block = block[: block.index("}") + 1]
    # flex: 0 0 auto (fixed, no shrink) is gone -- it could not give
    # up width at 360px CSS px and overflowed the header. flex: 1 1 8.5rem
    # with min-inline-size: 0 lets the field shrink below 8.5rem there while
    # max-inline-size: 8.5rem still caps it at 390/412px and up.
    assert "flex: 1 1 8.5rem;" in block
    assert "min-inline-size: 0;" in block
    assert "max-inline-size: 8.5rem;" in block
    assert "min-block-size: 2.75rem;" in block  # () raised it to a 44 px touch target
    assert "box-sizing: border-box;" in block
    # The stale wide/growing rule mobile-plot.css used to carry is gone.
    assert "flex: 1 1 5rem;" not in mobile
    # ceres.css no longer carries a competing player-input rule for this
    # selector (it would only ever partially apply and mislead a reader).
    assert f"{SELECTOR} .board-header .player-input {{" not in ceres


def test_mobile_player_field_left_corners_are_square_and_covered_by_avatar():
    ceres = _ceres_css()
    padding_rule = (
        f"{SELECTOR} .board-header .player-switch:has(.player-avatar:not([hidden])) .player-input "
        "{ padding-inline-start: 1.35rem; border-start-start-radius: 0; border-end-start-radius: 0; }"
    )
    assert padding_rule in ceres
