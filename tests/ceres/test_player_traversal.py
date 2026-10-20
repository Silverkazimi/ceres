"""Observable guards for account validation and storage containment."""

from __future__ import annotations

from pathlib import Path
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from ceres import capture, security
from ceres.app import create_app
from ceres.config import CeresConfig


def _client(tmp_path: Path) -> TestClient:
    return TestClient(create_app(CeresConfig(root=tmp_path / ".ceres", player="player0005")))


def test_traversal_player_is_refused_without_absolute_path(tmp_path: Path):
    """Mutation check: removing player validation made this request fail with an uncaught path-boundary error; the guard went red.
 Re-verified 2026-08-28: changed security.py's raised status_code from 422 to 400, watched this
 assertion go red, then restored it and confirmed green."""
    client = _client(tmp_path)
    response = client.get("/api/board", params={"player": "../../outside/secret"})

    assert response.status_code == 422
    assert set(response.json()) == {"detail"}
    assert "invalid player" == response.json()["detail"]
    assert str(tmp_path) not in response.text
    assert not (tmp_path / "outside").exists()


def test_traversal_does_not_read_or_create_outside_storage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Mutation check: removing validation and the path boundary allowed the outside snapshot read; the access guard went red."""
    config = CeresConfig(root=tmp_path / ".ceres", player="player0005")
    outside = tmp_path / "outside" / "secret.jsonl"
    accesses: list[Path] = []
    original_exists = Path.exists

    def track_exists(path: Path) -> bool:
        accesses.append(path)
        return original_exists(path)

    monkeypatch.setattr(Path, "exists", track_exists)
    with pytest.raises(security.InvalidPlayer):
        capture._latest_path(config, "../../outside/secret")

    assert accesses == []
    assert not outside.exists()


def test_valid_player0005_player_still_works_end_to_end(tmp_path: Path):
    """Mutation check: rejecting all non-default or valid account names made this request fail; the guard went red.
 A fresh tmp_path has no captured snapshot, so a valid player still reads 503
 ("no snapshot"), never 422 ("invalid player") -- that distinction is the proof
 validation passed the name through rather than rejecting it."""
    response = _client(tmp_path).get("/api/board", params={"player": "player0005"})

    assert response.status_code == 503
    assert "invalid player" not in response.text


def test_path_containment_survives_permissive_validator(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Mutation check: removing `resolve_under()` let a permissive validator escape; the containment guard went red."""
    config = CeresConfig(root=tmp_path / ".ceres", player="player0005")
    monkeypatch.setattr(security, "validate_player", lambda player: player)

    with pytest.raises(security.PathOutsideBase):
        capture._latest_path(config, "x/../../outside/secret")

    assert not (tmp_path / "outside").exists()


@pytest.mark.parametrize("value", ["", "Aaa", "aa", "a/b", "a..b", "a__b", "a" * 17])
def test_invalid_player_shapes_are_rejected(value: str):
    with pytest.raises(security.InvalidPlayer):
        security.validate_player(value)
