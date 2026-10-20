""": "Find combinables" for the Cards page -- read only (maintainer decisions ).

Owner (): "add a new feature to cards: find combinables. With it, we can a) find and combine our own cards and b) find a
quick price for either maxing it or making it stronger cheaply, when a large CC copy is on sale but single copies are going up
in price over a threshold to not suggest maxed".

a) Own copies. Every owned copy is grouped by print (card_detail_id, edition, foil -- the same three fields
``fides_cards.ops.build_combine_op`` re-checks). A copy is blocked, with its reason, when it is not the player's own (delegated
or rented in), listed, rented, delegated, staked on Land or cooling down, in a card set or the Guild Lending
Library, mid-unlock, in external custody, a Black Foil or Arcane Variant, or already at max CC -- the list of
``worker_upgrade._blocked_reasons`` plus ``fides_cards.ops._known_block`` (the build path's own refusal) plus the Cards page's
own staked/cooling status (a card lock is a warning, not a blocker:  below). A blocked copy is never part of a proposed set. A proposed set is the largest CC total of free
copies that stays at or under the print's max CC (never overcombined: PP is capped there), fewest copies on a tie, at least two
copies; the copies left over are packed into further sets the same way. The survivor is predicted with
``fides_cards.ops.survivor_rule`` (highest CC, then the alphabetically first Card ID). Signing reuses /api/combine/preview and
/api/combine/build unchanged: they take the explicit Card IDs of one set.

b) Upgrade price ("Market going rate"). From the maintainer's best copy -- or the result of the first proposed set when that is
higher -- the print's listings (``card_market.read_print``: one cached deep read, at most 60 listings, cheapest per CC first)
are priced level by level: the cost of level L is the cheapest set of listings whose CC reaches L's threshold
(``card_market.cheapest_covers``, the min-cost cover of the Maxed offer and the level bundles), and a step is the move from the
last accepted level to the next level the cover reaches. Its price per PP is (cost now - cost before) / (PP now - PP before),
where PP is the print's Land base PP at that CC (linear up to max CC, flat beyond, ``card_market.pp_at``). The going rate for
PP is the median of (price / Land base PP) over the current listings of the 100 prints with the cheapest rate per PP on the
whole market (exactly the listings the Market page's default view reads: ``card_market.read_market`` with no filters, sorted
by cost per PP, at most 3 listings per print), cached 60 s. Every step no dearer than the going rate is suggested; the first
dearer step stops the suggestion. "Max it" when every step passes, "Stronger cheaply -> Level N" when some do, nothing when
the first step is already dearer. The saving is (going rate x PP added) - cost. A large-CC listing that is cheaper per CC than
the singles is found by the cover itself.

Owner decisions  (2026-10-15) changed the defaults:
- Going rate, default ``basis="all_prints"``:
  the median over every Land print on sale of its cheapest price per PP, taken from the one ``for_sale_grouped`` answer (and
  ``/settings`` for DEC) that the market read already caches -- no extra upstream call. ``basis="best100"`` (the 
  definition above) stays an option.
- Steps, default ``rule="lookahead"``: a step goes from the last accepted level to whichever higher level adds PP most
  cheaply, so one cheap large copy to max is not hidden behind one dear intermediate level. ``rule="next"`` stays an option.
- Discount mid-level copy: a listing above 1 CC and below max CC whose price per
  CC is below the print's cheapest single-copy (1 CC) listing's price per CC. It is compared with single copies only, never
  with max-level listings. Each one is flagged with the % below single copies and listed first (``discounts``). When the
  suggested path buys such a copy and its later steps add only single copies at a worse price per CC than that copy, the
  suggestion stops at the discount copy ("Buy this Level N copy -- X% below single copies"), and the longer path is kept
  as ``max_option``. The list endpoint ranks prints whose grouped market row hints at one (cheapest price per CC below the
  cheapest listing's price) first; the deep read confirms it.
- Card lock (supersedes the  lock blocker): a copy under a card lock may combine; its set carries the warning
  ``card_lock`` ("This copy is under a card lock; the combined card will be locked as well."). Mid-unlock still blocks.

Pre-screen (list endpoint with ``market=1``): the first step of any print can never be cheaper per PP than the print's cheapest
price per CC on the market divided by its PP per CC (``normalise_print``'s ``usd_per_pp``), so a print whose cheapest rate is
dearer than the going rate can be ruled out without its deep read; the others are marked worth a price check.

No network here except through ``card_market``'s 60 s cache (public market GETs). Nothing is bought, signed or broadcast. The
API carries Card IDs (``uid``) only because a combine is signed by Card ID; the page must never display them.
"""

from __future__ import annotations

import statistics
import threading
import time
from datetime import datetime, timezone
from typing import Any

from fides_cards.ops import _known_block, survivor_rule

from . import card_market
from .card_browser import build_card_rows
from .level_cap import level_for_bcx, resolve_level_thresholds
from .worker_upgrade import _blocked_reasons

GOING_RATE_PRINTS = card_market.MAX_PRINTS   # the best 100 prints by rate per PP
GOING_RATE_MIN_SAMPLES = 10                  # fewer priced listings than this: no going rate, no suggestion
GOING_RATE_SECONDS = card_market.CACHE_SECONDS
GOING_RATE_BASES = ("all_prints", "best100")   # first = default ()
UPGRADE_RULES = ("lookahead", "next")          # first = default ()
LOCK_WARNING = {"code": "card_lock", "text": "This copy is under a card lock; the combined card will be locked as well."}
MAX_SETS_PER_PRINT = 20                      # leftover packing stops here (a whale with 300 singles of one print)

# Reason text -> a short code for the page's colour and label (the text itself is the game rule's wording).
_REASON_CODES = (
    ("not owned", "delegated_in"), ("delegated in", "delegated_in"), ("rented", "rented"), ("listed", "listed"),
    ("delegated", "delegated"), ("unstaking", "cooling"), ("cooling", "cooling"), ("survival", "cooling"),
    ("staked", "staked"), ("stake class", "staked"), ("card lock", "card_lock"), ("mid-unlock", "unlocking"),
    ("unlock", "unlocking"), ("card set", "card_set"), ("guild", "guild"), ("custody", "custody"),
    ("black foil", "black_foil"), ("arcane", "black_foil"), ("max", "max"), ("card id", "no_uid"), ("cc is not", "no_cc"),
)


def reason_code(text: str) -> str:
    low = str(text or "").lower()
    for needle, code in _REASON_CODES:
        if needle in low:
            return code
    return "blocked"


def _number(value: Any) -> float | None:
    return card_market._number(value)


def _int_cc(value: Any) -> int | None:
    number = _number(value)
    if number is None or number <= 0:
        return None
    return int(number) if float(number).is_integer() else None


# a) own copies

def copy_reasons(card: dict[str, Any], row: dict[str, Any], player: str) -> list[str]:
    """Every reason this owned copy cannot be a combine input now (empty = free)."""
    facts = dict(card.get("facts") or {})
    if card.get("classification") is not None:
        facts["classification"] = card["classification"]
    # a card lock no longer blocks (the game allows it; the result keeps the longest lock) -- it is a warning
    reasons = [r for r in _blocked_reasons(facts) if reason_code(r) != "card_lock"]
    owner = facts.get("player")
    if owner and str(owner).lower() != str(player).lower():
        reasons.append("card is not owned by this account")
    if not reasons:
        known = _known_block({k: v for k, v in facts.items() if k != "classification"})
        if known:
            reasons.append(f"card {known}")
    if row.get("status") == "staked" and not any(reason_code(r) == "staked" for r in reasons):
        reasons.append("card is Staked on Land")
    if row.get("status") == "cooling" and not any(reason_code(r) == "cooling" for r in reasons):
        reasons.append("card is cooling down" + (" (dead from Survival mode)" if "survival" in str(row.get("unavailable_reason") or "").lower() else ""))
    if row.get("foil") in (3, 4) and not any(reason_code(r) == "black_foil" for r in reasons):
        reasons.append("card is a Black Foil or Arcane Variant")
    if row.get("maxed") and not any(reason_code(r) == "max" for r in reasons):
        reasons.append("card is already at max level")
    if not (card.get("uid") or facts.get("uid")):
        reasons.append("Card ID is not present in the snapshot")
    if _int_cc(row.get("bcx")) is None:
        reasons.append("CC is not present in the snapshot")
    return list(dict.fromkeys(reasons))


def card_locked(card: dict[str, Any]) -> bool:
    """: the copy is under a card lock (the same fields worker_upgrade reads for its lock reason)."""
    facts = card.get("facts") or {}
    return any(facts.get(field) not in (None, False, "", 0, []) for field in ("lock_days", "card_locked", "is_locked", "locked"))


def best_combine_set(copies: list[dict[str, Any]], bcx_max: int) -> list[int] | None:
    """Indices of the free copies (each with an integer ``bcx``) whose CC sum is the largest at or under ``bcx_max``, at least
    two copies, fewest copies on a tie (then the larger copies, in input order). None when no two copies fit."""
    if not bcx_max or bcx_max <= 0:
        return None
    # Keep the reference DP's exact tie rule (earliest input-index tuple) while
    # processing repeated CC values as binary-split bounded quantities.
    if copies and all(copy["bcx"] == copies[0]["bcx"] for copy in copies):
        cc = copies[0]["bcx"]
        count = min(len(copies), bcx_max // cc) if cc > 0 else 0
        return list(range(count)) if count >= 2 else None

    # The historical DP's equal-rank tie is determined by dict insertion order
    # which is not equivalent to a binary-split item's traversal order. Preserve
    # that signing-sensitive behavior for mixed pools with repeated CC values.
    if len({copy["bcx"] for copy in copies}) != len(copies):
        best: dict[tuple[int, int], tuple[int, ...]] = {(0, 0): ()}
        for index, copy in enumerate(copies):
            cc = copy["bcx"]
            for (total, klass), chosen in list(best.items()):
                nxt = total + cc
                if nxt > bcx_max:
                    continue
                key = (nxt, min(klass + 1, 2))
                candidate = chosen + (index,)
                current = best.get(key)
                if current is None or len(candidate) < len(current):
                    best[key] = candidate
        pairs = [(total, chosen) for (total, klass), chosen in best.items() if klass == 2]
        if not pairs:
            return None
        _, chosen = max(pairs, key=lambda item: (item[0], -len(item[1])))
        return list(chosen)

    buckets: dict[int, list[int]] = {}
    for index, copy in enumerate(copies):
        buckets.setdefault(copy["bcx"], []).append(index)
    # state: (cc sum, min(count, 2)) -> chosen indices; count class 2 = "two or more"
    best: dict[tuple[int, int], tuple[int, ...]] = {(0, 0): ()}
    for cc, indices in buckets.items():
        index_set = set(indices)
        remaining, offset, quantity = len(indices), 0, 1
        while remaining:
            take = min(quantity, remaining)
            chunk = tuple(indices[offset:offset + take])
            weight = cc * take
            offset += take
            remaining -= take
            quantity <<= 1
            for (total, klass), chosen in list(best.items()):
                nxt = total + weight
                if nxt > bcx_max:
                    continue
                key = (nxt, min(klass + take, 2))
                # Binary splitting may represent a quantity with a later chunk
                # while omitting an earlier copy. The old per-copy DP always
                # preferred each bucket's earliest copies, so canonicalize it.
                prior_count = sum(index in index_set for index in chosen)
                candidate = tuple(sorted(
                    (index for index in chosen if index not in index_set),
                )) + tuple(indices[:prior_count + take])
                candidate = tuple(sorted(candidate))
                current = best.get(key)
                if current is None or (len(candidate), candidate) < (len(current), current):
                    best[key] = candidate
    pairs = [(total, chosen) for (total, klass), chosen in best.items() if klass == 2]
    if not pairs:
        return None
    total, chosen = max(pairs, key=lambda item: (item[0], -len(item[1])))
    return list(chosen)


def _pp_model(rows: list[dict[str, Any]], bcx_max: int | None) -> float | None:
    """The print's max-level Land base PP: the Cards page's figure, else derived from a copy that carries PP."""
    for row in rows:
        if row.get("max_level_pp") is not None:
            return float(row["max_level_pp"])
    if not bcx_max:
        return None
    for row in rows:
        pp, bcx = _number(row.get("land_base_pp")), _number(row.get("bcx"))
        if pp and pp > 0 and bcx and bcx > 0:
            return pp * bcx_max / min(bcx, bcx_max)
    return None


def _state(cc: float | None, thresholds: list[int] | None, max_pp: float | None, bcx_max: int | None) -> dict[str, Any]:
    level = level_for_bcx(thresholds, cc) if thresholds and cc else None
    return {"bcx": cc, "level": level, "land_base_pp": card_market.pp_at(max_pp, cc, bcx_max),
            "maxed": bool(bcx_max and cc is not None and cc >= bcx_max)}


def combine_sets(free: list[dict[str, Any]], bcx_max: int, thresholds: list[int] | None, max_pp: float | None) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Proposed sets from the free copies (largest first), and the free copies left in none of them."""
    pool = sorted(free, key=lambda c: (-c["bcx"], str(c["uid"])))
    sets: list[dict[str, Any]] = []
    while len(pool) >= 2 and len(sets) < MAX_SETS_PER_PRINT:
        if pool and all(copy["bcx"] == pool[0]["bcx"] for copy in pool):
            cc = pool[0]["bcx"]
            per_set = min(len(pool), bcx_max // cc) if cc > 0 else 0
            if per_set < 2:
                break
            chosen = list(range(per_set))
        else:
            chosen = best_combine_set(pool, bcx_max)
        if not chosen:
            break
        parts = [pool[i] for i in chosen]
        survivor = survivor_rule([{"uid": p["uid"], "card_detail_id": p["card_detail_id"], "edition": p["edition"],
                                   "foil": p["foil"], "bcx": p["bcx"]} for p in parts])
        lead = next((p for p in parts if p["uid"] == survivor), parts[0])
        total = sum(p["bcx"] for p in parts)
        sets.append({
            "warnings": [LOCK_WARNING] if any(p.get("warnings") for p in parts) else [],
            "uids": [p["uid"] for p in parts],
            "survivor_uid": None if survivor == "unknown" else survivor,
            "count": len(parts),
            "destroyed_count": len(parts) - 1,
            "parts_cc": [p["bcx"] for p in parts],
            "before": _state(lead["bcx"], thresholds, max_pp, bcx_max),
            "after": _state(total, thresholds, max_pp, bcx_max),
            "levels_gained": max(0, (level_for_bcx(thresholds, total) or 0) - (level_for_bcx(thresholds, lead["bcx"]) or 0)) if thresholds else None,
        })
        taken = set(chosen)
        pool = [c for i, c in enumerate(pool) if i not in taken]
    return sets, pool


def _print_header(row: dict[str, Any], thresholds: list[int] | None) -> dict[str, Any]:
    return {
        "id": card_market.print_id(row["card_detail_id"], row["edition"], row["foil"]),
        **{k: row.get(k) for k in ("card_detail_id", "name", "edition", "card_set", "rarity", "foil", "gold", "splinters",
                                   "max_level", "bcx_max", "max_level_pp")},
        "thresholds": thresholds,
        "market_url": card_market.market_url(row["card_detail_id"], row["foil"], row["edition"]),
    }


def build_groups(report: dict[str, Any], details_by_id: dict[int, dict[str, Any]], *, player: str,
                 now: datetime | None = None) -> list[dict[str, Any]]:
    """Every owned print with its copies, blockers, proposed sets and the upgrade base (no market)."""
    now = now or datetime.now(timezone.utc)
    cards = [c for c in report.get("cards") or [] if isinstance(c, dict)]
    rows = build_card_rows({**report, "cards": cards}, details_by_id, player=player, now=now)
    by_print: dict[tuple, list[tuple[dict[str, Any], dict[str, Any]]]] = {}
    for card, row in zip(cards, rows):
        if row.get("card_detail_id") is None or row.get("edition") is None or row.get("foil") is None:
            continue
        by_print.setdefault((row["card_detail_id"], row["edition"], row["foil"]), []).append((card, row))
    groups = []
    for (detail_id, edition, foil), members in by_print.items():
        head = members[0][1]
        bcx_max = head.get("bcx_max")
        thresholds = resolve_level_thresholds(detail_id, edition, head.get("rarity"), head.get("gold"), head.get("card_set"))
        max_pp = _pp_model([r for _, r in members], bcx_max)
        copies, free = [], []
        for card, row in members:
            reasons = copy_reasons(card, row, player)
            uid = str(card.get("uid") or (card.get("facts") or {}).get("uid") or "") or None
            view = {
                "uid": uid,  # for signing only; never shown on the page
                "bcx": _int_cc(row.get("bcx")) or row.get("bcx"),
                "level": row.get("level"),
                "land_base_pp": row.get("land_base_pp"),
                "status": row.get("status"),
                "plot": row.get("plot"),
                "maxed": row.get("maxed"),
                "combinable": not reasons,
                "warnings": [LOCK_WARNING] if card_locked(card) else [],
                "reasons": [{"code": reason_code(r), "text": r} for r in reasons],
            }
            copies.append(view)
            if not reasons:
                free.append({**view, "card_detail_id": detail_id, "edition": edition, "foil": foil})
        copies.sort(key=lambda c: (-(c["bcx"] or 0), not c["combinable"], str(c["uid"] or "")))
        note = None
        sets: list[dict[str, Any]] = []
        leftover: list[dict[str, Any]] = free
        if foil in (3, 4):
            note = "Black Foil and Arcane Variant cards never combine"
        elif not bcx_max:
            note = "the max CC of this print is not known, so no combine is proposed"
        elif len(free) >= 2:
            sets, leftover = combine_sets(free, int(bcx_max), thresholds, max_pp)
            if not sets:
                note = "any two free copies together would pass max CC"
        # the upgrade base: the first set's result, else the maintainer's best copy that is his own and not already a black foil
        own = [c for c in copies if not any(r["code"] == "delegated_in" for r in c["reasons"]) and isinstance(c["bcx"], int)]
        best = max(own, key=lambda c: c["bcx"], default=None)
        base = None
        if sets and (best is None or sets[0]["after"]["bcx"] >= best["bcx"]):
            base = {"source": "combine", **sets[0]["after"], "blocked": []}
        elif best is not None:
            base = {"source": "copy", **_state(best["bcx"], thresholds, max_pp, bcx_max),
                    "blocked": [r for r in best["reasons"] if r["code"] != "max"]}
        groups.append({
            "print": _print_header({**head, "max_level_pp": max_pp, "max_level": len(thresholds) if thresholds else head.get("max_level")}, thresholds),
            "copies": copies,
            "owned_count": len(copies),
            "combinable_count": len(free),
            "blocked_count": len(copies) - len(free),
            "sets": sets,
            "leftover_count": len(leftover) if sets or len(free) >= 2 else 0,
            "note": note,
            "base": base,
        })
    return groups


def _group_sort_key(group: dict[str, Any]) -> tuple:
    first = group["sets"][0] if group["sets"] else None
    hint = group.get("upgrade_hint") or {}
    return (0 if hint.get("discount_likely") else 1, -(first["levels_gained"] or 0) if first else 1, -(first["destroyed_count"] if first else 0),
            str(group["print"]["name"] or "").lower(), group["print"]["id"])


# b) the going rate and the step analysis

_RATE_LOCK = threading.Lock()
_RATE_CACHE: dict[tuple[int, str], tuple[float, dict[str, Any]]] = {}


def median_rate(listings: list[dict[str, Any]]) -> tuple[float | None, int]:
    """Median of price / Land base PP over the listings that carry both; (None, n) below GOING_RATE_MIN_SAMPLES."""
    rates = [x["price_usd"] / x["land_base_pp"] for x in listings
             if _number(x.get("price_usd")) and _number(x.get("land_base_pp")) and x["land_base_pp"] > 0 and x["price_usd"] > 0]
    if len(rates) < GOING_RATE_MIN_SAMPLES:
        return None, len(rates)
    return statistics.median(rates), len(rates)


def going_rate(details_by_id: dict[int, dict[str, Any]], *, basis: str = "all_prints", getter: card_market.Getter | None = None,
               cache: card_market.MarketCache | None = None, now: float | None = None) -> dict[str, Any]:
    """The market's going rate for one Land PP, in USD and DEC, cached 60 s.

    ``all_prints`` ( default): see below. ``best100`` (, option): the median of price / Land base PP over the current listings of the 100
    prints with the cheapest rate per PP (the Market page's default read, at most 3 listings per print).
    ``all_prints``: the median over every Land print on sale of its cheapest rate per PP
    (``for_sale_grouped``'s cheapest price per CC / the print's PP per CC) -- the typical price of PP across the market."""
    basis = basis if basis in GOING_RATE_BASES else GOING_RATE_BASES[0]
    cache = cache or card_market.CACHE
    now = time.monotonic() if now is None else now
    with _RATE_LOCK:
        hit = _RATE_CACHE.get((id(cache), basis))
        if hit and now - hit[0] < GOING_RATE_SECONDS:
            return hit[1]
    if basis == "all_prints":
        calls: list[str] = []
        get = getter or card_market._default_get
        grouped = cache.get(card_market.API_ORIGIN + card_market.GROUPED_PATH, get, calls)
        settings = cache.get(card_market.API_ORIGIN + card_market.SETTINGS_PATH, get, calls)
        dec_usd = _number((settings or {}).get("dec_price")) if isinstance(settings, dict) else None
        rates = [it["usd_per_pp"] for it in (card_market.normalise_print(r, details_by_id) for r in (grouped if isinstance(grouped, list) else [])
                                            if isinstance(r, dict)) if it and it["usd_per_pp"]]
        usd = statistics.median(rates) if len(rates) >= GOING_RATE_MIN_SAMPLES else None
        samples, prints = len(rates), len(rates)
        method = "median of every Land print's cheapest price per PP on the market"
    else:
        market = card_market.read_market(details_by_id, {}, sort="cost_per_pp", player=None, getter=getter, cache=cache)
        usd, samples = median_rate(market.get("listings") or [])
        dec_usd = market.get("dec_usd")
        prints = min(GOING_RATE_PRINTS, market.get("prints_read") or 0)
        calls = market.get("upstream_calls") or []
        method = "median price per Land PP of the current listings of the 100 prints with the cheapest rate per PP"
    body = {
        "basis": basis,
        "usd_per_pp": usd,
        "dec_per_pp": usd / dec_usd if usd is not None and dec_usd else None,
        "samples": samples,
        "prints": prints,
        "dec_usd": dec_usd,
        "method": method,
        "upstream_calls": calls,
    }
    with _RATE_LOCK:
        _RATE_CACHE[(id(cache), basis)] = (now, body)
    return body


def clear_rate_cache() -> None:
    with _RATE_LOCK:
        _RATE_CACHE.clear()


def _level_states(base_bcx: int, item: dict[str, Any], pool: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """Per level above the base, the cheapest cover (cumulative), deduplicated: a cover that already reaches a higher level is
    one state, at the level it reaches. Also the first level the listings on sale cannot reach."""
    thresholds, bcx_max, max_pp = item["thresholds"], int(item["bcx_max"]), item["max_level_pp"]
    start_level = level_for_bcx(thresholds, base_bcx) or 0
    levels = list(range(start_level + 1, len(thresholds) + 1))
    covers = card_market.cheapest_covers([(int(x["bcx"]), float(x["price_usd"])) for x in pool],
                                         [thresholds[level - 1] - base_bcx for level in levels], bcx_max - base_bcx)
    states: list[dict[str, Any]] = []
    seen: set[tuple[str, ...]] = set()
    for level, chosen in zip(levels, covers):
        if chosen is None:
            return states, {"level": level, "cc_needed": thresholds[level - 1] - base_bcx, "cc_on_sale": sum(int(x["bcx"]) for x in pool)}
        parts = [pool[i] for i in chosen]
        signature = tuple(sorted(x["lkey"] for x in parts))
        cc = base_bcx + sum(int(x["bcx"]) for x in parts)
        reached = level_for_bcx(thresholds, cc) or level
        if signature in seen or (states and reached <= states[-1]["level"]):
            continue
        seen.add(signature)
        states.append({"level": reached, "bcx": cc, "cost": sum(float(x["price_usd"]) for x in parts),
                       "pp": card_market.pp_at(max_pp, cc, bcx_max) or 0.0, "keys": signature, "parts": parts})
    return states, None


def mark_discounts(pool: list[dict[str, Any]], bcx_max: int) -> tuple[float | None, list[dict[str, Any]]]:
    """: the print's cheapest single-copy (1 CC) price per CC, and every discount mid-level listing (above 1 CC, below
    max CC, cheaper per CC than that single copy), largest discount first. Max-level listings are never the reference and
    never flagged. Each listing gets ``usd_per_cc`` and ``below_single_pct`` (None when it is not a discount)."""
    singles = [float(x["price_usd"]) for x in pool if int(x["bcx"]) == 1]
    single = min(singles) if singles else None
    found = []
    for x in pool:
        cc = int(x["bcx"])
        x["usd_per_cc"] = float(x["price_usd"]) / cc
        x["below_single_pct"] = None
        if single and 1 < cc < bcx_max and x["usd_per_cc"] < single:
            x["below_single_pct"] = (1 - x["usd_per_cc"] / single) * 100
            found.append(x)
    found.sort(key=lambda x: (-x["below_single_pct"], x["usd_per_cc"]))
    return single, found


def discount_stop(path: list[dict[str, Any]], discounts: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    """: the accepted state to stop at -- the last one on the suggested path that buys a discount mid-level copy when
    every later step only adds single copies at a worse price per CC than that copy. None when there is no such stop."""
    if not path or not discounts:
        return None
    final = path[-1]
    final_keys = set(final["keys"])
    for state in reversed(path[:-1]):
        held = [discounts[k] for k in state["keys"] if k in discounts]
        if not held or not set(state["keys"]) <= final_keys:
            continue
        kept = set(state["keys"])
        extra = [x for x in final["parts"] if x["lkey"] not in kept]
        if not extra or any(int(x["bcx"]) != 1 for x in extra):
            return None
        extra_rate = sum(float(x["price_usd"]) for x in extra) / len(extra)
        return state if extra_rate > min(d["usd_per_cc"] for d in held) else None
    return None


def _summary(state: dict[str, Any], pp_start: float, going: float, to_dec: Any, bcx_max: int) -> dict[str, Any]:
    added = state["pp"] - pp_start
    going_cost = going * added
    return {"level": state["level"], "total_usd": state["cost"], "total_dec": to_dec(state["cost"]), "pp_added": added,
            "to_bcx": min(state["bcx"], bcx_max), "saving_usd": going_cost - state["cost"],
            "saving_dec": to_dec(going_cost - state["cost"]), "keys": list(state["keys"])}


def upgrade_steps(base_bcx: int, item: dict[str, Any], listings: list[dict[str, Any]], going_usd_per_pp: float | None,
                  dec_usd: float | None, *, rule: str = "lookahead") -> dict[str, Any]:
    """The going-rate analysis from an owned CC total (pure; the module docstring has the rule).

    ``rule="lookahead"`` ( default): each step goes, from the last accepted level, to the higher level
    with the lowest added price per PP (the lower convex hull of cost against PP), so a cheap large copy that reaches max is
    not hidden behind one dear intermediate level. ``rule="next"`` (, option): each step goes to the next level the
    cheapest cover reaches. In both, the first step dearer than the going rate stops the suggestion, and a discount mid-level
    copy can stop it earlier (module docstring)."""
    bcx_max = item.get("bcx_max")
    thresholds = item.get("thresholds")
    max_pp = item.get("max_level_pp")
    max_level = len(thresholds) if thresholds else None
    to_dec = (lambda usd: usd / dec_usd if usd is not None and dec_usd else None)
    out: dict[str, Any] = {"steps": [], "verdict": None, "cart": None, "rule": rule if rule in UPGRADE_RULES else UPGRADE_RULES[0],
                           "discounts": [], "single_usd_per_cc": None}
    rule = out["rule"]

    def verdict(kind: str, **extra: Any) -> dict[str, Any]:
        out["verdict"] = {"kind": kind, **extra}
        return out

    if not thresholds or not bcx_max or max_pp is None:
        return verdict("unknown_levels", label="The level table or the PP of this print is not known")
    start_level = level_for_bcx(thresholds, base_bcx) or 0
    if base_bcx >= bcx_max:
        return verdict("already_max", level=max_level, label="Already at max level")
    pool = [x for x in listings if not x.get("own") and x.get("lkey") and _int_cc(x.get("bcx")) and _number(x.get("price_usd"))]
    if not pool:
        return verdict("no_listings", label="No copy of this card is on sale")
    out["single_usd_per_cc"], discounts = mark_discounts(pool, int(bcx_max))
    out["discounts"] = [{k: d.get(k) for k in ("lkey", "bcx", "level", "price_usd", "price_dec", "usd_per_cc", "below_single_pct")}
                        for d in discounts]
    if going_usd_per_pp is None:
        return verdict("no_rate", label="The market's going rate for PP could not be measured")
    states, unreachable = _level_states(base_bcx, {**item, "max_level_pp": max_pp}, pool)
    pp_start = card_market.pp_at(max_pp, base_bcx, bcx_max) or 0.0
    origin = {"level": start_level, "bcx": base_bcx, "cost": 0.0, "pp": pp_start, "keys": (), "parts": []}

    def rate_from(a: dict[str, Any], b: dict[str, Any]) -> float | None:
        add_pp = b["pp"] - a["pp"]
        return (b["cost"] - a["cost"]) / add_pp if add_pp > 0 else None

    prev, accepted, stopped = origin, origin, None
    path: list[dict[str, Any]] = []
    remaining = list(states)
    while remaining:
        if rule == "lookahead":
            scored = [(rate_from(prev, s), s) for s in remaining]
            scored = [(r, s) for r, s in scored if r is not None]
            if not scored:
                break
            rate, nxt = min(scored, key=lambda t: (t[0], -t[1]["level"]))
        else:
            nxt = remaining[0]
            rate = rate_from(prev, nxt)
        remaining = [s for s in remaining if s["level"] > nxt["level"]]
        passes = stopped is None and rate is not None and rate <= going_usd_per_pp
        add_cost = nxt["cost"] - prev["cost"]
        step = {
            "from_level": prev["level"], "to_level": nxt["level"], "from_bcx": min(prev["bcx"], int(bcx_max)),
            "to_bcx": min(nxt["bcx"], int(bcx_max)), "overcombined_cc": max(0, nxt["bcx"] - int(bcx_max)),
            "add_cost_usd": add_cost, "add_cost_dec": to_dec(add_cost), "add_pp": nxt["pp"] - prev["pp"],
            "usd_per_pp": rate, "dec_per_pp": to_dec(rate), "vs_going": rate / going_usd_per_pp if rate is not None else None,
            "suggested": passes, "total_usd": nxt["cost"], "total_dec": to_dec(nxt["cost"]),
            "count": len(nxt["parts"]), "keys": list(nxt["keys"]),
        }
        out["steps"].append(step)
        if passes:
            accepted = nxt
            path.append(nxt)
        elif stopped is None:
            stopped = step
        prev = nxt
    added_pp = accepted["pp"] - pp_start
    common = {"from_level": start_level, "from_bcx": base_bcx, "going_usd_per_pp": going_usd_per_pp,
              "stop": ({k: stopped[k] for k in ("to_level", "usd_per_pp", "vs_going")} if stopped else None),
              "unreachable": unreachable}
    if accepted["level"] == start_level:
        return verdict("none", level=start_level, label="No level is cheaper than the market's going rate for PP", **common)
    discount_keys = {d["lkey"]: d for d in discounts}
    full = _summary(accepted, pp_start, going_usd_per_pp, to_dec, int(bcx_max))
    stop_at = discount_stop(path, discount_keys)
    max_option = None
    if stop_at is not None and stop_at is not accepted:
        max_option = {"kind": "max" if accepted["level"] >= max_level else "level", **full}
        accepted = stop_at
        added_pp = accepted["pp"] - pp_start
    cost = accepted["cost"]
    going_cost = going_usd_per_pp * added_pp
    kind = "max" if accepted["level"] >= max_level else "level"
    held = [discount_keys[k] for k in accepted["keys"] if k in discount_keys]
    discount = None
    if held and kind != "max":
        best = max(held, key=lambda d: d["below_single_pct"])
        kind = "discount"
        discount = {k: best.get(k) for k in ("lkey", "bcx", "level", "usd_per_cc", "below_single_pct")}
    common["max_option"] = max_option
    common["discount"] = discount
    out["cart"] = {
        "listings": accepted["parts"],
        "keys": list(accepted["keys"]),
        "bundle": f"Lv {accepted['level']}",
        "group_prefix": "up",
    }
    if kind == "discount":
        more = len(accepted["keys"]) - 1
        label = (f"Buy this Level {discount['level'] or '?'} copy" + (f" (+{more} more)" if more > 0 else "")
                 + f" \u2014 {discount['below_single_pct']:.0f}% below single copies")
    elif kind == "max":
        label = "Max it"
    else:
        label = f"Stronger cheaply \u2192 Level {accepted['level']}"
    return verdict(kind, level=accepted["level"], label=label,
                   total_usd=cost, total_dec=to_dec(cost), pp_added=added_pp, to_bcx=min(accepted["bcx"], int(bcx_max)),
                   going_cost_usd=going_cost, going_cost_dec=to_dec(going_cost),
                   saving_usd=going_cost - cost, saving_dec=to_dec(going_cost - cost), **common)


def analyse_upgrade(details_by_id: dict[int, dict[str, Any]], report: dict[str, Any], *, player: str, card_detail_id: int,
                    edition: int, foil: int, basis: str = "all_prints", rule: str = "lookahead", getter: card_market.Getter | None = None,
                    cache: card_market.MarketCache | None = None, now: datetime | None = None) -> dict[str, Any] | None:
    """One print: the maintainer's base (best copy or first proposed set), the market read, the going rate and the steps.
    None when the print is not a Land worker in the catalogue."""
    rate = going_rate(details_by_id, basis=basis, getter=getter, cache=cache)
    market = card_market.read_print(details_by_id, card_detail_id, edition, foil, player=player, getter=getter, cache=cache)
    own_report = {**report, "cards": [c for c in report.get("cards") or [] if isinstance(c, dict)
                                       and (c.get("facts") or {}).get("card_detail_id") == card_detail_id
                                       and (c.get("facts") or {}).get("edition") == edition]}
    group = next((g for g in build_groups(own_report, details_by_id, player=player, now=now)
                  if g["print"]["card_detail_id"] == card_detail_id and g["print"]["edition"] == edition and g["print"]["foil"] == foil), None)
    if market is None:
        if group is None:
            return None
        # owned, but the market never carries it (soulbound editions, no Land PP): nothing to price
        return {"print": group["print"], "dec_usd": None, "going_rate": {k: v for k, v in rate.items() if k != "upstream_calls"},
                "base": group["base"], "combine_first": False, "steps": [], "cart": None,
                "verdict": {"kind": "not_on_market", "label": "This card is not sold on the market"},
                "listings_read": 0, "on_sale": 0, "truncated": False, "upstream_calls": rate.get("upstream_calls") or []}
    item = dict(market["print"])
    if item.get("max_level_pp") is None and group and group["print"].get("max_level_pp") is not None:
        item["max_level_pp"] = group["print"]["max_level_pp"]
    base = group["base"] if group else None
    base_bcx = int(base["bcx"]) if base and base.get("bcx") else 0
    if base_bcx <= 0:
        analysis = {"steps": [], "cart": None, "verdict": {"kind": "not_owned", "label": "You own no copy of this card"}}
    else:
        analysis = upgrade_steps(base_bcx, item, market["listings"], rate["usd_per_pp"], market["dec_usd"], rule=rule)
    return {
        "print": item,
        "dec_usd": market["dec_usd"],
        "going_rate": {k: v for k, v in rate.items() if k != "upstream_calls"},
        "base": base,
        "combine_first": bool(base and base.get("source") == "combine"),
        **analysis,
        "listings_read": market["count"],
        "on_sale": market["on_sale"],
        "truncated": market["truncated"],
        "upstream_calls": (rate.get("upstream_calls") or []) + market["upstream_calls"],
    }


# the list

def build_combinables(report: dict[str, Any], details_by_id: dict[int, dict[str, Any]], *, player: str,
                      filters: dict[str, Any] | None = None, market: bool = False, basis: str = "all_prints",
                      getter: card_market.Getter | None = None,
                      cache: card_market.MarketCache | None = None, now: datetime | None = None) -> dict[str, Any]:
    """Every owned print with a proposed combine set (and, with ``market``, every print worth an upgrade price check)."""
    filters = filters or {}
    groups = [g for g in build_groups(report, details_by_id, player=player, now=now) if card_market.print_matches(g["print"], filters)]
    rate = None
    prices: dict[tuple, dict[str, Any]] = {}
    if market:
        rate = going_rate(details_by_id, basis=basis, getter=getter, cache=cache)
        calls: list[str] = []
        grouped = (cache or card_market.CACHE).get(card_market.API_ORIGIN + card_market.GROUPED_PATH, getter or card_market._default_get, calls)
        for row in grouped if isinstance(grouped, list) else []:
            if isinstance(row, dict):
                item = card_market.normalise_print(row, details_by_id)
                if item:
                    prices[(item["card_detail_id"], item["edition"], item["foil"])] = item
    shown = []
    blocked_only = 0
    for group in groups:
        p = group["print"]
        if market:
            item = prices.get((p["card_detail_id"], p["edition"], p["foil"]))
            base = group["base"]
            if base is None or base.get("maxed") or p["foil"] in (3, 4):
                hint = {"possible": False, "reason": "max" if base and base.get("maxed") else "none"}
            elif item is None or item.get("usd_per_pp") is None:
                hint = {"possible": False, "reason": "not_on_sale"}
            elif rate["usd_per_pp"] is None:
                hint = {"possible": None, "reason": "no_rate"}
            else:
                ok = item["usd_per_pp"] <= rate["usd_per_pp"]
                low, per_cc = item.get("low_price_usd"), item.get("low_price_per_cc_usd")
                # a cheaper price per CC than the cheapest listing's own price means some copy above 1 CC is cheaper
                # per CC than the cheapest (usually single) copy -- a likely discount mid-level copy, confirmed by the deep read
                likely = bool(ok and low and per_cc is not None and per_cc < low)
                hint = {"possible": ok, "reason": "check" if ok else "dearer", "print_usd_per_pp": item["usd_per_pp"],
                        "discount_likely": likely}
            group["upgrade_hint"] = hint
        if group["sets"] or (market and group["upgrade_hint"].get("possible")):
            shown.append(group)
        elif group["owned_count"] >= 2 and not group["sets"]:
            blocked_only += 1
    shown.sort(key=_group_sort_key)
    return {
        "going_rate": ({k: v for k, v in rate.items() if k != "upstream_calls"} if rate else None),
        "summary": {
            "prints_owned": len(groups),
            "groups": len(shown),
            "with_sets": sum(1 for g in shown if g["sets"]),
            "upgrade_checks": sum(1 for g in shown if (g.get("upgrade_hint") or {}).get("possible")),
            "discount_likely": sum(1 for g in shown if (g.get("upgrade_hint") or {}).get("discount_likely")),
            "copies_to_combine": sum(s["count"] for g in shown for s in g["sets"]),
            "copies_destroyed": sum(s["destroyed_count"] for g in shown for s in g["sets"]),
            "multi_copy_without_set": blocked_only,
        },
        "groups": shown,
        "count": len(shown),
    }
