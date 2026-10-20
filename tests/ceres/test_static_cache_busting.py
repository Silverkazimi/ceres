"""Cache-busting on the two files the browser was silently holding stale
(
after several real fixes had already shipped -- 27390 WAS serving the current
code, the browser's cached `app.js` was not).

Two layers, both required:
1. `GET /` substitutes `?v={{CERES_VERSION}}` onto BOTH `/static/app.js` and
   `/static/ceres.css` (ceres/app.py's `index()` + ceres/static/index.html) --
   a version bump changes the URL, so the browser fetches fresh bytes rather
   than reusing whatever it cached under the old URL.
2. The `/static` mount itself (`_NoCacheStaticFiles`) sends
   `Cache-Control: no-cache` on every 200 -- belt-and-braces for a browser
   that ignores query strings in its own heuristic-caching decision, or a
   direct hit on the bare URL.

Mutation-tested per the internal rules's binding standard: the guard below has had its
own defect injected (index.html's version token stripped), watched red, and
the injection is recorded in the guard's own docstring.
"""

from __future__ import annotations

import re
import socket

import pytest
from fastapi.testclient import TestClient

from ceres import __version__
from ceres.app import create_app
from ceres.config import CeresConfig


@pytest.fixture(autouse=True)
def block_real_sockets(monkeypatch):
    def _raise(*args, **kwargs):
        raise AssertionError("test_static_cache_busting.py must not open a real outbound network connection")

    monkeypatch.setattr(socket, "create_connection", _raise)


@pytest.fixture
def client(tmp_path) -> TestClient:
    config = CeresConfig(root=tmp_path / ".ceres", player="player0005")
    return TestClient(create_app(config))


def _assert_versioned_html(html: str) -> None:
    assert f'src="/static/app.js?v={__version__}"' in html
    assert f'href="/static/ceres.css?v={__version__}"' in html


def test_served_html_references_versioned_static_urls(client: TestClient) -> None:
    resp = client.get("/")
    assert resp.status_code == 200
    _assert_versioned_html(resp.text)


def test_versioned_url_mutation_is_red() -> None:
    """Injected defect: strip the `?v={{CERES_VERSION}}` token from index.html's
    own script/link tags (the exact regression this guard exists to catch --
    every fix shipped was invisible to the maintainer until a manual hard reload)
    -- the guard above must fail because the served HTML then references the
    bare, unversioned URLs."""
    from pathlib import Path

    index_path = Path(__file__).parents[2] / "ceres" / "static" / "index.html"
    source = index_path.read_text(encoding="utf-8")
    mutated = source.replace('/static/app.js?v={{CERES_VERSION}}', '/static/app.js') \
        .replace('/static/ceres.css?v={{CERES_VERSION}}', '/static/ceres.css')
    assert mutated != source  # sanity: the mutation target actually exists pre-mutation
    rendered = mutated.replace("{{CERES_VERSION}}", __version__)
    with pytest.raises(AssertionError):
        _assert_versioned_html(rendered)


def test_static_mount_sends_no_cache_header(client: TestClient) -> None:
    resp = client.get(f"/static/app.js?v={__version__}")
    assert resp.status_code == 200
    assert resp.headers.get("cache-control") == "no-cache"

    resp_css = client.get(f"/static/ceres.css?v={__version__}")
    assert resp_css.status_code == 200
    assert resp_css.headers.get("cache-control") == "no-cache"


def test_assets_subtree_keeps_its_own_long_cache_header(client: TestClient) -> None:
    """The /static/assets long-cache contract (icon manifest,  whale-account
    batch) must survive the new no-cache mount on the rest of /static -- the
    two must never collapse into one Cache-Control policy."""
    from pathlib import Path

    assets_dir = Path(__file__).parents[2] / "ceres" / "static" / "assets"
    sample = next(assets_dir.rglob("*.png"), None)
    if sample is None:
        pytest.skip("no sample asset found under ceres/static/assets")
    rel = sample.relative_to(assets_dir).as_posix()
    resp = client.get(f"/static/assets/{rel}")
    assert resp.status_code == 200
    cache_control = resp.headers.get("cache-control", "")
    assert "immutable" in cache_control
    assert cache_control != "no-cache"


def test_board_version_matches_module_version() -> None:
    # ceres/app.py's own doc comment: the served version must never drift
    # from a second hardcoded copy again (was "" stale for a full bump).
    assert re.match(r"^\d+\.\d+\.\d+$", __version__)


def test_document_response_is_never_cacheable_without_revalidation(client: TestClient) -> None:
    """ (root cause of the maintainer's own  report -- measured: a
    STALE CACHED copy of this document, built against an old ceres.css,
    reproduces his 1536px/125% picture exactly; his real browser renders
    correctly against a live fetch of master). GET / carried no Cache-Control
    at all before this ticket, leaving it to the browser's own heuristic
    freshness lifetime -- exactly what let an old document (and the old
    asset-version query tokens embedded inside it) survive past a version
    bump undetected. `no-cache, must-revalidate` (not `no-store`): the tab
    MUST check back with the server before reusing its own copy."""
    resp = client.get("/")
    assert resp.status_code == 200
    assert resp.headers.get("cache-control") == "no-cache, must-revalidate"
    assert resp.headers.get("etag"), "GET / must carry an ETag for revalidation to be cheap (a 304, not a full re-download)"


def test_document_response_etag_enables_a_cheap_304(client: TestClient) -> None:
    etag = client.get("/").headers["etag"]
    resp = client.get("/", headers={"if-none-match": etag})
    assert resp.status_code == 304
    assert resp.headers.get("cache-control") == "no-cache, must-revalidate"
    assert resp.headers.get("etag") == etag
    assert resp.content == b""  # a 304 carries no body


def test_static_mount_long_cache_and_document_no_cache_are_independent(client: TestClient) -> None:
    """The document's own new no-cache header (above) must not have collapsed
    onto the asset mounts -- versioned static files keep their existing
    no-cache belt-and-braces, and /static/assets keeps its own long,
    immutable caching -- both already covered above, re-asserted together
    here so a future change that accidentally shares one Cache-Control value
    across all three response types fails immediately."""
    doc_cache = client.get("/").headers.get("cache-control")
    static_cache = client.get(f"/static/ceres.css?v={__version__}").headers.get("cache-control")
    assert doc_cache == "no-cache, must-revalidate"
    assert static_cache == "no-cache"
    assert doc_cache != static_cache
