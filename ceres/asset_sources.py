""" S9 (maintainer decision : the public repository carries no game artwork; Ceres fetches each image from the game's own image host the
first time it is needed and keeps it in the local assets folder (the player's own machine).

`ceres/static/assets/sources.json` (written by the public export from the private manifest) is a list of {path, source_url, sha256, byte_size}. Anything not listed, any other host, a wrong hash or an oversized body is refused. Nothing here stores or sends user data.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlparse

ALLOWED_HOSTS = frozenset({"d36mxiodymuqjm.cloudfront.net"})
MAX_BYTES = 5 * 1024 * 1024
TIMEOUT_SECONDS = 10.0
_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


def load_sources(assets_dir: Path) -> dict[str, dict]:
    """The reviewed source table; an absent or damaged file means "nothing can be fetched" (the private checkout ships its own images)."""
    try:
        rows = json.loads((assets_dir / "sources.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(rows, list):
        return {}
    return {row["path"]: {"url": row.get("source_url"), "sha256": row.get("sha256"), "bytes": row.get("byte_size")}
            for row in rows if isinstance(row, dict) and isinstance(row.get("path"), str)}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """ S10 round 2 / L1-03: an artwork request is never redirected (a redirect could leave the allowed host, even to a local address)."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa
        raise urllib.error.HTTPError(req.full_url, code, "redirect refused", headers, fp)


_OPENER = urllib.request.build_opener(_NoRedirect)
# Asset downloads run on their own two workers, so a slow or failing image host can never occupy the threads API requests need.
EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="ceres-asset")
FAILURE_BACKOFF_SECONDS = 60.0
_FAILED_AT: dict[str, float] = {}


def _default_fetch(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "ceres-asset-fetch"})
    with _OPENER.open(request, timeout=TIMEOUT_SECONDS) as response:  # the host is checked against ALLOWED_HOSTS first; redirects are refused
        body = response.read(MAX_BYTES + 1)
    if len(body) > MAX_BYTES:
        raise ValueError("asset larger than the limit")
    return body


def fetch_asset(assets_dir: Path, relative: str, *, fetcher=_default_fetch) -> Path | None:
    """Download one listed asset into `assets_dir` (atomic, hash-checked). Returns its path, or None when it cannot be provided."""
    if not relative or relative.startswith("/") or ".." in Path(relative).parts:
        return None
    entry = load_sources(assets_dir).get(relative)
    if not isinstance(entry, dict):
        return None
    url = str(entry.get("url") or "")
    if urlparse(url).scheme != "https" or urlparse(url).hostname not in ALLOWED_HOSTS:
        return None
    target = assets_dir / relative
    with _LOCKS_GUARD:
        lock = _LOCKS.setdefault(relative, threading.Lock())
    with lock:
        if target.is_file():
            return target
        if time.monotonic() - _FAILED_AT.get(relative, -1e9) < FAILURE_BACKOFF_SECONDS:
            return None  # failed a moment ago: no new download until the backoff has passed
        try:
            body = fetcher(url)
        except Exception:  # noqa: BLE001 - any network failure just means "not available now"
            _FAILED_AT[relative] = time.monotonic()
            return None
        expected = str(entry.get("sha256") or "")
        if not body or len(body) > MAX_BYTES or (expected and hashlib.sha256(body).hexdigest() != expected):
            _FAILED_AT[relative] = time.monotonic()
            return None
        target.parent.mkdir(parents=True, exist_ok=True)
        descriptor, name = tempfile.mkstemp(prefix=".fetch-", suffix=".tmp", dir=target.parent)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(body)
            os.replace(name, target)
        except OSError:
            Path(name).unlink(missing_ok=True)
            return None
        return target
