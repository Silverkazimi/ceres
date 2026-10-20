"""the living style guide at /styleguide.

The page is read only: served like /cards (version tokens, revalidated document, CSP), links every real Ceres stylesheet
with the served-version cache key, reads no data (no fetch, no /api), never shows a UID, and names rule ids that exist
in library/ceres-ui-standards.md.
"""

from __future__ import annotations

import re
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from ceres.routes_board import served_version
from ceres.routes_styleguide import router
from ceres.security_headers import csp_mode

ROOT = Path(__file__).resolve().parents[2]
STATIC = ROOT / "ceres" / "static"
HTML = STATIC / "styleguide.html"
JS = STATIC / "styleguide.js"
CSS = STATIC / "styleguide.css"
STANDARD = ROOT / "library" / "ceres-ui-standards.md"

STYLESHEETS = ("ceres.css", "buttons.css", "badge-colours.css", "card-browser.css", "card-checkout.css", "mobile-plot.css")

# Pure modules only: each has no top-level side effect and no network read on import. card-browser.js (starts the page)
# card-checkout.js (wallet, broadcast gate), wallet.js, app.js and version-banner.js (polls /api/dev/watch) must never be
# loaded by the style guide.
ALLOWED_IMPORTS = {
    "./tooltip.js", "./customSelect.js", "./card-browser-model.js", "./card-browser-tips.js", "./countdown.js",
    "./card-market.js", "./card-market-model.js", "./card-cart.js", "./card-cart-model.js",
}

UID_PATTERNS = (
    re.compile(r"\b[CG]\d{1,2}-\d{1,5}-[A-Z0-9]{6,}\b"),   # card UIDs (C7-353-ZQSMK8OY54, G1-...)
    re.compile(r"\bstarter-\d+-[A-Za-z0-9]{4,}\b"),         # starter card UIDs
    re.compile(r"\bI-\d{1,4}-[0-9a-f]{8,}\b"),              # deed UIDs (I-290-fcb672...)
    re.compile(r"data-(?:card|deed)-uid", re.I),            # UID-carrying attributes
)


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def _page() -> tuple[str, dict]:
    response = _client().get("/styleguide")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    return response.text, dict(response.headers)


def test_page_is_served_with_version_tokens_cache_and_csp():
    html, headers = _page()
    assert "{{" not in html, "every version token is filled"
    assert headers["cache-control"] == "no-cache, must-revalidate"
    assert headers.get("etag")
    if csp_mode() != "off":
        assert any(name.startswith("content-security-policy") for name in headers)
    again = _client().get("/styleguide", headers={"if-none-match": headers["etag"]})
    assert again.status_code == 304


def test_every_real_stylesheet_is_linked_with_the_served_cache_key():
    html, _ = _page()
    version = served_version()
    for name in STYLESHEETS + ("styleguide.css",):
        assert (STATIC / name).is_file(), name
        assert f'<link rel="stylesheet" href="/static/{name}?v={version}">' in html, name
    assert f'<script type="module" src="/static/styleguide.js?v={version}"></script>' in html
    # the page's own CSS comes last, so it can never win over a component rule by order
    links = re.findall(r'<link rel="stylesheet" href="/static/([^"?]+)', html)
    assert links[-1] == "styleguide.css"
    assert links[:-1] == ["ceres.css", "buttons.css", "badge-colours.css", "mobile-plot.css", "card-browser.css", "card-checkout.css"]


def test_no_data_reads():
    for path in (HTML, JS, CSS):
        source = path.read_text(encoding="utf-8")
        assert "fetch(" not in source, path.name
        assert "XMLHttpRequest" not in source and "EventSource" not in source and "WebSocket" not in source, path.name
        assert "/api" not in source, path.name
    html = HTML.read_text(encoding="utf-8")
    scripts = re.findall(r'<script[^>]*\bsrc="([^"]+)"', html)
    assert scripts == ["/static/styleguide.js?v={{CERES_VERSION}}"], "no other script (version-banner.js polls /api/dev/watch)"
    imports = set(re.findall(r"""^import\s.*?from\s+['"]([^'"]+)['"];""", JS.read_text(encoding="utf-8"), re.M | re.S))
    assert imports and imports <= ALLOWED_IMPORTS, imports - ALLOWED_IMPORTS


def test_no_uid_patterns():
    html, _ = _page()
    for name, source in (("served html", html), ("styleguide.js", JS.read_text(encoding="utf-8"))):
        for pattern in UID_PATTERNS:
            assert not pattern.search(source), f"{name}: {pattern.pattern} -> {pattern.search(source).group(0)}"


def test_every_button_is_a_ceres_btn():
    # K-1: the style guide adds no button outside the shared family (the K-1 guard scans ceres/static too).
    for path in (HTML, JS):
        for tag in re.findall(r"<button\b[^>]*>", path.read_text(encoding="utf-8")):
            assert "ceres-btn" in tag, f"{path.name}: {tag}"


def test_sections_name_rules_that_exist_in_the_standard():
    js = JS.read_text(encoding="utf-8")
    standard = STANDARD.read_text(encoding="utf-8")
    ids = set()
    for rules in re.findall(r"rules: \[([^\]]*)\]", js):
        ids.update(re.findall(r"'([A-Z]-\d+[a-z]?)'", rules))
    assert {"C-1", "C-4", "T-3", "K-1", "K-3", "K-4", "W-4"} <= ids
    missing = sorted(i for i in ids if not re.search(rf"\*\*{re.escape(i)}\*\*|\b{re.escape(i)}\b", standard))
    assert not missing, missing
    # each section names its CSS classes too
    assert js.count("classes: [") == js.count("rules: [") >= 14


def test_page_layout_css_uses_tokens_only():
    css = CSS.read_text(encoding="utf-8")
    body = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b|\brgba?\(|\bhsla?\(", body)
    assert "!important" not in body
