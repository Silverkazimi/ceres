"""Lazy, bounded, content-addressed cache for card art served by Ceres."""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import re
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from fastapi import FastAPI
from fastapi.responses import FileResponse, Response

from .board import load_details_by_id

LOGGER = logging.getLogger("ceres.card-art")

CARD_CDN_BASE = "https://d36mxiodymuqjm.cloudfront.net"
CARD_CDN_HOST = "d36mxiodymuqjm.cloudfront.net"
MAX_CANDIDATES = 3
PER_CANDIDATE_TIMEOUT_SECONDS = 2.0
REQUEST_WAIT_SECONDS = 5.5
MAX_WALK_SECONDS = 6.0
MAX_IMAGE_BYTES = 12 * 1024 * 1024
NEGATIVE_TTL_SECONDS = 60.0
RETRY_AFTER_SECONDS = 1
ALLOWED_EXTENSIONS = frozenset({"jpg", "png", "webp"})

# Twin of ceres/static/icons.js: CARD_CDN_SET and CARD_CDN_SOURCE_SET.
CARD_CDN_SET: dict[int, str] = {
    0: "cards_v2.2", 1: "cards_beta", 2: "cards_v2.2", 3: "cards_beta",
    4: "cards_untamed", 5: "cards_untamed", 6: "cards_gladiator", 7: "cards_chaos",
    8: "cards_riftwatchers", 9: "cards_nightmare", 10: "cards_soulbound",
    11: "cards_nightmare", 12: "cards_rebellion", 13: "cards_soulboundrb",
    14: "cards_v2.2", 15: "cards_v2.2", 16: "cards_v2.2", 17: "cards_v2.2",
    18: "cards_v2.2", 19: "cards_land", 20: "cards_v2.2", 21: "cards_land",
}
CARD_CDN_SOURCE_SET: dict[str, str] = {
    "alpha": "cards_v2.2", "beta": "cards_beta", "chaos": "cards_chaos",
    "untamed": "cards_untamed", "rebellion": "cards_rebellion",
    "soulbound": "cards_soulbound", "conclave": "cards_v2.2",
}

_WIRE_RE = re.compile(r"^(?P<detail>[1-9][0-9]{0,9})~(?P<edition>[A-Za-z0-9_-]+)~(?P<skin>[A-Za-z0-9_-]+)~(?P<gold>regular|gold)\.(?P<ext>[a-z0-9]+)$")
_FILENAME_RE = re.compile(r"^[0-9a-f]{64}\.(?:jpg|png|webp)$")
_SEGMENT_RE = re.compile(r"^[A-Za-z0-9._-]+$")


@dataclass(frozen=True)
class CardArtKey:
    card_detail_id: int
    edition_folder: str
    skin: str | None
    gold: bool
    extension: str

    @property
    def wire(self) -> str:
        skin_token = "base" if self.skin is None else _b64(self.skin)
        return f"{self.card_detail_id}~{_b64(self.edition_folder)}~{skin_token}~{'gold' if self.gold else 'regular'}"

    @property
    def route_value(self) -> str:
        return f"{self.wire}.{self.extension}"


@dataclass(frozen=True)
class FetchResult:
    status: int
    content_type: str = ""
    body: bytes = b""
    url: str | None = None


@dataclass
class _Flight:
    event: threading.Event


def _b64(value: str) -> str:
    return base64.urlsafe_b64encode(value.encode("utf-8")).decode("ascii").rstrip("=")


def _unb64(value: str) -> str:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4)).decode("utf-8")


def make_card_art_key(
    card_detail_id: int,
    edition_folder: str,
    skin: str | None = None,
    gold: bool = False,
    extension: str = "jpg",
) -> str:
    key = CardArtKey(int(card_detail_id), edition_folder, skin or None, bool(gold), extension.lower())
    _validate_key_parts(key, require_extension=True)
    return key.route_value


def parse_card_art_key(value: str) -> CardArtKey:
    if not isinstance(value, str) or len(value) > 256:
        raise ValueError("invalid card-art key")
    match = _WIRE_RE.fullmatch(value)
    if match is None:
        raise ValueError("invalid card-art key")
    try:
        edition_folder = _unb64(match.group("edition"))
        skin_token = match.group("skin")
        skin = None if skin_token == "base" else _unb64(skin_token)
    except (ValueError, UnicodeError):
        raise ValueError("invalid card-art key") from None
    key = CardArtKey(
        int(match.group("detail")), edition_folder, skin, match.group("gold") == "gold", match.group("ext"),
    )
    _validate_key_parts(key, require_extension=True)
    if key.route_value != value:
        raise ValueError("non-canonical card-art key")
    return key


def _validate_key_parts(key: CardArtKey, *, require_extension: bool) -> None:
    if key.card_detail_id <= 0 or not _SEGMENT_RE.fullmatch(key.edition_folder):
        raise ValueError("invalid card-art tuple")
    if key.edition_folder not in set(CARD_CDN_SET.values()) | set(CARD_CDN_SOURCE_SET.values()):
        raise ValueError("unknown card-art edition folder")
    if key.skin is not None and not _SEGMENT_RE.fullmatch(key.skin):
        raise ValueError("invalid card-art skin")
    if require_extension and key.extension not in ALLOWED_EXTENSIONS:
        raise ValueError("invalid card-art extension")


def candidate_table_twin_guard(js_text: str | None = None) -> bool:
    if js_text is None:
        js_text = (Path(__file__).parent / "static" / "icons.js").read_text(encoding="utf-8")
    numeric = {int(index): folder for index, folder in re.findall(r"CARD_CDN_SET\[(\d+)\]\s*=\s*'([^']+)'", js_text)}
    source_match = re.search(r"export const CARD_CDN_SOURCE_SET = \{(?P<body>.*?)\n\};", js_text, re.S)
    source = dict(re.findall(r"([a-z]+):\s*'([^']+)'", source_match.group("body"))) if source_match else {}
    return numeric == CARD_CDN_SET and source == CARD_CDN_SOURCE_SET


def assert_candidate_table_twin(js_text: str | None = None) -> None:
    if not candidate_table_twin_guard(js_text):
        raise RuntimeError("card-art Python CDN tables no longer match icons.js")


def _detail_for(details_by_id: Mapping[Any, Any], detail_id: int) -> Mapping[str, Any] | None:
    detail = details_by_id.get(detail_id)
    if detail is None:
        detail = details_by_id.get(str(detail_id))
    return detail if isinstance(detail, Mapping) else None


def resolve_card_art_candidates(key: CardArtKey, details_by_id: Mapping[Any, Any] | None = None) -> list[str]:
    assert_candidate_table_twin()
    details = details_by_id if details_by_id is not None else load_details_by_id()
    detail = _detail_for(details, key.card_detail_id)
    if detail is None:
        return []
    name = detail.get("name") or detail.get("card_name")
    source_set = CARD_CDN_SOURCE_SET.get(str(detail.get("card_set") or "").lower())
    if not isinstance(name, str) or not name:
        return []
    sets = list(dict.fromkeys([key.edition_folder, source_set] if source_set else [key.edition_folder]))
    skins = [key.skin, None] if key.skin else [None]
    filename = urllib.parse.quote(name, safe="") + ("_gold" if key.gold else "")
    candidates: list[str] = []
    for folder in sets:
        for skin in skins:
            for extension in ("jpg", "png"):
                candidates.append(f"{CARD_CDN_BASE}/{folder}/{urllib.parse.quote(skin, safe='') + '/' if skin else ''}{filename}.{extension}")
    return list(dict.fromkeys(candidates))[:MAX_CANDIDATES]


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def fetch_card_art(url: str, timeout: float) -> FetchResult:
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or parsed.hostname != CARD_CDN_HOST or parsed.query or parsed.fragment:
        raise ValueError("unapproved card-art upstream")
    request = urllib.request.Request(url, headers={"Accept": "image/*"}, method="GET")
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(request, timeout=timeout) as response:
            body = response.read(MAX_IMAGE_BYTES + 1)
            return FetchResult(response.status, response.headers.get_content_type(), body, response.geturl())
    except urllib.error.HTTPError as exc:
        return FetchResult(exc.code, exc.headers.get_content_type() if exc.headers else "", b"", url)


def _coerce_fetch_result(value: Any) -> FetchResult:
    if isinstance(value, FetchResult):
        return value
    if isinstance(value, Mapping):
        status = value.get("status", value.get("status_code", 0))
        content_type = value.get("content_type", value.get("mime", ""))
        body = value.get("body", value.get("content", b""))
        return FetchResult(int(status), str(content_type or ""), bytes(body or b""), value.get("url"))
    status = getattr(value, "status", getattr(value, "status_code", 0))
    headers = getattr(value, "headers", {})
    content_type = getattr(value, "content_type", "") or (headers.get("Content-Type", "") if hasattr(headers, "get") else "")
    body = getattr(value, "body", getattr(value, "content", b""))
    return FetchResult(int(status), str(content_type or ""), bytes(body or b""), getattr(value, "url", None))


class CardArtStore:
    def __init__(
        self,
        root: Path | str | None = None,
        *,
        fetcher: Callable[[str, float], FetchResult] = fetch_card_art,
        details_loader: Callable[[], Mapping[Any, Any]] = load_details_by_id,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.root = Path(root) if root is not None else Path(__file__).parent / "static" / "assets" / "cards"
        self.root.mkdir(parents=True, exist_ok=True)
        self.manifest_path = self.root / "manifest.json"
        self.fetcher = fetcher
        self.details_loader = details_loader
        self.clock = clock
        self._manifest_lock = threading.RLock()
        self._flight_lock = threading.Lock()
        self._flights: dict[str, _Flight] = {}
        self._fetch_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="ceres-card-art")

    def _empty_manifest(self) -> dict[str, Any]:
        return {"schema_version": 1, "resolver_revision": 1, "generated_at": self.clock(), "count": 0, "records": {}}

    def _read_manifest(self) -> dict[str, Any]:
        try:
            data = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return self._empty_manifest()
        if not isinstance(data, dict) or not isinstance(data.get("records"), dict):
            return self._empty_manifest()
        return data

    def _publish_manifest(self, manifest: dict[str, Any]) -> None:
        manifest["generated_at"] = self.clock()
        manifest["count"] = len(manifest["records"])
        handle = tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=self.root, prefix=".manifest-", suffix=".tmp", delete=False)
        temporary = Path(handle.name)
        try:
            with handle:
                json.dump(manifest, handle, sort_keys=True, separators=(",", ":"))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.manifest_path)
        finally:
            temporary.unlink(missing_ok=True)

    def _record(self, route_key: str) -> dict[str, Any] | None:
        with self._manifest_lock:
            return self._read_manifest()["records"].get(route_key)

    def _usable_ready(self, route_key: str) -> tuple[Path, dict[str, Any]] | None:
        record = self._record(route_key)
        if not record or record.get("state") != "ready":
            return None
        filename = record.get("published_filename")
        if not isinstance(filename, str) or not _FILENAME_RE.fullmatch(filename):
            return None
        path = (self.root / filename).resolve()
        try:
            path.relative_to(self.root.resolve())
            if path.stat().st_size > MAX_IMAGE_BYTES or hashlib.sha256(path.read_bytes()).hexdigest() != record.get("sha256"):
                return None
        except OSError:
            return None
        return path, record

    def _publish_bytes(self, body: bytes, extension: str) -> tuple[str, str, int]:
        digest = hashlib.sha256(body).hexdigest()
        filename = f"{digest}.{extension}"
        destination = self.root / filename
        if not destination.exists():
            handle = tempfile.NamedTemporaryFile("wb", dir=self.root, prefix=".card-", suffix=".tmp", delete=False)
            temporary = Path(handle.name)
            try:
                with handle:
                    handle.write(body)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, destination)
            finally:
                temporary.unlink(missing_ok=True)
        return filename, digest, len(body)

    def _save_record(self, route_key: str, record: dict[str, Any]) -> None:
        with self._manifest_lock:
            manifest = self._read_manifest()
            manifest["records"][route_key] = record
            self._publish_manifest(manifest)

    def prune(self, now: float | None = None) -> int:
        now = self.clock() if now is None else now
        with self._manifest_lock:
            manifest = self._read_manifest()
            records = manifest["records"]
            expired = [key for key, record in records.items() if record.get("state") != "ready" and float(record.get("next_retry_at", 0)) <= now]
            for key in expired:
                records.pop(key, None)
            if expired:
                self._publish_manifest(manifest)
            return len(expired)

    def _failure(self, route_key: str, key: CardArtKey, state: str, error_class: str, candidates: list[str], statuses: list[int]) -> None:
        prior = self._record(route_key) or {}
        now = self.clock()
        self._save_record(route_key, {
            "wire_key": key.wire, "card_detail_id": key.card_detail_id, "edition_folder": key.edition_folder,
            "skin": key.skin, "gold": key.gold, "extension": key.extension, "candidate_urls": candidates,
            "published_filename": None, "sha256": None, "byte_count": 0, "content_type": None,
            "state": state, "last_status": statuses[-1] if statuses else None, "last_error_class": error_class,
            "attempts": int(prior.get("attempts", 0)) + 1, "fetched_at": prior.get("fetched_at"),
            "last_attempt_at": now, "next_retry_at": now + NEGATIVE_TTL_SECONDS,
        })

    def _run_flight(self, route_key: str, key: CardArtKey, flight: _Flight) -> None:
        try:
            try:
                candidates = resolve_card_art_candidates(key, self.details_loader())
            except Exception as exc:
                LOGGER.warning("card-art resolver failed for %s: %s", route_key, exc)
                # S6 / L3-08: a card-metadata failure is temporary (the metadata may load next time); it must not become a verdict
                # that this card has no artwork.
                self._failure(route_key, key, "retryable_upstream", "resolver_error", [], [])
                return
            if not candidates:
                self._failure(route_key, key, "invalid_key", "invalid_key", [], [])
                return
            statuses: list[int] = []
            invalid_content = False
            started = time.monotonic()
            for candidate in candidates[:MAX_CANDIDATES]:
                if time.monotonic() - started >= MAX_WALK_SECONDS:
                    break
                try:
                    result = _coerce_fetch_result(self.fetcher(candidate, PER_CANDIDATE_TIMEOUT_SECONDS))
                except Exception as exc:
                    LOGGER.info("card-art candidate failed %s: %s", candidate, exc)
                    statuses.append(599)
                    continue
                statuses.append(result.status)
                if result.status != 200:
                    continue
                content_type = result.content_type.split(";", 1)[0].strip().lower()
                if not content_type.startswith("image/") or not result.body or len(result.body) > MAX_IMAGE_BYTES:
                    invalid_content = True
                    continue
                filename, digest, byte_count = self._publish_bytes(result.body, key.extension)
                now = self.clock()
                prior = self._record(route_key) or {}
                self._save_record(route_key, {
                    "wire_key": key.wire, "card_detail_id": key.card_detail_id, "edition_folder": key.edition_folder,
                    "skin": key.skin, "gold": key.gold, "extension": key.extension, "candidate_url": candidate,
                    "candidate_urls": candidates, "published_filename": filename, "sha256": digest,
                    "byte_count": byte_count, "content_type": content_type, "state": "ready", "last_status": 200,
                    "last_error_class": None, "attempts": int(prior.get("attempts", 0)) + 1,
                    "fetched_at": now, "last_attempt_at": now, "next_retry_at": None,
                })
                return
            self._failure(route_key, key, "invalid_content" if invalid_content else "retryable_upstream", "invalid_content" if invalid_content else "retryable_upstream", candidates, statuses)
        finally:
            flight.event.set()
            with self._flight_lock:
                if self._flights.get(route_key) is flight:
                    self._flights.pop(route_key, None)

    def _start_flight(self, route_key: str, key: CardArtKey) -> _Flight:
        with self._flight_lock:
            existing = self._flights.get(route_key)
            if existing is not None:
                return existing
            flight = _Flight(threading.Event())
            self._flights[route_key] = flight
            self._fetch_pool.submit(self._run_flight, route_key, key, flight)
            return flight

    def response_for(self, route_value: str) -> Response:
        try:
            key = parse_card_art_key(route_value)
        except ValueError:
            return Response(status_code=404, headers={"Cache-Control": "no-store"})
        cached = self._usable_ready(route_value)
        if cached is not None:
            path, record = cached
            return FileResponse(path, media_type=record.get("content_type") or f"image/{key.extension}", headers={"Cache-Control": "public, max-age=2592000, immutable"})
        record = self._record(route_value)
        if record and record.get("state") == "invalid_key" and not (record.get("next_retry_at") and float(record["next_retry_at"]) <= self.clock()):
            return Response(status_code=404, headers={"Cache-Control": "no-store"})  # S6 / L3-08: a negative record expires at its retry time
        if record and record.get("next_retry_at") and float(record["next_retry_at"]) > self.clock():
            state = record.get("state")
            if state == "retryable_upstream" and record.get("last_status") in {403, 404}:
                status = 404
            elif state == "invalid_content":
                status = 502
            else:
                status = 503
            return self._retry_response(status)
        flight = self._start_flight(route_value, key)
        if not flight.event.wait(REQUEST_WAIT_SECONDS):
            return self._retry_response(503)
        cached = self._usable_ready(route_value)
        if cached is not None:
            path, record = cached
            return FileResponse(path, media_type=record.get("content_type") or f"image/{key.extension}", headers={"Cache-Control": "public, max-age=2592000, immutable"})
        record = self._record(route_value) or {}
        if record.get("state") == "invalid_key":
            return Response(status_code=404, headers={"Cache-Control": "no-store"})
        if record.get("state") == "invalid_content":
            return self._retry_response(502)
        if record.get("last_status") in {403, 404}:
            return self._retry_response(404)
        return self._retry_response(503)

    @staticmethod
    def _retry_response(status: int) -> Response:
        return Response(status_code=status, headers={"Cache-Control": "no-store", "Retry-After": str(RETRY_AFTER_SECONDS), "X-Ceres-Art-State": "retryable"})


def register_card_art_route(
    app: FastAPI,
    store: CardArtStore | None = None,
    *,
    enabled: bool = True,
) -> CardArtStore | None:
    if not enabled:
        @app.get("/art/card/{key:path}", name="card-art")
        def disabled_card_art(key: str) -> Response:
            return Response(status_code=404, headers={"Cache-Control": "no-store"})

        return None

    store = store or CardArtStore()
    app.state.card_art_store = store

    @app.get("/art/card/{key:path}", name="card-art")
    def card_art(key: str) -> Response:
        return store.response_for(key)

    return store
