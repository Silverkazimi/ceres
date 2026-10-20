""": a private Ceres instance on a fixture set, plus Playwright routes that keep the browser offline.

For browser tests and the render rig: the page does a real capture (the live-capture-only rule holds) while every
upstream answer, server-side and browser-side, comes from the fixture set.
"""

from __future__ import annotations

import contextlib
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path
from typing import Iterator
from urllib.parse import urlsplit

from ceres import fixture_upstream

ROOT = Path(__file__).resolve().parents[2]
# A 1x1 transparent PNG for every game image host: the page's art must never reach the CDN in fixture runs.
PLACEHOLDER_PNG = bytes.fromhex(  # RGBA 0,0,0,0 (the first version was an opaque red pixel)
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000b49444154789c636000020000"
    "0500017a5eab3f0000000049454e44ae426082"
)
FIXTURE_HOSTS = frozenset({"api.splinterlands.com", "api.hive.blog", "vapi.splinterlands.com"})
IMAGE_HOSTS = frozenset({"d36mxiodymuqjm.cloudfront.net", "runi.splinterlands.com"})
STATIC_ASSETS = ROOT / "ceres" / "static" / "assets"
# Game artwork Ceres downloads into the (git-ignored) local asset folders on first use. Offline, a missing file
# would 404 and the page's art loader would retry it for about 30 s; it gets the placeholder instead.
_ART_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp", ".svg", ".gif")


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


@contextlib.contextmanager
def spawn_fixture_ceres(set_dir: Path, *, overlays: str = "", player: str = "sample01") -> Iterator[str]:
    """Start `python3 -m ceres` on loopback against `set_dir`; yield its base URL; always stop it."""
    port = _free_port()
    with tempfile.TemporaryDirectory(prefix="ceres-fixture-home-") as home:
        env = dict(os.environ)
        env.update({
            "HOME": home,
            fixture_upstream.ENV: str(set_dir),
            fixture_upstream.OVERLAYS_ENV: overlays,
            "CERES_TEST_LOG_DIR": str(Path(home) / "logs"),
            "PYTHONPATH": os.pathsep.join(p for p in sys.path if p),
        })
        env.pop("CERES_DEV_WATCH", None)
        env.pop(fixture_upstream.CACHE_ENV, None)
        log = open(Path(home) / "server.log", "w+", encoding="utf-8")
        process = subprocess.Popen(
            [sys.executable, "-m", "ceres", "--port", str(port), "--no-reload-watch", "--player", player],
            cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT, text=True,
        )
        base = f"http://127.0.0.1:{port}"
        try:
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                try:
                    with urllib.request.urlopen(f"{base}/api/health", timeout=2) as response:
                        if response.status == 200:
                            break
                except OSError:
                    time.sleep(0.2)
            else:
                log.seek(0)
                raise RuntimeError(f"fixture Ceres did not start:\n{log.read()}")
            yield base
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            log.close()


def install_routes(page, set_dir: Path, *, overlays: list[str] | None = None, images: str = "placeholder") -> list[str]:
    """Serve the page's own upstream calls from the set, images from a placeholder; abort anything else.

    ``images="cdn"`` lets the game's two static image hosts load for real (render comparisons need the art);
    API calls stay on the fixture set either way. Returns the list of aborted URLs."""
    fixtures = fixture_upstream.FixtureSet(Path(set_dir), overlays=overlays)
    aborted: list[str] = []

    def handle(route):
        request = route.request
        parts = urlsplit(request.url)
        host = parts.hostname or ""
        if host in ("127.0.0.1", "localhost"):
            if parts.path.startswith("/static/assets/") and parts.path.lower().endswith(_ART_SUFFIXES) and images != "cdn":
                local = (STATIC_ASSETS / parts.path.removeprefix("/static/assets/")).resolve()
                if local.is_relative_to(STATIC_ASSETS) and not local.is_file():
                    return route.fulfill(status=200, content_type="image/png", body=PLACEHOLDER_PNG)
            return route.continue_()
        if host in IMAGE_HOSTS and images == "cdn":
            return route.continue_()
        if host in IMAGE_HOSTS:
            return route.fulfill(status=200, content_type="image/png", body=PLACEHOLDER_PNG)
        if host in FIXTURE_HOSTS:
            target = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
            body = request.post_data_buffer if request.method.upper() == "POST" else None
            status, headers, data = fixtures.lookup(request.method, host, target, body)
            return route.fulfill(status=status, headers={**headers, "Access-Control-Allow-Origin": "*"}, body=data)
        aborted.append(request.url)
        return route.abort()

    page.route("**/*", handle)
    return aborted
