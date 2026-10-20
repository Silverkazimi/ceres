from pathlib import Path


ROOT = Path(__file__).parents[2]
FILL_JS = (ROOT / "ceres/static/fill-plot.js").read_text(encoding="utf-8")


def test_fill_proposal_carries_staged_worksite_and_plain_first_clear_prompt() -> None:
    assert "staged_worksite: stagedWorksiteTarget()" in FILL_JS
    assert "Choose a worksite to start the build" in (ROOT / "ceres/fill_plot.py").read_text(encoding="utf-8")
