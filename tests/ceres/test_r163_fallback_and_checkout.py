"""Two maintainer-reported defects, both fixed by removal or by specificity.

 / R-163 — the art fallback printed the card name INTO the art area while the
bottom caption kept showing it too, so a tile whose art failed showed the name twice.
His ruling fixes the direction: "The idea for the name nadge is interesting bt would have
to be on the bottom, to never block the card art, which has uually their head there."
The TOP occurrence goes; `.slot-caption`'s `.slot-name` is untouched.

 — "checkout buttons is broken when showing a number". The header family block at
`.board-header .checkout-btn:not([hidden])` (0,3,0) pins the square control size, and the
rule written to widen the two word-carrying buttons was `.board-header .checkout-btn`
(0,2,0), so it lost: the label clipped to "Chec" and `display: inline-grid` put the count
on a second row.

RED MUTATIONS, one per test, named at each test.
"""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
EOS = (ROOT / "ceres" / "static" / "eos.js").read_text(encoding="utf-8")
CSS = (ROOT / "ceres" / "static" / "ceres.css").read_text(encoding="utf-8")


def _fallback_body() -> str:
    """The body of eos.js's `fallback()` -- the Eos art-unavailable path only."""
    start = EOS.index("card.className = 'card-art-fallback eos-art-fallback'")
    end = EOS.index("image.replaceWith(card)", start)
    return EOS[start:end]


def test_the_eos_art_fallback_does_not_print_the_card_name():
    """RED MUTATION: restore the `.card-art-fallback-name` span and re-add it to
 `card.append(...)` in eos.js's fallback(). This must turn red.

 The fix's own explanatory comment names the removed class for documentation (why no
 CSS change belongs here -- `.card-art-fallback-name` stays live for editor.js's
 separate non-Eos path), so the guard checks for the class actually being BUILT
 (`className = '...'`/`classList.add('...')`), not merely mentioned in prose."""
    body = _fallback_body()
    assert not re.search(r"(?:className\s*=|classList\.add\()\s*['\"][^'\"]*card-art-fallback-name", body), (
        "the Eos fallback builds a card-name span again; that is the duplicate the maintainer "
        "reported, and R-163 rules the TOP occurrence goes"
    )
    # The marker is what should remain -- proving we removed the name, not the whole element.
    assert "card-art-fallback-marker" in body
    assert "Art unavailable" in body


def test_the_bottom_caption_name_is_untouched():
    """RED MUTATION: delete `.slot-name` from editor.js's caption. Removing the BOTTOM
 occurrence would satisfy the defect report and violate R-163 in the same commit."""
    editor = (ROOT / "ceres" / "static" / "editor.js").read_text(encoding="utf-8")
    assert "slot-name" in editor, "the bottom caption name is the one his ruling keeps"


# (maintainer-approved button migration, phases 0+1 commit 32449374
# 2026-09-16) replaced the whole per-selector specificity-war architecture 's
# fix patched (a square ".board-header .checkout-btn:not([hidden])" family block vs. a
# ".board-header .player-go-btn:not([hidden])," widening override) with a shared
# token-driven `.ceres-btn` system: width comes from `--ceres-control-min-inline`
# (auto by default -- buttons.css), row layout from the shared button's own flex/grid
# rules, and hidden state from one reinforcing `.ceres-btn[hidden] { display: none }`
# rule instead of a specificity contest. `.board-header .checkout-btn:not([hidden])`
# and the `.player-go-btn` widening override no longer exist in ceres.css at all
# they were deleted, not renamed, so the below re-targets 's three user-facing
# guarantees (readable label, one row, never painted while hidden) at the new
# architecture instead of asserting selectors that are gone by design.
BUTTONS_CSS = (ROOT / "ceres" / "static" / "buttons.css").read_text(encoding="utf-8")
HTML = (ROOT / "ceres" / "static" / "index.html").read_text(encoding="utf-8")


def test_the_word_carrying_header_buttons_outrank_the_square_family_rule():
    """The checkout button now carries the shared `.ceres-btn` contract rather than a
 bespoke square rule, so its width is the shared token's `auto` default, not a fixed
 square that would clip a "Checkout 3" label.

 RED MUTATION: give `.ceres-btn` a fixed `--ceres-control-min-inline` (drop the
 `auto` default). This must turn red."""
    assert 'class="checkout-btn ceres-btn ceres-btn--primary"' in HTML, (
        "the checkout button must carry the shared ceres-btn contract, not a bespoke rule"
    )
    assert "ceres-control-min-inline: auto;" in BUTTONS_CSS


def test_the_count_and_the_label_share_one_row():
    """RED MUTATION: change `.ceres-btn`'s display to `inline-grid` with rows (drop the
 row-flow layout), reintroducing the two-line rendering reported."""
    start = BUTTONS_CSS.index("\n.ceres-btn {\n")
    block = BUTTONS_CSS[start:BUTTONS_CSS.index("}", start) + 1]
    assert re.search(r"display:\s*inline-flex", block), (
        "the shared button must lay its label and any count out in one inline-flex row"
    )
    assert "flex-direction: column" not in block


def test_the_hidden_state_guard_survives():
    """RED MUTATION: drop the `.ceres-btn[hidden] { display: none }` reinforcement. That
 qualifier is what stops the old specificity war (a `display` declaration outranking
 the UA `[hidden] { display: none }`) from ever recurring under the shared button
 system -- without it a future density/variant rule could again outrank `[hidden]`
 and paint "Checkout 0" on every empty board, under the Iris picker."""
    assert re.search(r"\.ceres-btn\[hidden\],\s*\n\.ceres-btn-group\[hidden\]\s*\{\s*\n\s*display:\s*none;", BUTTONS_CSS)
