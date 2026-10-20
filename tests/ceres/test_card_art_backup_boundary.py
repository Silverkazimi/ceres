"""Backup boundary guards for the card-art cache."""

from __future__ import annotations

import os

import pytest

from pathlib import Path

from fastapi.testclient import TestClient

from ceres import card_art_cache
from ceres.app import create_app
from ceres.config import CeresConfig


def _tree(root: Path) -> set[Path]:
    return {path.relative_to(root) for path in root.rglob("*")}


def test_card_art_cache_defaults_on_when_estate_row_exists(monkeypatch) -> None:
    """Red mutation: changing the default to disabled must fail this guard."""
    monkeypatch.delenv("CERES_CARD_ART_CACHE", raising=False)
    # The maintainer's backup-coverage document lives outside the repository; point CERES_BACKUP_COVERAGE_DOC at it.
    estate_path = os.environ.get("CERES_BACKUP_COVERAGE_DOC")
    if not estate_path:
        pytest.skip("CERES_BACKUP_COVERAGE_DOC not set")
    estate_file = Path(estate_path)
    estate_text = estate_file.read_text(encoding="utf-8")
    has_cards_row = any(
        "ceres/static/assets/cards/" in line and line.lstrip().startswith("|")
        for line in estate_text.splitlines()
    )

    assert has_cards_row, f"estate backup coverage is missing the card-art row: {estate_file}"
    assert CeresConfig().card_art_cache_enabled is True


def test_card_art_cache_env_overrides_default(monkeypatch) -> None:
    """Red mutation: ignoring CERES_CARD_ART_CACHE must fail the override guard."""
    monkeypatch.setenv("CERES_CARD_ART_CACHE", "true")
    assert CeresConfig().card_art_cache_enabled is True
    monkeypatch.setenv("CERES_CARD_ART_CACHE", "0")
    assert CeresConfig().card_art_cache_enabled is False


def test_disabled_route_does_not_fetch_or_write(tmp_path: Path, monkeypatch) -> None:
    """Red mutation: constructing the store or calling the fetcher while disabled must fail."""
    upstream_calls: list[tuple[object, ...]] = []

    def forbidden_fetch(*args: object, **kwargs: object) -> None:
        upstream_calls.append((*args, *kwargs.values()))

    def forbidden_store(*args: object, **kwargs: object) -> None:
        raise AssertionError("disabled card-art route constructed a writable store")

    monkeypatch.setattr(card_art_cache, "fetch_card_art", forbidden_fetch)
    monkeypatch.setattr(card_art_cache, "CardArtStore", forbidden_store)
    app = create_app(CeresConfig(root=tmp_path, card_art_cache_enabled=False))
    before = _tree(tmp_path)

    response = TestClient(app).get("/art/card/not-a-real-key")

    assert response.status_code == 404
    assert response.headers["cache-control"] == "no-store"
    assert upstream_calls == []
    assert _tree(tmp_path) == before
    assert not hasattr(app.state, "card_art_store")


def test_project_backup_row_names_card_cache_path() -> None:
    """Red mutation: removing the path, CDN note, or restore command must fail."""
    row_file = Path(__file__).resolve().parents[2] / "library" / "backup-coverage-cards.md"
    row = row_file.read_text(encoding="utf-8")

    assert "ceres/static/assets/land/../cards/" in row
    assert "regenerable from the Splinterlands CDN" in row
    assert "mkdir -p ceres/static/assets/cards" in row
    assert "rsync -a ~/.ceres/assets-backup" in row
