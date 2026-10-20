"""Fixed, read-only Splinterlands API adapter."""

from __future__ import annotations

import json
import urllib.parse
from dataclasses import dataclass
from typing import Any

from mnemosyne.accounting import wrap_injected_transport

from . import fastjson
from .collection_stream import narrow_collection, parse_collection_stream
from .errors import SchemaError
from .transport import API_ORIGIN, FixedGetTransport, ResponseData

COLLECTION_PATH = "/cards/collection/"
DETAILS_URL = API_ORIGIN + "/cards/get_details"
ITEM_DETAILS_PATH = "/players/item_details"
BALANCES_PATH = "/players/balances"


class SplinterlandsClient:
    supports_narrow = True  # `collection(player, narrow=..., previous=...)` is understood

    def __init__(self, timeout: float = 20.0, retries: int = 3, user_agent: str = "Praetoria/0.1", transport: FixedGetTransport | None = None):
        self.transport = wrap_injected_transport(transport) if transport is not None else FixedGetTransport(timeout=timeout, retries=retries, user_agent=user_agent)

    def _get_json(self, url: str) -> ResponseData:
        return self.transport.get_json(url)

    @staticmethod
    def _check_body_error(payload: Any) -> None:
        FixedGetTransport.check_envelope(payload)

    def collection(self, player: str, *, narrow: bool = False, previous: tuple[str, Any] | None = None) -> ResponseData:
        """The player's full card collection, fetched live on every call ().

        : `narrow=True` stream-parses the body and keeps only the card keys the board
        reads (`collection_stream.DROPPED_COLLECTION_FIELDS` names the dropped ones), so the
        85 MB whale body and its 65k full dicts never exist at once. `previous=(etag, payload_or_loader)`
        is the caller's last parse: when the live response carries the same ETag the body is
        not read at all and `payload` comes back with `reused=True`. The request is still made
        and the server still answers it -- the ETag comparison replaces the parse, never the fetch.
        A transport without `supports_body_parser` (an injected fake) takes the whole-body path
        and is narrowed afterwards; it never reports an ETag, so it never reuses."""
        encoded = urllib.parse.quote(player, safe="")
        url = API_ORIGIN + COLLECTION_PATH + encoded
        parser = None
        reused: list[bool] = []
        if (narrow or previous is not None) and getattr(self.transport, "supports_body_parser", False):
            def parser(headers: Any, stream: Any) -> Any:
                etag = headers.get("ETag") if headers is not None else None
                if previous is not None and etag and str(etag).strip() == previous[0]:
                    held = previous[1]  # a payload, or a loader for one kept on disk
                    try:
                        payload = held() if callable(held) else held
                    except (SchemaError, OSError, ValueError):
                        payload = None  # S6 / L3-03: a stored payload that does not match its tag is not reused; the live body is parsed
                    if payload is not None:
                        reused.append(True)
                        return payload  # body left unread; the connection is discarded
                if narrow:
                    return parse_collection_stream(stream)
                chunks = []
                while True:
                    chunk = stream.read()
                    if not chunk:
                        break
                    chunks.append(chunk)
                return fastjson.loads(b"".join(chunks))
        if parser is not None:
            result = self.transport.get_json(url, parser)
            if reused:
                result = ResponseData(result.data, result.retrieved_at, result.attempts, result.etag, True)
        else:
            result = self._get_json(url)
            if narrow:
                result = ResponseData(narrow_collection(result.data), result.retrieved_at, result.attempts, getattr(result, "etag", None))
        if not isinstance(result.data, dict) or not isinstance(result.data.get("cards"), list):
            raise SchemaError("collection response must be an object containing a cards array")
        returned_player = result.data.get("player")
        if returned_player and returned_player != player:
            raise SchemaError("collection response player does not match the requested player")
        return result

    def details(self) -> ResponseData:
        result = self._get_json(DETAILS_URL)
        if not isinstance(result.data, list):
            raise SchemaError("card details response must be a JSON array")
        if not result.data:
            raise SchemaError("card details response was empty")
        return result

    def item_details(self) -> ResponseData:
        result = self._get_json(API_ORIGIN + ITEM_DETAILS_PATH)
        if not isinstance(result.data, list):
            raise SchemaError("item details response must be a JSON array")
        return result

    def balances(self, player: str) -> ResponseData:
        """Unauthenticated per-token balance list, e.g. `{"player": "player0005",
        "token": "TC", "balance": 0.98, "last_update_date": "..."}`. Confirmed live
        2026-08-26 (`work/ceres-worksite-phase2/recon-notes.md`)."""
        encoded = urllib.parse.quote(player, safe="")
        result = self._get_json(f"{API_ORIGIN}{BALANCES_PATH}?username={encoded}")
        if not isinstance(result.data, list):
            raise SchemaError("balances response must be a JSON array")
        return result
