""" regression guard for Buy & max combine signing."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

def test_buy_max_rebuilds_and_requires_review_after_witness_change():
    client = (ROOT / "ceres/static/card-checkout.js").read_text()
    route = (ROOT / "ceres/routes_market_purchase.py").read_text()
    combine = (ROOT / "ceres/routes_combine.py").read_text()

    sign = client[client.index("async function signCombine(g)"):client.index("// the combine's game outcome")]
    assert "post('combine'" in sign
    assert "JSON.stringify(g.witness" in sign
    assert "g.state = 'review-changed'" in sign
    assert 'data-co-action="combine-confirm"' in client
    assert '"witness": {' in route
    assert '"card_detail_id": record.get("card_detail_id")' in combine
    assert '"edition": record.get("edition")' in combine
    assert '"foil": record.get("foil", record.get("gold"))' in combine
    assert '"bcx": _bcx(record)' in combine
    assert '"survivor": preview.get("predicted_survivor_uid")' in route
    assert '"destroyed": (preview.get("hazard") or {}).get("destroyed_uids", [])' in route
