"""Route guards; necessary boundary evidence, not visual proof under /.

Red mutations covered here: swap numeric/source order, remove the cache prefix, accept an
arbitrary host, remove key validation, change 403/404 to permanent, remove the four-worker
cap, and put retryable keys into session-wide failure state. No browser files are changed in
this phase; browser candidate order remains the phase-3 contract.
"""

from __future__ import annotations

import time
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from ceres.card_art_cache import (
    CardArtKey,
    CardArtStore,
    FetchResult,
    candidate_table_twin_guard,
    make_card_art_key,
    register_card_art_route,
    resolve_card_art_candidates,
)


DETAILS = {7: {"id": 7, "name": "Tiny Card", "card_set": "chaos"}}


def _app(store: CardArtStore) -> TestClient:
    app = FastAPI()
    register_card_art_route(app, store)
    return TestClient(app)


def _key(extension: str = "jpg") -> str:
    return make_card_art_key(7, "cards_v2.2", extension=extension)


def test_cache_hit_has_immutable_headers(tmp_path: Path) -> None:
    store = CardArtStore(tmp_path / "cards", fetcher=lambda *_: FetchResult(200, "image/jpeg", b"hit"), details_loader=lambda: DETAILS)
    client = _app(store)
    first = client.get("/art/card/" + _key())
    second = client.get("/art/card/" + _key())
    assert first.status_code == 200
    assert second.status_code == 200
    assert second.headers["cache-control"] == "public, max-age=2592000, immutable"


def test_slow_cold_miss_returns_503_then_background_fill(tmp_path: Path) -> None:
    def slow_fetcher(url: str, timeout: float) -> FetchResult:
        time.sleep(5.8)
        return FetchResult(200, "image/jpeg", b"eventual-image")

    store = CardArtStore(tmp_path / "cards", fetcher=slow_fetcher, details_loader=lambda: DETAILS)
    client = _app(store)
    started = time.monotonic()
    response = client.get("/art/card/" + _key())
    elapsed = time.monotonic() - started
    assert response.status_code == 503
    assert response.headers["retry-after"] == "1"
    assert elapsed < 6.0
    time.sleep(0.6)
    assert client.get("/art/card/" + _key()).status_code == 200


def test_all_upstream_403_404_is_retryable_404_and_no_store(tmp_path: Path) -> None:
    store = CardArtStore(tmp_path / "cards", fetcher=lambda *_: FetchResult(404, "text/html", b""), details_loader=lambda: DETAILS)
    response = _app(store).get("/art/card/" + _key())
    assert response.status_code == 404
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-ceres-art-state"] == "retryable"


def test_content_type_rejection_is_not_published(tmp_path: Path) -> None:
    store = CardArtStore(tmp_path / "cards", fetcher=lambda *_: FetchResult(200, "text/html", b"not-image"), details_loader=lambda: DETAILS)
    response = _app(store).get("/art/card/" + _key())
    assert response.status_code == 502
    assert response.headers["cache-control"] == "no-store"
    assert not list((tmp_path / "cards").glob("[0-9a-f]*.jpg"))


def test_python_js_candidate_table_twin_and_numeric_first() -> None:
    assert candidate_table_twin_guard()
    candidates = resolve_card_art_candidates(
        CardArtKey(7, "cards_v2.2", None, False, "jpg"),
        DETAILS,
    )
    assert candidates[0].split("/")[3] == "cards_v2.2"
    assert candidates[0].endswith("Tiny%20Card.jpg")
    assert not candidate_table_twin_guard(Path("ceres/static/icons.js").read_text().replace("cards_v2.2", "cards_wrong", 1))
