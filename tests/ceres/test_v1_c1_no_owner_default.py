""" (v1.0 plan C1): Ceres ships with no built-in account."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import ceres.__main__ as ceres_main
from ceres.app import create_app
from ceres.config import CeresConfig


def _run_main(monkeypatch: pytest.MonkeyPatch, argv: list[str]) -> CeresConfig:
    seen: dict[str, CeresConfig] = {}
    monkeypatch.setattr(sys, "argv", ["ceres", *argv, "--no-reload-watch"])
    monkeypatch.setattr(ceres_main, "create_app", lambda config: seen.setdefault("config", config))
    monkeypatch.setattr(ceres_main, "configure_logging", lambda *a, **k: None)
    monkeypatch.setattr(ceres_main.uvicorn, "run", lambda *a, **k: None)
    ceres_main.main()
    return seen["config"]


def test_config_has_no_default_player_and_no_signing_allowlist(tmp_path: Path) -> None:
    config = CeresConfig(root=tmp_path)
    assert config.player is None
    assert config.harvest_signing_allowlist == frozenset()


def test_cli_without_player_starts_with_no_default(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _run_main(monkeypatch, []).player is None


def test_cli_player_flag_still_sets_the_default(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _run_main(monkeypatch, ["--player", "someaccount"]).player == "someaccount"


def test_request_without_player_is_refused_when_no_default(tmp_path: Path) -> None:
    client = TestClient(create_app(CeresConfig(root=tmp_path)))
    response = client.get("/api/board")
    assert response.status_code == 422
    assert response.json()["detail"] == "player required"


def test_start_page_document_names_no_account(tmp_path: Path) -> None:
    client = TestClient(create_app(CeresConfig(root=tmp_path)))
    response = client.get("/")
    assert response.status_code == 200
    assert "player0005" not in response.text
