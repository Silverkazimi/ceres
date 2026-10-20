"""a swap the pool refunded ("LP swap refund <id>") ends as a loud failure instead of waiting for a payout forever.
Owner's case 2026-10-14: swap 115303, 600.392 SPS sent 08:04:48 UTC, bridge external_swap 08:05:12, refund 08:05:39."""
import json

from ceres import harvest_routing_bridge as bridge, routes_wallet_lp as lp_routes


def _step():
    effects = {"external_swap_required": True, "pending_swap_id": 115303, "pending_external_amount": 600.392,
               "external_transfer_account": "player0015", "external_swap_token": "SPS", "external_swap_player": "player0005"}
    return lp_routes.settlement_step("a" * 40, "swap_sell", "SPS", effects, True, None)


def _row(index, tx, op_id, payload):
    return [index, {"trx_id": tx, "op_in_trx": 0, "timestamp": "2026-10-14T08:05:39",
                    "op": ["custom_json", {"id": op_id, "json": json.dumps(payload), "required_auths": ["player0015"], "required_posting_auths": []}]}]


def test_bridge_refund_transfer_ends_the_swap(monkeypatch):
    rows = [_row(1, "d" * 40, "sm_land_operation", {"n": "x", "op": "external_swap", "player": "player0005", "pending_swap_id": "115303", "external_token_amount": 600.392}),
            _row(2, "e" * 40, "sm_token_transfer", {"n": "y", "to": "player0005", "qty": 600.392, "memo": "LP swap refund 115303", "token": "SPS"})]
    monkeypatch.setattr(bridge, "_history", lambda account, start: list(reversed(rows)))  # newest first, as the loop sees the refund before the op
    settled = bridge.settle("player0005", _step())
    assert settled["state"] == "refunded" and settled["bridge_transaction_id"] == "e" * 40
    assert settled["refunded"] == [{"symbol": "SPS", "amount": "600.392"}]
    view = lp_routes.settlement_view(settled)
    assert view["status"] == "refunded" and view["refunded"][0]["amount"] == "600.392"
    # another player's refund or another swap id is not ours
    other = [_row(3, "f" * 40, "sm_token_transfer", {"n": "z", "to": "bob", "qty": 1, "memo": "LP swap refund 115303", "token": "SPS"}),
             _row(4, "0" * 40, "sm_token_transfer", {"n": "z", "to": "player0005", "qty": 1, "memo": "LP swap refund 115304", "token": "SPS"})]
    monkeypatch.setattr(bridge, "_history", lambda account, start: other)
    assert bridge.settle("player0005", _step())["state"] == "settling"


def test_card_turns_a_refund_into_a_red_final_note():
    from pathlib import Path
    lp = (Path(__file__).parents[2] / "ceres" / "static" / "wallet-lp.js").read_text()
    assert "result.status === 'refunded'" in lp and "lpNoteTone: 'danger'" in lp and "The pool refunded" in lp
    assert "role=\"${st.lpNoteTone === 'danger' ? 'alert' : 'status'}\"" in lp
