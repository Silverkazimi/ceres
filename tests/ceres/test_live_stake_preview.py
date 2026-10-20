from pathlib import Path


REGIONAL_DEC = Path(__file__).parents[2] / "ceres" / "static" / "regional-dec.js"


def test_live_stake_preview_uses_fresh_server_figures_and_confirms_only_shortfall():
    source = REGIONAL_DEC.read_text()

    assert "const PREVIEW_DEBOUNCE_MS = 300;" in source
    assert "async function api(path, body, options = {})" in source
    assert "api('/preview', { region_uid: job.review.region_uid, action: job.review.action, amount: job.amount })" in source
    assert "review.preview = null;" in source
    assert "review.previewAmount = null;" in source
    assert "review.previewRequest !== request" in source
    assert "review.previewAmount === review.amount" in source
    assert "data-dec=\"review\"" not in source
    assert "Continue despite worker shortfall" in source
    assert "preview.shortfall_after && !review.shortfallConfirmed" in source
    assert "below requirement after this change. Worker production may decrease." in source
    assert "D.review.shortfallConfirmed = false;" in source
    assert "D.review.preview = fresh;" in source


def test_live_stake_preview_caps_spacing_inflight_and_duplicate_amounts():
    source = REGIONAL_DEC.read_text()

    assert "const PREVIEW_MIN_INTERVAL_MS = 2000;" in source
    assert "let previewInFlight = false;" in source
    assert "let previewQueued = null;" in source
    assert "if (previewInFlight || !previewQueued) return;" in source
    assert "const wait = PREVIEW_MIN_INTERVAL_MS - (Date.now() - previewLastStartedAt);" in source
    assert "previewLastStartedAt = Date.now();" in source
    assert "previewQueued = job;" in source
    assert "review.lastPreviewedAmount === amount && review.previewAmount === amount" in source
    assert "if (!validStakeAmount(amount))" in source
