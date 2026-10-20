"""Simple checksum-backed cache for public API responses."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import time
from pathlib import Path
from typing import Any, Callable

from . import fastjson
from .errors import CacheError
from .models import CacheInfo

CACHE_FORMAT_VERSION = 2


def safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value)[:80]


class JsonCache:
    def __init__(self, root: Path | None = None):
        # PRAETORIA_CACHE_DIR (set only by Ceres's fixture mode) isolates the cache from the real one.
        self.root = root or Path(os.environ.get("PRAETORIA_CACHE_DIR") or Path.home() / ".praetoria" / "cache")
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        return self.root / f"{safe_name(key)}.json"

    def read(self, key: str, ttl: int, offline: bool = False, refresh: bool = False) -> tuple[Any | None, CacheInfo]:
        path = self._path(key)
        if refresh:
            return None, CacheInfo("refresh")
        try:
            document = path.read_bytes()
            # slice through a memoryview -- `partition` copied the whole payload a second
            # time (154 MB for a whale's collection) before it was even parsed.
            newline = document.find(b"\n")
            separator = newline >= 0
            header = document[:newline] if separator else document
            raw = memoryview(document)[newline + 1:] if separator else memoryview(b"")
            if separator:
                candidate = json.loads(header.decode("utf-8"))
            else:
                candidate = None
            if isinstance(candidate, dict) and candidate.get("version") == CACHE_FORMAT_VERSION:
                envelope = candidate
                if hashlib.sha256(raw).hexdigest() != envelope.get("sha256"):
                    raise CacheError(f"cache checksum mismatch: {path}")
                data = fastjson.loads(raw)  # the checksum is over the stored bytes, codec-independent
            else:
                envelope = json.loads(document.decode("utf-8"))
                data = envelope["data"]
                legacy_raw = json.dumps(data, sort_keys=True, separators=(",", ":")).encode()
                if hashlib.sha256(legacy_raw).hexdigest() != envelope.get("sha256"):
                    raise CacheError(f"cache checksum mismatch: {path}")
            age = int(max(0, time.time() - float(envelope["stored_at"])))
            fresh = age <= ttl
            if not fresh and not offline:
                return None, CacheInfo("expired", envelope.get("retrieved_at"), age, envelope.get("sha256"))
            return data, CacheInfo("hit_fresh" if fresh else "hit_stale_offline", envelope.get("retrieved_at"), age, envelope.get("sha256"))
        except FileNotFoundError:
            if offline:
                raise CacheError(f"offline cache entry not found: {path}")
            return None, CacheInfo("miss")
        except (json.JSONDecodeError, UnicodeDecodeError, KeyError, OSError) as exc:
            raise CacheError(f"invalid cache entry: {path}") from exc

    def write(self, key: str, data: Any, retrieved_at: str) -> CacheInfo:
        path = self._path(key)
        raw = fastjson.dumps(data, sort_keys=True)  # same compact sorted shape, hashed as written
        envelope = {
            "format": "praetoria-json-cache",
            "retrieved_at": retrieved_at,
            "sha256": hashlib.sha256(raw).hexdigest(),
            "stored_at": time.time(),
            "version": CACHE_FORMAT_VERSION,
        }
        document = json.dumps(envelope, sort_keys=True, separators=(",", ":")).encode() + b"\n" + raw
        # S6 / L3-06: a temporary file of its OWN per write (the old name carried only the process id, so two threads of one process
        # shared it: one could rename the other's half-written document into place, and the other then failed).
        temporary = None
        try:
            descriptor, name = tempfile.mkstemp(prefix=path.stem + ".", suffix=".tmp", dir=path.parent)
            temporary = Path(name)
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(document)
            os.chmod(temporary, 0o600)
            temporary.replace(path)
            return CacheInfo("fetched", retrieved_at, 0, envelope["sha256"])
        except OSError as exc:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
            raise CacheError(f"could not write cache entry: {path}") from exc

    def get_or_fetch(self, key: str, ttl: int, offline: bool, refresh: bool, fetch: Callable[[], Any]) -> tuple[Any, CacheInfo]:
        value, info = self.read(key, ttl, offline, refresh)
        if value is not None:
            return value, info
        result = fetch()
        data = result.data
        return data, self.write(key, data, result.retrieved_at)
