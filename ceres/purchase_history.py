""": "My purchases" -- the player's own card-market purchases with PP and price statistics, read only on the maintainer's press.

Owner (): "do we log the buys? It is all on the blockchain anyways" / "what do you think? it could show features lie
avergae PP/$ and other useful stats" -> "Yes, on demand (Recommended)". This is NOT the removed Receipts screen (,
library/ceres-receipts-removal.md): no chain or account-history read, no background or automatic read, nothing stored on the
server or in the browser beyond a 60-second in-memory cache of the upstream answers. It runs only when the route is called,
and the page calls the route only when the maintainer presses "Load my purchases".

Source (evidence, 2026-10-15): the official card market's Activity page (``c_SalesActivity`` chunk of splinterlands.com) calls
``getMarketSalesHistory`` = ``GET {SL_API_URL}/market/history?player=<p>&game=splinterlands`` and keeps the rows whose
``purchaser`` is the player as "Purchases" (the others are "Sales"). Measured on api.splinterlands.com the same day (public, no
login): newest first, at most ``SOURCE_ROWS`` (200) rows of purchases AND sales together, reaching back about ``SOURCE_DAYS``
(30) days; ``limit``, ``offset``, ``from``, ``type`` and ``purchaser`` change nothing. Each row: ``card_id`` (the copy's uid),
``card_detail_id``, ``edition``, ``foil``/``gold``, ``xp``, ``buy_price`` (USD), ``payment`` ("18347.704 DEC"),
``completed_date``, ``transaction_id``, ``purchaser``, ``seller``, ``status``. (The  finding "the buyer's market feed never
lists purchases" was about plots and the non-card feed; this card feed does list them.)

Requests per press: ``/market/history`` once, ``/cards/find?ids=`` once (a by-id lookup of at most ``FIND_IDS`` bought
copies: whether a copy was later combined -- ``combined_card_id`` set, or it now holds more CC than it was bought with -- and the
game's own ``land_base_pp`` while the copy is unchanged), and the separate ``/market/player/activity`` purchase feed once for
non-card money totals. The going rate and today's prices come from card_market's shared 60 s cache of
``/market/for_sale_grouped`` and ``/settings`` (the Market and Find combinables read the same two answers).

CC at purchase. The row carries ``xp``, not CC. Modern prints: CC = xp. Alpha and Beta prints (``level_cap`` regime): CC =
xp / xp_per_card + 1 (regular) or xp / xp_per_card (gold), with the per-card xp of ``ceres/data/level_thresholds.json``
(the game's /settings) -- checked on the maintainer's 2026-10-14 purchases against ``/cards/find``'s ``bcx``: Beta rare 4500 xp -> 61,
4125 -> 56, Beta legendary 7500 -> 11, Beta common 7560 -> 505, Alpha gold rare 500 -> 1, 1000 -> 2.

Land base PP (as card_market): the game's ``land_base_pp`` for the copy wins while the copy still holds the CC it was bought
with; otherwise the print's max-level PP x min(CC, max CC) / max CC. Cards that are not Land workers (towers, soulbound,
``no_pp``) count in the money totals but carry no PP.

Statistics (definitions, all on the purchases in the chosen window):
- totals: listings bought, distinct transactions, USD (the listing's ``buy_price``) and the amount paid per currency.
- PP bought: sum of the Land base PP of every Land copy at its purchase CC.
- average PP/$: PP bought / USD paid for those copies (weighted, not a mean of ratios); also per month (UTC), per rarity, per
  edition (card set).
- going rate: card_combinables.going_rate (default basis, : the median over every Land print on sale of its cheapest
  price per PP) -- TODAY's rate; the game keeps no historic rate. Each purchase's PP/$ is compared with it (``vs_going``: purchase
  PP/$ / going PP/$) and tiered like the Market's PP/$ colours: top at or above the going PP/$, green within 20% more cost
  per PP, orange within 40%, red worse.
- best / worst buys: the three highest and lowest PP/$ (Land copies with a price).
- value today: the print's cheapest price per CC on the market now x the copy's CC (``for_sale_grouped`` ``low_price_bcx``), next
  to what was paid for the same copies; prints not on sale today are left out of both sides.
- later combined: bought copies that ``/cards/find`` shows burned into another copy or holding more CC than bought; and copies no
  longer owned by the player.

No uid, transaction id, market id or seller ever leaves this module: a purchase is shown by card name, level, CC and date.
Identical copies bought on the same UTC day are shown together, including across transactions and prices, but only when their
paid currency matches. Transactions still have opaque ``batch`` numbers (1, 2, ... in the order of the answer) for statistics.
"""

from __future__ import annotations

import re
import threading
import time
import urllib.parse
from collections import OrderedDict
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from praetoria.transport import API_ORIGIN, LAND_ORIGIN, FixedGetTransport

from . import card_combinables, card_market
from .bcx_cap import _regime_for
from .level_cap import _XP_PER_CARD_KEYS, _load as _level_data, level_for_bcx, resolve_level_thresholds

Getter = Callable[[str], Any]

HISTORY_PATH = "/market/history"
FIND_PATH = "/cards/find"
OTHER_PATH = "/market/player/activity"
CACHE_SECONDS = 60.0
WINDOWS = (7, 14, 30)        # days the page offers; the game keeps about SOURCE_DAYS
DEFAULT_DAYS = 30
SOURCE_DAYS = 30             # measured 2026-10-15: the oldest row of every account read was 29-30 days old
SOURCE_ROWS = 200            # measured 2026-10-15: never more rows (purchases and sales together)
FIND_IDS = 100               # bought copies looked up by id per press (newest first)
BEST_WORST = 3
MAX_PLAYERS_CACHED = 64

_RARITY_INDEX = {"common": 0, "rare": 1, "epic": 2, "legendary": 3}
_UID = re.compile(r"[A-Za-z0-9-]{4,40}")


def _default_get(url: str) -> Any:
    return FixedGetTransport(timeout=20, retries=1).get_json(url).data


def history_url(player: str) -> str:
    return f"{API_ORIGIN}{HISTORY_PATH}?{urllib.parse.urlencode({'player': player, 'game': 'splinterlands'})}"


def find_url(uids: list[str]) -> str:
    return f"{API_ORIGIN}{FIND_PATH}?{urllib.parse.urlencode({'ids': ','.join(uids)}, safe=',')}"


def other_url(player: str) -> str:
    return f"{LAND_ORIGIN}{OTHER_PATH}?{urllib.parse.urlencode({'player': player, 'types': 'purchase', 'sort': 'desc', 'limit': 200, 'offset': 0})}"


def other_total(feed: Any, player: str, since: datetime) -> dict[str, Any]:
    """Money only from the separate account activity feed. Excludes card IDs and sales."""
    rows = feed.get("data", []) if isinstance(feed, dict) else feed
    rows = rows if isinstance(rows, list) else []
    buys = [r for r in rows if isinstance(r, dict) and str(r.get("buyer") or "").lower() == player.lower()
            and r.get("type") == "purchase" and (parse_time(r.get("createdDate")) or datetime.min.replace(tzinfo=timezone.utc)) >= since
            and not re.match(r"^C\d*-", str(r.get("itemId") or ""), re.IGNORECASE)]
    return {"count": len(buys), "usd": round(sum(card_market._number(r.get("usdValue")) or 0 for r in buys), 6),
            "dec": round(sum((card_market._number(r.get("price")) or 0) for r in buys if r.get("currency") == "DEC"), 3),
            "capped": len(rows) >= 200,
            "note": "From the game's separate market activity feed; it may omit some purchases. DEC is shown only when paid in DEC."}


# parsing

def parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        out = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return out if out.tzinfo else out.replace(tzinfo=timezone.utc)


def parse_payment(value: Any) -> tuple[float | None, str | None]:
    """'18347.704 DEC' -> (18347.704, 'DEC'); 'CREDITS' kept as the game spells it."""
    parts = str(value or "").split()
    if len(parts) != 2:
        return None, None
    amount = card_market._number(parts[0])
    currency = parts[1].strip().upper()
    return (amount, currency) if amount is not None and re.fullmatch(r"[A-Z]{2,10}", currency) else (None, None)


def bcx_from_xp(xp: Any, *, card_set: Any, edition: Any, card_detail_id: Any, rarity: Any, gold: bool) -> int | None:
    """CC of a copy from the market row's ``xp`` (see the module docstring)."""
    xp_value = card_market._number(xp)
    if xp_value is None or xp_value < 0:
        return None
    regime = _regime_for(card_set, edition, card_detail_id)
    if regime in ("alpha", "beta"):
        index = _RARITY_INDEX.get(rarity)
        per_card = (_level_data().get(_XP_PER_CARD_KEYS[(regime, bool(gold))]) or [None] * 4)[index] if index is not None else None
        if not per_card:
            return None
        return int(xp_value // per_card) if gold else int(xp_value // per_card) + 1
    return int(xp_value) if xp_value >= 1 else None


def _print_info(detail_id: int | None, edition: int | None, foil: int | None,
                details_by_id: dict[int, dict[str, Any]]) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """(Land print summary or None, catalogue detail or None)."""
    detail = details_by_id.get(detail_id) if detail_id is not None else None
    if not detail:
        return None, None
    item = card_market.normalise_print({"card_detail_id": detail_id, "edition": edition, "foil": foil}, details_by_id)
    return item, detail


def purchase_rows(history: Any, player: str) -> list[dict[str, Any]]:
    """The history rows that are this player's completed purchases (``purchaser`` = player, ``status`` 1), newest first."""
    rows = [r for r in (history if isinstance(history, list) else []) if isinstance(r, dict)
            and str(r.get("purchaser") or "").lower() == player.lower() and str(r.get("status")) == "1"
            and parse_time(r.get("completed_date"))]
    rows.sort(key=lambda r: parse_time(r.get("completed_date")), reverse=True)
    return rows


def normalise_purchase(row: dict[str, Any], details_by_id: dict[int, dict[str, Any]], batch: int) -> dict[str, Any]:
    """One purchase row -> the shown purchase (no uid, no transaction id, no seller) plus private ``_uid`` for the by-id read."""
    detail_id = card_market._int(row.get("card_detail_id"))
    edition = card_market._int(row.get("edition"))
    foil = card_market._int(row.get("foil"))
    if foil is None:
        foil = 1 if row.get("gold") else 0
    item, detail = _print_info(detail_id, edition, foil, details_by_id)
    rarity_raw = (detail or {}).get("rarity")
    rarity = card_market.RARITY_NAMES.get(rarity_raw) if isinstance(rarity_raw, int) else None
    card_set = item["card_set"] if item else (card_market.print_card_set(edition, detail) if detail else None)
    gold = foil in (1, 2)
    num_cards = card_market._int(row.get("num_cards")) or 1
    bcx = bcx_from_xp(row.get("xp"), card_set=card_set, edition=edition, card_detail_id=detail_id, rarity=rarity, gold=gold) \
        if num_cards == 1 else None
    thresholds = resolve_level_thresholds(detail_id, edition, rarity, gold, card_set) if detail else None
    pp = card_market.pp_at(item["max_level_pp"], bcx, item["bcx_max"]) if item else None
    price_usd = card_market._number(row.get("buy_price"))
    paid, currency = parse_payment(row.get("payment"))
    when = parse_time(row.get("completed_date"))
    uid = str(row.get("card_id") or "")
    return {
        "date": when.isoformat().replace("+00:00", "Z") if when else None,
        "month": when.strftime("%Y-%m") if when else None,
        "batch": batch,
        "name": (detail or {}).get("name") or "Unknown card",
        "card_detail_id": detail_id,
        "edition": edition,
        "foil": foil,
        "card_set": card_set,
        "rarity": rarity,
        "land": item is not None,
        "num_cards": num_cards,
        "bcx": bcx,
        "bcx_max": item["bcx_max"] if item else None,
        "level": level_for_bcx(thresholds, bcx) if bcx else None,
        "max_level": len(thresholds) if thresholds else None,
        "pp": pp,
        "pp_source": "formula" if pp is not None else None,
        "price_usd": price_usd,
        "paid": paid,
        "paid_currency": currency,
        "pp_per_usd": card_market._ratio(pp, price_usd),
        "combined": None,
        "owned": None,
        "_uid": uid if _UID.fullmatch(uid) and num_cards == 1 else None,
        "_low_per_cc": None,
    }


def apply_find(purchases: list[dict[str, Any]], found: Any, player: str) -> int:
    """Marks each looked-up purchase: ``combined`` (burned into another copy, or now more CC than bought), ``owned`` (the
    player still holds it or the copy it went into), and the game's own PP while the copy is unchanged. Returns copies read."""
    by_uid = {c.get("uid"): c for c in (found if isinstance(found, list) else []) if isinstance(c, dict) and c.get("uid")}
    read = 0
    for p in purchases:
        card = by_uid.get(p.get("_uid"))
        if not card:
            continue
        read += 1
        now_bcx = card_market._number(card.get("bcx"))
        grown = now_bcx is not None and p["bcx"] is not None and now_bcx > p["bcx"]
        p["combined"] = bool(card.get("combined_card_id")) or grown
        p["owned"] = str(card.get("player") or "").lower() == player.lower()
        game_pp = card_market._number(card.get("land_base_pp"))
        if p["land"] and not p["combined"] and now_bcx is not None and now_bcx == p["bcx"] and game_pp and game_pp > 0:
            p["pp"], p["pp_source"] = game_pp, "game"
            p["pp_per_usd"] = card_market._ratio(game_pp, p["price_usd"])
    return read


# statistics

def rate_tier(pp_per_usd: float | None, going_pp_per_usd: float | None) -> str | None:
    if not pp_per_usd or not going_pp_per_usd or pp_per_usd <= 0 or going_pp_per_usd <= 0:
        return None
    gap = round(max(0, going_pp_per_usd / pp_per_usd - 1), 9)
    return "is-top" if gap == 0 else "is-good" if gap <= 0.2 else "is-fair" if gap <= 0.4 else "is-dear"


def _group(purchases: list[dict[str, Any]]) -> dict[str, Any]:
    land = [p for p in purchases if p["pp"] and p["price_usd"]]
    usd = sum(p["price_usd"] or 0 for p in purchases)
    pp = sum(p["pp"] for p in land)
    land_usd = sum(p["price_usd"] for p in land)
    return {"count": len(purchases), "usd": round(usd, 6), "pp": round(pp, 3), "pp_per_usd": card_market._ratio(pp, land_usd)}


def _by(purchases: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    groups: OrderedDict[str, list[dict[str, Any]]] = OrderedDict()
    for p in purchases:
        groups.setdefault(p.get(key) or "unknown", []).append(p)
    return [{"key": k, **_group(v)} for k, v in groups.items()]


def _brief(p: dict[str, Any]) -> dict[str, Any]:
    return {k: p[k] for k in ("date", "name", "card_set", "rarity", "foil", "level", "bcx", "pp", "price_usd", "pp_per_usd", "tier")}


def summarise(purchases: list[dict[str, Any]], going: dict[str, Any] | None) -> dict[str, Any]:
    going_usd = (going or {}).get("usd_per_pp")
    going_pp_per_usd = 1 / going_usd if going_usd else None
    for p in purchases:
        p["vs_going"] = card_market._ratio(p["pp_per_usd"], going_pp_per_usd) if p["pp_per_usd"] else None
        p["tier"] = rate_tier(p["pp_per_usd"], going_pp_per_usd)
        p["value_now_usd"] = p["_low_per_cc"] * p["bcx"] if p["_low_per_cc"] and p["bcx"] else None
    paid: dict[str, float] = {}
    for p in purchases:
        if p["paid"] is not None and p["paid_currency"]:
            paid[p["paid_currency"]] = round(paid.get(p["paid_currency"], 0.0) + p["paid"], 3)
    overall = _group(purchases)
    ranked = sorted((p for p in purchases if p["pp_per_usd"]), key=lambda p: (-p["pp_per_usd"], p["date"] or ""))
    valued = [p for p in purchases if p["value_now_usd"] is not None and p["price_usd"]]
    land_pp = [p for p in purchases if p["pp"] and p["price_usd"]]
    looked_up = [p for p in purchases if p["combined"] is not None]
    by_rarity = sorted(_by(purchases, "rarity"), key=lambda g: list(_RARITY_INDEX).index(g["key"]) if g["key"] in _RARITY_INDEX else 9)
    return {
        "count": overall["count"],
        "transactions": len({p["batch"] for p in purchases}),
        "usd": overall["usd"],
        "paid": paid,
        "land_count": len(land_pp),
        "pp": overall["pp"],
        "pp_per_usd": overall["pp_per_usd"],
        "going_pp_per_usd": going_pp_per_usd,
        "vs_going": card_market._ratio(overall["pp_per_usd"], going_pp_per_usd),
        "tier": rate_tier(overall["pp_per_usd"], going_pp_per_usd),
        "value_at_going_usd": round(sum(p["pp"] for p in land_pp) * going_usd, 6) if going_usd and land_pp else None,
        "value_at_going_paid_usd": round(sum(p["price_usd"] for p in land_pp), 6) if land_pp else None,
        "value_now_usd": round(sum(p["value_now_usd"] for p in valued), 6) if valued else None,
        "value_now_paid_usd": round(sum(p["price_usd"] for p in valued), 6) if valued else None,
        "value_now_count": len(valued),
        "best": [_brief(p) for p in ranked[:BEST_WORST]],
        "worst": [_brief(p) for p in ranked[BEST_WORST:][::-1][:BEST_WORST]],  # never a copy already listed as a best buy
        "by_month": sorted(_by(purchases, "month"), key=lambda g: g["key"], reverse=True),
        "by_rarity": by_rarity,
        "by_edition": sorted(_by(purchases, "card_set"), key=lambda g: -g["usd"]),
        "combined": sum(1 for p in looked_up if p["combined"]),
        "not_owned": sum(1 for p in looked_up if p["owned"] is False),
        "looked_up": len(looked_up),
    }


def collapse_purchases(purchases: list[dict[str, Any]], going_pp_per_usd: float | None = None) -> list[dict[str, Any]]:
    """Show identical copies bought on one UTC day and in one currency together; statistics remain per copy.

    Sum the row's money, PP and current value, then rate that sum against today's going PP/$.
    """
    groups: OrderedDict[tuple[Any, ...], dict[str, Any]] = OrderedDict()
    for p in purchases:
        key = ((p["date"] or "")[:10], p["card_detail_id"], p["edition"], p["foil"],
               p["bcx"], p["level"], p["paid_currency"])
        if key not in groups:
            groups[key] = {**p, "quantity": 1}
            continue
        row = groups[key]
        row["quantity"] += 1
        for field in ("price_usd", "paid", "pp", "value_now_usd"):
            if row[field] is not None and p[field] is not None:
                row[field] = round(row[field] + p[field], 6)
            else:
                row[field] = None
        row["combined"] = row["combined"] or p["combined"]
        row["owned"] = row["owned"] and p["owned"] if row["owned"] is not None and p["owned"] is not None else None
        row["pp_source"] = row["pp_source"] if row["pp_source"] == p["pp_source"] else "formula"
        row["pp_per_usd"] = card_market._ratio(row["pp"], row["price_usd"])
        row["vs_going"] = card_market._ratio(row["pp_per_usd"], going_pp_per_usd) if row["pp_per_usd"] else None
        row["tier"] = rate_tier(row["pp_per_usd"], going_pp_per_usd)
    return list(groups.values())


# the read (route only)

class PurchaseCache:
    """60-second memory cache of the player's history, by-id cards and other-purchase feed. Nothing is persisted."""

    def __init__(self, seconds: float = CACHE_SECONDS) -> None:
        self.seconds = seconds
        self._lock = threading.Lock()
        self._store: OrderedDict[str, tuple[float, Any]] = OrderedDict()

    def get(self, url: str, getter: Getter, calls: list[str], now: float | None = None) -> Any:
        now = time.monotonic() if now is None else now
        with self._lock:
            hit = self._store.get(url)
            if hit is not None and now - hit[0] < self.seconds:
                return hit[1]
        data = getter(url)
        calls.append(url.split("?", 1)[0].replace(API_ORIGIN, ""))
        with self._lock:
            self._store[url] = (now, data)
            for key in [k for k, (at, _) in self._store.items() if now - at >= self.seconds]:
                self._store.pop(key, None)
            while len(self._store) > 3 * MAX_PLAYERS_CACHED:
                self._store.popitem(last=False)
        return data

    def clear(self) -> None:
        with self._lock:
            self._store.clear()


CACHE = PurchaseCache()


def _window(days: Any) -> int:
    value = card_market._int(days)
    return value if value in WINDOWS else DEFAULT_DAYS


def build(player: str, details_by_id: dict[int, dict[str, Any]], *, days: Any = DEFAULT_DAYS, getter: Getter | None = None,
          cache: PurchaseCache | None = None, market_cache: card_market.MarketCache | None = None,
          now: datetime | None = None) -> dict[str, Any]:
    """The whole answer of GET /api/cards/purchases. Upstream: history, cards/find and other-purchase activity once each
    (cached 60 s here), plus today's grouped market through card_market's shared cache."""
    get = getter or _default_get
    cache = cache or CACHE
    market_cache = market_cache or card_market.CACHE
    window = _window(days)
    now = now or datetime.now(timezone.utc)
    calls: list[str] = []
    history = cache.get(history_url(player), get, calls)
    rows_read = len(history) if isinstance(history, list) else 0
    all_buys = purchase_rows(history, player)
    since = now - timedelta(days=window)
    rows = [r for r in all_buys if parse_time(r["completed_date"]) >= since]
    batches: dict[str, int] = {}  # transaction id -> 1, 2, ... (the id itself never leaves this function)
    purchases = [normalise_purchase(r, details_by_id, batches.setdefault(str(r.get("transaction_id") or f"row-{i}"), len(batches) + 1))
                 for i, r in enumerate(rows)]

    # the newest FIND_IDS bought copies of the whole answer (not only this window): one URL, so a window change inside the
    # 60 s cache reads nothing new from this source
    uids = [u for u in (str(r.get("card_id") or "") for r in all_buys if (card_market._int(r.get("num_cards")) or 1) == 1)
            if _UID.fullmatch(u)][:FIND_IDS]
    looked_up = 0
    if uids:
        found = cache.get(find_url(sorted(set(uids))), get, calls)
        looked_up = apply_find(purchases, found, player)

    going = card_combinables.going_rate(details_by_id, getter=get, cache=market_cache)
    grouped = market_cache.get(card_market.API_ORIGIN + card_market.GROUPED_PATH, get, calls)
    low = {}
    for r in grouped if isinstance(grouped, list) else []:
        if isinstance(r, dict):
            foil = card_market._int(r.get("foil"))
            foil = (1 if r.get("gold") else 0) if foil is None else foil
            low[(card_market._int(r.get("card_detail_id")), card_market._int(r.get("edition")), foil)] = card_market._number(r.get("low_price_bcx"))
    for p in purchases:
        p["_low_per_cc"] = low.get((p["card_detail_id"], p["edition"], p["foil"]))
    summary = summarise(purchases, going)
    try:
        other_feed = cache.get(other_url(player), get, calls)
        summary["other_purchases"] = other_total(other_feed, player, since)
    except Exception:
        summary["other_purchases"] = {"count": 0, "usd": None, "dec": None, "capped": False,
                                      "note": "The separate purchase feed could not be read. Try Read again."}

    oldest_row = min((parse_time(r.get("completed_date")) for r in (history or []) if isinstance(r, dict) and parse_time(r.get("completed_date"))),
                     default=None) if isinstance(history, list) else None
    capped = rows_read >= SOURCE_ROWS
    covered_from = max(since, oldest_row) if capped and oldest_row else max(since, now - timedelta(days=SOURCE_DAYS))
    shown = [{k: v for k, v in p.items() if not k.startswith("_")} for p in collapse_purchases(purchases, summary["going_pp_per_usd"])]
    return {
        "player": player,
        "days": window,
        "windows": list(WINDOWS),
        "read_at": now.isoformat().replace("+00:00", "Z"),
        "purchases": shown,
        "summary": summary,
        "going": {"usd_per_pp": going.get("usd_per_pp"), "pp_per_usd": summary["going_pp_per_usd"], "dec_per_pp": going.get("dec_per_pp"),
                  "dec_usd": going.get("dec_usd"), "basis": going.get("basis"), "method": going.get("method"), "today": True},
        "coverage": {
            "rows_read": rows_read,
            "capped": capped,
            "covered_from": covered_from.isoformat().replace("+00:00", "Z"),
            "source_days": SOURCE_DAYS,
            "looked_up": looked_up,
            "lookup_limit": FIND_IDS,
            "note": ("The game's market history holds its newest 200 purchases and sales; older ones in this window are not in it."
                     if capped else "The game's market history reaches back about 30 days."),
        },
        "upstream_calls": calls + [c for c in going.get("upstream_calls") or [] if c not in calls],
    }
