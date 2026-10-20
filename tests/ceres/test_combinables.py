""" "Find combinables" -- own-copy grouping, blockers, proposed combine sets that never pass max CC, and the
going-rate upgrade analysis ("Max it" / "Stronger cheaply -> Level N") with the listing keys the shop cart needs. :
all-prints going rate and look-ahead steps by default, discount mid-level copies, card lock as a warning.

Everything runs on synthetic snapshots and a synthetic upstream market (no live network in any test)."""

from __future__ import annotations

import socket
import statistics
import urllib.parse
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ceres import capture as capture_mod
from ceres import card_combinables as cc
from ceres import card_market, client_limits, routes_combinables
from ceres.card_market import MarketCache
from ceres.config import CeresConfig

PLAYER = "player0005"
RARE, LEGEND, GOLD_RARE = 10, 11, 12
DETAILS = {
    RARE: {"id": RARE, "name": "Test Rare", "rarity": 2, "type": "Monster", "color": "Red", "tier": None, "no_pp": False},
    LEGEND: {"id": LEGEND, "name": "Test Legend", "rarity": 4, "type": "Monster", "color": "Blue", "tier": None, "no_pp": False},
    GOLD_RARE: {"id": GOLD_RARE, "name": "Test Foil", "rarity": 2, "type": "Monster", "color": "Green", "tier": None, "no_pp": False},
}
for extra in range(20, 34):  # the going-rate population
    DETAILS[extra] = {"id": extra, "name": f"Rate Card {extra}", "rarity": 2, "type": "Monster", "color": "White", "tier": None, "no_pp": False}

_SEQ = iter(range(1, 10_000))


def _card(detail_id: int, bcx: int, *, foil: int = 0, edition: int = 7, bucket: str = "unstaked_apparent", **facts) -> dict:
    uid = facts.pop("uid", None) or f"C7-{detail_id}-T{next(_SEQ):05d}"
    base = {"player": PLAYER, "uid": uid, "card_detail_id": detail_id, "gold": foil in (1, 2), "foil": foil, "edition": edition,
            "card_set": "chaos", "bcx": bcx, "level": None, "market_id": None, "rental_type": None, "delegated_to": None,
            "unlock_date": None, "stake_ref_uid": None, "stake_end_date": None, "land_base_pp": "0", "survival_date": None}
    base.update(facts)
    return {"uid": uid, "asset_ref": uid, "classification": {"bucket": bucket}, "facts": base}


def _report(cards: list[dict]) -> dict:
    return {"player": PLAYER, "generated_at": "2026-10-15T08:00:00Z", "cards": cards, "plots": []}


def _group(groups, detail_id, foil=0):
    return next(g for g in groups if g["print"]["card_detail_id"] == detail_id and g["print"]["foil"] == foil)


# a) grouping and blockers

def test_groups_by_card_edition_and_foil():
    cards = [_card(RARE, 1), _card(RARE, 1), _card(RARE, 1, foil=1), _card(RARE, 1, edition=8), _card(LEGEND, 1)]
    groups = cc.build_groups(_report(cards), DETAILS, player=PLAYER)
    keys = sorted((g["print"]["card_detail_id"], g["print"]["edition"], g["print"]["foil"], g["owned_count"]) for g in groups)
    assert keys == [(RARE, 7, 0, 2), (RARE, 7, 1, 1), (RARE, 8, 0, 1), (LEGEND, 7, 0, 1)]
    assert len(_group(groups, RARE)["sets"]) == 1 and not _group(groups, RARE, foil=1)["sets"]


def test_every_blocked_copy_has_its_reason_and_is_never_combined():
    future = "2099-01-01T00:00:00Z"
    blocked = {
        "listed": _card(RARE, 1, market_id="m-1"),
        "rented": _card(RARE, 1, rental_type="rental"),
        "delegated": _card(RARE, 1, delegated_to="someone"),
        "staked": _card(RARE, 1, stake_ref_uid="I-1-a", bucket="stake_active_unconfirmed"),
        "cooling": _card(RARE, 1, stake_ref_uid="I-1-a", stake_end_date=future, bucket="stake_cooling_down"),
        "unlocking": _card(RARE, 1, unlock_date=future),
        "card_set": _card(RARE, 1, in_card_set=True),
        "delegated_in": _card(RARE, 1, player="someone_else", delegated_to=PLAYER),
        "max": _card(RARE, 115),
    }
    free = [_card(RARE, 1), _card(RARE, 2)]
    groups = cc.build_groups(_report(free + list(blocked.values())), DETAILS, player=PLAYER)
    group = _group(groups, RARE)
    by_uid = {c["uid"]: c for c in group["copies"]}
    for code, card in blocked.items():
        copy = by_uid[card["uid"]]
        assert not copy["combinable"], code
        assert code in {r["code"] for r in copy["reasons"]}, (code, copy["reasons"])
        assert all(r["text"] for r in copy["reasons"])
    assert group["combinable_count"] == 2 and group["blocked_count"] == len(blocked)
    proposed = {uid for s in group["sets"] for uid in s["uids"]}
    assert proposed == {c["uid"] for c in free}


def test_card_lock_is_a_warning_not_a_blocker():
    locked, plain = _card(LEGEND, 3, lock_days=30), _card(LEGEND, 2)
    group = _group(cc.build_groups(_report([locked, plain]), DETAILS, player=PLAYER), LEGEND)
    copy = next(c for c in group["copies"] if c["uid"] == locked["uid"])
    assert copy["combinable"] and copy["reasons"] == [] and copy["warnings"] == [cc.LOCK_WARNING]
    assert group["sets"][0]["warnings"] == [cc.LOCK_WARNING]
    assert cc.LOCK_WARNING["text"] == "This copy is under a card lock; the combined card will be locked as well."
    free = _group(cc.build_groups(_report([_card(LEGEND, 3), _card(LEGEND, 2)]), DETAILS, player=PLAYER), LEGEND)
    assert free["sets"][0]["warnings"] == []


def test_black_foil_never_combines():
    groups = cc.build_groups(_report([_card(GOLD_RARE, 1, foil=3), _card(GOLD_RARE, 1, foil=3)]), DETAILS, player=PLAYER)
    group = _group(groups, GOLD_RARE, foil=3)
    assert not group["sets"] and group["note"]
    assert all("black_foil" in {r["code"] for r in c["reasons"]} for c in group["copies"])


def test_set_never_passes_max_cc_and_reaches_it_when_it_can():
    # Test Legend (chaos legendary): levels at 1/3/6/11 CC, max CC 11
    cards = [_card(LEGEND, 6), _card(LEGEND, 4), _card(LEGEND, 3), _card(LEGEND, 2)]
    group = _group(cc.build_groups(_report(cards), DETAILS, player=PLAYER), LEGEND)
    first = group["sets"][0]
    assert sorted(first["parts_cc"], reverse=True) == [6, 3, 2]  # 11 exactly; 6+4 = 10 would stop short
    assert first["after"] == {"bcx": 11, "level": 4, "land_base_pp": 1500.0, "maxed": True}
    assert first["before"]["bcx"] == 6 and first["before"]["level"] == 3
    assert all(sum(s["parts_cc"]) <= 11 for s in group["sets"])
    assert group["leftover_count"] == 1


def test_two_copies_over_max_are_not_proposed():
    group = _group(cc.build_groups(_report([_card(LEGEND, 7), _card(LEGEND, 6)]), DETAILS, player=PLAYER), LEGEND)
    assert group["sets"] == [] and "max CC" in group["note"]


def test_leftover_copies_are_packed_into_further_sets():
    group = _group(cc.build_groups(_report([_card(LEGEND, 1) for _ in range(30)]), DETAILS, player=PLAYER), LEGEND)
    assert [sum(s["parts_cc"]) for s in group["sets"]] == [11, 11, 8]
    assert len({u for s in group["sets"] for u in s["uids"]}) == 30


def test_survivor_is_the_highest_cc_copy():
    big = _card(RARE, 9, uid="C7-10-ZZZZZ")
    group = _group(cc.build_groups(_report([_card(RARE, 2, uid="C7-10-AAAAA"), big]), DETAILS, player=PLAYER), RARE)
    assert group["sets"][0]["survivor_uid"] == "C7-10-ZZZZZ"
    assert group["sets"][0]["destroyed_count"] == 1


def test_list_counts_groups_and_respects_print_filters():
    cards = [_card(RARE, 1), _card(RARE, 1), _card(LEGEND, 1), _card(LEGEND, 1), _card(LEGEND, 6, market_id="m"), _card(LEGEND, 6, market_id="n")]
    body = cc.build_combinables(_report(cards), DETAILS, player=PLAYER)
    assert body["count"] == 2 and body["summary"]["copies_to_combine"] == 4 and body["summary"]["copies_destroyed"] == 2
    only = cc.build_combinables(_report(cards), DETAILS, player=PLAYER, filters={"rarity": "legendary"})
    assert [g["print"]["card_detail_id"] for g in only["groups"]] == [LEGEND]
    assert cc.build_combinables(_report(cards), DETAILS, player=PLAYER, filters={"q": "nothing"})["count"] == 0


def test_base_is_the_combined_result_or_the_best_copy():
    combined = _group(cc.build_groups(_report([_card(RARE, 3), _card(RARE, 4)]), DETAILS, player=PLAYER), RARE)
    assert combined["base"]["source"] == "combine" and combined["base"]["bcx"] == 7
    staked = _group(cc.build_groups(_report([_card(RARE, 20, stake_ref_uid="I-1", bucket="stake_active_unconfirmed"), _card(RARE, 1)]),
                                    DETAILS, player=PLAYER), RARE)
    assert staked["base"]["source"] == "copy" and staked["base"]["bcx"] == 20
    assert "staked" in {r["code"] for r in staked["base"]["blocked"]}


# b) median and steps

def test_median_rate_and_minimum_samples():
    rows = [{"price_usd": p, "land_base_pp": 100.0} for p in (0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0, 5.0)]
    rate, n = cc.median_rate(rows + [{"price_usd": 1.0, "land_base_pp": None}, {"price_usd": 1.0, "land_base_pp": 0}])
    assert n == 11 and rate == pytest.approx(0.006)
    assert cc.median_rate(rows[:cc.GOING_RATE_MIN_SAMPLES - 1]) == (None, cc.GOING_RATE_MIN_SAMPLES - 1)


ITEM = {"card_detail_id": RARE, "edition": 7, "foil": 0, "bcx_max": 11, "thresholds": [1, 2, 6, 11], "max_level_pp": 1100.0}


def _listing(key: str, bcx: int, price: float, own: bool = False) -> dict:
    return {"lkey": key, "bcx": bcx, "price_usd": price, "price_dec": price / 0.0005, "own": own, "card_detail_id": RARE,
            "edition": 7, "foil": 0, "name": "Test Rare"}


def test_all_steps_pass_is_max_it_with_every_listing_for_the_cart():
    listings = [_listing(f"s{i:02d}", 1, 0.10) for i in range(10)]
    out = cc.upgrade_steps(1, ITEM, listings, 0.002, 0.0005)
    assert out["verdict"]["kind"] == "max" and out["verdict"]["label"] == "Max it"
    assert all(s["suggested"] for s in out["steps"])
    assert out["verdict"]["total_usd"] == pytest.approx(1.0) and out["verdict"]["pp_added"] == pytest.approx(1000)
    assert out["verdict"]["saving_usd"] == pytest.approx(0.002 * 1000 - 1.0)
    assert sorted(out["cart"]["keys"]) == sorted(x["lkey"] for x in listings)
    assert sorted(x["lkey"] for x in out["cart"]["listings"]) == sorted(out["cart"]["keys"])
    assert out["cart"]["bundle"] == "Lv 4" and out["cart"]["group_prefix"] == "up"


def test_a_dear_step_stops_at_level_n():
    listings = [_listing(f"c{i}", 1, 0.10) for i in range(5)] + [_listing(f"d{i}", 1, 0.50) for i in range(5)]
    assert [s["suggested"] for s in cc.upgrade_steps(1, ITEM, listings, 0.002, 0.0005, rule="next")["steps"]] == [True, True, False]
    out = cc.upgrade_steps(1, ITEM, listings, 0.002, 0.0005)  # look-ahead: L1 -> L3 in one step (equal rate, higher level)
    v = out["verdict"]
    assert v["kind"] == "level" and v["level"] == 3 and v["label"].endswith("Level 3")
    assert [(s["from_level"], s["to_level"], s["suggested"]) for s in out["steps"]] == [(1, 3, True), (3, 4, False)]
    assert v["stop"]["to_level"] == 4 and v["stop"]["usd_per_pp"] == pytest.approx(0.005)
    assert v["total_usd"] == pytest.approx(0.5) and v["saving_usd"] == pytest.approx(0.002 * 500 - 0.5)
    assert sorted(out["cart"]["keys"]) == [f"c{i}" for i in range(5)]


def test_first_step_dearer_suggests_nothing():
    out = cc.upgrade_steps(1, ITEM, [_listing(f"x{i}", 1, 0.5) for i in range(10)], 0.002, 0.0005)
    assert out["verdict"]["kind"] == "none" and out["cart"] is None and not any(s["suggested"] for s in out["steps"])


def test_big_copy_beats_rising_singles_through_the_cover():
    singles = [_listing("a", 1, 0.10), _listing("b", 1, 0.10)] + [_listing(f"r{i}", 1, 0.40) for i in range(8)]
    big = _listing("big", 10, 1.20)
    out = cc.upgrade_steps(1, ITEM, singles + [big], 0.002, 0.0005)
    assert out["verdict"]["kind"] == "max" and out["cart"]["keys"] == ["big"]
    assert out["verdict"]["total_usd"] == pytest.approx(1.20)
    assert out["steps"][-1]["count"] == 1 and out["steps"][-1]["to_level"] == 4


def test_own_and_keyless_listings_are_never_bought():
    listings = [_listing("own", 10, 0.01, own=True), {**_listing("nokey", 10, 0.01), "lkey": None}] + [_listing(f"s{i}", 1, 0.10) for i in range(10)]
    out = cc.upgrade_steps(1, ITEM, listings, 0.002, 0.0005)
    assert "own" not in out["cart"]["keys"] and None not in out["cart"]["keys"]


def test_lookahead_option_reaches_a_cheap_max_behind_a_dear_level():
    listings = [_listing("A", 1, 0.10), _listing("B", 1, 0.50), _listing("G", 4, 0.90), _listing("F", 10, 1.30)]
    nxt = cc.upgrade_steps(1, ITEM, listings, 0.002, 0.0005, rule="next")
    assert nxt["rule"] == "next" and nxt["verdict"]["level"] == 2 and nxt["cart"]["keys"] == ["A"]
    look = cc.upgrade_steps(1, ITEM, listings, 0.002, 0.0005)
    assert look["rule"] == "lookahead"
    assert look["verdict"]["kind"] == "max" and look["cart"]["keys"] == ["F"]
    assert [(s["from_level"], s["to_level"]) for s in look["steps"]] == [(1, 2), (2, 4)]


def test_defaults_are_all_prints_and_lookahead():
    assert cc.GOING_RATE_BASES[0] == "all_prints" and cc.UPGRADE_RULES[0] == "lookahead"
    assert cc.upgrade_steps(1, ITEM, [_listing("a", 1, 0.1)], 0.002, None)["rule"] == "lookahead"


def _mid(key, bcx, price, level):
    return {**_listing(key, bcx, price), "level": level}


def test_discount_mid_level_copies_are_compared_with_single_copies_only():
    pool = [_listing("s1", 1, 0.10), _listing("s2", 1, 0.12), _mid("d40", 5, 0.30, 3), _mid("d10", 3, 0.27, 2),
            _mid("dear", 3, 0.36, 2), _mid("max", 11, 0.40, 4)]
    single, found = cc.mark_discounts(pool, 11)
    assert single == pytest.approx(0.10)
    assert [x["lkey"] for x in found] == ["d40", "d10"]  # largest discount first; the max-level copy is never flagged
    assert found[0]["below_single_pct"] == pytest.approx(40.0) and found[1]["below_single_pct"] == pytest.approx(10.0)
    assert next(x for x in pool if x["lkey"] == "max")["below_single_pct"] is None
    assert cc.mark_discounts([_mid("d", 5, 0.30, 3), _mid("max", 11, 0.40, 4)], 11) == (None, [])  # no single copy: no reference


def test_suggestion_stops_at_a_discount_copy_when_max_needs_only_dearer_singles():
    listings = [_mid("D", 5, 0.30, 3)] + [_listing(f"s{i}", 1, 0.10) for i in range(5)]
    out = cc.upgrade_steps(1, ITEM, listings, 0.01, 0.0005)
    v = out["verdict"]
    assert all(s["suggested"] for s in out["steps"])  # max would pass the going rate ...
    assert v["kind"] == "discount" and v["level"] == 3  # ... but the suggestion stops at the discount copy
    assert v["label"] == "Buy this Level 3 copy \u2014 40% below single copies"
    assert v["discount"]["lkey"] == "D" and out["cart"]["keys"] == ["D"] and v["total_usd"] == pytest.approx(0.30)
    assert v["max_option"]["kind"] == "max" and v["max_option"]["total_usd"] == pytest.approx(0.80)
    assert out["discounts"][0]["lkey"] == "D" and out["single_usd_per_cc"] == pytest.approx(0.10)


def test_no_discount_stop_when_the_rest_is_not_only_single_copies():
    listings = [_mid("D", 5, 0.30, 3), _mid("G", 5, 0.60, 3), _listing("s1", 1, 0.10), _listing("s2", 1, 0.10)]
    out = cc.upgrade_steps(1, ITEM, listings, 0.01, 0.0005)
    assert out["verdict"]["kind"] == "max" and sorted(out["cart"]["keys"]) == ["D", "G"]
    assert out["verdict"]["max_option"] is None


def test_discount_hint_ranks_first_in_the_list():
    plain = {"sets": [{"levels_gained": 5, "destroyed_count": 9}], "print": {"name": "A", "id": "1-7-0"}, "upgrade_hint": {}}
    hinted = {"sets": [], "print": {"name": "Z", "id": "2-7-0"}, "upgrade_hint": {"possible": True, "discount_likely": True}}
    assert sorted([plain, hinted], key=cc._group_sort_key)[0] is hinted


def test_already_max_unknown_and_empty_market():
    assert cc.upgrade_steps(11, ITEM, [_listing("a", 1, 0.1)], 0.002, None)["verdict"]["kind"] == "already_max"
    assert cc.upgrade_steps(1, {**ITEM, "thresholds": None}, [], 0.002, None)["verdict"]["kind"] == "unknown_levels"
    assert cc.upgrade_steps(1, ITEM, [], 0.002, None)["verdict"]["kind"] == "no_listings"
    assert cc.upgrade_steps(1, ITEM, [_listing("a", 1, 0.1)], None, None)["verdict"]["kind"] == "no_rate"


# the going rate and the routes on a synthetic upstream

RATE_PRICES = {20 + i: p for i, p in enumerate((0.11, 0.12, 0.13, 0.14, 0.15, 0.16, 0.17, 0.18, 0.19, 0.20, 0.21, 0.30))}
RATE_PP = 1100.0  # every rate card listing is a maxed chaos rare (115 CC)


def _row(detail_id, bcx, price, n, seller="player_0001", edition=7, foil=0):
    return {"market_id": f"{detail_id:04d}{n:036d}-1", "card_detail_id": detail_id, "edition": edition, "foil": foil, "gold": foil == 1,
            "bcx": bcx, "buy_price": price, "price_bcx": price / bcx, "seller": seller, "type": "SELL", "currency": "USD",
            "level": None, "land_base_pp": None}


def _upstream():
    by_card = {RARE: [_row(RARE, 10, 0.50, 1), _row(RARE, 1, 0.20, 2), _row(RARE, 1, 0.25, 3), _row(RARE, 5, 0.10, 4, seller=PLAYER)]}
    grouped = {d: [_row(d, 115, p, 100 + d)] for d, p in RATE_PRICES.items()}
    grouped[RARE] = by_card[RARE]
    fsg = [{"card_detail_id": d, "edition": 7, "foil": 0, "gold": False, "qty": len(rows), "low_price": min(r["buy_price"] for r in rows),
            "low_price_bcx": min(r["price_bcx"] for r in rows)} for d, rows in grouped.items()]
    calls: list[str] = []

    def get(url: str):
        parsed = urllib.parse.urlsplit(url)
        assert parsed.hostname == "api.splinterlands.com", url
        calls.append(parsed.path)
        query = urllib.parse.parse_qs(parsed.query)
        if parsed.path == "/market/for_sale_grouped":
            return fsg
        if parsed.path == "/settings":
            return {"dec_price": 0.0005}
        if parsed.path == "/market/market_query_grouped":
            ids = [int(i) for i in query["card_ids"][0].split(",")]
            return [{"card_detail_id": i, "foil": 0, "result": grouped.get(i, [])} for i in ids]
        if parsed.path == "/market/market_query_by_card":
            return by_card.get(int(query["card_detail_id"][0]), [])
        raise AssertionError(parsed.path)
    return get, calls


def test_going_rate_is_the_median_of_the_best_prints_and_cached():
    cc.clear_rate_cache()
    get, calls = _upstream()
    cache = MarketCache()
    rate = cc.going_rate(DETAILS, basis="best100", getter=get, cache=cache)
    market = card_market.read_market(DETAILS, {}, sort="cost_per_pp", getter=get, cache=MarketCache())
    expected = statistics.median(x["price_usd"] / x["land_base_pp"] for x in market["listings"] if x["land_base_pp"])
    assert rate["usd_per_pp"] == pytest.approx(expected) and rate["basis"] == "best100"
    assert rate["dec_per_pp"] == pytest.approx(expected / 0.0005) and rate["samples"] >= cc.GOING_RATE_MIN_SAMPLES
    before = len(calls)
    assert cc.going_rate(DETAILS, basis="best100", getter=get, cache=cache) is rate and len(calls) == before  # 60 s cache
    cc.clear_rate_cache()
    fresh_get, fresh_calls = _upstream()
    other = cc.going_rate(DETAILS, getter=fresh_get, cache=MarketCache())  # the default
    assert other["basis"] == "all_prints" and other["samples"] == len(RATE_PRICES) + 1
    assert sorted(fresh_calls) == ["/market/for_sale_grouped", "/settings"]  # lightweight: the two cached reads, nothing more
    cc.clear_rate_cache()


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setattr(socket, "create_connection", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no network")))
    get, _calls = _upstream()
    monkeypatch.setattr(card_market, "_default_get", get)
    monkeypatch.setattr(card_market, "CACHE", MarketCache())
    cc.clear_rate_cache()
    monkeypatch.setattr(routes_combinables, "load_details_by_id", lambda: DETAILS)
    cards = [_card(RARE, 1, uid="C7-10-OWN01"), _card(RARE, 1, uid="C7-10-OWN02"), _card(LEGEND, 1), _card(LEGEND, 1)]
    monkeypatch.setattr(capture_mod, "latest_snapshot", lambda config, player: (_report(cards), SimpleNamespace(token="t", generated_at="2026-10-15T08:00:00Z")))
    monkeypatch.setattr(capture_mod, "load_card_pool", lambda report: report)
    buckets: list[str] = []
    real_limit = client_limits.limit
    monkeypatch.setattr(client_limits, "limit", lambda request, bucket: (buckets.append(bucket), real_limit(request, bucket))[1])
    app = FastAPI()
    app.state.ceres_config = CeresConfig(player=PLAYER)
    app.include_router(routes_combinables.router)
    test_client = TestClient(app)
    test_client.buckets = buckets
    yield test_client
    cc.clear_rate_cache()


def test_list_route_shape_without_market_reads_no_market(client):
    response = client.get("/api/cards/combinables")
    assert response.status_code == 200
    body = response.json()
    assert body["market"] is False and body["going_rate"] is None and client.buckets == []
    group = next(g for g in body["groups"] if g["print"]["card_detail_id"] == RARE)
    assert group["sets"][0]["uids"] and {"before", "after", "survivor_uid", "count"} <= set(group["sets"][0])
    assert {"id", "name", "max_level", "bcx_max", "thresholds"} <= set(group["print"])


def test_list_route_with_market_marks_upgrade_checks(client):
    body = client.get("/api/cards/combinables?market=1").json()
    assert client.buckets == ["market"] and body["going_rate"]["usd_per_pp"] > 0
    group = next(g for g in body["groups"] if g["print"]["card_detail_id"] == RARE)
    assert group["upgrade_hint"]["reason"] in {"check", "dearer"}
    assert client.get("/api/cards/combinables?market=1&basis=nope").status_code == 400


def test_upgrade_route_returns_steps_and_cart_listings_without_market_ids(client):
    response = client.get(f"/api/cards/combinables/upgrade?card_detail_id={RARE}&edition=7&foil=0&basis=all_prints")
    assert response.status_code == 200 and client.buckets == ["market"]
    body = response.json()
    assert body["base"]["source"] == "combine" and body["combine_first"] is True and body["base"]["bcx"] == 2
    assert body["going_rate"]["basis"] == "all_prints" and body["rule"] == "lookahead"
    assert body["verdict"]["kind"] in {"max", "level", "none"}
    if body["cart"]:
        assert all(x["lkey"] and not x["own"] for x in body["cart"]["listings"])
    text = response.text
    assert "market_id" not in text and "seller" not in text and "player_0001" not in text
    assert client.get(f"/api/cards/combinables/upgrade?card_detail_id={RARE}&edition=7&foil=9").status_code == 400
    assert client.get(f"/api/cards/combinables/upgrade?card_detail_id={RARE}&edition=7&foil=0&rule=x").status_code == 400


def test_routes_without_snapshot_are_503(client, monkeypatch):
    monkeypatch.setattr(capture_mod, "latest_snapshot", lambda config, player: (None, None))
    assert client.get("/api/cards/combinables").status_code == 503
    assert client.get(f"/api/cards/combinables/upgrade?card_detail_id={RARE}&edition=7&foil=0").status_code == 503


def test_list_route_print_filter_answers_one_print(client):
    """ UI: the board's combine dialog opened from the Cards page reads one print's copies and proposed set."""
    body = client.get(f"/api/cards/combinables?print={RARE}-7-0").json()
    assert [g["print"]["id"] for g in body["groups"]] == [f"{RARE}-7-0"] and body["count"] == 1
    assert body["filters"]["print"] == f"{RARE}-7-0" and sorted(body["groups"][0]["sets"][0]["uids"]) == ["C7-10-OWN01", "C7-10-OWN02"]
    assert client.get(f"/api/cards/combinables?print={LEGEND}-8-0").json()["groups"] == []
    for bad in ("x", "1-2", "1-2-9", "1-2-3-4"):
        assert client.get(f"/api/cards/combinables?print={bad}").status_code == 400
