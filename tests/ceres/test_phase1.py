from __future__ import annotations

from pathlib import Path

from ceres import capture as capture_mod
from ceres.config import CeresConfig
from praetoria.cache import JsonCache
from praetoria.land_analysis import AssetRepairContext, LandCapture
from praetoria.transport import ResponseData

from tests.ceres.test_shared_verification_evidence import _bundle


def _complete_bundle() -> dict:
    """Add plot status to every deed so quick capture sees a complete bundle."""
    bundle = _bundle()
    for deed in bundle["data"]["deeds"]:
        deed["plot_status"] = "natural"
    return bundle


class _AssetsOnlyLandClient:
    def __init__(self) -> None:
        self.asset_calls: list[str] = []
        self.bundle_calls = 0

    def bundle(self, player: str) -> ResponseData:
        self.bundle_calls += 1
        return ResponseData(_complete_bundle(), "2037-01-01T00:00:01Z", 1)

    def assets(self, deed_uid: str) -> ResponseData:
        self.asset_calls.append(deed_uid)
        return ResponseData({"status": "success", "data": {"cards": [], "items": []}}, "2037-01-01T00:00:10Z", 1)


class _NoCollectionFetch:
    def __init__(self) -> None:
        self.calls = 0

    def collection(self, player: str) -> ResponseData:
        self.calls += 1
        return ResponseData({"cards": []}, "2037-01-01T00:00:05Z", 1)


def _capture(tmp_path: Path) -> tuple[LandCapture, _AssetsOnlyLandClient, _NoCollectionFetch]:
    land_client = _AssetsOnlyLandClient()
    collection_client = _NoCollectionFetch()
    capture = LandCapture(
        land_client=land_client,
        collection_client=collection_client,
        cache=JsonCache(tmp_path / "cache"),
        min_delay=0.0,
    )
    capture._asset_repair_context = AssetRepairContext(
        player="player0005",
        bundle=_complete_bundle(),
        bundle_observed_at="2037-01-01T00:00:01Z",
        collection={"cards": []},
        collection_observed_at="2037-01-01T00:00:05Z",
        prior_resources_by_deed={},
        card_names={},
        item_catalogue=[{"id": 7, "name": "Power Core"}],
        unassigned_items={"power_core": {"items": [{"uid": "core-1"}]}},
        include_asset_ids=True,
    )
    return capture, land_client, collection_client


def test_asset_repair_deduplicates_in_queue_order_and_reads_assets_only(tmp_path):
    """Mutation: route repair through expand_deed or add per-deed bundle/collection reads; this must fail."""
    capture, land_client, collection_client = _capture(tmp_path)
    land_client.bundle("player0005")
    collection_client.collection("player0005")

    repaired = capture.repair_assets(["deed-2", "deed-1", "deed-2"], "player0005")

    assert land_client.asset_calls == ["deed-2", "deed-1"]
    assert land_client.bundle_calls == 1
    assert collection_client.calls == 1
    assert repaired["item_catalogue"] == [{"id": 7, "name": "Power Core"}]
    assert repaired["unassigned_items"] == {"power_core": {"items": [{"uid": "core-1"}]}}


def test_asset_repair_merge_preserves_account_sections(tmp_path, monkeypatch):
    """Mutation: replace the report wholesale or blank an account section; this must fail."""
    capture, _, _ = _capture(tmp_path)
    base = capture.repair_assets([], "player0005")
    base["item_catalogue"] = [{"id": 11, "name": "Ancient Totem"}]
    base["unassigned_items"] = {"totem": {"items": [{"uid": "totem-1"}]}}
    base["coverage"]["assets"]["needs_refetch"] = ["deed-1"]
    sectionless = dict(base)
    sectionless.pop("item_catalogue")
    sectionless.pop("unassigned_items")
    capture.repair_assets = lambda deed_uids, player: sectionless
    published: list[dict] = []

    def publish_once(config, player, prior, incoming, queued):
        patch = capture_mod._asset_repair_patch(prior, incoming, queued)
        published.append(patch)
        return capture_mod.merge_reports(prior, patch)

    monkeypatch.setattr(capture_mod, "_publish_asset_repair", publish_once)
    merged = capture_mod._self_heal_refetches(
        CeresConfig(root=tmp_path / "ceres"), "player0005", base, capture=capture,
    )

    assert len(published) == 1
    assert merged["item_catalogue"] == base["item_catalogue"]
    assert merged["unassigned_items"] == base["unassigned_items"]


def test_quick_capture_repairs_with_one_bulk_read_and_one_publication(tmp_path, monkeypatch):
    """Mutation: add per-deed bulk reads or saves; one run must turn red."""
    capture, land_client, collection_client = _capture(tmp_path)
    capture._asset_repair_context = None
    monkeypatch.setattr(capture, "_capture_prior_resources", lambda *args, **kwargs: {})
    monkeypatch.setattr(capture, "_card_names", lambda *args, **kwargs: {})
    monkeypatch.setattr(capture, "_fetch_item_catalogue", lambda *args, **kwargs: [{"id": 7, "name": "Power Core"}])
    monkeypatch.setattr(capture, "_capture_unassigned_items", lambda *args, **kwargs: {"power_core": {"items": [{"uid": "core-1"}]}})

    report = capture.capture_quick("player0005", include_asset_ids=True)
    report.setdefault("coverage", {}).setdefault("assets", {})["needs_refetch"] = ["deed-2", "deed-1"]
    # only genuinely failed/stale reads enter self-heal; never-requested deeds stay on demand.
    retry_statuses = {"deed-1": "failed", "deed-2": "dropped_stale"}
    for plot in report["plots"]:
        if plot.get("deed_uid") in retry_statuses:
            plot["asset_fetch"] = {"status": retry_statuses[plot["deed_uid"]], "reason": "upstream timeout"}
    saves: list[dict] = []

    def save_locked(value, path, *, pool_may_have_changed=True, before_pool_write=None):
        saves.append(value)
        return None, value, value, None, None

    monkeypatch.setattr(capture_mod, "latest_snapshot", lambda config, player: (report, None))
    monkeypatch.setattr(capture_mod, "_save_snapshot_and_refresh_cache_locked", save_locked)
    monkeypatch.setattr(capture_mod, "_append_coverage_journal", lambda *args: None)

    capture_mod._self_heal_refetches(CeresConfig(root=tmp_path / "ceres"), "player0005", report, capture=capture)

    assert land_client.bundle_calls == 1
    assert collection_client.calls == 1
    assert land_client.asset_calls == ["deed-2", "deed-1"]
    assert len(saves) == 1
