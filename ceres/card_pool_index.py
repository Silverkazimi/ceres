"""Small, per-player indexes for the card-pool fields used by board projections."""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any, Mapping

from .worker_upgrade import _facts, _identity

logger = logging.getLogger("ceres.card_pool_index")

_MAX_INDEXES = 2
_cache: OrderedDict[tuple[Any, ...], "CardPoolIndex"] = OrderedDict()
_cache_lock = threading.Lock()
_inflight: dict[tuple[Any, ...], threading.Event] = {}


def _pool_identity(report: Mapping[str, Any], cards: list[dict[str, Any]]) -> tuple[Any, ...]:
    raw_path = report.get("_cards_sidecar_path")
    player = (report.get("query") or {}).get("player") if isinstance(report.get("query"), Mapping) else None
    player = player or report.get("player") or ""
    if raw_path:
        path = Path(str(raw_path))
        coverage = report.get("coverage")
        collection = coverage.get("collection", {}) if isinstance(coverage, Mapping) else {}
        fetched_at = collection.get("fetched_at") if isinstance(collection, Mapping) else None
        card_pool = report.get("card_pool")
        fetched_at = fetched_at or (card_pool.get("fetched_at") if isinstance(card_pool, Mapping) else None)
        if fetched_at:
            return (str(player), str(path), str(fetched_at))
        location = str(path)
    else:
        location = "inline"
    digest = hashlib.sha256()
    digest.update(json.dumps(cards, sort_keys=True, separators=(",", ":"), default=str).encode())
    return (str(player), location, digest.hexdigest())


class CardPoolIndex:
    """Normalized pool rows grouped by the exact worker identity key."""

    __slots__ = ("identity", "by_identity", "by_detail_id", "by_asset_ref", "invalid_count", "card_count", "estimated_bytes")

    def __init__(self, identity: tuple[Any, ...], cards: list[dict[str, Any]]) -> None:
        grouped: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
        by_detail_id: dict[str, dict[tuple[Any, ...], list[dict[str, Any]]]] = {}
        by_asset_ref: dict[Any, list[dict[str, Any]]] = {}
        invalid_count = 0
        estimated_bytes = 0
        for raw in cards:
            row = _facts(raw)
            if row is None:
                invalid_count += 1
                continue
            key = _identity(row)
            retained = {
                "asset_ref": row.get("asset_ref"),
                "uid": row.get("uid"),
                "card_uid": row.get("card_uid"),
                "card_detail_id": row.get("card_detail_id"),
                "edition": row.get("edition"),
                "foil": row.get("foil"),
                "gold": row.get("gold"),
                "is_gold": row.get("is_gold"),
                "bcx": row.get("bcx"),
                "bcx_max": row.get("bcx_max"),
                "card_set": row.get("card_set"),
                "level": row.get("level"),
                "max_level": row.get("max_level"),
                "is_max_level": row.get("is_max_level"),
                "stake_type_uid": row.get("stake_type_uid"),
                "skin": row.get("skin"),
                "stake_ref_uid": row.get("stake_ref_uid"),
                "stake_end_date": row.get("stake_end_date"),
                "is_unstaking": row.get("is_unstaking"),
                "unstaking": row.get("unstaking"),
                "classification": {
                    field: row["classification"].get(field)
                    for field in ("bucket", "reason")
                    if isinstance(row.get("classification"), Mapping) and field in row["classification"]
                } or None,
                "market_id": row.get("market_id"),
                "rental_type": row.get("rental_type"),
                "delegated_to": row.get("delegated_to"),
                "delegated_to_player": row.get("delegated_to_player"),
                "in_card_set": row.get("in_card_set"),
                "card_set_id": row.get("card_set_id"),
                "in_guild_lending_library": row.get("in_guild_lending_library"),
                "is_unlocking": row.get("is_unlocking"),
                "unlocking": row.get("unlocking"),
                "black_foil": row.get("black_foil"),
                "is_black_foil": row.get("is_black_foil"),
                "arcane_variant": row.get("arcane_variant"),
                "is_arcane_variant": row.get("is_arcane_variant"),
                "external_custody": row.get("external_custody"),
                "custody": row.get("custody"),
                "unlock_date": row.get("unlock_date"),
            }
            if key is not None:
                grouped.setdefault(key, []).append(retained)
                by_detail_id.setdefault(key[0], {}).setdefault(key, []).append(retained)
            asset_ref = retained.get("asset_ref")
            if asset_ref is not None:
                by_asset_ref.setdefault(asset_ref, []).append(retained)
            estimated_bytes += 72 + 16 * len(retained) + 24 + 72 + sum(
                len(str(value)) for value in retained.values() if value is not None
            )
        self.identity = identity
        self.by_identity = grouped
        self.by_detail_id = by_detail_id
        self.by_asset_ref = by_asset_ref
        self.invalid_count = invalid_count
        self.card_count = len(cards)
        self.estimated_bytes = estimated_bytes

    def matching(self, key: tuple[Any, ...]) -> list[dict[str, Any]]:
        return self.by_identity.get(key, [])

    @property
    def estimated_bytes_at_124k(self) -> int:
        return int(self.estimated_bytes * 124_000 / max(self.card_count, 1))


def get_card_pool_index(report: Mapping[str, Any]) -> CardPoolIndex:
    cards = report.get("cards")
    if not isinstance(cards, list):
        raise TypeError("card pool index requires a materialized card list")
    identity = _pool_identity(report, cards)
    while True:
        with _cache_lock:
            cached = _cache.get(identity)
            if cached is not None:
                _cache.move_to_end(identity)
                return cached
            event = _inflight.get(identity)
            if event is None:
                event = threading.Event()
                _inflight[identity] = event
                builder = True
            else:
                builder = False
        if builder:
            started = time.perf_counter()
            try:
                built = CardPoolIndex(identity, cards)
                with _cache_lock:
                    _cache[identity] = built
                    _cache.move_to_end(identity)
                    while len(_cache) > _MAX_INDEXES:
                        _cache.popitem(last=False)
                logger.info(
                    "built card-pool index player=%s cards=%d groups=%d bytes~%d bytes_124k~%d seconds=%.3f",
                    identity[0], built.card_count, len(built.by_identity), built.estimated_bytes,
                    built.estimated_bytes_at_124k,
                    time.perf_counter() - started,
                )
                return built
            finally:
                with _cache_lock:
                    waiter = _inflight.pop(identity, None)
                    if waiter is not None:
                        waiter.set()
        event.wait()


def clear_card_pool_index_cache() -> None:
    with _cache_lock:
        _cache.clear()


def invalidate(player: str) -> None:
    player = str(player)
    with _cache_lock:
        for identity in tuple(_cache):
            if identity[0] == player:
                _cache.pop(identity, None)
