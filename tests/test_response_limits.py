import gzip
import io
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
    assert transport._response_limit("https://api.splinterlands.com/cards/collection/player0016") == LARGE_RESPONSE_BYTES
    assert transport._response_limit("https://vapi.splinterlands.com/land/deeds?player=player0016") == LARGE_RESPONSE_BYTES


def test_bundle_over_limit_returns_an_actionable_outcome():
    # RED MUTATION: let the bundle exception escape from `capture_quick`; this guard must turn red.
    error = ResponseTooLarge("/land/deeds?player=player0016", "trace-bundle", LARGE_RESPONSE_BYTES, LARGE_RESPONSE_BYTES + 1)
    capture = LandCapture(
        land_client=_TooLargeLandClient(error),
        collection_client=_NoopCollectionClient(),
        cache=object(),
        min_delay=0,
        sleeper=lambda _delay: None,
    )

    report = capture.capture_quick("player0016")
    assert report["status"] == "response_too_large"
    assert report["response_too_large"]["endpoint"] == "/land/deeds?player=player0016"
    assert report["capture"]["outcome"]["trace_id"] == "trace-bundle"


def test_card_picker_exposes_the_collection_failure_without_a_success_response():
    # RED MUTATION: keep the old 502/200 fallback; this guard must turn red.
    report = {
        "capture": {
            "failures": [{
                "deed_uid": "collection",
                "outcome": {
                    "status": "response_too_large",
                    "endpoint": "/cards/collection/player0016",
                    "trace_id": "trace-collection",
                },
            }],
        },
    }
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(ceres_config=object())))

    original_snapshot = routes_candidates.capture_mod.latest_snapshot
    original_player = routes_candidates.player_for_request
    original_details = routes_candidates.load_details_by_id
    routes_candidates.capture_mod.latest_snapshot = lambda _config, _player: (report, None)
    routes_candidates.player_for_request = lambda _request, _player: "player0016"
    routes_candidates.load_details_by_id = lambda: {}
    try:
        with pytest.raises(HTTPException) as raised:
            routes_candidates.get_candidates("deed", request, group_id="worker")
    finally:
        routes_candidates.capture_mod.latest_snapshot = original_snapshot
        routes_candidates.player_for_request = original_player
        routes_candidates.load_details_by_id = original_details
    assert raised.value.status_code == 503
    assert "card collection could not be read" in str(raised.value.detail)
    assert "trace-collection" in str(raised.value.detail)
