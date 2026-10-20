"""Quick producer guards for collection-backed worker identity."""

from __future__ import annotations

import json
from pathlib import Path

from praetoria.land_analysis import analyze_bundle
from praetoria.land_analysis import LandCapture
from praetoria.cache import JsonCache
from praetoria.transport import ResponseData


LAND_FIXTURES = Path(__file__).parents[1] / "fixtures" / "land"


def _fixture(name: str):
    return json.loads((LAND_FIXTURES / name).read_text(encoding="utf-8"))


def test_quick_collection_workers_join_names_without_asset_only_fields():
    details = _fixture("card_details_sample.json")
    card_names = {item["id"]: item["name"] for item in details if item.get("id") is not None and item.get("name")}
    report = analyze_bundle(
        "player0005",
        _fixture("deeds_multi.json"),
        {},
        collection_payload=_fixture("collection_sample.json"),
        card_names=card_names,
    )

    assert report["workers"]
    expected_names = {item["id"]: item["name"] for item in details if item.get("id") is not None and item.get("name")}
    assert all(worker["name"] == expected_names[worker["card_detail_id"]] for worker in report["workers"])
    assert all("slot" not in worker for worker in report["workers"])
    assert all(worker.get("slot") in (None, "", 0) for worker in report["workers"])


def test_quick_collection_workers_without_card_names_remain_undetailed():
    report = analyze_bundle(
        "player0005",
        _fixture("deeds_multi.json"),
        {},
        collection_payload=_fixture("collection_sample.json"),
        card_names=None,
    )

    assert report["workers"]
    assert all("name" not in worker for worker in report["workers"])


def test_card_details_fetch_uses_counted_request_path(tmp_path):
    details = _fixture("card_details_sample.json")

    class DetailsClient:
        def details(self):
            return ResponseData(details, "2026-09-04T00:00:00Z", 1)

    capture = LandCapture(
        collection_client=DetailsClient(),
        cache=JsonCache(tmp_path),
        min_delay=0,
        sleeper=lambda _delay: None,
    )

    names = capture._card_names(offline=False)

    assert names[details[0]["id"]] == details[0]["name"]
    assert capture._requests == 1
