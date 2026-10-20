"""'s medium finding: the row-expand path lost the trace id.

`expand_deed` fetches the same account-wide land bundle as `capture`/`capture_quick`,
so a near-boundary account hits the response cap on an ordinary row-expand click too.
That raise is deliberately left uncaught inside `expand_plot` -- aborting before the
merge/write section is what keeps the stored snapshot from being overwritten with a
partial read -- but it used to fall through to a bare `except Exception` and surface as
a generic 502 with no endpoint and no trace id.

RED MUTATION for this file: delete the `except ResponseTooLarge` branch in
`ceres/routes_board.py::expand_plot`. Both guards below must turn red -- the first on
the status code (502, not 503) and the second on the missing trace id.
"""
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from ceres import capture as capture_mod
from ceres import routes_board
from praetoria.transport import ResponseTooLarge


def _raise_too_large(*_args, **_kwargs):
    raise ResponseTooLarge(
        endpoint="/land/deeds?player=player0017",
        trace_id="trace-expand-bundle",
        limit_bytes=268435456,
        observed_bytes=300000000,
    )


@pytest.fixture
def _expand_raises_too_large(monkeypatch):
    monkeypatch.setattr(routes_board, "_require_same_origin", lambda _request: None)
    monkeypatch.setattr(routes_board, "_config", lambda _request: object())
    monkeypatch.setattr(routes_board, "player_for_request", lambda _request, _player: "player0017")
    monkeypatch.setattr(capture_mod, "is_running", lambda _player: False)
    monkeypatch.setattr(capture_mod, "expand_plot", _raise_too_large)
    return SimpleNamespace(headers={}, url=SimpleNamespace(path="/api/plots/deed-1/expand"))


def test_row_expand_reports_the_cap_rather_than_a_generic_gateway_error(_expand_raises_too_large):
    with pytest.raises(HTTPException) as raised:
        routes_board.expand_plot("deed-1", _expand_raises_too_large)
    # 502 would mean the bare `except Exception` swallowed it again.
    assert raised.value.status_code == 503


def test_row_expand_failure_names_the_endpoint_and_trace_id(_expand_raises_too_large):
    with pytest.raises(HTTPException) as raised:
        routes_board.expand_plot("deed-1", _expand_raises_too_large)
    detail = str(raised.value.detail)
    # The two facts that make the failure actionable rather than merely reported.
    assert "/land/deeds?player=player0017" in detail
    assert "trace-expand-bundle" in detail
