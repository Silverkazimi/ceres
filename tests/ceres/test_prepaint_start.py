from __future__ import annotations

import re
from pathlib import Path

from fastapi.testclient import TestClient

from ceres.app import create_app
from ceres.config import CeresConfig
from ceres.security_headers import inline_script_hashes


ROOT = Path(__file__).resolve().parents[2]


def test_queryless_start_page_is_prepaint_hidden_and_csp_hashed(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("CERES_CSP", "report-only")
    response = TestClient(create_app(CeresConfig(root=tmp_path, player="someaccount"))).get("/")
    assert response.status_code == 200
    html = response.text
    prepaint_scripts = [body for body in re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", html, re.S) if "new URLSearchParams(location.search).get('player')" in body]
    assert len(prepaint_scripts) == 1
    assert "root.classList.toggle('start-page',!String(player||'').trim())" in prepaint_scripts[0]

    css = (ROOT / "ceres/static/ceres.css").read_text(encoding="utf-8")
    assert "html.start-page header.board-header" in css
    assert "html.start-page #table-head-sticky" in css
    assert "html.start-page #plot-table-scroll" in css

    policy = response.headers["Content-Security-Policy-Report-Only"]
    script_src = next(part for part in policy.split(";") if part.strip().startswith("script-src "))
    allowed = set(script_src.split()[1:])
    assert set(inline_script_hashes(html)) <= allowed
