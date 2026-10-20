from __future__ import annotations

import subprocess
import urllib.parse
import urllib.request
from pathlib import Path

from ceres.app import create_app
from ceres.config import CeresConfig
from ceres.logging_setup import log_file
from tests.ceres.conftest import (
    PROJECT_ROOT,
    _IGNORED_ASSET_DIRS,
    _ensure_worktree_asset_links,
    _main_checkout_root,
    _spawn_live_ceres_isolated,
)


def test_spawned_test_server_uses_its_own_log_directory(tmp_path: Path) -> None:
    test_log_directory = tmp_path / "server-logs"
    real_log = Path.home() / ".ceres" / "logs" / "ceres.log"

    with _spawn_live_ceres_isolated(log_directory=test_log_directory) as (_, base_url):
        test_log = test_log_directory / "ceres.log"
        assert test_log.is_file()
        assert test_log.resolve() != real_log.resolve()

        main_checkout = _main_checkout_root()
        for relative in _IGNORED_ASSET_DIRS:
            asset_file = next(
                (path for path in (main_checkout / relative).rglob("*") if path.is_file()),
                None,
            )
            if asset_file is None:
                continue
            request_path = asset_file.relative_to(main_checkout / "ceres/static").as_posix()
            url = f"{base_url}/static/{urllib.parse.quote(request_path, safe='/')}"
            with urllib.request.urlopen(url, timeout=10) as response:
                assert response.status == 200


def test_worktree_asset_links_resolve_and_are_git_ignored() -> None:
    main_checkout = _main_checkout_root()
    if main_checkout == PROJECT_ROOT:
        return

    _ensure_worktree_asset_links()
    for relative in _IGNORED_ASSET_DIRS:
        source = main_checkout / relative
        if not source.is_dir():
            continue
        destination = PROJECT_ROOT / relative
        assert destination.is_symlink()
        assert destination.resolve() == source.resolve()
        subprocess.run(
            ["git", "check-ignore", "--quiet", "--no-index", "--", str(relative)],
            cwd=PROJECT_ROOT,
            check=True,
        )


def test_real_board_defaults_are_unchanged_without_override(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("CERES_TEST_LOG_DIR", raising=False)
    assert log_file(CeresConfig().root) == Path.home() / ".ceres" / "logs" / "ceres.log"
    app = create_app(CeresConfig(root=tmp_path / ".ceres"))
    static_mounts = [
        route.app
        for route in app.routes
        if getattr(route, "path", None) in {"/static", "/static/assets"}  # fastapi 0.141 _IncludedRouter has no .path
    ]
    assert len(static_mounts) == 2
    assert all(mount.follow_symlink is False for mount in static_mounts)
