""" S9 (decision : artwork is fetched from the official host on first use, hash-checked, and never from anywhere else."""
import hashlib
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ceres import app as app_module
from ceres.asset_sources import ALLOWED_HOSTS, fetch_asset, load_sources

BODY = b"\x89PNG fake image bytes"
HOST = sorted(ALLOWED_HOSTS)[0]


def _assets(tmp_path, files):
    rows = [{"path": path, "source_url": entry["url"], "sha256": entry["sha256"], "byte_size": entry["bytes"]} for path, entry in files.items()]
    (tmp_path / "sources.json").write_text(json.dumps(rows), encoding="utf-8")
    return tmp_path


def _entry(url=None, body=BODY):
    return {"url": url or f"https://{HOST}/website/x.png", "sha256": hashlib.sha256(body).hexdigest(), "bytes": len(body)}


def test_a_listed_asset_is_fetched_once_and_kept(tmp_path):
    assets = _assets(tmp_path, {"icons/x.png": _entry()})
    calls = []
    fetcher = lambda url: calls.append(url) or BODY
    path = fetch_asset(assets, "icons/x.png", fetcher=fetcher)
    assert path == assets / "icons" / "x.png" and path.read_bytes() == BODY
    assert fetch_asset(assets, "icons/x.png", fetcher=fetcher) == path and len(calls) == 1      # kept: no second download


@pytest.mark.parametrize("relative", ["../etc/passwd", "/abs.png", "icons/../../x.png", "unlisted.png", ""])
def test_unlisted_or_unsafe_paths_are_refused(tmp_path, relative):
    assets = _assets(tmp_path, {"icons/x.png": _entry()})
    assert fetch_asset(assets, relative, fetcher=lambda url: BODY) is None


def test_other_hosts_wrong_hashes_oversized_and_failing_fetches_are_refused(tmp_path):
    assets = _assets(tmp_path, {"a.png": _entry(url="https://evil.example/a.png"), "b.png": _entry(body=b"other"), "c.png": _entry(), "d.png": _entry(),
                               "e.png": _entry(url=f"http://{HOST}/e.png")})
    assert fetch_asset(assets, "a.png", fetcher=lambda url: BODY) is None                      # host not allowed
    assert fetch_asset(assets, "b.png", fetcher=lambda url: BODY) is None                      # hash differs
    assert fetch_asset(assets, "c.png", fetcher=lambda url: (_ for _ in ()).throw(OSError("down"))) is None
    assert fetch_asset(assets, "d.png", fetcher=lambda url: b"x" * (6 * 1024 * 1024)) is None
    assert fetch_asset(assets, "e.png", fetcher=lambda url: BODY) is None                      # not https
    assert not list(tmp_path.glob("*.png"))


def test_missing_or_damaged_source_table_means_nothing_to_fetch(tmp_path):
    assert load_sources(tmp_path) == {}
    (tmp_path / "sources.json").write_text("{not json", encoding="utf-8")
    assert load_sources(tmp_path) == {} and fetch_asset(tmp_path, "x.png", fetcher=lambda url: BODY) is None


def test_the_static_mount_fetches_a_missing_listed_image(tmp_path, monkeypatch):
    assets = _assets(tmp_path, {"icons/x.png": _entry()})
    monkeypatch.setattr(app_module, "fetch_asset", lambda directory, relative: fetch_asset(directory, relative, fetcher=lambda url: BODY))
    app = FastAPI()
    app.mount("/static/assets", app_module._CachedStaticFiles(directory=str(assets)), name="assets")
    client = TestClient(app)
    answer = client.get("/static/assets/icons/x.png")
    assert answer.status_code == 200 and answer.content == BODY and "immutable" in answer.headers["cache-control"]
    assert client.get("/static/assets/icons/unlisted.png").status_code == 404


# round 2 audit (L1-02, L1-03): no redirects, bounded failures, own workers
def test_a_redirect_from_the_image_host_is_refused():
    import http.server
    import threading
    import urllib.error
    from ceres import asset_sources

    class Redirect(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(302)
            self.send_header("Location", "http://127.0.0.1:1/private")
            self.end_headers()

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Redirect)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with pytest.raises(urllib.error.HTTPError, match="redirect refused|302"):
            asset_sources._default_fetch(f"http://127.0.0.1:{server.server_port}/x.png")
    finally:
        server.shutdown()


def test_a_failed_download_is_not_retried_during_the_backoff(tmp_path, monkeypatch):
    from ceres import asset_sources
    assets = _assets(tmp_path, {"icons/x.png": _entry()})
    monkeypatch.setattr(asset_sources, "_FAILED_AT", {})
    calls = []
    def failing(url):
        calls.append(url)
        raise OSError("down")
    assert fetch_asset(assets, "icons/x.png", fetcher=failing) is None
    assert fetch_asset(assets, "icons/x.png", fetcher=failing) is None
    assert len(calls) == 1                                               # the second request did not download again
    monkeypatch.setattr(asset_sources, "FAILURE_BACKOFF_SECONDS", 0.0)
    assert fetch_asset(assets, "icons/x.png", fetcher=lambda url: BODY) is not None   # after the backoff it tries again and succeeds


def test_asset_downloads_run_on_their_own_workers_and_attempts_on_a_thread():
    from pathlib import Path
    root = Path(__file__).parents[2] / "ceres"
    assert "run_in_executor(_ASSET_EXECUTOR" in (root / "app.py").read_text(encoding="utf-8")
    harvest = (root / "routes_harvest.py").read_text(encoding="utf-8")
    assert "await asyncio.to_thread(begin_harvest" in harvest and "await asyncio.to_thread(evidence.begin_attempt" in harvest
