""": the unreachable legacy worker action stays removed."""
from pathlib import Path

STATIC = Path(__file__).parents[2] / "ceres" / "static"


def test_dead_button_handler_and_phone_relabel_are_absent():
    for name in ("candidates.js", "picker-actions.js", "rows.js"):
        source = (STATIC / name).read_text()
        assert "worker-upgrade-action" not in source
        assert 'data-slot-action="upgrade-worker"' not in source
    assert "beginWorkerUpgrade" not in (STATIC / "picker-actions.js").read_text()
    assert "workerUpgradeActionHtml" not in (STATIC / "candidates.js").read_text()
    assert "combineTileActionHtml" in (STATIC / "combine.js").read_text()
