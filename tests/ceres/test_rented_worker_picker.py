from pathlib import Path

from ceres.candidates import rank_candidates
from ceres.fill_plot import clean_settings


ROOT = Path(__file__).parents[2]
PICKER = (ROOT / "ceres/static/candidates.js").read_text(encoding="utf-8")
FILL_PLOT = (ROOT / "ceres/fill_plot.py").read_text(encoding="utf-8")


def test_worker_and_runi_picker_requests_include_rented_in():
    assert "const workerLike = picker.groupId === 'worker' || picker.groupId === 'runi';" in PICKER
    assert "if (workerLike) params.set('include_rented_in', 'true');" in PICKER


def test_rented_in_worker_row_formats_end_date_with_rented_tag():
    assert "const formattedRentalExpiry = rentedIn ? humanizeInstant(candidate.rental.expires_at)?.absolute" in PICKER
    assert "? `Rented · ends ${formattedRentalExpiry}`" in PICKER
    assert "function candidateStatusHtml(candidate, picker = S.CERES_PICKER)" in PICKER


def test_auto_fill_excludes_rented_in_cards_by_default():
    assert clean_settings({})["allow_rented"] is False
    assert clean_settings({"allow_rented": True})["allow_rented"] is False
    assert 'include_rented_in=bool(settings["allow_rented"])' in FILL_PLOT
    assert 'key in raw and key != "allow_rented"' in FILL_PLOT


def test_delegated_in_card_remains_available_in_worker_picker(report, details_by_id):
    report["cards"].append({
        "asset_ref": "delegated-in-worker-picker",
        "classification": {"bucket": "unstaked_apparent"},
        "facts": {
            "card_detail_id": 2,
            "land_base_pp": 1000,
            "level": 1,
            "delegated_to": "player0005",
            "player": "owner",
        },
    })
    body = rank_candidates(
        report,
        report["plots"][0]["deed_uid"],
        "worker",
        player="player0005",
        details_by_id=details_by_id,
    )
    assert "delegated-in-worker-picker" in {row["asset_ref"] for row in body["candidates"]}
