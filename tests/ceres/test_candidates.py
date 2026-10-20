from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from ceres import capture as capture_mod
from ceres import routes_candidates as candidates_routes
from ceres.candidates import cooldown_state, rank_candidates, rental_direction
from ceres.config import CeresConfig


NOW = datetime(2026, 8, 24, tzinfo=timezone.utc)


def test_cap_total_and_continuation(report, details_by_id):
    # 33, not 30 -- the shared fixture holds 3 Soulkeep cards now excludes.
    for index in range(33):
        report["cards"].append({
            "asset_ref": f"idle-{index}",
            "classification": {"bucket": "unstaked_apparent"},
            "facts": {"card_detail_id": 2, "land_base_pp": 100 + index, "level": 1, "edition": 0},
        })
    deed = report["plots"][0]["deed_uid"]
    first = rank_candidates(report, deed, "worker", player="player0005", now=NOW, limit=25, details_by_id=details_by_id)
    second = rank_candidates(report, deed, "worker", player="player0005", now=NOW, limit=25, offset=25, details_by_id=details_by_id)
    assert first["returned"] == 25
    assert first["pool_size"] == first["total"]
    assert first["pool_size"] >= 50
    assert len({row["asset_ref"] for row in first["candidates"]} & {row["asset_ref"] for row in second["candidates"]}) == 0


def test_default_construction_pp_is_the_active_preset(report, details_by_id):
    """Guards that the default preset really orders `rank_candidates`'s real API output by
    descending magnitude ( / v0.5.80's `construction_sort_key` fix), not by estimate
    status alone. The previous version of this test asserted the opposite of the fix: that
    every `point`-status row precedes every `unknown`-status row regardless of magnitude --
    which is exactly the defect v0.5.80 removed (a high-base-PP unknown-status card
    used to sink behind every point-status row; measured live, the 6 highest-base-PP cards on a
    plot were buried at positions 2503-2508 of 4,060). Corrected per the internal rules's mutation-
    standard corollary ("assert on the observable, not the spelling") to assert the real
    invariant on the actual response fields: each row's own magnitude -- its construction_pp
    value/low when known, else its terrain-adjusted (or raw) base_pp, exactly as
    `construction_sort_key` computes it -- is non-increasing across the returned candidates.
    Injection: reverted `ceres/candidates.py::construction_sort_key` to lead its tuple with
    `rank` (`return (rank, primary, impact_tie, ...)`, the pre-fix ordering) and re-ran this
    test against the real `assets_active_above_cap.json` fixture -- went red with a magnitude
    inversion (a lower-magnitude row sorted ahead of a higher-magnitude one) at the point where
    the fixture's above-cap unknown-status cards were pushed behind lower-PP rows; restored and
    confirmed green.
    """
    deed = report["plots"][0]["deed_uid"]
    body = rank_candidates(report, deed, "worker", player="player0005", now=NOW, include_cooling=True, limit=100, details_by_id=details_by_id)
    assert body["preset"] == {"id": "construction_pp", "label": "Boostable", "applied": True}

    def magnitude(row):
        estimate = row["construction_pp"]
        if estimate["status"] == "point":
            return estimate["value"]
        if estimate["status"] == "range":
            return estimate["low"]
        terrain = row["terrain_boost"]
        if terrain is not None:
            return row["base_pp"] * (1 + terrain)
        return row["base_pp"]

    magnitudes = [magnitude(row) for row in body["candidates"]]
    assert magnitudes == sorted(magnitudes, reverse=True)


def test_idle_exclusion_counts_do_not_include_staked_cooling_rows(report, details_by_id):
    report["workers"].append({
        "name": "Cooling staked", "card_detail_id": 2, "land_base_pp": 1000,
        "total_harvest_pp": 1000, "asset_ref": "cooling-staked", "stake_ref_uid": report["plots"][0]["deed_uid"],
        "asset_kind": "worker_card", "slot": 99,
    })
    body = rank_candidates(
        report, report["plots"][0]["deed_uid"], "worker", player="player0005", now=NOW,
        bucket="idle", details_by_id=details_by_id,
    )
    assert body["excluded_counts"]["cooling"] == 0
    assert body["pool_size"] + sum(body["excluded_counts"].values()) == sum(
        row.get("classification", {}).get("bucket") in {"unstaked_apparent", "stake_expired_or_historical"}
        for row in report["cards"]
    )


def test_directional_rental_is_opt_in_and_unknown_fails_closed():
    rented_in = {"rental_type": "fixed", "delegated_to": "player0005", "player": "owner", "expiration_date": "2026-09-01T00:00:00Z"}
    assert rental_direction(rented_in, "player0005") == ("in", None, "2026-09-01T00:00:00Z")
    assert rental_direction({"rental_type": "fixed"}, "player0005")[0] == "unknown"


def test_unknown_rental_direction_stays_out_of_the_pool(report, details_by_id):
    report["cards"].append({
        "asset_ref": "unknown-rental",
        "classification": {"bucket": "unstaked_apparent"},
        "facts": {"card_detail_id": 2, "land_base_pp": 1000, "level": 1, "rental_type": "fixed"},
    })
    deed = report["plots"][0]["deed_uid"]
    body = rank_candidates(report, deed, "worker", player="player0005", now=NOW, limit=100, details_by_id=details_by_id)
    assert "unknown-rental" not in {row["asset_ref"] for row in body["candidates"]}
    assert body["excluded_counts"]["unknown_direction"] >= 1


def test_cooldown_unknown_class_is_not_free():
    result = cooldown_state("unstaked_apparent", None, "STK-LND-UNKNOWN", NOW)
    assert result["ready"] is False
    assert result["class"] == "unknown"


def test_cooling_candidate_is_marked_when_explicitly_included(report, details_by_id):
    report["cards"].append({
        "asset_ref": "cooling-card",
        "classification": {"bucket": "stake_expired_or_historical"},
        "facts": {"card_detail_id": 2, "land_base_pp": 1000, "level": 1, "stake_end_date": "2026-08-25T00:00:00Z"},
    })
    deed = report["plots"][0]["deed_uid"]
    body = rank_candidates(report, deed, "worker", player="player0005", now=NOW, include_cooling=True, limit=100, details_by_id=details_by_id)
    row = next(row for row in body["candidates"] if row["asset_ref"] == "cooling-card")
    assert row["cooldown"]["ready"] is False
    assert row["cooldown"]["ready_at"] is not None


def test_solo_bloodline_holder_is_offerable_as_a_staked_candidate(report, details_by_id):
    # 2026-08-26 ruling (supersedes the earlier exclusion ruling): a solo BLOODLINE
    # holder is no longer excluded from being offered as a candidate for OTHER slots
    # it is just an ordinary staked candidate like any other.
    worker = report["workers"][0]
    worker["card_detail_id"] = 867
    report["cards"].append({
        "asset_ref": worker["asset_ref"],
        "classification": {"bucket": "assigned_active"},
        "facts": {"card_detail_id": 867, "land_base_pp": 150, "level": 1},
    })
    # Query a DIFFERENT plot than the holder's own (plots[0]) -- querying its own plot
    # would exclude it via the unrelated "same_plot" rule regardless of protection.
    deed = report["plots"][1]["deed_uid"]
    body = rank_candidates(report, deed, "worker", player="player0005", now=NOW, include_cooling=True, bucket="staked", details_by_id=details_by_id)
    assert worker["asset_ref"] in {row["asset_ref"] for row in body["candidates"]}


def test_eligibility_exclusions_are_counted(report, details_by_id):
    base = {"card_detail_id": 2, "land_base_pp": 100, "level": 1}
    cases = [
        ("listed", {**base, "market_id": "listing"}),
        ("delegated", {**base, "delegated_to": "other"}),
        ("locked", {**base, "unlock_date": "2099-01-01T00:00:00Z"}),
        ("rented", {**base, "rental_type": "fixed"}),
    ]
    for index, (_label, facts) in enumerate(cases):
        report["cards"].append({"asset_ref": f"blocked-{index}", "classification": {"bucket": "unstaked_apparent"}, "facts": facts})
    deed = report["plots"][0]["deed_uid"]
    body = rank_candidates(report, deed, "worker", player="player0005", now=NOW, details_by_id=details_by_id)
    assert body["excluded_counts"]["market_listed"] >= 1
    assert body["excluded_counts"]["delegated"] >= 1
    assert body["excluded_counts"]["unlock_locked"] >= 1
    assert body["excluded_counts"]["rented"] >= 1


def test_rented_in_requires_flag_and_surfaces_expiry(report, details_by_id):
    report["cards"].append({
        "asset_ref": "rented-in",
        "classification": {"bucket": "unstaked_apparent"},
        "facts": {"card_detail_id": 2, "land_base_pp": 1000, "level": 1, "rental_type": "fixed", "delegated_to": "player0005", "player": "owner", "expiration_date": "2026-09-01T00:00:00Z"},
    })
    deed = report["plots"][0]["deed_uid"]
    hidden = rank_candidates(report, deed, "worker", player="player0005", now=NOW, details_by_id=details_by_id)
    shown = rank_candidates(report, deed, "worker", player="player0005", now=NOW, include_rented_in=True, details_by_id=details_by_id)
    assert "rented-in" not in {row["asset_ref"] for row in hidden["candidates"]}
    row = next(row for row in shown["candidates"] if row["asset_ref"] == "rented-in")
    assert row["rental"]["expires_at"] == "2026-09-01T00:00:00Z"
    assert "force-harvests" in row["rental"]["warning"]


def test_get_candidates_uses_snapshot_without_capture(tmp_path, monkeypatch, report):
    """GET candidates reads the latest snapshot without starting a refresh.

    Mutation check: injected ``capture_mod.start_refresh(config, player)`` in the route; the forbidden-refresh assertion went red.
    """
    calls = {"snapshot": 0, "refresh": 0}

    def fake_latest(config, player):
        calls["snapshot"] += 1
        return report, object()

    def forbidden(*args, **kwargs):
        calls["refresh"] += 1
        raise AssertionError("GET candidates must not capture")

    monkeypatch.setattr(capture_mod, "latest_snapshot", fake_latest)
    monkeypatch.setattr(capture_mod, "start_refresh", forbidden)
    monkeypatch.setattr(candidates_routes, "load_details_by_id", lambda: {})
    deed = report["plots"][0]["deed_uid"]
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(ceres_config=CeresConfig(root=tmp_path / ".ceres", player="player0005")),
        ),
    )
    response = candidates_routes.get_candidates(deed, request, group_id="worker", limit=1, offset=0)
    assert response.status_code == 200
    assert calls == {"snapshot": 1, "refresh": 0}
