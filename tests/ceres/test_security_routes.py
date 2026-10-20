from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from ceres import capture
from ceres.config import CeresConfig
import ceres.routes_board as routes_board
import ceres.routes_combine as routes_combine
import ceres.routes_op_preview as routes_op_preview


def request_for(config: CeresConfig, *, origin: str | None = None) -> Request:
    headers = [(b"host", b"testserver")]
    if origin is not None:
        headers.append((b"origin", origin.encode()))
    return Request({
        "type": "http",
        "method": "POST",
        "scheme": "http",
        "server": ("testserver", 80),
        "path": "/",
        "query_string": b"",
        "headers": headers,
        "client": ("127.0.0.1", 1),
        "root_path": "",
        "app": SimpleNamespace(state=SimpleNamespace(ceres_config=config)),
    })


@pytest.mark.parametrize("handler,args", [
    (routes_board.capture_refresh, ()),
    (routes_board.expand_plot, ("some-deed",)),
])
def test_cross_origin_post_to_bodyless_route_is_refused(tmp_path, monkeypatch, handler, args):
    """Injection run: removing the _require_same_origin() call from this route made it
    return the route's normal 409/202 instead of refusing the cross-origin POST -- verified
    live on routes_board.capture_refresh, restored after confirming the RED->GREEN flip."""
    config = CeresConfig(root=tmp_path, player="player0005")
    monkeypatch.setattr(capture, "is_running", lambda player: True)
    with pytest.raises(HTTPException) as raised:
        handler(*args, request_for(config, origin="https://evil.example"))
    assert raised.value.status_code == 403
    assert raised.value.detail == "cross-origin POST refused"


def test_capture_status_redacts_absolute_snapshot_path(tmp_path, monkeypatch):
    """Injection run: returning CaptureStatus.as_dict() directly exposed the absolute snapshot path in the response."""
    config = CeresConfig(root=tmp_path, player="player0005")
    leaked_path = "/srv/example/.ceres/snapshots/land-sample01-latest.json"
    monkeypatch.setattr(
        capture,
        "freshness",
        lambda config, player: capture.CaptureStatus(
            state="failed",
            error=f"failed to read {leaked_path}",
            latest_snapshot=capture.SnapshotRef(leaked_path, "2026-08-27T00:00:00Z", player),
        ),
    )
    result = routes_board.capture_status(request_for(config), None)
    assert leaked_path not in str(result)
    assert "path" not in result["latest_snapshot"]
    # R-111 / 4125a78f: public status now uses board-read wording while retaining redaction.
    assert result["error"] == "the last board read failed"


def test_snapshot_error_redacts_absolute_path(tmp_path, monkeypatch):
    """Injection run: embedding the caught exception in the 503 detail made the server filesystem path observable."""
    config = CeresConfig(root=tmp_path, player="player0005")
    leaked_path = "/srv/example/.ceres/snapshots/land-sample01-latest.json"

    def boom(config, player):
        raise OSError(f"cannot open {leaked_path}")

    monkeypatch.setattr(capture, "latest_snapshot", boom)
    with pytest.raises(HTTPException) as raised:
        routes_board.get_board(request_for(config), None)
    assert raised.value.status_code == 503
    assert leaked_path not in str(raised.value.detail)


@pytest.mark.parametrize("snapshot", [routes_op_preview._snapshot, routes_combine._snapshot])
def test_operation_snapshot_error_redacts_absolute_path(tmp_path, monkeypatch, snapshot):
    """Injection run: returning the snapshot exception from operation routes exposed the server filesystem path."""
    config = CeresConfig(root=tmp_path, player="player0005")
    leaked_path = "/srv/example/.ceres/snapshots/land-sample01-latest.json"

    def boom(config, player):
        raise OSError(f"cannot open {leaked_path}")

    monkeypatch.setattr(capture, "latest_snapshot", boom)
    with pytest.raises(HTTPException) as raised:
        snapshot(request_for(config), "player0005")
    assert raised.value.status_code == 503
    assert leaked_path not in str(raised.value.detail)


def test_board_cache_evicts_old_entries(monkeypatch, report):
    """Injection run: leaving _board_cache as an unbounded dict allowed one entry per player and exceeded the cache cap."""
    routes_board._board_cache.clear()
    monkeypatch.setattr(routes_board, "details_cache_mtime", lambda: 1)
    monkeypatch.setattr(routes_board, "project_board", lambda *args, **kwargs: {"schema": "ceres.board.v1"})
    for index in range(routes_board.BOARD_CACHE_MAXSIZE + 1):
        # cae1c740: the board cache is keyed by snapshot identity token.
        ref = SimpleNamespace(path=f"/tmp/snapshot-{index}.json", token=f"token-{index}")
        routes_board._cached_project_board(report, ref, {}, True, f"player-{index}")

    assert len(routes_board._board_cache) == routes_board.BOARD_CACHE_MAXSIZE
    assert ("/tmp/snapshot-0.json", "player-0", True, "token-0") not in routes_board._board_cache
