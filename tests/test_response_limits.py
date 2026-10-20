import gzip
import io
import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from ceres import routes_candidates
from praetoria.land_analysis import LandCapture
from praetoria.transport import LARGE_RESPONSE_BYTES, MAX_RESPONSE_BYTES, FixedGetTransport, ResponseTooLarge


class _Response:
    def __init__(self, body: bytes, headers: dict[str, str] | None = None):
        self._stream = io.BytesIO(body)
        self.headers = headers or {}
        self.read_calls: list[int] = []

    def read(self, amount: int = -1) -> bytes:
        self.read_calls.append(amount)
        return self._stream.read(amount)

    def close(self) -> None:
        self._stream.close()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


class _Opener:
    mnemosyne_observation = "single_attempt"

    def __init__(self, response: _Response):
        self.response = response

    def open(self, _request, timeout):
        return self.response


class _TooLargeLandClient:
    def __init__(self, error: ResponseTooLarge):
        self.error = error

    def bundle(self, _player):
        raise self.error


class _NoopCollectionClient:
    def collection(self, _player):
        raise AssertionError("collection should not affect the oversized-bundle outcome")

    def details(self):
        raise AssertionError("card details should not affect the oversized-bundle outcome")

    def item_details(self):
        raise AssertionError("item details should not affect the oversized-bundle outcome")


def test_streaming_limit_aborts_plain_body_before_materialising_the_payload():
    # RED MUTATION: replace `_read_limited` with `response.read(limit + 1)`; this guard must turn red.
    response = _Response(b"x" * 100)
    transport = FixedGetTransport(max_response_bytes=16, opener=_Opener(response))

    try:
        transport.get_json("https://api.splinterlands.com/cards/get_details")
    except ResponseTooLarge as exc:
        assert exc.result["status"] == "response_too_large"
        assert exc.result["endpoint"] == "/cards/get_details"
        assert exc.result["trace_id"]
        assert exc.result["freshness"]["status"] == "unavailable"
    else:
        raise AssertionError("oversized response was accepted")
    assert max(response.read_calls) <= 16 + 1


def test_streaming_limit_applies_after_gzip_decompression():
    # RED MUTATION: enforce the compressed byte count instead of decoded bytes; this guard must turn red.
    response = _Response(gzip.compress(b"x" * 100), {"Content-Encoding": "gzip"})
    transport = FixedGetTransport(max_response_bytes=16, opener=_Opener(response))

    try:
        transport.get_json("https://api.splinterlands.com/cards/get_details")
    except ResponseTooLarge as exc:
        assert exc.result["observed_bytes"] > 16
    else:
        raise AssertionError("oversized decompressed response was accepted")


def test_default_cap_is_tight_for_an_endpoint_without_an_override():
    # RED MUTATION: return the large cap for every endpoint; this guard must turn red.
    transport = FixedGetTransport()
    assert transport._response_limit("https://api.splinterlands.com/cards/get_details") == MAX_RESPONSE_BYTES


def test_large_endpoint_override_is_explicit():
    # RED MUTATION: remove the collection/deeds overrides; this guard must turn red.
    transport = FixedGetTransport()
    assert transport._response_limit("https://api.splinterlands.com/cards/collection/player0017") == LARGE_RESPONSE_BYTES
    assert transport._response_limit("https://vapi.splinterlands.com/land/deeds?player=player0017") == LARGE_RESPONSE_BYTES


def test_bundle_over_limit_returns_an_actionable_outcome():
    # RED MUTATION: let the bundle exception escape from `capture_quick`; this guard must turn red.
    error = ResponseTooLarge("/land/deeds?player=player0017", "trace-bundle", LARGE_RESPONSE_BYTES, LARGE_RESPONSE_BYTES + 1)
    capture = LandCapture(
        land_client=_TooLargeLandClient(error),
        collection_client=_NoopCollectionClient(),
        cache=object(),
        min_delay=0,
        sleeper=lambda _delay: None,
    )

    report = capture.capture_quick("player0017")
    assert report["status"] == "response_too_large"
    assert report["response_too_large"]["endpoint"] == "/land/deeds?player=player0017"
    assert report["capture"]["outcome"]["trace_id"] == "trace-bundle"


def test_card_picker_exposes_the_collection_failure_without_a_success_response(monkeypatch, caplog):
    report = {
        "plots": [{"deed_uid": "deed"}],
        "capture": {
            "failures": [{
                "deed_uid": "collection",
                "outcome": {
                    "status": "response_too_large",
                    "endpoint": "/cards/collection/player0017",
                    "trace_id": "trace-collection",
                },
            }],
        },
    }
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(ceres_config=object())))
    refreshes = []

    def failed_refresh(_config, player, deed_uid, include_collection=False, **_kwargs):
        refreshes.append((player, deed_uid, include_collection))
        raise RuntimeError("simulated collection refresh failure")

    def ranking_must_not_run(*_args, **_kwargs):
        raise AssertionError("candidates were ranked from a card pool that could not be read")

    monkeypatch.setattr(routes_candidates.capture_mod, "latest_snapshot", lambda _config, _player: (report, None))
    monkeypatch.setattr(routes_candidates.capture_mod, "expand_plot", failed_refresh)
    monkeypatch.setattr(routes_candidates, "player_for_request", lambda _request, _player: "player0017")
    monkeypatch.setattr(routes_candidates, "load_details_by_id", lambda: {})
    monkeypatch.setattr(routes_candidates, "rank_candidates", ranking_must_not_run)
    with caplog.at_level("WARNING", logger="ceres.routes_candidates"):
        response = routes_candidates.get_candidates("deed", request, group_id="worker")

    assert refreshes == [("player0017", "deed", True)]
    assert response.status_code == 202
    body = json.loads(response.body)
    assert body["status"] == "card_pool_refresh_retryable"
    assert body["candidates"] == [] and body["total"] == -1
    assert "trace-collection" in caplog.text
