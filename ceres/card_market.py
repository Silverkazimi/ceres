""": the read-only card market ("shop", phase 2 of the Cards page).

: the Cards page reads the market when it enters Market
mode and again (debounced) after each filter change that changes which prints are in scope; this supersedes the earlier
read-on-press rule for the market only. One read costs a small, bounded number of public, unauthenticated GETs on
api.splinterlands.com (the same host and transport as every other Ceres read, praetoria.transport), each cached 60 s:

1. ``/market/for_sale_grouped`` -- one row per print (card_detail_id, edition, foil) on sale, with the cheapest
   listing (``low_price``) and the cheapest price per CC (``low_price_bcx``), both in USD. ~2,300 rows, ~40 KB
   gzipped. Upstream takes no working filter, so set / rarity / foil / splinter are applied here.
2. ``/settings`` -- ``dec_price`` (USD per DEC), to show every price in DEC as well.
3. ``/market/market_query_grouped?card_ids=..&foil=F&limit=N`` -- the real listings (sorted by price per CC,
   cheapest first, measured 2026-10-14) for the best prints of step 1, one call per foil class present, at most
   100 card ids per call. Each listing carries its own bcx, level, buy_price and ``land_base_pp``.

Land PP. A listing's ``land_base_pp`` is the game's own figure and wins. When it is missing, PP is derived from the
same rules as the Cards page: the print's max-level PP (``card_browser.max_level_land_pp``) times
``min(bcx, bcx_max) / bcx_max`` -- Land PP is linear in CC up to the max-level CC and flat beyond
(library/cards-and-collection.md "PP-per-CC table"). Prints with no formula (gold arcane, black foils, an
unmapped set) carry ``None`` until a listing gives the figure.

Cost per PP: the listing's price / its current PP, in USD (ranking, ) and DEC.

Maxed. With ``maxed`` set only max-level offers are returned: listings already at max CC,
and per print one combined offer -- the set of that print's listings whose CC sums to at least max CC at the minimum
total price (fewest listings on a tie), see ``cheapest_max_combo``. ``market_query_grouped`` returns at most 10 rows
per card id (measured 2026-10-15, any ``limit``), so a print whose group came back full is read again, deeper, with
``market_query_by_card`` (``MAXED_DEEP_LISTINGS`` cheapest per CC), for at most ``MAXED_DEEP_PRINTS`` prints.

Pages: prints are read in the order of a bound of their best offer that passes every filter (``rank_key``), and a page
shows only the offers between two such bounds, so no print left unread can beat a shown offer; see the block above
``rank_key``. Without Maxed each print keeps its best LISTINGS_PER_PRINT listings under the sort itself, read with a level
slice of the grouped query (``level_slice``) and in depth where the rows read cannot prove them best (``_certified``), at
most PAGE_DEEP_BUDGET uncached deep reads per market read (best rank first); a print past it keeps the offers of its grouped
rows and is counted in ``uncertain``. A page reads further blocks of prints until its window holds MIN_PAGE_OFFERS offers, at
most MAX_PAGE_BLOCKS blocks and PAGE_CALL_BUDGET upstream GETs.

No uid, market id, seller or card id leaves this module: a listing is shown by card name, level and CC. The one exception
is : ``read_print`` (the card
menu of one print) adds each listing's seller account name; the grid listings never carry it. : a listing
carries an opaque ``lkey`` (the first 16 hex of a SHA-256 over its market id, ``listing_key``) so the cart can hold it and
re-find it after a re-read; the market id itself is never sent and the key is never shown. Nothing here can buy, sign or
transfer anything.

Card menu and cart. ``read_print`` reads one print in
depth (``market_query_by_card``, the same URL and 60 s cache entry as the Maxed deep read; upstream returns at most 60
listings, cheapest per CC first, measured 2026-10-15 for a print with 865 on sale) and ``level_bundles`` builds, per level,
the cheapest set of listings whose CC reaches that level's threshold (the game's level table, ``level_cap``), with the same
min-cost cover as ``cheapest_max_combo``. ``check_listings`` re-reads the prints of a cart to mark sold listings and changed
prices.
"""

from __future__ import annotations

import hashlib
import math
import threading
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

from praetoria.transport import API_ORIGIN, FixedGetTransport

from .bcx_cap import resolve_bcx_max
from .board import card_splinters
from .card_browser import RARITY_NAMES, max_level_land_pp
from .level_cap import level_for_bcx, resolve_level_thresholds
from .metrics import land_ability_row

GROUPED_PATH = "/market/for_sale_grouped"
LISTINGS_PATH = "/market/market_query_grouped"
SETTINGS_PATH = "/settings"
CACHE_SECONDS = 60.0
MAX_PRINTS = 100            # prints whose real listings are read per market read
IDS_PER_CALL = 100          # card ids per market_query_grouped call
LISTINGS_PER_PRINT = 3      # listings kept per print (cheapest per CC first)
# (maintainer: "When searched by name show only one copy per foil/set ... All below folds into a container tile opening the
# menu wth all copies"): with a name filter a print keeps up to this many listings, read in depth, so the page can fold the small
# copies of a print into one container tile (card-market-model.js cmFold)
NAME_LISTINGS_PER_PRINT = 60


def per_print(filters: dict[str, Any] | None) -> int:
    """Listings kept per print: LISTINGS_PER_PRINT, or NAME_LISTINGS_PER_PRINT under a name filter ()."""
    return NAME_LISTINGS_PER_PRINT if str((filters or {}).get("q") or "").strip() else LISTINGS_PER_PRINT
UPSTREAM_LIMIT = 6          # rows asked per card id: the group mixes editions, so a few spare rows
MAX_LISTINGS = 300          # hard cap on the response
MARKET_ORIGIN = "https://splinterlands.com"
SORTS = ("cost_per_pp", "price", "pp")
BY_CARD_PATH = "/market/market_query_by_card"
GROUPED_ROWS_CAP = 10       # market_query_grouped never returns more rows per card id (measured 2026-10-15)
# every print in scope whose
# grouped answer came back full is read in depth, whatever the sort, so a print's maxed price never depends on the sort (a print
# combined from 10 rows next to one combined from 60 was the bug). Cold cost: up to 100 deep reads, 5 at a time, cached 60 s.
MAXED_DEEP_PRINTS = MAX_PRINTS
MAXED_DEEP_LISTINGS = 60    # Maxed: listings asked per deep read (cheapest per CC first)
MAXED_SINGLES_PER_PRINT = 3  # Maxed: listings already at max level kept per print (cheapest price first)
DEEP_WORKERS = 8            # parallel deep reads ( hotfix, )
CHECK_PRINTS = 12           # prints re-read per cart check
OVER_CHEAPEST_FLAG = 0.25   # a listing 25 % or more over the print's cheapest price per CC is marked

# Print identity for a market row. Listings carry only (card_detail_id, edition, foil); card_details carries
# `tier` (the set a card was first released in). Measured on player0005's 2026-10-14 snapshot (7,968 copies)
# card_set is the tier's set, except Gladius (edition 6) and the tier-less Alpha/Beta prints (editions 0/1).
_TIER_SET = {0: "alpha", 1: "beta", 3: "untamed", 4: "untamed", 7: "chaos", 9: "other", 12: "rebellion",
             14: "conclave", 15: "foundations", 19: "land"}
_EDITION_SET = {0: "alpha", 1: "beta", 4: "untamed", 5: "untamed", 6: "gladius", 7: "chaos", 8: "chaos",
                9: "other", 10: "chaos", 11: "other", 12: "rebellion", 13: "rebellion", 14: "conclave",
                15: "foundations", 16: "foundations", 17: "conclave", 18: "conclave", 19: "land", 20: "conclave", 21: "land"}
SOULBOUND_EDITIONS = {10, 13, 16, 18}  # never on the sale market; dropped defensively
FOIL_FRAME = {0: "regular", 1: "gold", 2: "gold", 3: "black", 4: "black"}


def _number(value: Any) -> float | None:
    if value is None or value == "" or isinstance(value, bool):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if out == out else None  # NaN -> None


def _int(value: Any) -> int | None:
    number = _number(value)
    return int(number) if number is not None else None


def print_card_set(edition: Any, detail: dict[str, Any]) -> str | None:
    edition = _int(edition)
    if edition == 6:
        return "gladius"
    tier = _int(detail.get("tier"))
    if tier is not None and tier in _TIER_SET:
        return _TIER_SET[tier]
    return _EDITION_SET.get(edition) if edition is not None else None


def market_url(card_detail_id: Any, foil: Any, edition: Any) -> str | None:
    """The official card page, "For Sale" tab (route /card-detail/:id/:frame/:edition of splinterlands.com, 2026-10-14)."""
    detail_id, edition, frame = _int(card_detail_id), _int(edition), FOIL_FRAME.get(_int(foil))
    if detail_id is None or edition is None or frame is None:
        return None
    return f"{MARKET_ORIGIN}/card-detail/{detail_id}/{frame}/{edition}?tab=for-sale"


def pp_at(max_pp: float | None, bcx: float | None, bcx_max: int | None) -> float | None:
    """Land base PP of a copy with `bcx` CC: linear up to the max-level CC, flat beyond."""
    if max_pp is None or bcx is None or bcx <= 0:
        return None
    if not bcx_max:  # 0 = always max level (black foils); None = unknown
        return max_pp if bcx_max == 0 else None
    return max_pp * min(bcx, bcx_max) / bcx_max


def _ratio(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator is None or denominator <= 0:
        return None
    return numerator / denominator


def normalise_print(row: dict[str, Any], details_by_id: dict[int, dict[str, Any]]) -> dict[str, Any] | None:
    """One for_sale_grouped row -> a print summary, or None when it is not a Land worker on sale."""
    detail_id = _int(row.get("card_detail_id"))
    detail = details_by_id.get(detail_id) if detail_id is not None else None
    if not detail:
        return None
    edition = _int(row.get("edition"))
    foil = _int(row.get("foil"))
    if foil is None:
        foil = 1 if row.get("gold") else 0
    if edition in SOULBOUND_EDITIONS or bool(detail.get("no_pp")):
        return None  # no Land PP or not for sale: nothing to rank
    if str(detail.get("type") or "").lower() not in ("", "monster", "summoner", "archon"):
        return None
    rarity_raw = detail.get("rarity")
    rarity = RARITY_NAMES.get(rarity_raw) if isinstance(rarity_raw, int) else None
    card_set = print_card_set(edition, detail)
    gold = foil in (1, 2)
    bcx_max = resolve_bcx_max(detail_id, edition, rarity, gold, card_set, foil)
    thresholds = resolve_level_thresholds(detail_id, edition, rarity, gold, card_set)
    max_pp = max_level_land_pp(card_set, rarity, foil)
    pp_per_cc = _ratio(max_pp, float(bcx_max)) if bcx_max else None
    low_bcx = _number(row.get("low_price_bcx"))
    return {
        "card_detail_id": detail_id,
        "name": detail.get("name") or "Unknown card",
        "edition": edition,
        "card_set": card_set,
        "rarity": rarity,
        "foil": foil,
        "gold": gold,
        "splinters": card_splinters(detail),
        "max_level": len(thresholds) if thresholds else None,
        "bcx_max": bcx_max,
        "max_level_pp": max_pp,
        "pp_per_cc": pp_per_cc,
        "qty": _int(row.get("qty")),
        "low_price_usd": _number(row.get("low_price")),
        "low_price_per_cc_usd": low_bcx,
        "usd_per_pp": _ratio(low_bcx, pp_per_cc),  # the print's cheapest rate on the market
    }


def filter_values(value: Any) -> set[str]:
    """: a filter value is one value or a comma list (OR);
    empty = any."""
    if isinstance(value, (list, tuple, set)):
        parts = [str(v) for v in value]
    else:
        parts = str(value if value is not None else "").split(",")
    return {p.strip().lower() for p in parts if p.strip()}


def print_matches(item: dict[str, Any], filters: dict[str, Any]) -> bool:
    sets = filter_values(filters.get("set"))
    if sets and str(item["card_set"] or "").lower() not in sets:
        return False
    rarities = filter_values(filters.get("rarity"))
    if rarities and str(item["rarity"] or "").lower() not in rarities:
        return False
    foils = filter_values(filters.get("foil"))
    if foils and str(item["foil"]) not in foils:
        return False
    splinters = filter_values(filters.get("splinter"))
    if splinters and not splinters & {str(s).lower() for s in item["splinters"]}:
        return False
    needle = str(filters.get("q") or "").strip().lower()
    if needle and needle not in str(item["name"]).lower():
        return False
    # the PP range picks the prints in
    # scope before the 100-print cap. A print qualifies when a copy of it can be in the range: its max-level PP reaches Min PP
    # and its one-CC PP is not above Max PP. A print without a PP formula (black foils, gold arcane) cannot be judged here and
    # stays in scope; its listings are filtered by their own PP.
    low, high = pp_bounds(filters)
    max_pp = item.get("max_level_pp")
    if low is not None and max_pp is not None and max_pp < low:
        return False
    if high is not None and item.get("pp_per_cc") is not None and item["pp_per_cc"] > high:
        return False
    if filters.get("maxed") and high is not None and max_pp is not None and max_pp > high:
        return False  # with Maxed every offer has the max-level PP
    return True


def pp_bounds(filters: dict[str, Any]) -> tuple[float | None, float | None]:
    low = _number(str(filters.get("min_pp") or "").replace(",", "").strip() or None)
    high = _number(str(filters.get("max_pp") or "").replace(",", "").strip() or None)
    return (low if low is not None and low > 0 else None, high if high is not None and high >= 0 else None)


def pp_in_range(pp: float | None, filters: dict[str, Any]) -> bool:
    """: one listing or combined offer against the PP range, by its own PP (the client's cmMatches rule)."""
    low, high = pp_bounds(filters)
    if low is None and high is None:
        return True
    if pp is None:
        return False
    return (low is None or pp >= low) and (high is None or pp <= high)


def maxed_cost(p: dict[str, Any]) -> float | None:
    """: a lower bound for the price of one maxed card of this print -- its cheapest $ per CC times max CC (black
    foils are always max: their cheapest listing). Upstream's grouped answer carries both, so it costs no extra read."""
    if p.get("foil") in (3, 4):
        return p.get("low_price_usd")
    rate, cc = p.get("low_price_per_cc_usd"), p.get("bcx_max")
    return rate * cc if rate is not None and cc else None


# ranking bounds and windows
# Owner: "audit all our filters on cards and market. sort price and sort pp. There is a bug after all. Filter 3000 min PP and
# sort price. All rarity shows no legendary. filtering for legendaries I get the cheapest offers on the market. So it is not
# just omitting results, it is omitting the best results."
# The Market reads the real listings of a bounded number of prints per page, so the order in which prints are read must
# never cut a print whose offers belong on the page. Every offer has an order key under the chosen sort (``offer_key``), and
# every print a key that is a lower bound of the keys of all its offers that pass every active filter (``rank_key``). Prints
# are read in rank-key order; page p reads the first p x MAX_PRINTS prints (earlier pages come from the 60 s cache) and
# shows only the offers with rank_key(first print left unread by page p-1) <= key < rank_key(first print left unread by
# page p). No unread print can then beat a shown offer: each page is complete and the pages follow each other in order.
# Keys are tuples of integers (prices in micro-USD, PP in thousandths, $/PP in 1e-9) so equal values compare equal
# price (price, -PP, print, n) -- equal prices: more PP first
# pp (-PP, price, print, n) -- equal PP: cheaper first
# cost_per_pp ($/PP, price, print, n) -- equal rates: cheaper first
# A print's bound of its first component (``print_bounds``)
# price max(cheapest listing, cheapest $/CC x the CC needed for Min PP); Maxed: cheapest $/CC x max CC
# pp min(max-level PP, Max PP, PP per CC x the most whole CC under Max PP)
# cost cheapest $/CC / PP per CC (a PP range can only make the rate worse)
# and its second component is a bound of the offers that tie on the first (see rank_key), so ties never stall a page.
# Prints without a PP formula (gold arcane, black foils) are bounded by the measured rule of their foil (``rank_max_pp``).

Q_USD = 1_000_000
Q_PP = 1_000
Q_RATE = 1_000_000_000
_BIG = 10 ** 18


def _floor_q(value: float, scale: int) -> int:
    return math.floor(value * scale + 1e-3)


def _ceil_q(value: float, scale: int) -> int:
    return math.ceil(value * scale - 1e-3)


def rank_max_pp(p: dict[str, Any]) -> float | None:
    """The max-level PP used for ranking. A print with the formula uses it. Gold arcane (foil 2) has the gold value and a
    black foil (3/4) five times it (library/cards-and-collection.md: black foil = 5x gold; listings measured 2026-10-15:
    Conclave common gold arcane 1,000, black foil 5,000, legendary black foil arcane 25,000)."""
    if p.get("max_level_pp") is not None:
        return p["max_level_pp"]
    foil = p.get("foil")
    if foil not in (2, 3, 4):
        return None
    gold = max_level_land_pp(p.get("card_set"), p.get("rarity"), 1)
    if gold is None:
        return None
    return gold if foil == 2 else gold * 5


def cc_range(p: dict[str, Any], filters: dict[str, Any]) -> tuple[int, int | None] | None:
    """The CC a copy of this print needs to be in the PP range: (lowest, highest or None = no ceiling). (1, None) when the
    print has no PP formula; None when no copy can be in range."""
    low, high = pp_bounds(filters)
    ppc, bcx_max, max_pp = p.get("pp_per_cc"), p.get("bcx_max"), p.get("max_level_pp")
    if not ppc or not bcx_max or max_pp is None:
        return (1, None)
    cc_lo = 1 if low is None else max(1, math.ceil(low / ppc - 1e-9))
    if cc_lo > bcx_max:
        return None
    cc_hi = None if high is None or high >= max_pp else math.floor(high / ppc + 1e-9)
    if cc_hi is not None and cc_hi < cc_lo:
        return None
    return (cc_lo, cc_hi)


RATE_ROUNDING = 0.0005  # upstream rounds low_price_bcx to 3 decimals (0.066875 $/CC shows as 0.067, 2026-10-15)


def rate_floor(p: dict[str, Any]) -> float | None:
    """A true lower bound of the print's cheapest $ per CC: upstream's rounded figure less half its last digit."""
    rate = p.get("low_price_per_cc_usd")
    return max(0.0, rate - RATE_ROUNDING) if rate is not None else None


def print_bounds(p: dict[str, Any], filters: dict[str, Any], maxed: bool = False) -> dict[str, float | None]:
    """: bounds of the best offer of this print that passes the filters -- price_lb (no qualifying offer is cheaper),
    pp_ub (none has more PP), rate_lb (none is cheaper per PP), and cc_lo (the fewest CC of a qualifying copy)."""
    _, high = pp_bounds(filters)
    low_price, rate = p.get("low_price_usd"), rate_floor(p)
    ppc, bcx_max = p.get("pp_per_cc"), p.get("bcx_max")
    est = rank_max_pp(p)
    span = cc_range(p, filters) or (1, None)
    cc_lo = span[0]
    if maxed:
        # maxed_cost with the rate's rounding taken off (a black foil: its cheapest listing)
        price_lb = low_price if p.get("foil") in (3, 4) or not bcx_max or rate is None else max(low_price or 0.0, rate * bcx_max)
    elif ppc and rate is not None and low_price is not None:
        price_lb = max(low_price, rate * cc_lo)
    else:
        price_lb = low_price
    pp_ub = est if est is not None else None
    if pp_ub is not None and high is not None:
        pp_ub = min(pp_ub, high)
        if ppc and span[1] is not None and not maxed:
            # CC is whole: the biggest copy under Max PP holds cc_hi CC (a hair of slack for the game's 3-decimal PP figure)
            pp_ub = min(pp_ub, ppc * span[1] * (1 + 1e-9) + 0.5 / Q_PP)
    if ppc and rate is not None:
        rate_lb = rate / ppc
    elif est and bcx_max and rate is not None:
        rate_lb = rate * bcx_max / est
    elif est and bcx_max == 0 and low_price is not None:
        rate_lb = low_price / est
    else:
        rate_lb = None
    return {"price_lb": price_lb, "pp_ub": pp_ub, "rate_lb": rate_lb, "cc_lo": cc_lo}


def _ident(item: dict[str, Any]) -> tuple[int, int, int]:
    def part(key: str) -> int:
        value = _int(item.get(key))
        return value if value is not None else -1
    return (part("card_detail_id"), part("edition"), part("foil"))


def rank_key(p: dict[str, Any], filters: dict[str, Any], sort: str, maxed: bool = False) -> tuple:
    """: a lower bound of ``offer_key`` over every offer of this print that passes the filters (see the block above)."""
    b = print_bounds(p, filters, maxed)
    price_lb, pp_ub, rate_lb = b["price_lb"], b["pp_ub"], b["rate_lb"]
    rate, ppc, bcx_max = rate_floor(p), p.get("pp_per_cc"), p.get("bcx_max")
    price_q = _floor_q(price_lb, Q_USD) if price_lb is not None else -_BIG
    if sort == "price":
        # an offer priced exactly price_lb holds at most price_lb / cheapest $/CC CC: its PP is at most that much
        cond = pp_ub
        if not maxed and ppc and rate and price_lb is not None and bcx_max:
            cc_max = math.floor((price_lb + 1.0 / Q_USD) / rate + 1e-9)
            cond = ppc * min(max(cc_max, 1), bcx_max)
            cond = min(cond, pp_ub) if pp_ub is not None else cond
        second = -_ceil_q(cond, Q_PP) if cond is not None else -_BIG
        return (price_q, second, _ident(p), -1)
    if sort == "pp":
        first = -_ceil_q(pp_ub, Q_PP) if pp_ub is not None else -_BIG
        # an offer with exactly pp_ub PP holds at least pp_ub / PP per CC CC, at no less than the cheapest $/CC each
        cond = price_lb
        if not maxed and ppc and rate is not None and pp_ub is not None and price_lb is not None:
            cc_need = max(1, math.ceil((pp_ub - 1.0 / Q_PP) / ppc - 1e-9))
            cond = max(price_lb, rate * cc_need)
        second = _floor_q(cond, Q_USD) if cond is not None else -_BIG
        return (first, second, _ident(p), -1)
    first = _floor_q(rate_lb, Q_RATE) if rate_lb is not None else -_BIG
    return (first, price_q, _ident(p), -1)


def offer_key(offer: dict[str, Any], sort: str, seq: int = 0) -> tuple | None:
    """: one offer's order key under the sort; None when the sort's value is unknown (no PP for the PP or rate sort)."""
    price, pp = offer.get("price_usd"), offer.get("land_base_pp")
    if price is None:
        return None
    price_q = round(price * Q_USD)
    if sort == "price":
        return (price_q, -round((pp or 0) * Q_PP), _ident(offer), seq)
    if not pp or pp <= 0:
        return None
    if sort == "pp":
        return (-round(pp * Q_PP), price_q, _ident(offer), seq)
    return (round(price / pp * Q_RATE), price_q, _ident(offer), seq)


def bound_value(key: tuple | None, sort: str) -> float | None:
    """The first component of a key back in its own unit ($, PP or $ per PP), for the response's window."""
    if key is None or abs(key[0]) >= _BIG:
        return None
    if sort == "price":
        return key[0] / Q_USD
    if sort == "pp":
        return -key[0] / Q_PP
    return key[0] / Q_RATE


def rank_prints(prints: list[dict[str, Any]], filters: dict[str, Any], sort: str, maxed: bool = False) -> list[dict[str, Any]]:
    """Every print in scope, in rank-key order ()."""
    pool = [p for p in prints if print_matches(p, filters) and p.get("low_price_usd") is not None and cc_range(p, filters) is not None]
    return sorted(pool, key=lambda p: rank_key(p, filters, sort, maxed))


def pick_prints(prints: list[dict[str, Any]], filters: dict[str, Any], sort: str, limit: int = MAX_PRINTS, offset: int = 0,
                maxed: bool = False) -> list[dict[str, Any]]:
    """The prints whose listings are read, best bound first ( ``rank_key``). : the next page is the next
    `limit` prints of the same ranking."""
    offset = max(0, int(offset or 0))
    return rank_prints(prints, filters, sort, maxed)[offset:offset + limit]


def normalise_listing(row: dict[str, Any], item: dict[str, Any], dec_usd: float | None, player: str | None) -> dict[str, Any] | None:
    bcx = _number(row.get("bcx"))
    price_usd = _number(row.get("buy_price"))
    if bcx is None or bcx <= 0 or price_usd is None or price_usd <= 0:
        return None
    currency = str(row.get("currency") or "USD").upper()
    if currency != "USD":
        return None  # every sale listing measured is priced in USD; anything else is not ranked
    bcx_max = item["bcx_max"]
    reported = _number(row.get("land_base_pp"))
    if reported is not None and reported > 0:
        pp, pp_source = reported, "listing"
    else:
        pp, pp_source = pp_at(item["max_level_pp"], bcx, bcx_max), "formula"
        if pp is None:
            pp_source = None
    if item["foil"] in (3, 4):
        maxed: bool | None = True
    elif bcx_max:
        maxed = bcx >= bcx_max
    else:
        maxed = None
    max_pp = item["max_level_pp"]
    if max_pp is None and maxed and pp:
        max_pp = pp  # a maxed listing is its own max-level measurement (black foils)
    missing_cc = max(0.0, float(bcx_max) - bcx) if bcx_max else 0.0
    rate = item["low_price_per_cc_usd"]
    if maxed:
        max_cost_usd: float | None = price_usd
    elif bcx_max and rate is not None:
        max_cost_usd = price_usd + missing_cc * rate
    else:
        max_cost_usd = None
    to_dec = (lambda usd: usd / dec_usd if usd is not None and dec_usd else None)
    price_dec = to_dec(price_usd)
    max_cost_dec = to_dec(max_cost_usd)
    return {
        "card_detail_id": item["card_detail_id"],
        "name": item["name"],
        "edition": item["edition"],
        "card_set": item["card_set"],
        "rarity": item["rarity"],
        "foil": item["foil"],
        "gold": item["gold"],
        "splinters": item["splinters"],
        "level": _int(row.get("level")),
        "max_level": item["max_level"],
        "bcx": bcx,
        "bcx_max": bcx_max,
        "maxed": maxed,
        "land_base_pp": pp,
        "pp_source": pp_source,
        "max_level_pp": max_pp,
        "price_usd": price_usd,
        "price_dec": price_dec,
        "price_currency": currency,
        "price_per_cc_usd": _number(row.get("price_bcx")),
        "pp_per_dec": _ratio(pp, price_dec),
        "pp_per_usd": _ratio(pp, price_usd),
        "dec_per_pp": _ratio(price_dec, pp),
        "max_cost_usd": max_cost_usd,
        "max_cost_dec": max_cost_dec,
        "missing_cc": missing_cc if bcx_max else None,
        "pp_per_dec_max": _ratio(max_pp, max_cost_dec),
        "dec_per_pp_max": _ratio(max_cost_dec, max_pp),
        "own": bool(player) and str(row.get("seller") or "").lower() == str(player).lower(),
        "lkey": listing_key(row),
        "market_url": market_url(item["card_detail_id"], item["foil"], item["edition"]),
    }


def listing_key(row: dict[str, Any]) -> str | None:
    """: an opaque, stable key for one sale listing (never shown; the market id itself never leaves the server)."""
    market_id = str(row.get("market_id") or "").strip()
    if not market_id:
        return None
    return hashlib.sha256(f"ceres-listing:{market_id}".encode()).hexdigest()[:16]


def print_id(card_detail_id: Any, edition: Any, foil: Any) -> str:
    return f"{_int(card_detail_id)}-{_int(edition)}-{_int(foil)}"


def _print_key(row: dict[str, Any], group_foil: Any = None) -> tuple:
    foil = _int(row.get("foil"))
    return (_int(row.get("card_detail_id")), foil if foil is not None else _int(group_foil), _int(row.get("edition")))


def rows_by_print(picked: list[dict[str, Any]], groups: list[dict[str, Any]]) -> dict[tuple, list[dict[str, Any]]]:
    """Upstream sale rows of each picked print, in upstream order (cheapest per CC first). Groups mix editions."""
    wanted = {(p["card_detail_id"], p["foil"], p["edition"]) for p in picked}
    out: dict[tuple, list[dict[str, Any]]] = {}
    for group in groups or []:
        if not isinstance(group, dict):
            continue
        for row in group.get("result") or []:
            if not isinstance(row, dict) or str(row.get("type") or "SELL").upper() != "SELL":
                continue
            key = _print_key(row, group.get("foil"))
            if key in wanted:
                out.setdefault(key, []).append(row)
    return out


def listings_for(picked: list[dict[str, Any]], groups: list[dict[str, Any]], dec_usd: float | None, player: str | None,
                 raw: dict[tuple, list[dict[str, Any]]] | None = None, filters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    by_key = {(p["card_detail_id"], p["foil"], p["edition"]): p for p in picked}
    raw = raw if raw is not None else rows_by_print(picked, groups)
    out = []
    for key, item in by_key.items():
        kept = 0
        for row in raw.get(key, []):
            if kept >= LISTINGS_PER_PRINT:
                break
            listing = normalise_listing(row, item, dec_usd, player)
            if listing is not None and pp_in_range(listing["land_base_pp"], filters or {}):
                kept += 1
                out.append(listing)
    return out


def _cover_states(offers: list[tuple[float, float]], target: int) -> dict[int, tuple[int, int, tuple[int, ...]]]:
    """0/1 min-cost cover: for each CC total reached (capped at `target`), the cheapest set of offers (cc, price) that sums to
    it, fewest offers on a tie. Prices compare as integer micro-USD so ties are exact."""
    best: dict[int, tuple[int, int, tuple[int, ...]]] = {0: (0, 0, ())}
    for index, (cc, price) in enumerate(offers):
        cc_int, cost = _int(cc), _number(price)
        if not cc_int or cc_int <= 0 or cost is None or cost < 0:
            continue
        micro = round(cost * 1_000_000)
        for reached, (total, count, chosen) in list(best.items()):  # snapshot: each offer is used at most once
            if reached >= target:
                continue
            nxt = min(target, reached + cc_int)
            candidate = (total + micro, count + 1, chosen + (index,))
            current = best.get(nxt)
            if current is None or candidate[:2] < current[:2]:
                best[nxt] = candidate
    return best


def cheapest_max_combo(offers: list[tuple[float, float]], target: Any) -> list[int] | None:
    """: the indices of the offers
    (cc, price) whose CC sums to at least `target` at the minimum total price; on equal price the fewest offers. None
    when the offers cannot reach `target`. A 0/1 min-cost cover: states are the CC reached (capped at target)."""
    target = _int(target)
    if not target or target <= 0:
        return None
    hit = _cover_states(offers, target).get(target)
    return list(hit[2]) if hit else None


def cheapest_covers(offers: list[tuple[float, float]], thresholds: list[int], cap: int) -> list[list[int] | None]:
    """: for each CC threshold, the cheapest set of offers whose CC sums to at least it (fewest on a tie), from one
    cover table capped at `cap` (the max-level CC). None where the offers cannot reach the threshold."""
    cap = _int(cap) or 0
    if cap <= 0:
        return [None for _ in thresholds]
    best = _cover_states(offers, cap)
    out: list[list[int] | None] = []
    for threshold in thresholds:
        hits = [v for k, v in best.items() if k >= min(threshold, cap) and k > 0]
        out.append(list(min(hits, key=lambda v: v[:2])[2]) if hits else None)
    return out


def _combined_offer(item: dict[str, Any], parts: list[dict[str, Any]], dec_usd: float | None) -> dict[str, Any]:
    price_usd = sum(x["price_usd"] for x in parts)
    price_dec = price_usd / dec_usd if dec_usd else None
    max_pp = item["max_level_pp"]
    return {
        **{k: item[k] for k in ("card_detail_id", "name", "edition", "card_set", "rarity", "foil", "gold", "splinters", "max_level", "bcx_max")},
        "level": item["max_level"],
        "bcx": sum(x["bcx"] for x in parts),
        "maxed": True,
        "land_base_pp": max_pp,
        "pp_source": "formula" if max_pp is not None else None,
        "max_level_pp": max_pp,
        "price_usd": price_usd,
        "price_dec": price_dec,
        "price_currency": "USD",
        "price_per_cc_usd": _ratio(price_usd, sum(x["bcx"] for x in parts)),
        "pp_per_dec": _ratio(max_pp, price_dec),
        "pp_per_usd": _ratio(max_pp, price_usd),
        "dec_per_pp": _ratio(price_dec, max_pp),
        "max_cost_usd": price_usd,
        "max_cost_dec": price_dec,
        "missing_cc": 0.0,
        "pp_per_dec_max": _ratio(max_pp, price_dec),
        "dec_per_pp_max": _ratio(price_dec, max_pp),
        "own": False,
        "lkey": None,
        "combined": len(parts),
        "parts_cc": sorted((x["bcx"] for x in parts), reverse=True),
        "parts_keys": [x["lkey"] for x in parts],
        "market_url": market_url(item["card_detail_id"], item["foil"], item["edition"]),
    }


def maxed_offers(item: dict[str, Any], rows: list[dict[str, Any]], dec_usd: float | None, player: str | None) -> list[dict[str, Any]]:
    """ (Maxed\" and show only maxed cards"): one print's max-level offers.

    : the offers are sequential -- offer 1 is the cheapest way to one max-level card from every listing (a single
    maxed listing or the min-cost combination, ``cheapest_max_combo``), its listings are then taken out, offer 2 is the cheapest
    way from what remains, and so on, up to MAXED_SINGLES_PER_PRINT offers; each carries ``maxed_seq`` (1, 2, 3). The player's
    own listings are never part of the sequence; an own maxed listing is still shown, marked as own, after it."""
    listings = [x for x in (normalise_listing(r, item, dec_usd, player) for r in rows) if x is not None]
    pool = [x for x in listings if not x["own"]]
    bcx_max = item["bcx_max"]
    out: list[dict[str, Any]] = []
    for seq in range(1, MAXED_SINGLES_PER_PRINT + 1):
        if bcx_max == 0:  # black foils: every copy is a maxed card; the cheapest first
            pick = [min(range(len(pool)), key=lambda i: pool[i]["price_usd"])] if pool else None
        elif bcx_max:
            pick = cheapest_max_combo([(x["bcx"], x["price_usd"]) for x in pool], bcx_max)
        else:
            pick = None  # unknown max CC: a listing already known as maxed is offered alone, cheapest first
            singles = [i for i, x in enumerate(pool) if x["maxed"]]
            if singles:
                pick = [min(singles, key=lambda i: pool[i]["price_usd"])]
        if not pick:
            break
        parts = [pool[i] for i in pick]
        offer = {**parts[0], "combined": 1} if len(parts) == 1 else _combined_offer(item, parts, dec_usd)
        offer["maxed_seq"] = seq
        out.append(offer)
        taken = set(pick)
        pool = [x for i, x in enumerate(pool) if i not in taken]
    own = sorted((x for x in listings if x["own"] and x["maxed"]), key=lambda x: x["price_usd"])[:1]
    out.extend({**x, "combined": 1, "maxed_seq": None} for x in own)
    return out


def _bundle(level: int | None, parts: list[dict[str, Any]], item: dict[str, Any], thresholds: list[int] | None,
            dec_usd: float | None) -> dict[str, Any]:
    cc = sum(x["bcx"] for x in parts)
    price_usd = sum(x["price_usd"] for x in parts)
    price_dec = price_usd / dec_usd if dec_usd else None
    bcx_max = item["bcx_max"]
    if bcx_max == 0:  # black foils: every copy is max level
        pp = max((x["land_base_pp"] or 0) for x in parts) or None
        reached = item["max_level"]
    else:
        pp = pp_at(item["max_level_pp"], cc, bcx_max)
        reached = level_for_bcx(thresholds, cc) if thresholds else level
    return {
        "level": level,
        "reached_level": reached,
        "max": bool(item["max_level"]) and reached == item["max_level"],
        "reachable": True,
        "bcx": cc,
        "count": len(parts),
        "keys": [x["lkey"] for x in parts],
        "parts_cc": [x["bcx"] for x in parts],
        "price_usd": price_usd,
        "price_dec": price_dec,
        "land_base_pp": pp,
        "pp_per_usd": _ratio(pp, price_usd),
        "pp_per_dec": _ratio(pp, price_dec),
    }


def level_bundles(item: dict[str, Any], listings: list[dict[str, Any]], dec_usd: float | None) -> list[dict[str, Any]]:
    """: per level, max level first, the cheapest set of this
    print's listings whose CC reaches the level's threshold in the game's level table (``resolve_level_thresholds``),
    fewest listings on a tie -- the same min-cost cover as the Maxed combination (``cheapest_max_combo``). The player's own
    listings and listings without a key are never part of a bundle. A level whose cheapest bundle already reaches a higher
    level is folded into that level's bundle (the same listings). A level the listings on sale cannot reach is returned with
    ``reachable: False`` and the CC still missing."""
    pool = [x for x in listings if not x["own"] and x.get("lkey")]
    max_level = item["max_level"]
    bcx_max = item["bcx_max"]
    if bcx_max == 0:  # black foils: one copy is a max-level card; the cheapest copy is the bundle
        if not pool:
            return [{"level": max_level, "reached_level": None, "max": True, "reachable": False, "cc_needed": 1, "cc_available": 0}]
        return [_bundle(max_level, [min(pool, key=lambda x: x["price_usd"])], item, None, dec_usd)]
    thresholds = resolve_level_thresholds(item["card_detail_id"], item["edition"], item["rarity"], item["gold"], item["card_set"])
    if not thresholds or not bcx_max or not max_level:
        return []
    available = sum(x["bcx"] for x in pool)
    covers = cheapest_covers([(x["bcx"], x["price_usd"]) for x in pool], thresholds, bcx_max)
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, ...]] = set()
    for level in range(max_level, 0, -1):
        chosen = covers[level - 1]
        if chosen is None:
            out.append({"level": level, "reached_level": None, "max": level == max_level, "reachable": False,
                        "cc_needed": thresholds[level - 1], "cc_available": available})
            continue
        bundle = _bundle(level, [pool[i] for i in chosen], item, thresholds, dec_usd)
        signature = tuple(sorted(bundle["keys"]))
        if signature in seen:
            continue  # this level's cheapest set already reaches a higher level: shown once, there
        seen.add(signature)
        out.append(bundle)
    return out


def over_cheapest(listings: list[dict[str, Any]]) -> None:
    """: each listing's price per CC over the print's cheapest, as a fraction (0.4 = 40 % more per CC)."""
    rates = [x["price_usd"] / x["bcx"] for x in listings if x["bcx"] and x["price_usd"]]
    low = min(rates) if rates else None
    for x in listings:
        rate = x["price_usd"] / x["bcx"] if x["bcx"] else None
        x["over_cheapest"] = (rate / low - 1) if low and rate else None
        x["over_flag"] = bool(x["over_cheapest"] is not None and x["over_cheapest"] >= OVER_CHEAPEST_FLAG)


# upstream reads (only from the route)

Getter = Callable[[str], Any]


def _default_get(url: str) -> Any:
    return FixedGetTransport(timeout=20, retries=1).get_json(url).data


def deep_url(item: dict[str, Any], levels: tuple[int | None, int | None] | None = None) -> str:
    """One print in depth (cheapest per CC first). : `levels` (min, max) narrows it to the levels that can hold the
    print's best offers under the sort; without it the URL is the one the card menu, the cart check and Maxed share."""
    params: dict[str, Any] = {"card_detail_id": item["card_detail_id"], "foil": item["foil"], "edition": item["edition"],
                              "limit": MAXED_DEEP_LISTINGS}
    params.update(_level_params(levels))
    return f"{API_ORIGIN}{BY_CARD_PATH}?{urllib.parse.urlencode(params)}"


def _level_params(levels: tuple[int | None, int | None] | None) -> dict[str, int]:
    out: dict[str, int] = {}
    if levels:
        if levels[0] is not None:
            out["min_level"] = levels[0]
        if levels[1] is not None:
            out["max_level"] = levels[1]
    return out


def listing_urls(picked: list[dict[str, Any]], limit: int = UPSTREAM_LIMIT,
                 levels: tuple[int | None, int | None] | None = None) -> list[str]:
    by_foil: dict[int, list[int]] = {}
    for p in picked:
        ids = by_foil.setdefault(p["foil"], [])
        if p["card_detail_id"] not in ids:
            ids.append(p["card_detail_id"])
    urls = []
    for foil in sorted(by_foil):
        ids = by_foil[foil]
        for start in range(0, len(ids), IDS_PER_CALL):
            query = urllib.parse.urlencode({"card_ids": ",".join(str(i) for i in ids[start:start + IDS_PER_CALL]),
                                            "foil": foil, "limit": limit, **_level_params(levels)})
            urls.append(f"{API_ORIGIN}{LISTINGS_PATH}?{query}")
    return urls


PRICE_NARROW_RATIO = 4  # the Price sort reads two levels only when max CC is over 4x the CC Min PP needs


def level_slice(p: dict[str, Any], filters: dict[str, Any], sort: str) -> tuple[int | None, int | None] | None:
    """: the upstream levels (min, max; None = open) that hold this print's best offers under the sort, by the game's
    level table: the price sort wants the smallest copies in range (the level of the CC Min PP needs and the next), the PP
    sort the biggest (the level of the CC Max PP allows, or max level, and the one below), the rate sort every level in
    range. None when the print has no level table or PP formula, or nothing would be narrowed."""
    span = cc_range(p, filters)
    if not span or not p.get("pp_per_cc") or not p.get("bcx_max"):
        return None
    thresholds = resolve_level_thresholds(p.get("card_detail_id"), p.get("edition"), p.get("rarity"), p.get("gold"), p.get("card_set"))
    if not thresholds:
        return None
    top = len(thresholds)
    lvl = lambda cc: level_for_bcx(thresholds, cc) or 1  # noqa: E731
    cc_lo, cc_hi = span
    if sort == "price":
        # the smallest copies in range: the level of the CC Min PP needs and the next when the print's CC range is wide (a
        # 1-CC copy is not dearer per CC than a 400-CC one by much, so it is the cheapest), else every level from there up
        low = lvl(cc_lo)
        high = min(top, low + 1) if p["bcx_max"] > PRICE_NARROW_RATIO * cc_lo else top
        out = (low if low > 1 else None, high if high < top else None)
        return out if out != (None, None) else None
    if sort == "pp":
        high = lvl(cc_hi) if cc_hi is not None else top
        low = max(1, high - 1)
        out = (low if low > 1 else None, high if high < top else None)
        return out if out != (None, None) else None
    out = (lvl(cc_lo) if cc_lo > 1 and lvl(cc_lo) > 1 else None, lvl(cc_hi) if cc_hi is not None and lvl(cc_hi) < top else None)
    return out if out != (None, None) else None


def _row_rate(row: dict[str, Any]) -> float | None:
    """A row's $ per CC as upstream orders by it (its rounded price_bcx; computed when missing)."""
    rate = _number(row.get("price_bcx"))
    if rate is None:
        rate = _ratio(_number(row.get("buy_price")), _number(row.get("bcx")))
    return rate


def _row_id(row: dict[str, Any]) -> Any:
    return row.get("market_id") or row.get("uid") or (row.get("bcx"), row.get("buy_price"), row.get("seller"), row.get("level"))


def _merge_rows(*lists: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[Any] = set()
    out: list[dict[str, Any]] = []
    for rows in lists:
        for row in rows:
            key = _row_id(row)
            if key not in seen:
                seen.add(key)
                out.append(row)
    out.sort(key=lambda r: (_row_rate(r) if _row_rate(r) is not None else float("inf")))  # upstream order: cheapest per CC first
    return out


def _groups_by_print(answers: list[Any], limit: int) -> dict[tuple, dict[str, Any]]:
    """Per (card_detail_id, foil): the upstream rows, whether the group came back full, and its last (dearest) rate per CC."""
    out: dict[tuple, dict[str, Any]] = {}
    for answer in answers:
        for group in answer if isinstance(answer, list) else []:
            if not isinstance(group, dict):
                continue
            rows = [r for r in (group.get("result") or []) if isinstance(r, dict)]
            rates = [x for x in (_row_rate(r) for r in rows) if x is not None]
            out[(_int(group.get("card_detail_id")), _int(group.get("foil")))] = {
                "rows": rows, "foil": group.get("foil"), "full": len(rows) >= limit, "last": max(rates) if rates else None}
    return out


def _own_rows(p: dict[str, Any], rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    key = (p["card_detail_id"], p["foil"], p["edition"])
    return [r for r in rows if str(r.get("type") or "SELL").upper() == "SELL" and _print_key(r, p["foil"]) == key]


def _levels_agree(p: dict[str, Any], rows: list[dict[str, Any]]) -> bool:
    """True when upstream's level of every row read equals the game's level table at its CC (then a level slice can be
    trusted to hold every qualifying copy; promo editions measured 2026-10-15 disagree and are read without the slice)."""
    thresholds = resolve_level_thresholds(p.get("card_detail_id"), p.get("edition"), p.get("rarity"), p.get("gold"), p.get("card_set"))
    if not thresholds:
        return False
    return all(_int(r.get("level")) == level_for_bcx(thresholds, _number(r.get("bcx"))) for r in rows)


def print_offers(p: dict[str, Any], rows: list[dict[str, Any]], sort: str, filters: dict[str, Any], dec_usd: float | None,
                 player: str | None) -> list[dict[str, Any]]:
    """: one print's best LISTINGS_PER_PRINT listings in the PP range, by the sort's own key (not upstream's per-CC
    order: under Price the cheapest copy in range, under PP the biggest)."""
    listings = [x for x in (normalise_listing(r, p, dec_usd, player) for r in rows) if x is not None and pp_in_range(x["land_base_pp"], filters)]
    order = sorted(range(len(listings)), key=lambda i: (offer_key(listings[i], sort) is None, offer_key(listings[i], sort) or (), i))
    return [listings[i] for i in order[:per_print(filters)]]


def _certified(p: dict[str, Any], offers: list[dict[str, Any]], sort: str, filters: dict[str, Any], info: dict[str, Any]) -> bool:
    """: True when no listing left unread can beat the offers kept for this print (on the sort's first value). `info`:
    base_full / base_last (the unsliced read), slice / slice_full / slice_last (the level-sliced read) and agree (upstream
    levels follow the level table)."""
    if not info["base_full"]:
        return True  # every listing of the print was read
    # upstream orders by its rounded price_bcx (3 decimals): a row left unread is at least the last one's rounded rate less
    # half a digit per CC
    floor = lambda x: max(0.0, x - RATE_ROUNDING) if x is not None else None  # noqa: E731
    info = {**info, "base_last": floor(info.get("base_last")), "slice_last": floor(info.get("slice_last"))}
    span = cc_range(p, filters) or (1, None)
    ppc, rate, bcx_max = p.get("pp_per_cc"), rate_floor(p), p.get("bcx_max")
    sliced = info.get("slice") is not None and info.get("agree")
    in_last = (info["slice_last"] if info.get("slice_full") else None) if sliced else info["base_last"]
    # An offer is safe when it is strictly better than every unread listing could be; an equal value is not enough, as the
    # tie-break (offer_key's second value) could still favour an unread one -- except where that is bounded too (PP below).
    strict = 1 - 1e-9
    if sort == "cost_per_pp":
        if not ppc:
            return False
        bound = in_last / ppc if in_last is not None else float("inf")
        values = [o["price_usd"] / o["land_base_pp"] for o in offers if o.get("land_base_pp")]
        return len(values) == len(offers) and (len(offers) >= per_print(filters) or bound == float("inf")) and all(v < bound * strict for v in values)
    if sort == "price":
        if sliced:
            bound = info["slice_last"] * span[0] if info.get("slice_full") and info["slice_last"] is not None else float("inf")
            top_level = info["slice"][1]
            if top_level is not None:
                # copies above the slice hold at least the next level's CC, at no less than the dearest rate the full
                # unsliced read reached (every row it left unread is at least that dear per CC)
                thresholds = resolve_level_thresholds(p.get("card_detail_id"), p.get("edition"), p.get("rarity"), p.get("gold"), p.get("card_set"))
                above = thresholds[top_level] if thresholds and top_level < len(thresholds) else None
                floor_rate = max(rate or 0.0, info["base_last"] or 0.0)
                bound = min(bound, floor_rate * above) if above else bound
        else:
            bound = info["base_last"] * span[0] if info["base_last"] is not None else float("inf")
        # an offer at the print's cheapest listing price (for_sale_grouped's low_price, exact) cannot be undercut by an unread
        # copy; an unread copy at that same price is an equal-value tie, not a better offer
        floor_price = p.get("low_price_usd")
        return (len(offers) >= per_print(filters) or bound == float("inf")) and all(
            o["price_usd"] < bound * strict or (floor_price is not None and o["price_usd"] <= floor_price + 0.5 / Q_USD) for o in offers)
    # PP (most first): unread copies inside the slice may reach pp_ub; below the slice they stay under its lowest CC
    pp_ub = print_bounds(p, filters)["pp_ub"]
    if not ppc or pp_ub is None:
        return False
    eps = 1.0 / Q_PP
    below = 0.0
    if sliced:
        thresholds = resolve_level_thresholds(p.get("card_detail_id"), p.get("edition"), p.get("rarity"), p.get("gold"), p.get("card_set"))
        low = info["slice"][0]
        below = ppc * thresholds[low - 1] if low and thresholds and low > 1 else 0.0
        top, last = (pp_ub, info["slice_last"]) if info.get("slice_full") else (None, None)
    else:
        top, last = pp_ub, info["base_last"]
    if top is None and below <= 0:
        return bool(offers) or True

    def safe(o: dict[str, Any]) -> bool:
        pp = o.get("land_base_pp") or 0.0
        if pp < below - eps:
            return False
        if top is None or pp > top + eps:
            return True
        if pp < top - eps:
            return False
        # equal PP: an unread copy with that much PP holds at least pp / PP per CC CC, at no less than `last` per CC
        cc_need = max(1, math.ceil((pp - eps) / ppc - 1e-9))
        return last is not None and o["price_usd"] < last * cc_need * strict
    return len(offers) >= per_print(filters) and all(safe(o) for o in offers)


class MarketCache:
    """60-second cache of each upstream answer, keyed by URL (the market is public, not per player)."""

    def __init__(self, seconds: float = CACHE_SECONDS) -> None:
        self.seconds = seconds
        self._lock = threading.Lock()
        self._store: dict[str, tuple[float, Any]] = {}

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
        return data

    def fresh(self, url: str, now: float | None = None) -> bool:
        """True when `url` would be answered from the cache (: a cached deep read costs no budget)."""
        now = time.monotonic() if now is None else now
        with self._lock:
            hit = self._store.get(url)
            return hit is not None and now - hit[0] < self.seconds

    def clear(self) -> None:
        with self._lock:
            self._store.clear()


CACHE = MarketCache()
_FAILED = object()


MIN_PAGE_OFFERS = 60   # a page reads further blocks of MAX_PRINTS prints until its window holds this many offers
MAX_PAGE_BLOCKS = 15   # ... or this many new blocks were read (grouped reads are cheap; deep reads have their own budget)
PAGE_DEEP_BUDGET = 60   # ... without Maxed, deep reads per market read (best-ranked doubtful prints first)
PAGE_CALL_BUDGET = 150  # ... or this many upstream GETs were made by this read (a further block is not started past it; the
# api answers 429 to a burst of a few hundred deep reads, measured 2026-10-15 under PP + Max PP 200)


def _deep_levels(info: dict[str, Any], sort: str) -> tuple[int | None, int | None] | None:
    """The levels a deep read asks for: the print's slice, under Price open upwards and under PP open downwards (so no copy
    in range is left out and a print with up to MAXED_DEEP_LISTINGS listings is read whole), or none when upstream's levels do
    not follow the level table."""
    levels = info.get("slice") if info.get("agree") else None
    if levels and sort == "price":
        levels = (levels[0], None) if levels[0] is not None else None
    elif levels and sort == "pp":
        levels = (None, levels[1]) if levels[1] is not None else None
    return levels


def _read_prints(block: list[dict[str, Any]], filters: dict[str, Any], sort: str, maxed: bool, dec_usd: float | None,
                 player: str | None, getter: Getter, cache: MarketCache, calls: list[str],
                 budget: list[int] | None = None) -> dict[str, Any]:
    """: one block of prints (at most MAX_PRINTS) read and turned into offers, each print's own: the unsliced grouped
    read, without Maxed a level-sliced grouped read per slice, and deep reads where they are needed."""
    base_urls = listing_urls(block, GROUPED_ROWS_CAP)
    slices = {} if maxed else {_ident(p): level_slice(p, filters, sort) for p in block}
    by_slice: dict[tuple, list[dict[str, Any]]] = {}
    for p in block:
        if slices.get(_ident(p)) is not None:
            by_slice.setdefault(slices[_ident(p)], []).append(p)
    slice_urls = [(u, levels) for levels, members in by_slice.items() for u in listing_urls(members, GROUPED_ROWS_CAP, levels)]
    urls = list(dict.fromkeys(base_urls + [u for u, _ in slice_urls]))
    with ThreadPoolExecutor(max_workers=DEEP_WORKERS) as pool:
        fetched = dict(zip(urls, pool.map(lambda u: cache.get(u, getter, calls), urls)))
    base = _groups_by_print([fetched[u] for u in base_urls], GROUPED_ROWS_CAP)
    sliced: dict[tuple, dict[tuple, dict[str, Any]]] = {}
    for url, levels in slice_urls:
        for key, value in _groups_by_print([fetched[url]], GROUPED_ROWS_CAP).items():
            sliced.setdefault(levels, {})[key] = value
    none = {"rows": [], "full": False, "last": None}
    raw: dict[tuple, list[dict[str, Any]]] = {}
    info: dict[tuple, dict[str, Any]] = {}
    for p in block:
        ident = _ident(p)
        group = base.get((p["card_detail_id"], p["foil"]), none)
        levels = slices.get(ident)
        part = sliced.get(levels, {}).get((p["card_detail_id"], p["foil"]), none) if levels else None
        rows = _merge_rows(_own_rows(p, group["rows"]), _own_rows(p, part["rows"]) if part else [])
        raw[ident] = rows
        info[ident] = {"base_full": group["full"], "base_last": group["last"], "slice": levels,
                       "slice_full": bool(part and part["full"]), "slice_last": part["last"] if part else None,
                       "agree": bool(levels) and _levels_agree(p, rows)}
    # Maxed (/): every print whose grouped answer came back full is read in depth, whatever the sort, so a
    # print's maxed price never depends on the sort. Otherwise (): a full print whose kept offers cannot be shown to
    # beat every listing left unread, read with its level slice when upstream's levels follow the level table.
    if maxed:
        deep = [(p, None) for p in block if info[_ident(p)]["base_full"]]
    else:
        named = per_print(filters) > LISTINGS_PER_PRINT
        doubtful = [p for p in block
                    if (named and info[_ident(p)]["base_full"]) or not _certified(p, print_offers(p, raw[_ident(p)], sort, filters, dec_usd, player), sort, filters, info[_ident(p)])]
        # the deep reads of one market read are bounded (best rank first); a print past the budget keeps the offers its
        # grouped rows give and is counted uncertain. A read deep URL is cached, so a later page re-reads it for free.
        deep = []
        for p in doubtful:
            levels = None if named else _deep_levels(info[_ident(p)], sort)  # a named print is read whole
            if budget is not None and not cache.fresh(deep_url(p, levels)):
                if budget[0] <= 0:
                    continue
                budget[0] -= 1
            deep.append((p, levels))
    failed = uncertain = 0
    if not maxed:
        uncertain += len(doubtful) - len(deep)
    if deep:
        def deep_read(job: tuple) -> Any:
            try:
                return cache.get(deep_url(job[0], job[1]), getter, calls)
            except Exception:  # noqa: BLE001 -- one failed print is left out, never shown from its 10 grouped rows
                return _FAILED
        with ThreadPoolExecutor(max_workers=DEEP_WORKERS) as pool:
            answers = list(pool.map(deep_read, deep))
        for (p, levels), answer in zip(deep, answers):
            ident = _ident(p)
            if answer is _FAILED:
                raw[ident] = []  # a price from incomplete data is never shown
                failed += 1
                continue
            got = [r for r in (answer if isinstance(answer, list) else []) if isinstance(r, dict)]
            rates = [x for x in (_row_rate(r) for r in got) if x is not None]
            if maxed:
                raw[ident] = _print_rows(p, answer) or raw[ident]
                continue
            raw[ident] = _merge_rows(raw[ident], _print_rows(p, answer))
            i = info[ident]
            full, last = len(got) >= MAXED_DEEP_LISTINGS, (max(rates) if rates else None)
            i.update({"slice": levels, "slice_full": full, "slice_last": last} if levels else {"base_full": full, "base_last": last})
            if not _certified(p, print_offers(p, raw[ident], sort, filters, dec_usd, player), sort, filters, i):
                uncertain += 1
    offers: dict[tuple, list[dict[str, Any]]] = {}
    for p in block:
        rows = raw[_ident(p)]
        if maxed:
            offers[_ident(p)] = [o for o in maxed_offers(p, rows, dec_usd, player) if pp_in_range(o["land_base_pp"], filters)]
        else:
            offers[_ident(p)] = print_offers(p, rows, sort, filters, dec_usd, player)
    return {"offers": offers, "deep": len(deep), "failed": failed, "uncertain": uncertain}


def read_market(details_by_id: dict[int, dict[str, Any]], filters: dict[str, Any], *, sort: str = "cost_per_pp",
                player: str | None = None, getter: Getter | None = None, cache: MarketCache | None = None,
                maxed: bool = False, page: int = 1, start: int | None = None) -> dict[str, Any]:
    getter = getter or _default_get
    cache = cache or CACHE
    calls: list[str] = []
    grouped = cache.get(API_ORIGIN + GROUPED_PATH, getter, calls)
    settings = cache.get(API_ORIGIN + SETTINGS_PATH, getter, calls)
    dec_usd = _number((settings or {}).get("dec_price")) if isinstance(settings, dict) else None
    prints = [p for p in (normalise_print(r, details_by_id) for r in (grouped if isinstance(grouped, list) else []) if isinstance(r, dict)) if p]
    filters = {**filters, "maxed": bool(maxed)}
    page = max(1, int(page or 1))
    sort = sort if sort in SORTS else "cost_per_pp"
    # (generalises 's Maxed + Price window to every sort and filter): prints are read in rank-key order; a page
    # starts at print `start` (the previous page's `next_start`; page p alone means start (p-1) x MAX_PRINTS), reads every
    # print before it too (from the 60 s cache), then blocks of MAX_PRINTS prints until its window holds MIN_PAGE_OFFERS
    # offers (at most MAX_PAGE_BLOCKS blocks), and shows the offers whose key lies between the rank keys of print `start` and
    # of the first print left unread. No unread print can beat a shown offer; the next page goes on from there.
    ranked = rank_prints(prints, filters, sort, maxed)
    start = max(0, int(start)) if start is not None else (page - 1) * MAX_PRINTS
    start = min(start, len(ranked))
    key_lo = rank_key(ranked[start], filters, sort, maxed) if start > 0 and start < len(ranked) else None
    totals = {"deep": 0, "failed": 0, "uncertain": 0}
    offers: dict[tuple, list[dict[str, Any]]] = {}
    deep_budget = [PAGE_DEEP_BUDGET]

    def read(block: list[dict[str, Any]]) -> None:
        got = _read_prints(block, filters, sort, maxed, dec_usd, player, getter, cache, calls, deep_budget)
        offers.update(got["offers"])
        for k in totals:
            totals[k] += got[k]

    for i in range(0, 0 if start >= len(ranked) else start, MAX_PRINTS):  # the earlier pages' prints: their later offers may belong here
        read(ranked[i:min(start, i + MAX_PRINTS)])

    def in_window(key: tuple | None, key_hi: tuple | None) -> bool:
        return key is not None and (key_lo is None or key >= key_lo) and (key_hi is None or key < key_hi)

    end, blocks, key_hi = start, 0, None
    if start < len(ranked):
        while True:
            block = ranked[end:end + MAX_PRINTS]
            read(block)
            end += len(block)
            blocks += 1
            key_hi = rank_key(ranked[end], filters, sort, maxed) if end < len(ranked) else None
            count = sum(1 for mine in offers.values() for seq, o in enumerate(mine) if in_window(offer_key(o, sort, seq), key_hi))
            if end >= len(ranked) or count >= MIN_PAGE_OFFERS or blocks >= MAX_PAGE_BLOCKS or len(calls) >= PAGE_CALL_BUDGET:
                break
    shown: list[tuple[tuple, dict[str, Any]]] = []
    unknown: list[dict[str, Any]] = []
    past_end = start > 0 and start >= len(ranked)  # a page after the last one: every offer was shown before
    for rank, p in enumerate([] if past_end else ranked[:end]):
        for seq, offer in enumerate(offers.get(_ident(p), [])):
            key = offer_key(offer, sort, seq)
            if in_window(key, key_hi):
                shown.append((key, offer))
            elif key is None and rank >= start:
                unknown.append(offer)  # no PP under the PP or rate sort: shown once, after its print's page
    shown.sort(key=lambda x: x[0])
    listings = [o for _, o in shown] + unknown
    for listing in listings:
        listing["land_abilities"] = land_ability_row(listing["card_detail_id"], listing["level"], details_by_id) or []
    matching = [p for p in prints if print_matches(p, filters)]
    window = [bound_value(key_lo, sort), bound_value(key_hi, sort)]
    return {
        "dec_usd": dec_usd,
        "prints_on_sale": len(prints),
        "prints_matching": len(matching),
        "prints_read": end,  # cumulative ("best 200 of N" after one Load more)
        "page": page,
        "start": start,
        "next_start": end,  # Load more asks for the page from here
        "has_more": end < len(ranked),
        "listings": listings,
        "count": len(listings),
        "capped": end < len(ranked),
        "sort": sort,
        "window": window,  # this page holds sort values from window[0] (incl.) to window[1] (excl.); null = open
        "price_window": window if sort == "price" else None,  # name, kept for the Price sort
        "maxed": bool(maxed),
        "deep_reads": totals["deep"],
        "deep_failed": totals["failed"],  # prints left out because their deep read failed
        "uncertain": totals["uncertain"],  # prints read in depth whose kept offers still could not be proven best
        "upstream_calls": calls,  # paths only; [] when every answer came from the 60 s cache
    }


def _print_rows(item: dict[str, Any], answer: Any) -> list[dict[str, Any]]:
    key = (item["card_detail_id"], item["foil"], item["edition"])
    return [r for r in (answer if isinstance(answer, list) else [])
            if isinstance(r, dict) and str(r.get("type") or "SELL").upper() == "SELL" and _print_key(r, item["foil"]) == key]


def _print_item(details_by_id: dict[int, dict[str, Any]], grouped: Any, card_detail_id: int, edition: int, foil: int) -> dict[str, Any] | None:
    row = next((r for r in (grouped if isinstance(grouped, list) else []) if isinstance(r, dict)
                and _int(r.get("card_detail_id")) == card_detail_id and _int(r.get("edition")) == edition
                and (_int(r.get("foil")) if r.get("foil") is not None else (1 if r.get("gold") else 0)) == foil), None)
    return normalise_print(row or {"card_detail_id": card_detail_id, "edition": edition, "foil": foil}, details_by_id)


def read_print(details_by_id: dict[int, dict[str, Any]], card_detail_id: Any, edition: Any, foil: Any, *, player: str | None = None,
               getter: Getter | None = None, cache: MarketCache | None = None) -> dict[str, Any] | None:
    """: one print's card menu -- its header, every listing upstream gives (cheapest per CC first) and the bundles
    per level. Three cached GETs at most: for_sale_grouped and settings (shared with /api/market) and one deep read (the
    same URL as the Maxed deep read). None when the print is not a Land worker in the catalogue."""
    getter = getter or _default_get
    cache = cache or CACHE
    detail_id, edition_i, foil_i = _int(card_detail_id), _int(edition), _int(foil)
    if detail_id is None or edition_i is None or foil_i is None:
        return None
    calls: list[str] = []
    grouped = cache.get(API_ORIGIN + GROUPED_PATH, getter, calls)
    settings = cache.get(API_ORIGIN + SETTINGS_PATH, getter, calls)
    dec_usd = _number((settings or {}).get("dec_price")) if isinstance(settings, dict) else None
    item = _print_item(details_by_id, grouped, detail_id, edition_i, foil_i)
    if item is None:
        return None
    rows = _print_rows(item, cache.get(deep_url(item), getter, calls)) if item["qty"] else []
    listings = []
    for row in rows:
        listing = normalise_listing(row, item, dec_usd, player)
        if listing is not None:
            listing["seller"] = str(row.get("seller") or "").strip()[:40] or None  # the menu only, never the grid
            listings.append(listing)
    over_cheapest(listings)
    for listing in listings:
        listing["land_abilities"] = land_ability_row(listing["card_detail_id"], listing["level"], details_by_id) or []
    header = {k: item[k] for k in ("card_detail_id", "name", "edition", "card_set", "rarity", "foil", "gold", "splinters",
                                   "max_level", "bcx_max", "max_level_pp", "qty")}
    # the level table goes to the menu, which prices bundles from the copies not already in the cart
    header["thresholds"] = (None if item["bcx_max"] == 0 else
                            resolve_level_thresholds(item["card_detail_id"], item["edition"], item["rarity"], item["gold"], item["card_set"]))
    return {
        "print": {**header, "id": print_id(detail_id, edition_i, foil_i),
                  "market_url": market_url(detail_id, foil_i, edition_i)},
        "dec_usd": dec_usd,
        "listings": listings,
        "count": len(listings),
        "on_sale": item["qty"],
        "truncated": bool(item["qty"] and item["qty"] > len(rows)),
        "bundles": level_bundles(item, listings, dec_usd),
        "upstream_calls": calls,
    }


def parse_print_ids(value: str | None, limit: int = CHECK_PRINTS) -> list[tuple[int, int, int]]:
    out: list[tuple[int, int, int]] = []
    for part in str(value or "").split(","):
        bits = part.strip().split("-")
        if len(bits) != 3 or not all(b.isdigit() for b in bits):
            continue
        key = (int(bits[0]), int(bits[1]), int(bits[2]))
        if key not in out:
            out.append(key)
        if len(out) >= limit:
            break
    return out


def check_listings(details_by_id: dict[int, dict[str, Any]], prints: list[tuple[int, int, int]], *, player: str | None = None,
                   getter: Getter | None = None, cache: MarketCache | None = None) -> dict[str, Any]:
    """: the cart's re-check -- for each print (at most CHECK_PRINTS), the listings on sale now, by key, with their
    current price; a cart key missing from its print is sold or withdrawn. One cached deep read per print."""
    getter = getter or _default_get
    cache = cache or CACHE
    calls: list[str] = []
    grouped = cache.get(API_ORIGIN + GROUPED_PATH, getter, calls)
    settings = cache.get(API_ORIGIN + SETTINGS_PATH, getter, calls)
    dec_usd = _number((settings or {}).get("dec_price")) if isinstance(settings, dict) else None
    items = [i for i in (_print_item(details_by_id, grouped, *p) for p in prints[:CHECK_PRINTS]) if i]
    answers: list[Any] = []
    if items:
        with ThreadPoolExecutor(max_workers=DEEP_WORKERS) as pool:
            answers = list(pool.map(lambda i: cache.get(deep_url(i), getter, calls) if i["qty"] else [], items))
    out: dict[str, Any] = {}
    for item, answer in zip(items, answers):
        live = {}
        for row in _print_rows(item, answer):
            listing = normalise_listing(row, item, dec_usd, player)
            if listing and listing["lkey"]:
                live[listing["lkey"]] = {"price_usd": listing["price_usd"], "price_dec": listing["price_dec"], "own": listing["own"]}
        out[print_id(item["card_detail_id"], item["edition"], item["foil"])] = {"listings": live, "on_sale": item["qty"],
                                                                                "complete": not item["qty"] or item["qty"] <= len(live)}
    return {"dec_usd": dec_usd, "prints": out, "upstream_calls": calls}
