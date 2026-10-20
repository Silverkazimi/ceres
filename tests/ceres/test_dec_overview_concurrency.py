import time
from datetime import datetime as RealDateTime, timezone
from types import SimpleNamespace

from ceres import routes_dec


def test_overview_overlaps_independent_reads_without_extra_upstream_calls(monkeypatch):
    player = "alice"
    fixed_time = "2026-09-23T12:00:00+00:00"
    calls = []
    intervals = {}
    regions = ["PR-ABC-1", "PR-ABC-2"]

    def delayed(name, result):
        calls.append(name)
        intervals[name] = [time.monotonic(), None]
        time.sleep(0.2)
        intervals[name][1] = time.monotonic()
        return result

    class FakeLandClient:
        def __init__(self, **kwargs):
            pass

        def owned_deed_counts(self, requested_player):
            assert requested_player == player
            payload = {"data": [{"uid": uid, "count": 1} for uid in regions]}
            return SimpleNamespace(data=delayed("owned", payload))

    def fake_read(path, **params):
        if path == "/land/stake/decstaked":
            assert params == {"player": player}
            return delayed("stakes", {"status": "success", "data": []})
        assert path == "/land/stake/dec/region"
        uid = params["region_uid"]
        calls.append(("region", uid))
        time.sleep(0.2)
        return {
            "status": "success",
            "data": {"uid": uid, "dec_staked": 120, "dec_stake_needed": 100, "dec_stake_in_use": 80},
        }

    def fake_balance(requested_player):
        assert requested_player == player
        return delayed("balance", 42.5)

    class FrozenDateTime:
        @staticmethod
        def now(tz=None):
            return RealDateTime.fromisoformat(fixed_time).astimezone(tz or timezone.utc)

    monkeypatch.setattr(routes_dec, "LandClient", FakeLandClient)
    monkeypatch.setattr(routes_dec, "_read", fake_read)
    monkeypatch.setattr(routes_dec, "_balance", fake_balance)
    monkeypatch.setattr(routes_dec, "datetime", FrozenDateTime)

    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(
        ceres_config=SimpleNamespace(player=player, harvest_signing_enabled=True),
    )))
    started = time.monotonic()
    response = routes_dec.overview(request)
    elapsed = time.monotonic() - started

    assert len(calls) == 3 + len(regions)
    primary = [intervals[name] for name in ("owned", "stakes", "balance")]
    assert max(interval[0] for interval in primary) < min(interval[1] for interval in primary)
    assert elapsed < 0.75
    assert response == {
        "player": player,
        "regions": [
            {"region_uid": uid, "staked": 120, "required": 100, "in_use": 80, "surplus": 20, "shortfall": 0}
            for uid in regions
        ],
        "balance": 42.5,
        "observed_at": fixed_time,
        "signing_enabled": True,
    }
