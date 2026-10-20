""" S6 (v1 clean-up, audit findings L3-04, L3-06, ...): caches must not serve stale data or collide."""
import threading
from pathlib import Path

from praetoria.cache import JsonCache

ROOT = Path(__file__).parents[2]


def test_l3_06_concurrent_writers_of_one_key_never_fail_or_mix(tmp_path):
    cache = JsonCache(tmp_path)
    errors = []

    def writer(index):
        try:
            for round_ in range(40):
                cache.write("shared-key", {"writer": index, "round": round_, "pad": "x" * 5000}, "2026-10-01T00:00:00Z")
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=writer, args=(i,)) for i in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert not errors
    data, info = cache.read("shared-key", ttl=600, offline=False)
    assert data["pad"] == "x" * 5000 and info.state == "hit_fresh"
    assert not list(tmp_path.glob("*.tmp"))        # no temporary file left behind


def test_l3_04_quick_capture_reads_spare_items_live():
    source = (ROOT / "praetoria" / "land_analysis.py").read_text(encoding="utf-8")
    assert "ttl=300," not in source.split("def _capture_unassigned_after_catalogue")[1].split("bundle_error: ResponseTooLarge")[0]
    assert source.count("include_asset_ids, catalogue_lookup, ttl=0,") == 2


# L3-08: a temporary metadata failure must not become a permanent "no artwork"
def test_l3_08_artwork_recovers_after_a_metadata_failure(tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from ceres.card_art_cache import CardArtStore, FetchResult, make_card_art_key, register_card_art_route

    now = [1000.0]
    state = {"broken": True}

    def details():
        if state["broken"]:
            raise RuntimeError("card metadata unavailable")
        return {7: {"id": 7, "name": "Tiny Card", "card_set": "chaos"}}

    store = CardArtStore(tmp_path / "cards", fetcher=lambda *_: FetchResult(200, "image/jpeg", b"art"), details_loader=details, clock=lambda: now[0])
    app = FastAPI()
    register_card_art_route(app, store)
    client = TestClient(app)
    route = "/art/card/" + make_card_art_key(7, "cards_v2.2", extension="jpg")
    assert client.get(route).status_code in (404, 503)            # metadata is down
    state["broken"] = False
    now[0] += 24 * 3600                                           # past any retry deadline
    assert client.get(route).status_code == 200                   # it asks again and the artwork appears


# L3-07: the shared SPS rate must not carry the measuring account's DEC shortage
def test_l3_07_the_shared_rate_is_the_intrinsic_one():
    from ceres import sps_rate
    board = {"generated_at": "2026-10-01T00:00:00Z", "plots": [
        {"resource": "SPS", "harvest_pp": 100000, "work_per_hour": 5.0, "work_per_hour_unscaled": 10.0}]}   # 50% DEC efficiency on this account
    assert abs(sps_rate.measure(board)["rate"] - 0.0001) < 1e-12                # 10 / 100,000, not 5 / 100,000
    legacy = {"plots": [{"resource": "SPS", "harvest_pp": 100000, "work_per_hour": 8.0}]}
    assert abs(sps_rate.measure(legacy)["rate"] - 0.00008) < 1e-12                # no unscaled figure: unchanged behaviour


# L3-03: the collection memo never pairs an ETag with another writer's payload
def test_l3_03_a_payload_that_does_not_match_its_tag_is_not_reused(tmp_path):
    from praetoria.errors import SchemaError
    from praetoria.land_analysis import CollectionMemo
    memo = CollectionMemo(tmp_path)
    memo.remember("alice", "etag-1", {"player": "alice", "cards": [{"uid": "A"}]}, True)
    memo._memory.clear()                                       # force the disk path
    etag, load = memo.previous("alice")
    assert etag == "etag-1" and load()["cards"] == [{"uid": "A"}] and "_memo_etag" not in load()
    memo.remember("alice", "etag-2", {"player": "alice", "cards": [{"uid": "B"}]}, True)   # another writer replaces the pair
    memo._memory.clear()
    tag_path, _payload_path = memo._paths("alice")
    tag_path.write_text("etag-1\n", encoding="utf-8")        # a stale tag next to the new payload (the torn state the audit reproduced)
    stale_tag, stale_load = memo.previous("alice")
    import pytest
    with pytest.raises(SchemaError):
        stale_load()
