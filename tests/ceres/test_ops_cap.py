""" guards for the shared operations-per-transaction policy."""

import asyncio
from types import SimpleNamespace

import pytest

from fides import op as fides_op
from ceres import harvest_routing as routing
from ceres import op_preview as op_preview_mod
from ceres import routes_harvest_routing as routes
from ceres import routing_plan, routing_runs_v2 as runs


def _manifest(player):
    pool = routing.Pool(1, "GRAIN", routing.amount("1000"), routing.amount("1000"),
                        routing.amount("1000"), False)
    rule = {"id": "sale-1", "kind": "sell", "source_region": "PR-AAA-1", "symbol": "GRAIN",
            "destination_symbol": "DEC", "quantity": "10", "slippage": "2.5"}
    quote = routing.swap_quote("GRAIN", "DEC", "10", {"GRAIN": pool}, "2.5")
    step = {"id": "sale-1", "kind": "sell", "status": "ready", "source_region": "PR-AAA-1",
            "destination_region": routing.INVENTORY, "symbol": "GRAIN", "destination_symbol": "DEC",
            "quantity": "10", "slippage": "2.5", "quote": quote.public()}
    return routing_plan.compile_manifest(rules=[rule], steps=[step], pools={"GRAIN": pool},
                                         account=player, regions=1, enabled_kinds={"sale_dec"})


def _report():
    deed = "deed-1"
    return {
        "plots": [{"deed_uid": deed, "facts": {"plot": deed, "rarity": "common", "status": "natural",
                  "resource": "GRAIN", "territory": "T", "worksite_type": "Grain Farm"},
                  "worksite_facts": {"project_type": "worksite_grain"}, "staking_facts": {
                      "max_workers_allowed": 5, "worker_count": 1, "is_power_core_staked": True,
                      "is_energized": False, "is_runi_staked": False}}],
        "workers": [{"asset_ref": "old-ref", "uid": "CARD-OLD", "stake_type_uid": "STK-LND-WKR",
                     "stake_ref_uid": deed, "slot": 1, "asset_kind": "worker_card", "name": "Old",
                     "delegated_to": None, "rental_type": None}],
        "cards": [{"asset_ref": "new-ref", "uid": "CARD-NEW", "facts": {"uid": "CARD-NEW",
                   "player": "player0005", "market_id": None, "delegated_to": None, "rental_type": None,
                   "unlock_date": None}}],
        "bonus_assets": [], "unassigned_items": {},
    }


def _preview_chunk_limit(monkeypatch):
    seen = []
    original = op_preview_mod.chunk_ops

    def capture(ops, **kwargs):
        seen.append(kwargs["max_ops_per_tx"])
        return original(ops, **kwargs)

    monkeypatch.setattr(op_preview_mod, "chunk_ops", capture)
    op_preview_mod.build_preview(_report(), [{"deed_uid": "deed-1", "group_id": "worker",
                              "action": "stake", "asset_ref": "new-ref", "slot_index": 1}],
                                account="player0005")
    return seen


def _state_cap(monkeypatch):
    monkeypatch.setattr(routes, "player_for_request", lambda _request, player: player or "fixtureowner")
    monkeypatch.setattr(routes, "_context", lambda _request, _player: {"public": {"observed_at": "now"}})
    monkeypatch.setattr(routes, "_config", lambda _request: SimpleNamespace(routing_kinds=set()))
    return asyncio.run(routes.state(object(), player="fixtureowner"))["max_ops_per_tx"]


def test_default_cap_is_four():
    assert fides_op.DEFAULT_MAX_OPS_PER_TX == 4
    assert op_preview_mod._first_clear_op_limit(4) == 4


@pytest.mark.parametrize("cap", [4, 5])
def test_all_python_consumers_follow_the_single_cap(monkeypatch, cap):
    """The default and a changed source value reach preview, routing, validation and state."""
    monkeypatch.setattr(fides_op, "DEFAULT_MAX_OPS_PER_TX", cap)

    assert _preview_chunk_limit(monkeypatch) == [cap]
    assert op_preview_mod._first_clear_op_limit(cap) == cap
    assert routes._projected_harvest_chunks(9) == (9 + cap - 1) // cap
    assert _state_cap(monkeypatch) == cap

    manifest = _manifest("fixtureowner")
    assert manifest["cap"] == cap
    runs._validate_manifest(manifest, "fixtureowner", {"sale_dec"})


def test_v2_validation_rejects_manifest_from_old_cap(monkeypatch):
    """A preview signed under another cap cannot pass the current run validator."""
    monkeypatch.setattr(fides_op, "DEFAULT_MAX_OPS_PER_TX", 5)
    manifest = _manifest("fixtureowner")
    monkeypatch.setattr(fides_op, "DEFAULT_MAX_OPS_PER_TX", 4)
    with pytest.raises(routing.RoutingError, match="current v2 routing preview"):
        runs._validate_manifest(manifest, "fixtureowner", {"sale_dec"})
