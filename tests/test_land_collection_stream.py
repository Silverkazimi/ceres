""": stream-parsed collection keeps the rows the old path kept; an equal ETag skips the parse."""
from __future__ import annotations

import gzip
import io
import json
from datetime import datetime, timezone

import pytest

import praetoria.api as api
from praetoria import collection_stream
from praetoria.land_analysis import classify_card
from praetoria.transport import FixedGetTransport

NOW = datetime(2026, 9, 14, 12, 0, tzinfo=timezone.utc)
ROWS = [
    {"uid": "C1", "card_detail_id": 1, "stake_ref_uid": "DEED-1", "stake_end_date": None, "xp": 5, "player": "p",
     "last_transferred_block": 109499809, "last_transferred_date": "2026-08-31T12:00:00.000Z"},
    {"uid": "C2", "card_detail_id": 2, "stake_ref_uid": "DEED-1", "stake_end_date": "2026-09-16T00:00:00.000Z", "mint": 3},
    {"uid": "C3", "card_detail_id": 3, "stake_ref_uid": None, "stake_end_date": None, "display_name": 'a } "b" {'},
    {"uid": "C4", "card_detail_id": 4, "market_id": "M1", "stake_ref_uid": None, "stake_end_date": None},
    {"uid": "C5", "card_detail_id": 5, "delegated_to": "other", "last_used_date": "x", "stake_end_date": None},
]
BODY = json.dumps({"player": "p", "cards": ROWS}).encode()


@pytest.mark.parametrize("chunk", [3, 64, 1 << 20])
def test_stream_parse_keeps_the_same_rows_as_the_old_path(chunk):
    streamed = collection_stream.parse_collection_stream(io.BytesIO(BODY), chunk_bytes=chunk)
    old = json.loads(BODY)["cards"]
    assert streamed["player"] == "p"
    assert streamed["cards"] == [{k: v for k, v in c.items() if k not in collection_stream.UNUSED_COLLECTION_FIELDS} for c in old]
    assert streamed["cards"][0]["last_transferred_block"] == 109499809
    assert streamed["cards"][0]["last_transferred_date"] == "2026-08-31T12:00:00.000Z"
    buckets = [classify_card(c, NOW).bucket for c in old]
    assert [classify_card(c, NOW).bucket for c in streamed["cards"]] == buckets
    assert buckets == ["stake_active_unconfirmed", "stake_cooling_down", "unstaked_apparent", "blocked", "blocked"]


def test_stream_parse_falls_back_on_an_error_envelope():
    assert collection_stream.parse_collection_stream(io.BytesIO(b'{"error":"no"}')) == {"error": "no"}


class _Response:
    def __init__(self, etag, body):
        self.headers = {"Content-Encoding": "gzip", "ETag": etag}
        self._body, self.status = io.BytesIO(gzip.compress(body)), 200

    def read(self, n=-1):
        return self._body.read(n)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        pass


class _Opener:
    mnemosyne_observation = "synthetic"

    def __init__(self, responses):
        self.responses = responses

    def open(self, request, timeout=None):
        return self.responses.pop(0)


def test_equal_etag_reuses_the_previous_subset_without_parsing(monkeypatch):
    calls: list[int] = []
    real = collection_stream.parse_collection_stream
    monkeypatch.setattr(api, "parse_collection_stream", lambda *a, **k: (calls.append(1), real(*a, **k))[1])
    opener = _Opener([_Response('W/"abc"', BODY), _Response('W/"abc"', b"{not json"), _Response('W/"def"', BODY)])
    client = api.SplinterlandsClient(transport=FixedGetTransport(opener=opener))
    first = client.collection("p", narrow=True)
    assert first.etag == 'W/"abc"' and not first.reused and calls == [1] and len(first.data["cards"]) == 5
    again = client.collection("p", narrow=True, previous=(first.etag, first.data))
    assert again.reused and again.data is first.data and calls == [1]  # body never decoded
    changed = client.collection("p", narrow=True, previous=(first.etag, lambda: pytest.fail("loader must not run")))
    assert not changed.reused and changed.etag == 'W/"def"' and calls == [1, 1]
