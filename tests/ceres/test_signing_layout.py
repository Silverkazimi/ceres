"""Guard the fixed signing regions that prevent Changes-modal bounce ()."""

import re
from pathlib import Path


CSS = (Path(__file__).parents[2] / "ceres/static/ceres.css").read_text(encoding="utf-8")
OPERATIONS = (Path(__file__).parents[2] / "ceres/static/operations.js").read_text(encoding="utf-8")
CHECKOUT = (Path(__file__).parents[2] / "ceres/static/checkout.js").read_text(encoding="utf-8")


def _rules(selector: str) -> list[dict[str, str]]:
    """Declarations of every rule whose selector list contains exactly `selector` (whitespace-normalised)."""
    want = re.sub(r"\s+", " ", selector.replace(">", " > ")).strip()
    found = []
    for selectors, body in re.findall(r"([^{}]+)\{([^{}]*)\}", re.sub(r"/\*.*?\*/", "", CSS, flags=re.S)):
        names = [re.sub(r"\s+", " ", name.replace(">", " > ")).strip() for name in selectors.split(",")]
        if want in names:
            found.append({key.strip(): value.strip() for key, _, value in
                          (part.partition(":") for part in body.split(";") if ":" in part)})
    assert found, selector
    return found


def _fixed_block_size(rules: list[dict[str, str]]) -> list[str]:
    return [rule["block-size"] for rule in rules if "block-size" in rule]


def test_changes_signing_regions_stay_inside_the_panel_without_overflow() -> None:
    # The folded signing design lets the status grow with its content; it must stay inside the panel
    # (border-box, may shrink, long words wrap) and must not be pinned to a fixed block size again.
    status = _rules(".changes-panel .ch-signing-status")
    assert all(rule.get("box-sizing") == "border-box" for rule in status)
    assert any(rule.get("min-inline-size") == "0" for rule in status)
    assert any(rule.get("overflow-wrap") == "anywhere" for rule in status)
    assert not _fixed_block_size(status) and not _fixed_block_size(_rules("#changes-overlay .ch-signing-status"))
    compact = _rules(".changes-panel .ch-signing-status>.op-preview-review.is-compact")
    assert all(rule.get("box-sizing") == "border-box" and rule.get("margin-block-end") == "0" for rule in compact)
    assert all(rule.get("min-block-size", "0") == "0" for rule in compact) and not _fixed_block_size(compact)
    # the footer note keeps its text inside its own box: anything taller scrolls inside it.
    note = _rules(".changes-panel .hm-foot .ch-signing-note")
    assert any(rule.get("box-sizing") == "border-box" for rule in note)
    assert any(rule.get("overflow-y") == "auto" for rule in note)
    # every folded operation stays inside the focus column, and its exact JSON scrolls inside a bounded box.
    assert any(rule.get("min-inline-size") == "0" for rule in _rules(".ch-signing-focus"))
    assert any(rule.get("min-inline-size") == "0" for rule in _rules(".ch-signing-operation li"))
    for selector in (".ch-signing-op-text strong", ".ch-signing-op-text small"):
        assert any(rule.get("overflow-wrap") == "anywhere" for rule in _rules(selector)), selector
    pre = _rules(".ch-signing-op pre")
    assert any(rule.get("overflow") == "auto" for rule in pre)
    assert any(rule.get("overflow-wrap") == "anywhere" for rule in pre)
    assert any(rule.get("max-block-size") for rule in pre) and not _fixed_block_size(pre)


def test_changes_signing_focus_shows_exact_current_operations_and_hides_plan_context() -> None:
    assert "export function signingFocusHtml(review)" in OPERATIONS
    assert "JSON.stringify(operation.body, null, 2)" in OPERATIONS
    # every operation is one readable line; its exact JSON is folded inside it.
    assert '<li><details class="ch-signing-op"><summary>' in OPERATIONS
    assert "<details open" not in OPERATIONS.split("export function signingFocusHtml", 1)[1].split("\nfunction ", 1)[0]
    assert "review.signingStates[index] = 'current'" in OPERATIONS
    assert "review.signingStates[index] = result?.state === 'domain-confirmed' ? 'confirmed' : 'signed'" in OPERATIONS
    assert "if (review?.broadcasting)" in CHECKOUT
    assert "signingFocusHtml(review)" in CHECKOUT
    assert "Only this signing run is shown until every request is handled." in OPERATIONS
