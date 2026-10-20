"""Guard the fixed signing regions that prevent Changes-modal bounce ()."""

from pathlib import Path


CSS = (Path(__file__).parents[2] / "ceres/static/ceres.css").read_text(encoding="utf-8")
OPERATIONS = (Path(__file__).parents[2] / "ceres/static/operations.js").read_text(encoding="utf-8")
CHECKOUT = (Path(__file__).parents[2] / "ceres/static/checkout.js").read_text(encoding="utf-8")


def test_changes_signing_regions_keep_fixed_block_geometry() -> None:
    assert ".changes-panel .ch-signing-status{box-sizing:border-box;block-size:5rem;overflow-y:auto}" in CSS
    assert ".changes-panel .ch-signing-status>.op-preview-review.is-compact{box-sizing:border-box;min-block-size:100%;margin-block-end:0}" in CSS
    assert ".changes-panel .hm-foot .ch-signing-note{box-sizing:border-box;display:flex;align-items:center;block-size:2.8rem;overflow-y:auto}" in CSS


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
