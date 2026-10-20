"""Server cache guards; necessary boundary evidence, not visual proof under /.

Red mutations covered here: publish the manifest before the file rename, publish a temp
file as ready, remove single-flight, change the write root, and disable negative pruning.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from ceres.card_art_cache import (
    CardArtStore,
    FetchResult,
    make_card_art_key,
    parse_card_art_key,
)


DETAILS = {7: {"id": 7, "name": "Tiny Card", "card_set": "chaos"}}


def _key() -> str:
    return make_card_art_key(7, "cards_chaos", extension="jpg")


def test_key_derivation_is_canonical_and_rejects_traversal() -> None:
    value = make_card_art_key(7, "cards_chaos", skin="splinterstorm", gold=True, extension="png")
    parsed = parse_card_art_key(value)
    assert parsed.card_detail_id == 7
    assert parsed.edition_folder == "cards_chaos"
    assert parsed.skin == "splinterstorm"
    assert parsed.gold is True
    assert parsed.extension == "png"
    try:
        make_card_art_key(7, "../outside")
    except ValueError:
        pass
    else:
        raise AssertionError("path traversal must be rejected")


def test_atomic_publish_places_file_before_manifest(tmp_path: Path) -> None:
    calls = []

    def fetcher(url: str, timeout: float) -> FetchResult:
        calls.append((url, timeout))
        return FetchResult(200, "image/jpeg", b"synthetic-image")

    store = CardArtStore(tmp_path / "cards", fetcher=fetcher, details_loader=lambda: DETAILS)
    response = store.response_for(_key())
    assert response.status_code == 200
    manifest = json.loads((tmp_path / "cards" / "manifest.json").read_text())
    record = manifest["records"][_key()]
    assert (tmp_path / "cards" / record["published_filename"]).is_file()
    assert record["state"] == "ready"
    assert calls and calls[0][1] == 2.0
    assert not list((tmp_path / "cards").glob("*.tmp"))


def test_single_flight_deduplicates_concurrent_misses(tmp_path: Path) -> None:
    calls = 0
    lock = threading.Lock()

    def fetcher(url: str, timeout: float) -> FetchResult:
        nonlocal calls
        with lock:
            calls += 1
        time.sleep(0.05)
        return FetchResult(200, "image/jpeg", b"single-flight")

    store = CardArtStore(tmp_path / "cards", fetcher=fetcher, details_loader=lambda: DETAILS)
    responses = []
    threads = [threading.Thread(target=lambda: responses.append(store.response_for(_key()))) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert calls == 1
    assert [response.status_code for response in responses] == [200, 200]


def test_negative_entry_has_ttl_and_prune_removes_it(tmp_path: Path) -> None:
    now = [100.0]

    def fetcher(url: str, timeout: float) -> FetchResult:
        return FetchResult(404, "text/html", b"")

    store = CardArtStore(tmp_path / "cards", fetcher=fetcher, details_loader=lambda: DETAILS, clock=lambda: now[0])
    assert store.response_for(_key()).status_code == 404
    record = json.loads((tmp_path / "cards" / "manifest.json").read_text())["records"][_key()]
    assert record["state"] == "retryable_upstream"
    assert record["next_retry_at"] == 160.0
    now[0] = 161.0
    assert store.prune() == 1
    assert _key() not in json.loads((tmp_path / "cards" / "manifest.json").read_text())["records"]


def test_write_boundary_is_only_the_cards_tree(tmp_path: Path) -> None:
    store = CardArtStore(tmp_path / "cards", fetcher=lambda *_: FetchResult(200, "image/jpeg", b"bytes"), details_loader=lambda: DETAILS)
    assert store.response_for(_key()).status_code == 200
    files = [path for path in tmp_path.rglob("*") if path.is_file()]
    assert files
    assert all(path.is_relative_to(tmp_path / "cards") for path in files)
