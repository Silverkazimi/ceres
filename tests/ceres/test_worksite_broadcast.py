""" Phase 4 -- `ceres/worksite_broadcast.py::build_worksite_preview`.

Fixture shapes match real snapshots under ~/.ceres/snapshots/ (facts.region carries the
region_uid string, e.g. "PR-CEF-78"; facts.terrain is capitalised, e.g. "Plains";
diagnostics is a list of {"type": ...} dicts -- board.py reads `item.get("type")`).
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from fides_land.ops import LandOpError
from ceres import capture as capture_mod
from ceres.config import CeresConfig
from ceres.routes_op_preview import plot_worksite_preview
from ceres.worksite_broadcast import build_worksite_completion_preview, build_worksite_preview
from ceres.transaction_identity import app_identity
from praetoria.transport import ResponseData


def _plot(
    deed_uid: str = "I-290-deed1",
    *,
    terrain: str = "Plains",
    resource: str = "GRAIN",
    status: str = "natural",
    plot_status: str = "natural",
    region: str = "PR-CEF-78",
    project_type: str = "worksite_grain",
    is_powered: bool = True,
    worker_count: int = 5,
    diagnostics: list[dict] | None = None,
) -> dict:
    return {
        "deed_uid": deed_uid,
        "facts": {
            "plot": "78-10-50", "region": region, "territory": "Central Fire",
            "resource": resource, "rarity": "common", "status": status,
            "worksite_type": "Grain Farm", "terrain": terrain,
        },
        "worksite_facts": {"project_type": project_type},
        "staking_facts": {
            "is_powered": is_powered, "worker_count": worker_count,
            "max_workers_allowed": 5, "total_work_per_hour": 1284,
        },
        "diagnostics": diagnostics or [],
    }


def _report(plot: dict, *, tc_balance=None) -> dict:
    return {"plots": [plot], "tc_balance": tc_balance}


class TestBuildWorksitePreview:
    def test_builds_a_real_signable_op_for_a_healthy_plot(self):
        report = _report(_plot())
        preview = build_worksite_preview(report, "I-290-deed1", "research", account="player0005")
        assert preview["account"] == "player0005"
        assert preview["key_tier"] == "Posting"
        assert preview["chunk_count"] == 1
        op = json.loads(preview["operations"][0][1]["json"])
        op.pop("n")
        assert op == {
            "op": "worksite_research_construction", "region_uid": "PR-CEF-78",
            "deed_uids": ["I-290-deed1"], "time_crystals": 0, "app": app_identity(),
        }
        codes = {n["code"] for n in preview["notices"]}
        assert "worksite_swap_auto_harvest" in codes

    def test_aura_target_carries_the_permit_notice(self):
        report = _report(_plot())
        preview = build_worksite_preview(report, "I-290-deed1", "aura", account="player0005")
        codes = {n["code"] for n in preview["notices"]}
        assert "worksite_swap_aura_permit" in codes

    def test_time_crystals_opt_in_carries_through_to_the_op(self):
        report = _report(_plot())
        preview = build_worksite_preview(report, "I-290-deed1", "research", account="player0005", time_crystals=50)
        op = json.loads(preview["operations"][0][1]["json"])
        assert op["time_crystals"] == 50

    def test_refuses_zero_pp_plot_h1(self):
        report = _report(_plot(is_powered=False, worker_count=0))
        with pytest.raises(LandOpError, match="zero PP"):
            build_worksite_preview(report, "I-290-deed1", "research", account="player0005")

    def test_refuses_illegal_target_for_terrain_h5(self):
        # Plains is a grain terrain -- "iron" is not one of its four legal targets.
        report = _report(_plot(terrain="Plains"))
        with pytest.raises(LandOpError, match="not one of this plot's legal worksites"):
            build_worksite_preview(report, "I-290-deed1", "iron", account="player0005")

    def test_first_clear_builds_with_zero_crystals(self):
        # (his decision 2026-09-14): a never-cleared plot may start its first clear.
        report = _report(_plot(diagnostics=[{"type": "no_worksite"}]))
        preview = build_worksite_preview(report, "I-290-deed1", "research", account="player0005")
        assert preview["first_clear"] is True
        assert preview["time_crystals"] == 0
        assert not any(n.get("code") == "worksite_first_clear_unverified" for n in preview["notices"])

    def test_first_clear_with_zero_workers_is_allowed_with_plain_warning(self):
        report = _report(_plot(worker_count=0, diagnostics=[{"type": "no_worksite"}]))
        preview = build_worksite_preview(report, "I-290-deed1", "research", account="player0005")
        warning = next(n for n in preview["notices"] if n["code"] == "worksite_first_clear_no_workers")
        assert warning["blocking"] == "none"
        assert warning["message"] == "The build will not progress until workers are staked."

    def test_first_clear_refuses_the_crystal_conversion(self):
        report = _report(_plot(diagnostics=[{"type": "no_worksite"}]))
        with pytest.raises(LandOpError, match="first clear is PP-scaled"):
            build_worksite_preview(report, "I-290-deed1", "research", account="player0005", time_crystals=50)

    def test_refuses_when_already_mid_change(self):
        report = _report(_plot(project_type="worksite_iron_construction"))
        with pytest.raises(LandOpError, match="not available"):
            build_worksite_preview(report, "I-290-deed1", "research", account="player0005")

    def test_refuses_keep_castle_plot(self):
        report = _report(_plot(project_type="castle_operation", plot_status="kingdom", status="kingdom"))
        with pytest.raises(LandOpError, match="not available"):
            build_worksite_preview(report, "I-290-deed1", "research", account="player0005")

    def test_refuses_unknown_deed(self):
        report = _report(_plot())
        with pytest.raises(ValueError, match="unknown deed_uid"):
            build_worksite_preview(report, "not-a-real-deed", "research", account="player0005")

    def test_time_crystal_balance_unknown_notice_only_when_opted_in_and_unknown(self):
        report = _report(_plot(), tc_balance=None)
        preview_off = build_worksite_preview(report, "I-290-deed1", "research", account="player0005", time_crystals=0)
        assert not any(n["code"] == "worksite_swap_tc_balance_unknown" for n in preview_off["notices"])
        preview_on = build_worksite_preview(report, "I-290-deed1", "research", account="player0005", time_crystals=50)
        assert any(n["code"] == "worksite_swap_tc_balance_unknown" for n in preview_on["notices"])

    def test_allowlist_check_runs_and_passes_for_a_well_formed_op(self):
        # Proves build_worksite_preview actually runs evaluate_allowlist rather than
        # trusting worksite_change_op's own guards alone -- a defence-in-depth check.
        report = _report(_plot())
        preview = build_worksite_preview(report, "I-290-deed1", "research", account="player0005")
        assert preview["operations"]

    def test_rejects_foreign_app_tag(self):
        with pytest.raises(LandOpError, match="must use app"):
            build_worksite_preview(_report(_plot()), "I-290-deed1", "research", account="player0005", app="peakmonsters")

    def test_ready_construction_builds_feed_worker_op_with_live_grain_requirement(self):
        plot = _plot(project_type="worksite_stone_construction")
        plot["worksite_facts"].update({
            "project_id": 397948, "projected_end": "2020-01-01T00:00:00Z",
            "hours_to_completion": 0, "grain_required": 500,
        })
        preview = build_worksite_completion_preview(_report(plot), plot["deed_uid"], account="player0005")
        payload = json.loads(preview["operations"][0][1]["json"])
        assert preview["kind"] == "worksite_completion"
        assert preview["grain_cost"] == 500
        assert payload["op"] == "update_worksite"
        assert payload["project_id"] == 397948
        assert payload["time_crystals"] == 0
        assert payload["auto_buy_grain"] is False
        from ceres.transaction_identity import app_identity
        assert payload["app"] == app_identity()
        assert "peakmonsters" not in json.dumps(payload).lower()

    def test_worksite_override_wins_over_a_stale_captured_report(self):
        # the stored capture can predate the countdown finishing (the build
        # completed while the board was already open); a live per-plot read overrides it rather
        # than the (stale) captured worksite_facts refusing a genuinely-ready completion.
        plot = _plot(project_type="worksite_stone_construction")
        plot["worksite_facts"].update({"hours_to_completion": 5, "projected_end": "2099-01-01T00:00:00Z"})
        with pytest.raises(LandOpError, match="not ready"):
            build_worksite_completion_preview(_report(plot), plot["deed_uid"], account="player0005")
        fresh = {
            "project_type": "worksite_stone_construction", "project_id": 397948,
            "projected_end": "2020-01-01T00:00:00Z", "hours_to_completion": 0, "grain_required": 500,
        }
        preview = build_worksite_completion_preview(
            _report(plot), plot["deed_uid"], account="player0005", worksite_override=fresh,
        )
        assert preview["kind"] == "worksite_completion"
        assert preview["grain_cost"] == 500


class TestWorksitePreviewRoute:
    """`POST /api/plots/{deed_uid}/worksite-preview` -- the browser-facing entry point,
 mirroring `test_op_preview.py::test_http_preview_contract_and_no_broadcast`'s pattern."""

    def _request(self, tmp_path, report):
        config = CeresConfig(root=tmp_path / ".ceres", player="player0005")
        return config, SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(ceres_config=config)))

    def test_http_contract_and_no_broadcast(self, monkeypatch, tmp_path):
        report = _report(_plot())
        config, request = self._request(tmp_path, report)
        monkeypatch.setattr(capture_mod, "latest_snapshot", lambda config, player: (report, object()))
        response = plot_worksite_preview("I-290-deed1", {"target": "research", "time_crystals": 0}, request)
        assert response.status_code == 200
        body = json.loads(response.body)
        assert body["key_tier"] == "Posting"
        op = body["chunks"][0]["operations"][0]
        assert op[0] == "custom_json"
        assert op[1]["id"] == "sm_land_operation"
        assert json.loads(op[1]["json"])["op"] == "worksite_research_construction"

    def test_http_400_on_zero_pp_refusal(self, monkeypatch, tmp_path):
        report = _report(_plot(is_powered=False, worker_count=0))
        config, request = self._request(tmp_path, report)
        monkeypatch.setattr(capture_mod, "latest_snapshot", lambda config, player: (report, object()))
        with pytest.raises(HTTPException) as excinfo:
            plot_worksite_preview("I-290-deed1", {"target": "research", "time_crystals": 0}, request)
        assert excinfo.value.status_code == 400
        assert "zero PP" in excinfo.value.detail

    def test_http_400_on_missing_target(self, monkeypatch, tmp_path):
        report = _report(_plot())
        config, request = self._request(tmp_path, report)
        monkeypatch.setattr(capture_mod, "latest_snapshot", lambda config, player: (report, object()))
        with pytest.raises(HTTPException) as excinfo:
            plot_worksite_preview("I-290-deed1", {}, request)
        assert excinfo.value.status_code == 400

    def test_http_503_when_no_snapshot(self, monkeypatch, tmp_path):
        config, request = self._request(tmp_path, {})
        monkeypatch.setattr(capture_mod, "latest_snapshot", lambda config, player: (None, None))
        with pytest.raises(HTTPException) as excinfo:
            plot_worksite_preview("I-290-deed1", {"target": "research"}, request)
        assert excinfo.value.status_code == 503

    def test_complete_action_reads_a_live_worksite_row_not_the_stale_capture(self, monkeypatch, tmp_path):
        # the captured report says still-building (not ready); the live read
        # says the build finished. The route must trust the live read, never the capture.
        from ceres import routes_op_preview

        plot = _plot(project_type="worksite_stone_construction")
        plot["worksite_facts"].update({"hours_to_completion": 5, "projected_end": "2099-01-01T00:00:00Z"})
        report = _report(plot)
        config, request = self._request(tmp_path, report)
        monkeypatch.setattr(capture_mod, "latest_snapshot", lambda config, player: (report, object()))

        fresh_row = {
            "project_type": "worksite_stone_construction", "project_id": 397948, "deed_uid": plot["deed_uid"],
            "projected_end": "2020-01-01T00:00:00Z", "hours_to_completion": 5, "grain_required": 500,
        }

        class _FakeResult:
            data = {"status": "success", "data": fresh_row}

        monkeypatch.setattr(routes_op_preview._worksite_client, "active_project", lambda deed_uid: _FakeResult())
        response = plot_worksite_preview(plot["deed_uid"], {"action": "complete"}, request)
        assert response.status_code == 200
        body = json.loads(response.body)
        assert body["kind"] == "worksite_completion"
        assert body["grain_cost"] == 500

    def test_complete_action_does_not_fall_back_to_capture_when_live_row_is_empty(self, monkeypatch, tmp_path):
        from ceres import routes_op_preview

        plot = _plot(project_type="worksite_stone_construction")
        plot["worksite_facts"].update({
            "project_id": 397948, "hours_to_completion": 0, "grain_required": 500,
            "projected_end": "2020-01-01T00:00:00Z",
        })
        report = _report(plot)
        config, request = self._request(tmp_path, report)
        monkeypatch.setattr(capture_mod, "latest_snapshot", lambda config, player: (report, object()))

        class _EmptyResult:
            data = {"status": "success", "data": None}

        monkeypatch.setattr(routes_op_preview._worksite_client, "active_project", lambda deed_uid: _EmptyResult())
        with pytest.raises(HTTPException) as excinfo:
            plot_worksite_preview(plot["deed_uid"], {"action": "complete"}, request)
        assert excinfo.value.status_code == 400
        assert "not ready" in excinfo.value.detail

    def test_complete_action_surfaces_a_live_read_failure_instead_of_a_stale_answer(self, monkeypatch, tmp_path):
        from ceres import routes_op_preview

        plot = _plot(project_type="worksite_stone_construction")
        plot["worksite_facts"].update({
            "project_id": 397948, "hours_to_completion": 0, "grain_required": 500,
            "projected_end": "2020-01-01T00:00:00Z",
        })
        report = _report(plot)
        config, request = self._request(tmp_path, report)
        monkeypatch.setattr(capture_mod, "latest_snapshot", lambda config, player: (report, object()))

        def _boom(deed_uid):
            raise TimeoutError("no response")

        monkeypatch.setattr(routes_op_preview._worksite_client, "active_project", _boom)
        with pytest.raises(HTTPException) as excinfo:
            plot_worksite_preview(plot["deed_uid"], {"action": "complete"}, request)
        assert excinfo.value.status_code == 502


class TestFetchWorksiteFacts:
    """`ceres/capture.py::fetch_worksite_facts` -- the fresh (uncached) re-read used ONLY
 for post-broadcast worksite-change domain verification. It reads the land bundle
 directly through `fresh_read`, bypassing cached capture expansion."""

    @staticmethod
    def _install_bundle(monkeypatch, payload):
        calls = []

        def bundle(player):
            calls.append(player)
            return ResponseData(payload, "2099-01-01T00:00:00Z", 1)

        fake_capture = SimpleNamespace(
            land_client=SimpleNamespace(bundle=bundle),
            fresh_read=lambda fetch: fetch(),
        )
        # f3b69201: patch the direct bundle seam used by fetch_worksite_facts.
        monkeypatch.setattr(capture_mod, "LandCapture", lambda: fake_capture)
        return calls

    @staticmethod
    def _bundle(deed_uid="I-290-deed1", *, include_target=True):
        deeds = [{"deed_uid": "other-deed"}]
        worksites = [{"deed_uid": "other-deed", "project_type": "worksite_grain"}]
        if include_target:
            deeds.append({"deed_uid": deed_uid})
            worksites.append({"deed_uid": deed_uid, "project_type": "worksite_aura_construction"})
        return {"status": "success", "data": {"deeds": deeds, "worksite_details": worksites, "staking_details": []}}

    def test_forces_a_fresh_bundle_fetch_and_extracts_the_target_deed(self, tmp_path, monkeypatch):
        config = CeresConfig(root=tmp_path / ".ceres", player="player0005")
        calls = self._install_bundle(monkeypatch, self._bundle())
        result = capture_mod.fetch_worksite_facts(config, "player0005", "I-290-deed1")
        assert result == {"deed_uid": "I-290-deed1", "project_type": "worksite_aura_construction"}
        assert calls == ["player0005"]

    def test_returns_none_when_deed_is_absent_from_the_fresh_bundle(self, tmp_path, monkeypatch):
        config = CeresConfig(root=tmp_path / ".ceres", player="player0005")
        self._install_bundle(monkeypatch, self._bundle(include_target=False))
        assert capture_mod.fetch_worksite_facts(config, "player0005", "I-290-deed1") is None

    def test_does_not_merge_into_or_persist_the_on_disk_snapshot(self, tmp_path, monkeypatch):
        # Evidence for a verification check, never a board mutation -- unlike expand_plot.
        config = CeresConfig(root=tmp_path / ".ceres", player="player0005")
        config.ensure_dirs()
        self._install_bundle(monkeypatch, self._bundle())
        capture_mod.fetch_worksite_facts(config, "player0005", "I-290-deed1")
        existing, _ = capture_mod.latest_snapshot(config, "player0005")
        assert existing is None  # nothing was ever written to disk


def test_first_clear_tiles_carry_the_clearing_estimate():
    from ceres.worksite_swap import build_swap_targets, clearing_pp_required, swap_control_available
    assert clearing_pp_required("Epic", "natural") == 2_100_000
    assert swap_control_available(plot_status="natural", diagnostics=["no_worksite"], project_type=None) is False
    assert swap_control_available(plot_status="natural", diagnostics=["no_worksite"], project_type=None, allow_first_clear=True) is True
    assert swap_control_available(plot_status="natural", diagnostics=["no_worksite"], project_type="worksite_grain_construction", allow_first_clear=True) is False
    tiles = build_swap_targets(terrain="Plains", current_resource=None, status="natural", total_construction_pp=29_500,
                               total_boost=0, deed_status_token_boost=0, total_work_per_hour=0, first_clear=True, rarity="epic")
    assert all(t["first_clear"] and t["tc_cost"] is None for t in tiles)
    assert round(tiles[0]["time_hours"]) == 71  # GitBook worked example: 2.1M PP at 29,500 base PP/h
    empty = build_swap_targets(terrain="Plains", current_resource=None, status="natural", total_construction_pp=0,
                               total_boost=0, deed_status_token_boost=0, total_work_per_hour=0, first_clear=True, rarity="epic")
    assert all(t["time_hours"] is None for t in empty)
