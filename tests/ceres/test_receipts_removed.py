""": the Receipts feature is gone -- no route, no button, no script, no dead helpers -- and what shared its code still works."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from ceres import game_read, stored_data_cleanup
from ceres.app import create_app
from ceres.config import CeresConfig

ROOT = Path(__file__).resolve().parents[2]
STATIC = ROOT / "ceres" / "static"
INDEX = (STATIC / "index.html").read_text(encoding="utf-8")
OPERATIONS = (STATIC / "operations.js").read_text(encoding="utf-8")

REMOVED_MODULES = ("routes_receipts", "receipts_api", "land_activity", "land_activity_source", "account_activity_source",
                   "deed_ownership")


def test_no_receipts_route_is_registered(tmp_path):
    app = create_app(CeresConfig(root=tmp_path / ".ceres", player="player0005"))
    paths = [getattr(route, "path", "") for route in app.routes]
    assert not [path for path in paths if "/receipts" in path], paths


@pytest.mark.parametrize("name", REMOVED_MODULES)
def test_removed_backend_modules_are_gone(name):
    assert not (ROOT / "ceres" / f"{name}.py").exists()


def test_new_market_purchase_module_is_not_the_removed_receipts_feature():
    source = (ROOT / "ceres" / "market_purchase.py").read_text(encoding="utf-8")
    assert 'OP_ID = "sm_market_purchase"' in source
    assert "ceres:receipts:v1:" not in source


def test_no_receipts_script_or_stylesheet_selectors_remain():
    assert not (STATIC / "receipts.js").exists()
    assert not (STATIC / "receipt-clock.js").exists()
    for css in ("ceres.css", "mobile-plot.css", "buttons.css"):
        text = (STATIC / css).read_text(encoding="utf-8")
        assert not re.search(r"\.receipts-|\.rc-|#receipts-|mobile-receipts|has-new-receipt", text), css
    assert "receipts.js" not in INDEX


def test_index_has_no_receipts_buttons():
    assert 'id="receipts-toggle"' not in INDEX
    assert 'id="mobile-receipts-btn"' not in INDEX
    assert "receipts-panel" not in INDEX


def test_phone_bottom_bar_has_exactly_four_equal_buttons():
    bar = re.search(r'<nav class="mobile-action-bar[^>]*id="mobile-action-bar".*?</nav>', INDEX, flags=re.S).group(0)
    ids = re.findall(r'<button[^>]*\bid="([^"]+)"', bar)
    assert ids == ["mobile-filters-btn", "mobile-changes-btn", "mobile-harvest-btn", "mobile-wallet-btn"]
    css = " ".join((STATIC / name).read_text(encoding="utf-8") for name in ("ceres.css", "mobile-plot.css"))
    bar_rules = re.findall(r"\.mobile-action-bar \{[^}]*grid-template-columns: repeat\((\d+), minmax\(0, 1fr\)\)", css)
    assert bar_rules and set(bar_rules) == {"4"}, bar_rules


def test_confirmation_pill_has_no_open_receipt_action_or_receipt_storage():
    assert "open-receipt" not in OPERATIONS
    assert "ceres:open-receipt" not in OPERATIONS
    assert "writeConfirmationReceipt" not in OPERATIONS
    assert "receiptStoreKey" not in OPERATIONS
    assert "Receipts removed; see library/ceres-receipts-removal.md" in OPERATIONS
    # The one-time cleanup of the old browser list is the only remaining mention of the storage key.
    assert "startsWith('ceres:receipts:v1:')" in OPERATIONS and "removeItem(name)" in OPERATIONS
    assert "ceres:receipts:v1:${" not in OPERATIONS and "localStorage.setItem(`ceres:receipts" not in OPERATIONS
    # What the pill keeps.
    for kept in ("View on Hive", "ceres-pill-progress", "retry-verification", "data-confirmation-action=\"${actionName}\""):
        assert kept in OPERATIONS


def test_harvest_verify_still_imports_and_uses_game_read():
    from ceres import routes_harvest

    source = Path(routes_harvest.__file__).read_text(encoding="utf-8")
    assert "from .game_read import ACTION_MAX, OutboundBudget" in source and "game_read._get(" in source
    assert callable(routes_harvest._verify_harvest_attempt_inner)
    budget = game_read.OutboundBudget("player0005", 2)
    budget.spend()
    budget.spend()
    with pytest.raises(game_read.BudgetExceeded):
        budget.spend()


def test_stored_receipts_files_are_removed_once(tmp_path):
    root = tmp_path / ".ceres"
    (root / "account_activity").mkdir(parents=True)
    (root / "snapshots").mkdir()
    for name in ("player0005.json", "worker-0.lock", "player0005.tmp"):
        (root / "account_activity" / name).write_text("{}")
    (root / "snapshots" / "deed-owners-player0005.json").write_text("[]")
    (root / "snapshots" / "latest-player0005.json").write_text("{}")  # a board snapshot is not touched here
    summary = {"files": 0, "bytes": 0, "by_category": {}, "errors": 0}
    stored_data_cleanup.remove_receipts_files(root, summary)
    assert summary["files"] == 4 and summary["errors"] == 0
    assert set(summary["by_category"]) == {"receipts_activity_state", "receipts_deed_owners"}
    assert not (root / "account_activity").exists()
    assert not (root / "snapshots" / "deed-owners-player0005.json").exists()
    assert (root / "snapshots" / "latest-player0005.json").exists()
