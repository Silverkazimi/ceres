""" (v1.0 plan C4): security headers from the response layer."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ceres.app import STATIC_DIR, create_app
from ceres.config import CeresConfig
from ceres.security_headers import CONNECT_SRC, _sha256, inline_handler_hashes


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    return TestClient(create_app(CeresConfig(root=tmp_path, player="someaccount")))


def _directives(policy: str) -> dict[str, list[str]]:
    parts = (p.strip() for p in policy.split(";"))
    return {part.split()[0]: part.split()[1:] for part in parts if part}


@pytest.mark.parametrize("path", ["/", "/api/health", "/static/app.js"])
def test_every_response_carries_the_static_headers(client: TestClient, path: str) -> None:
    response = client.get(path)
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"
    assert response.headers["X-Frame-Options"] == "DENY"


def test_csp_is_report_only_by_default(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CERES_CSP", raising=False)
    headers = client.get("/").headers
    assert "Content-Security-Policy" not in headers
    assert "Content-Security-Policy-Report-Only" in headers


@pytest.mark.parametrize("mode,header", [("enforce", "Content-Security-Policy"), ("report-only", "Content-Security-Policy-Report-Only")])
def test_csp_mode_switch(client: TestClient, monkeypatch: pytest.MonkeyPatch, mode: str, header: str) -> None:
    monkeypatch.setenv("CERES_CSP", mode)
    assert header in client.get("/").headers


def test_csp_off_sends_no_policy(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CERES_CSP", "off")
    headers = client.get("/").headers
    assert "Content-Security-Policy" not in headers
    assert "Content-Security-Policy-Report-Only" not in headers


def test_csp_hashes_every_inline_script_as_served(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CERES_CSP", "enforce")
    response = client.get("/")
    script_src = _directives(response.headers["Content-Security-Policy"])["script-src"]
    inline = [b for b in re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", response.text, re.S) if b.strip()]
    assert inline, "index.html is expected to carry inline scripts"
    for body in inline:
        assert _sha256(body) in script_src
    assert "'unsafe-inline'" not in script_src
    assert "'unsafe-eval'" not in script_src


def test_csp_allows_the_inline_image_fallback_handlers(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CERES_CSP", "enforce")
    script_src = _directives(client.get("/").headers["Content-Security-Policy"])["script-src"]
    handlers = inline_handler_hashes(STATIC_DIR)
    assert _sha256("this.classList.add('is-none')") in handlers
    assert "'unsafe-hashes'" in script_src
    assert set(handlers) <= set(script_src)


def test_csp_origins_and_framing(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CERES_CSP", "enforce")
    policy = _directives(client.get("/").headers["Content-Security-Policy"])
    assert policy["connect-src"] == list(CONNECT_SRC)
    assert policy["frame-ancestors"] == ["'none'"]
    assert policy["object-src"] == ["'none'"]
    assert policy["base-uri"] == ["'none'"]


def test_revalidated_document_keeps_its_policy(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CERES_CSP", "enforce")
    first = client.get("/")
    second = client.get("/", headers={"If-None-Match": first.headers["ETag"]})
    assert second.status_code == 304
    assert second.headers["Content-Security-Policy"] == first.headers["Content-Security-Policy"]


def test_every_browser_side_host_is_in_connect_src() -> None:
    """Every fetch/RPC/WebSocket origin literal in the page's own modules must be allowed."""
    hosts: set[str] = set()
    pattern = re.compile(r"(?:fetch\(|new WebSocket\(|_RPC = |_BASE = )['\"`]((?:https|wss)://[A-Za-z0-9.-]+)")
    for path in STATIC_DIR.rglob("*.js"):
        text = path.read_text(encoding="utf-8", errors="ignore")
        hosts.update(pattern.findall(text))
        hosts.update(re.findall(r"['\"`](wss://[A-Za-z0-9.-]+)", text))
    hosts.discard("https://d36mxiodymuqjm.cloudfront.net")  # image origin (img-src), never fetched
    hosts.discard("https://runi.splinterlands.com")  # image origin (img-src), never fetched
    assert hosts, "expected at least one browser-side host"
    assert hosts <= set(CONNECT_SRC), hosts - set(CONNECT_SRC)
