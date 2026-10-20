"""exact multi-copy CC, level and capped base PP projection."""
from ceres.combine_chain import expected_card
from ceres.level_cap import level_for_bcx, resolve_level_thresholds


def card(uid, cc, pp):
    return {"uid": uid, "card_detail_id": 7, "edition": 4, "foil": False,
            "card_set": "chaos", "bcx": cc, "bcx_max": 10, "land_base_pp": pp}


def test_several_inputs_sum_and_survivor():
    result = expected_card([card("C-Z", 2, 20), card("C-B", 3, 30), card("C-A", 1, 10)],
                           details_by_id={7: {"rarity": 4}})
    assert result["survivor_uid"] == "C-B"
    assert result["bcx"] == 6
    assert result["bcx_max"] == 10
    assert result["base_pp"] == 60
    assert result["base_pp_capped"] is False
    assert result["level"] == level_for_bcx(resolve_level_thresholds(7, 4, "legendary", False, "chaos"), 6)


def test_overcombined_keeps_cc_and_caps_base_pp():
    result = expected_card([card("C-A", 6, 60), card("C-B", 7, 70)], details_by_id={7: {"rarity": 4}})
    assert result["bcx"] == 13
    assert result["base_pp"] == 100
    assert result["base_pp_capped"] is True


def test_preview_routes_are_read_only_and_require_explicit_uids(monkeypatch):
    from types import SimpleNamespace
    from fastapi import HTTPException
    from ceres.app import create_app
    import ceres.routes_combine_chain as routes

    paths = set(create_app().openapi()["paths"])
    assert "/api/combine-chain/inputs" in paths
    assert "/api/combine-chain/expected-card" in paths
    monkeypatch.setattr(routes.client_limits, "limit", lambda *_args: None)
    monkeypatch.setattr(routes, "player_for_request", lambda request, player: "alice")
    monkeypatch.setattr(routes, "_snapshot", lambda request, account: ({"cards": [card("C-A", 2, 20), card("C-B", 3, 30)], "coverage": {"collection": {"complete": True}}, "generated_at": "2026-10-05T00:00:00Z"}, None, "2026-10-05T00:00:00Z"))
    monkeypatch.setattr(routes, "load_details_by_id", lambda: {7: {"rarity": 4}})
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(ceres_config=object())))
    assert routes.combine_chain_inputs({"uid": "C-A"}, request)["copies"][0]["eligible"]
    assert routes.combine_chain_expected_card({"uids": ["C-A", "C-B"]}, request)["expected"]["bcx"] == 5
    with __import__("pytest").raises(HTTPException):
        routes.combine_chain_expected_card({"cards": [{"id": 7}]}, request)
