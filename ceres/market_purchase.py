""" / : checkout of the Cards page cart -- "Buy from Ceres" (helpers; routes in routes_market_purchase.py).

Owner decisions. : Ceres signs the market purchase with Keychain / HiveAuth, Active key, with the usual confirm and
outcome checks; the currency is chosen at checkout ("Ask at checkout"); credits are allowed as well. : listings sold
by checkout time are struck through and the reduced cart and its new total are confirmed again; any price rise is shown amber
and must be re-confirmed, with no currency preselected; up to 150 listings per transaction, a larger cart is split.

The operation (work/notes/-market-purchase/design.md section 1, the official client and the maintainer's chain history):
``custom_json`` id ``sm_market_purchase``, ``required_auths [player]`` (Active), json
``{"currency": "DEC"|"CREDITS", "items": [<market_id>, ...], "price": <number>, "app": "ceres/<version>", "n": <nonce>}``.
``price`` is the official client's rule over the listings' USD ``buy_price`` sum S: DEC = S / dec_price to 3 decimals
(``parseFloat((S / dec_price).toFixed(3))``), CREDITS = S x 1000. One purchase per transaction, nothing next to it (the
game record is then exactly ``<trx id>``;  multi-op confirmation is still open).

Market ids never leave the server except inside the operation the wallet signs (the wallet must see what it signs). The
cart holds opaque listing keys (``card_market.listing_key``: 16 hex of a SHA-256 over the market id); a key is resolved by
reading its print's listings (the same cached deep read as the card menu) and remembered here, then every listing is read
again BY ID (``/market/status?ids=``, public, uncached) for availability and price. No history scan: the only account-history
read is the bounded own-account find by nonce when a wallet gave no transaction id (/), as the wallet swap does.
Nothing here signs or broadcasts.
"""

from __future__ import annotations

import json
import re
import secrets
import threading
import time
import urllib.parse
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Callable

from praetoria.transport import API_ORIGIN, FixedGetTransport

from . import card_market
from .card_market import listing_key
from .transaction_identity import app_identity

OP_ID = "sm_market_purchase"
GAME_TYPE = "market_purchase"
CURRENCIES = ("DEC", "CREDITS")
MAX_ITEMS_PER_TX = 150        # (maintainer): up to 150 listings per transaction; larger carts are split
MAX_CART_ITEMS = 200          # card-cart-model.js CART_MAX_ITEMS
STATUS_PATH = "/market/status"
STATUS_IDS_PER_CALL = 40      # ids per by-id status read (the game answers every listing of a listed transaction; filtered here)
QUOTE_TTL_S = 15 * 60
KEY_TTL_S = 6 * 3600
MAX_KEYS = 50000
MAX_QUOTES = 2000
DEC_PRICE_FALLBACK = None     # the official client falls back to 0.001; Ceres refuses instead of guessing
_NONCE = re.compile(r"[a-f0-9]{16}")
_MARKET_ID = re.compile(r"[0-9a-f]{40}-[0-9]{1,4}")
_LKEY = re.compile(r"[0-9a-f]{16}")
_PID = re.compile(r"(\d{1,6})-(\d{1,3})-(\d)")
_GROUP = re.compile(r"max-[a-z0-9-]{1,40}")   # a combined maxed card ("Add maxed card"), kept in ONE transaction

Getter = Callable[[str], Any]


class PurchaseError(ValueError):
    """A request Ceres refuses (422 at the route)."""


def _default_get(url: str) -> Any:
    return FixedGetTransport(timeout=20, retries=1).get_json(url).data


def _now() -> float:
    return time.time()


# ── listing keys -> market ids (server memory only) ───────────────────────────────────────────────────────────────────

class KeyRegistry:
    """lkey -> (market_id, pid) learned from the print reads; bounded and expiring. Never sent to a client."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._map: dict[str, tuple[str, str, float]] = {}

    def remember(self, lkey: str, market_id: str, pid: str, now: float | None = None) -> None:
        now = _now() if now is None else now
        with self._lock:
            self._map[lkey] = (market_id, pid, now)
            if len(self._map) > MAX_KEYS:
                for key, _ in sorted(self._map.items(), key=lambda kv: kv[1][2])[: len(self._map) - MAX_KEYS]:
                    self._map.pop(key, None)

    def get(self, lkey: str, pid: str, now: float | None = None) -> str | None:
        now = _now() if now is None else now
        with self._lock:
            hit = self._map.get(lkey)
        if not hit or hit[1] != pid or now - hit[2] > KEY_TTL_S:
            return None
        return hit[0]

    def clear(self) -> None:
        with self._lock:
            self._map.clear()


KEYS = KeyRegistry()


def parse_items(raw: Any) -> list[dict[str, Any]]:
    """The cart's items as sent: [{lkey, pid, seen_usd?}] (no names, ids or prices are trusted beyond the seen price)."""
    if not isinstance(raw, list) or not raw:
        raise PurchaseError("the cart is empty")
    if len(raw) > MAX_CART_ITEMS:
        raise PurchaseError(f"at most {MAX_CART_ITEMS} listings per checkout")
    out, seen = [], set()
    for item in raw:
        if not isinstance(item, dict):
            raise PurchaseError("invalid cart item")
        lkey, pid = str(item.get("lkey") or ""), str(item.get("pid") or "")
        if not _LKEY.fullmatch(lkey) or not _PID.fullmatch(pid):
            raise PurchaseError("invalid cart item")
        if lkey in seen:
            continue
        seen.add(lkey)
        seen_usd = item.get("seen_usd")
        if seen_usd is not None and (isinstance(seen_usd, bool) or not isinstance(seen_usd, (int, float)) or not seen_usd > 0):
            seen_usd = None
        group = item.get("group")
        group = group if isinstance(group, str) and _GROUP.fullmatch(group) else None
        out.append({"lkey": lkey, "pid": pid, "seen_usd": float(seen_usd) if seen_usd is not None else None, "group": group})
    return out


def _pid_item(pid: str) -> dict[str, int]:
    m = _PID.fullmatch(pid)
    return {"card_detail_id": int(m.group(1)), "edition": int(m.group(2)), "foil": int(m.group(3))}


def resolve_keys(items: list[dict[str, Any]], getter: Getter, cache: card_market.MarketCache | None = None,
                 keys: KeyRegistry | None = None, calls: list[str] | None = None) -> dict[str, str]:
    """lkey -> market_id for every item that can be found: the registry first, else one (cached) deep read of its print,
    the card menu's own URL. A key that is in neither is unresolved (not among the cheapest 60 upstream returns)."""
    cache = cache or card_market.CACHE
    keys = keys or KEYS
    calls = calls if calls is not None else []
    found: dict[str, str] = {}
    missing: dict[str, list[str]] = {}
    for it in items:
        mid = keys.get(it["lkey"], it["pid"])
        if mid:
            found[it["lkey"]] = mid
        else:
            missing.setdefault(it["pid"], []).append(it["lkey"])
    for pid, wanted in missing.items():
        answer = cache.get(card_market.deep_url(_pid_item(pid)), getter, calls)
        for row in answer if isinstance(answer, list) else []:
            if not isinstance(row, dict):
                continue
            mid = str(row.get("market_id") or "").strip()
            key = listing_key(row)
            if key and _MARKET_ID.fullmatch(mid):
                keys.remember(key, mid, pid)
                if key in wanted:
                    found[key] = mid
    return found


# ── by-id reads ──────────────────────────────────────────────────────────────────────────────────────────────────────

def read_status(market_ids: list[str], getter: Getter, calls: list[str] | None = None) -> dict[str, dict[str, Any]]:
    """market_id -> its /market/status row (fresh, never cached). The game answers every listing of each listed
    transaction for an id (a bulk listing returns all its rows), so rows are matched on ``sell_trx_id``. An id the game
    does not know is simply absent."""
    calls = calls if calls is not None else []
    wanted = list(dict.fromkeys(m for m in market_ids if _MARKET_ID.fullmatch(m)))
    out: dict[str, dict[str, Any]] = {}
    for start in range(0, len(wanted), STATUS_IDS_PER_CALL):
        chunk = wanted[start:start + STATUS_IDS_PER_CALL]
        url = f"{API_ORIGIN}{STATUS_PATH}?{urllib.parse.urlencode({'ids': ','.join(chunk)})}"
        answer = getter(url)
        calls.append(STATUS_PATH)
        rows = answer if isinstance(answer, list) else [answer] if isinstance(answer, dict) else []
        chunk_set = set(chunk)
        for row in rows:
            if isinstance(row, dict) and str(row.get("sell_trx_id") or "") in chunk_set:
                out[str(row["sell_trx_id"])] = row
    return out


def read_dec_price(getter: Getter, calls: list[str] | None = None) -> float | None:
    """/settings dec_price (USD per DEC), fresh -- the price signed is computed from it right before signing."""
    calls = calls if calls is not None else []
    settings = getter(API_ORIGIN + card_market.SETTINGS_PATH)
    calls.append(card_market.SETTINGS_PATH)
    value = card_market._number((settings or {}).get("dec_price")) if isinstance(settings, dict) else None
    return value if value and value > 0 else None


def read_balances(player: str, getter: Getter, calls: list[str] | None = None) -> dict[str, float]:
    """/players/balances: liquid DEC and CREDITS (an absent token is 0, as the game reports it)."""
    calls = calls if calls is not None else []
    rows = getter(f"{API_ORIGIN}/players/balances?{urllib.parse.urlencode({'username': player})}")
    calls.append("/players/balances")
    out = {"DEC": 0.0, "CREDITS": 0.0}
    for row in rows if isinstance(rows, list) else []:
        if isinstance(row, dict) and row.get("player") == player and row.get("token") in out:
            value = card_market._number(row.get("balance"))
            out[row["token"]] = value if value is not None and value > 0 else 0.0
    return out


# ── price rules ──────────────────────────────────────────────────────────────────────────────────────────────────────

def usd_sum(prices: list[float]) -> float:
    """The official client's S = rows.reduce((a, r) => a + r.buy_price, 0): float addition in order (same IEEE doubles)."""
    total = 0.0
    for p in prices:
        total += float(p)
    return total


def _to_fixed_3(value: float) -> float:
    """JavaScript ``parseFloat(x.toFixed(3))`` for x >= 0: the exact binary value, ties up."""
    return float(Decimal(value).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP))


def price_for(currency: str, usd_total: float, dec_price: float | None) -> float | int:
    """The ``price`` field. DEC: round(S / dec_price, 3). CREDITS: S x 1000 (credits are $0.001 each); a float artefact of
    the multiplication (0.739 * 1000 = 739.0000000000001) is cleaned to the whole number it stands for."""
    if currency == "DEC":
        if not dec_price or dec_price <= 0:
            raise PurchaseError("the DEC price is not available right now")
        return _to_fixed_3(usd_total / dec_price)
    if currency == "CREDITS":
        value = round(usd_total * 1000, 6)
        return int(value) if float(value).is_integer() else value
    raise PurchaseError("currency must be DEC or CREDITS")


def split(items: list[Any], size: int = MAX_ITEMS_PER_TX) -> list[list[Any]]:
    """Transactions of at most `size` listings, in cart order. : the listings of one maxed-card group (rows carrying
    the same ``group``) are never cut apart -- a group that does not fit the current transaction starts the next one; a group
    larger than `size` cannot be one purchase and is split like single listings (Buy & max is not offered for it)."""
    if not items:
        return []
    units: list[list[Any]] = []
    index: dict[str, list[Any]] = {}
    for item in items:
        group = item.get("group") if isinstance(item, dict) else None
        if group:
            if group not in index:
                index[group] = []
                units.append(index[group])
            index[group].append(item)
        else:
            units.append([item])
    chunks: list[list[Any]] = []
    current: list[Any] = []
    for unit in units:
        if len(unit) > size:
            for item in unit:
                if len(current) >= size:
                    chunks.append(current)
                    current = []
                current.append(item)
            continue
        if len(current) + len(unit) > size:
            chunks.append(current)
            current = []
        current.extend(unit)
    if current:
        chunks.append(current)
    return chunks


# ── the quote ────────────────────────────────────────────────────────────────────────────────────────────────────────

def _parse_date(value: Any) -> datetime | None:
    if not isinstance(value, str) or len(value) > 40:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def classify(item: dict[str, Any], market_id: str | None, row: dict[str, Any] | None, player: str,
             now: datetime | None = None) -> dict[str, Any]:
    """One cart listing at checkout: ok / price_changed (with was -> now) / sold / gone / own / unresolved."""
    now = now or datetime.now(timezone.utc)
    out = {"lkey": item["lkey"], "pid": item["pid"], "seen_usd": item.get("seen_usd"), "price_usd": None, "group": item.get("group")}
    if not market_id:
        return {**out, "status": "unresolved",
                "reason": "Ceres could not find this listing among the cheapest copies the market gives for this card; it may be sold."}
    if row is None:
        return {**out, "status": "gone", "reason": "No longer on sale (sold or withdrawn)."}
    if str(row.get("status")) == "1" or row.get("purchaser"):
        return {**out, "status": "sold", "reason": "Sold to another player."}
    expires = _parse_date(row.get("expiration_date"))
    if str(row.get("status")) != "0" or (expires is not None and expires <= now):
        return {**out, "status": "gone", "reason": "No longer on sale (withdrawn or expired)."}
    if str(row.get("type") or "SELL").upper() != "SELL" or str(row.get("currency") or "USD").upper() != "USD":
        return {**out, "status": "gone", "reason": "Not a sale listing priced in dollars."}
    if row.get("lock_block") or row.get("locked_by"):
        return {**out, "status": "gone", "reason": "Another purchase of this listing is in progress."}
    if str(row.get("seller") or "").lower() == player.lower():
        return {**out, "status": "own", "reason": "Your own listing."}
    want = _pid_item(item["pid"])
    cards = row.get("cards") if isinstance(row.get("cards"), list) else []
    if not cards or not all(isinstance(c, dict) and card_market._int(c.get("card_detail_id")) == want["card_detail_id"]
                            and card_market._int(c.get("edition")) == want["edition"]
                            and (card_market._int(c.get("foil")) if c.get("foil") is not None else (1 if c.get("gold") else 0)) == want["foil"]
                            for c in cards):
        return {**out, "status": "gone", "reason": "This listing no longer holds the card in your cart."}
    price = card_market._number(row.get("buy_price"))
    if price is None or price <= 0:
        return {**out, "status": "gone", "reason": "The listing has no readable price."}
    seen = item.get("seen_usd")
    if seen is not None and abs(price - seen) > 1e-9:
        return {**out, "price_usd": price, "status": "price_changed", "was_usd": seen, "rise": price > seen}
    return {**out, "price_usd": price, "status": "ok"}


BUYABLE = ("ok", "price_changed")


def totals(rows: list[dict[str, Any]], dec_price: float | None) -> dict[str, Any]:
    """USD and both currency totals of the buyable rows, per transaction of at most 150 (each priced on its own)."""
    buyable = [r for r in rows if r["status"] in BUYABLE]
    chunks = split(buyable)
    usd = usd_sum([r["price_usd"] for r in buyable])
    per_tx = []
    for chunk in chunks:
        s = usd_sum([r["price_usd"] for r in chunk])
        per_tx.append({"count": len(chunk), "usd": s,
                       "DEC": price_for("DEC", s, dec_price) if dec_price else None,
                       "CREDITS": price_for("CREDITS", s, dec_price)})
    dec_total = round(sum(t["DEC"] for t in per_tx), 3) if dec_price and per_tx else (0 if not per_tx else None)
    credits_total = sum(t["CREDITS"] for t in per_tx) if per_tx else 0
    if isinstance(credits_total, float):
        credits_total = int(credits_total) if credits_total.is_integer() else round(credits_total, 6)
    return {"count": len(buyable), "usd": usd, "DEC": dec_total, "CREDITS": credits_total, "transactions": per_tx}


def currency_view(total: dict[str, Any], balances: dict[str, float]) -> dict[str, Any]:
    """Per currency: the total, the balance and whether it is enough (the official client's insufficient-balance check)."""
    out = {}
    for cur in CURRENCIES:
        need = total.get(cur)
        have = balances.get(cur, 0.0)
        if not total["count"]:
            out[cur] = {"total": 0, "balance": have, "enough": False, "reason": "Nothing in the cart can be bought."}
        elif need is None:
            out[cur] = {"total": None, "balance": have, "enough": False, "reason": "The DEC price is not available right now."}
        elif have < need:
            out[cur] = {"total": need, "balance": have, "enough": False, "short": round(need - have, 3),
                        "reason": f"Not enough {'DEC' if cur == 'DEC' else 'credits'}."}
        else:
            out[cur] = {"total": need, "balance": have, "enough": True}
    return out


class QuoteStore:
    """quote_id -> the checked cart (server memory, 15 minutes). Holds market ids; only the id and the per-key view go out."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._quotes: dict[str, dict[str, Any]] = {}

    def put(self, quote: dict[str, Any]) -> str:
        qid = secrets.token_hex(12)
        now = _now()
        with self._lock:
            for key in [k for k, q in self._quotes.items() if now - q["created"] > QUOTE_TTL_S]:
                self._quotes.pop(key, None)
            while len(self._quotes) >= MAX_QUOTES:
                self._quotes.pop(next(iter(self._quotes)))
            self._quotes[qid] = {**quote, "created": now}
        return qid

    def get(self, qid: Any, player: str) -> dict[str, Any] | None:
        if not isinstance(qid, str) or not re.fullmatch(r"[0-9a-f]{24}", qid):
            return None
        with self._lock:
            quote = self._quotes.get(qid)
        if not quote or quote["player"] != player or _now() - quote["created"] > QUOTE_TTL_S:
            return None
        return quote

    def clear(self) -> None:
        with self._lock:
            self._quotes.clear()


QUOTES = QuoteStore()


def build_quote(player: str, items: list[dict[str, Any]], getter: Getter, *, cache: card_market.MarketCache | None = None,
                keys: KeyRegistry | None = None, now: datetime | None = None) -> dict[str, Any]:
    """Resolve, re-read by id, classify and total one cart. Internal: carries market ids (``_ids``)."""
    calls: list[str] = []
    ids = resolve_keys(items, getter, cache=cache, keys=keys, calls=calls)
    rows = read_status(list(ids.values()), getter, calls)
    dec_price = read_dec_price(getter, calls)
    balances = read_balances(player, getter, calls)
    checked = [classify(it, ids.get(it["lkey"]), rows.get(ids.get(it["lkey"], "")), player, now) for it in items]
    total = totals(checked, dec_price)
    return {"player": player, "items": checked, "_ids": {k: v for k, v in ids.items()}, "dec_price": dec_price,
            "balances": balances, "totals": total, "currencies": currency_view(total, balances), "upstream_calls": calls}


def public_quote(quote: dict[str, Any], quote_id: str) -> dict[str, Any]:
    """What the page sees: per-key rows, totals, balances, the split -- never a market id."""
    view = {k: v for k, v in quote.items() if not k.startswith("_") and k not in ("created",)}
    view["quote_id"] = quote_id
    view["max_per_transaction"] = MAX_ITEMS_PER_TX
    view["changes"] = {
        "sold": sum(1 for r in quote["items"] if r["status"] in ("sold", "gone", "unresolved", "own")),
        "price_rise": sum(1 for r in quote["items"] if r["status"] == "price_changed" and r.get("rise")),
        "price_drop": sum(1 for r in quote["items"] if r["status"] == "price_changed" and not r.get("rise")),
    }
    return view


def differences(old: dict[str, Any], new: dict[str, Any], currency: str, confirmed_total: Any) -> list[str]:
    """Why a fresh re-check differs from the quote the maintainer confirmed (empty = sign as confirmed)."""
    why = []
    before = {r["lkey"]: r for r in old["items"] if r["status"] in BUYABLE}
    after = {r["lkey"]: r for r in new["items"] if r["status"] in BUYABLE}
    if set(before) != set(after):
        why.append("listings sold or withdrawn since you confirmed")
    for key in set(before) & set(after):
        if abs(before[key]["price_usd"] - after[key]["price_usd"]) > 1e-9:
            why.append("a listing price changed since you confirmed")
            break
    fresh = new["totals"].get(currency)
    try:
        confirmed = float(confirmed_total)
    except (TypeError, ValueError):
        confirmed = None
    if fresh is None or confirmed is None or fresh > confirmed + 1e-9:
        why.append(f"the {currency} total rose since you confirmed")
    if not new["currencies"][currency]["enough"]:
        why.append(f"your {currency} balance is not enough")
    return why


def build_transactions(player: str, quote: dict[str, Any], currency: str, *, nonces: list[str] | None = None) -> list[dict[str, Any]]:
    """The exact operation(s): one sm_market_purchase per transaction, at most 150 listings each, key order as the official
    client sends it (currency, items, price) plus Ceres' app and nonce. Active (required_auths)."""
    if currency not in CURRENCIES:
        raise PurchaseError("currency must be DEC or CREDITS")
    buyable = [r for r in quote["items"] if r["status"] in BUYABLE]
    if not buyable:
        raise PurchaseError("nothing in the cart can be bought")
    out = []
    app = app_identity()
    for index, chunk in enumerate(split(buyable)):
        nonce = nonces[index] if nonces and index < len(nonces) else secrets.token_hex(8)
        if not _NONCE.fullmatch(nonce):
            raise PurchaseError("invalid nonce")
        market_ids = [quote["_ids"][r["lkey"]] for r in chunk]
        price = price_for(currency, usd_sum([r["price_usd"] for r in chunk]), quote["dec_price"])
        payload = {"currency": currency, "items": market_ids, "price": price, "app": app, "n": nonce}
        validate_payload(payload, player)
        operation = ["custom_json", {"id": OP_ID, "json": json.dumps(payload, separators=(",", ":")),
                                     "required_auths": [player], "required_posting_auths": []}]
        out.append({"index": index, "nonce": nonce, "operations": [operation], "payload": payload, "authority": "Active",
                    "lkeys": [r["lkey"] for r in chunk], "count": len(chunk), "currency": currency, "price": price,
                    "usd": usd_sum([r["price_usd"] for r in chunk])})
    return out


def validate_payload(payload: Any, player: str | None = None, *, current_app: bool = True) -> dict[str, Any]:
    """The only shape Ceres builds or verifies."""
    if not isinstance(payload, dict) or list(payload) != ["currency", "items", "price", "app", "n"]:
        raise PurchaseError("not a Ceres market purchase")
    if payload["currency"] not in CURRENCIES:
        raise PurchaseError("currency must be DEC or CREDITS")
    items = payload["items"]
    if (not isinstance(items, list) or not 1 <= len(items) <= MAX_ITEMS_PER_TX or len(set(items)) != len(items)
            or not all(isinstance(m, str) and _MARKET_ID.fullmatch(m) for m in items)):
        raise PurchaseError("invalid purchase items")
    price = payload["price"]
    if isinstance(price, bool) or not isinstance(price, (int, float)) or not price > 0:
        raise PurchaseError("invalid purchase price")
    if not isinstance(payload["app"], str) or not payload["app"].startswith("ceres/") or (current_app and payload["app"] != app_identity()):
        raise PurchaseError("not a Ceres purchase")
    if not isinstance(payload["n"], str) or not _NONCE.fullmatch(payload["n"]):
        raise PurchaseError("invalid nonce")
    return payload


# ── the outcome ──────────────────────────────────────────────────────────────────────────────────────────────────────

def _json(value: Any) -> Any:
    for _ in range(4):
        if not isinstance(value, str):
            return value
        try:
            value = json.loads(value)
        except (TypeError, json.JSONDecodeError):
            return None
    return value


def outcome(info: Any, player: str, tx: str, expected: dict[str, Any]) -> dict[str, Any]:
    """The game's record of one signed purchase -> per listing bought / not_bought, or not_found / pending / failed.

    ``trx_info.success`` and ``result.success`` true: ``result.by_seller[].items`` are the market ids bought; every other
    requested id is not bought (partial fills are reported per listing, never assumed all-or-nothing). ``success`` false:
    failed, with the game's own ``error`` text (the page escapes it). Anything else is pending (checked again)."""
    from fides.game_verdict import _inner_refusal

    items = list(expected["items"])
    keyed = [{"lkey": listing_key({"market_id": m}), "market_id": m} for m in items]
    if not isinstance(info, dict):
        return {"status": "not_found"}
    if info.get("player") != player:
        return {"status": "mismatch", "message": "This transaction belongs to another account."}
    if info.get("id") != tx or info.get("type") != GAME_TYPE:
        return {"status": "mismatch", "message": "This transaction is not this market purchase."}
    data = _json(info.get("data"))
    if not isinstance(data, dict) or data.get("n") != expected["n"] or list(data.get("items") or []) != items \
            or data.get("currency") != expected["currency"]:
        return {"status": "mismatch", "message": "The game's record differs from the purchase Ceres prepared."}
    result = _json(info.get("result"))
    refusal = _inner_refusal(result) if result is not None else None
    if info.get("success") is False or refusal is not None or (isinstance(result, dict) and result.get("success") is False):
        message = refusal or info.get("error") or (result.get("error") if isinstance(result, dict) else None) or "The game refused the purchase."
        return {"status": "failed", "message": str(message)[:500],
                "items": [{"lkey": k["lkey"], "outcome": "not_bought"} for k in keyed]}
    if info.get("success") is not True or not isinstance(result, dict) or result.get("success") is not True:
        return {"status": "pending"}
    bought: set[str] = set()
    for seller in result.get("by_seller") or []:
        if isinstance(seller, dict):
            bought.update(str(m) for m in seller.get("items") or [] if isinstance(m, str))
    rows = [{"lkey": k["lkey"], "outcome": "bought" if k["market_id"] in bought else "not_bought"} for k in keyed]
    n_bought = sum(1 for r in rows if r["outcome"] == "bought")
    paid = {key: card_market._number(result.get(key)) for key in ("total_usd", "total_dec")}
    return {"status": "confirmed", "fill": "all" if n_bought == len(rows) else "none" if n_bought == 0 else "partial",
            "items": rows, "bought": n_bought, "requested": len(rows), "num_cards": card_market._int(result.get("num_cards")),
            "paid_usd": paid["total_usd"], "paid_dec": paid["total_dec"]}


COMBINE_OP_ID = "sm_combine_cards"
COMBINE_GAME_TYPE = "combine_cards"
_UIDS = re.compile(r"[A-Za-z0-9-]{3,40}")


def combine_payload(operation: Any, player: str) -> dict[str, Any]:
    """: the Buy & max combine operation the page signed (Keychain tuple), checked: sm_combine_cards, Posting of this
    player, two or more distinct card ids and a nonce. Raises PurchaseError otherwise."""
    if not (isinstance(operation, list) and len(operation) == 2 and operation[0] == "custom_json" and isinstance(operation[1], dict)):
        raise PurchaseError("not a combine")
    body = operation[1]
    if body.get("id") != COMBINE_OP_ID or body.get("required_posting_auths") != [player] or body.get("required_auths") not in ([], None):
        raise PurchaseError("not this player's combine")
    payload = _json(body.get("json"))
    cards = payload.get("cards") if isinstance(payload, dict) else None
    if (not isinstance(cards, list) or not 2 <= len(cards) <= 150 or len(set(map(str, cards))) != len(cards)
            or not all(isinstance(c, str) and _UIDS.fullmatch(c) for c in cards) or not isinstance(payload.get("n"), str)):
        raise PurchaseError("invalid combine")
    return payload


def combine_outcome(info: Any, player: str, tx: str, payload: dict[str, Any], expected_bcx: float | None = None) -> dict[str, Any]:
    """: the game's record of the Buy & max combine (one by-id lookup) -> confirmed / failed / pending / not_found / mismatch.

    The game's own record is the answer, as for the purchase: ``success`` true with ``result.updated_card`` (one of the signed
    cards, now this player's, with the merged CC) and ``result.removed_cards`` (exactly the other signed cards) is the combine
    done. The live collection re-read of /api/op-verify is not used here: it also demands that the survivor's collection power
    equals the inputs' sum, which the game does not keep (maintainer's combine e72cfe27: the record shows 2,400 = the survivor's own
    60 CC at 40 per CC; minutes later /cards/find shows 4,830 = 115 CC at 42), so a finished combine stayed "pending" until the
    3-minute clock turned it red. Only ``success`` false is ``failed`` (with the game's text); everything else is not a failure.
    No card id is returned (the page shows card names, never ids)."""
    from fides.game_verdict import _inner_refusal

    cards = [str(c) for c in payload["cards"]]
    if not isinstance(info, dict):
        return {"status": "not_found"}
    if info.get("player") != player:
        return {"status": "mismatch", "message": "This transaction belongs to another account."}
    if info.get("id") != tx or info.get("type") != COMBINE_GAME_TYPE:
        return {"status": "mismatch", "message": "This transaction is not this combine."}
    data = _json(info.get("data"))
    if not isinstance(data, dict) or data.get("n") != payload.get("n") or [str(c) for c in data.get("cards") or []] != cards:
        return {"status": "mismatch", "message": "The game's record differs from the combine Ceres prepared."}
    result = _json(info.get("result"))
    refusal = _inner_refusal(result) if result is not None else None
    if info.get("success") is False or refusal is not None or (isinstance(result, dict) and result.get("success") is False):
        message = refusal or info.get("error") or (result.get("error") if isinstance(result, dict) else None) or "The game refused the combine."
        return {"status": "failed", "message": str(message)[:500]}
    if info.get("success") is not True or not isinstance(result, dict) or result.get("success") is not True:
        return {"status": "pending"}
    card = result.get("updated_card")
    removed = result.get("removed_cards")
    survivor = str(card.get("uid")) if isinstance(card, dict) else ""
    if (survivor not in cards or str(card.get("player") or "").lower() != player.lower() or not isinstance(removed, list)
            or sorted(map(str, removed)) != sorted(c for c in cards if c != survivor)):
        return {"status": "mismatch", "message": "The game accepted the combine, but its record does not show one card made of these copies."}
    bcx = card_market._number(card.get("bcx"))
    if bcx is None:
        return {"status": "mismatch", "message": "The game accepted the combine, but its record shows no CC for the card."}
    if expected_bcx is not None and bcx != expected_bcx:
        return {"status": "mismatch", "message": f"The game's record shows {bcx:g} CC; Ceres expected {expected_bcx:g} CC."}
    return {"status": "confirmed", "bcx": bcx, "land_base_pp": card_market._number(card.get("land_base_pp")), "count": len(cards)}


def find_in_history(rows: list, player: str, nonce: str) -> str | None:
    """The newest sm_market_purchase in the player's OWN recent history carrying this nonce (Active: required_auths)."""
    found = None
    for row in rows if isinstance(rows, list) else []:
        entry = row[1] if isinstance(row, list) and len(row) == 2 and isinstance(row[1], dict) else {}
        op, tx_id = entry.get("op"), entry.get("trx_id")
        if not (isinstance(op, list) and len(op) == 2 and op[0] == "custom_json" and isinstance(op[1], dict)
                and isinstance(tx_id, str) and re.fullmatch(r"[a-fA-F0-9]{40}", tx_id)):
            continue
        body = op[1]
        if body.get("id") != OP_ID or body.get("required_auths") != [player]:
            continue
        payload = _json(body.get("json"))
        if isinstance(payload, dict) and payload.get("n") == nonce:
            found = tx_id.lower()
    return found


# ── test buy ─────────────────────────────────────────────────────────────────────────────────────────────────

def cheapest_listing(details_by_id: dict[int, dict[str, Any]], player: str, getter: Getter,
                     cache: card_market.MarketCache | None = None) -> dict[str, Any] | None:
    """The single cheapest listing on the market (another player's, USD, a card in the catalogue): the cheapest print by
    ``for_sale_grouped`` low price, then its cheapest listing from the print's deep read. Returns a cart item."""
    cache = cache or card_market.CACHE
    calls: list[str] = []
    grouped = cache.get(API_ORIGIN + card_market.GROUPED_PATH, getter, calls)
    prints = [p for p in (card_market.normalise_print(r, details_by_id) for r in (grouped if isinstance(grouped, list) else [])
                          if isinstance(r, dict)) if p and p.get("qty") and p.get("low_price_usd")]
    for item in sorted(prints, key=lambda p: p["low_price_usd"])[:5]:
        answer = cache.get(card_market.deep_url(item), getter, calls)
        rows = [r for r in card_market._print_rows(item, answer)
                if str(r.get("seller") or "").lower() != player.lower() and card_market._number(r.get("buy_price"))]
        rows.sort(key=lambda r: card_market._number(r.get("buy_price")))
        for row in rows:
            key, mid = listing_key(row), str(row.get("market_id") or "")
            if key and _MARKET_ID.fullmatch(mid):
                pid = card_market.print_id(item["card_detail_id"], item["edition"], item["foil"])
                KEYS.remember(key, mid, pid)
                return {"lkey": key, "pid": pid, "seen_usd": card_market._number(row.get("buy_price")), "name": item["name"],
                        "level": card_market._int(row.get("level")), "bcx": card_market._number(row.get("bcx")),
                        "card_set": item["card_set"], "rarity": item["rarity"], "foil": item["foil"],
                        "card_detail_id": item["card_detail_id"], "edition": item["edition"]}
    return None


# ── : Buy & max -- the combine of a bought maxed-card group ──────────────────────────────────────────────────────

def group_uids(market_ids: list[str], rows: dict[str, dict[str, Any]], player: str) -> tuple[list[str], list[str]]:
    """The card uids of bought listings, from the game's by-id listing status (``/market/status?ids=``): a sold listing keeps
    its ``cards[].uid`` and names the buyer (``purchaser``, and ``cards[].player`` after the sale; measured 2026-10-15 on the
    maintainer's purchase cbba6648..-0: status 1, purchaser and card player = the maintainer, the same uid ``/cards/find`` lists as his).
    Returns (uids, market ids not yet readable as bought by this player)."""
    uids: list[str] = []
    waiting: list[str] = []
    for market_id in market_ids:
        row = rows.get(market_id)
        cards = row.get("cards") if isinstance(row, dict) and isinstance(row.get("cards"), list) else []
        if (not row or str(row.get("status")) != "1" or str(row.get("purchaser") or "").lower() != player.lower() or not cards
                or not all(isinstance(c, dict) and isinstance(c.get("uid"), str) and c["uid"] for c in cards)):
            waiting.append(market_id)
            continue
        uids.extend(c["uid"] for c in cards)
    return uids, waiting
