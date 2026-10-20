""" "My purchases": the player's card-market purchases from the game's own market history, with PP and price statistics,
read only on the maintainer's press.

Server (ceres/purchase_history.py, ceres/routes_purchase_history.py) on a recorded, pseudonymised capture of the maintainer's real
history and by-id card answers (tests/ceres/fixtures/ir9255_purchase_history.json, 2026-10-15) and a synthetic market; the page
(ceres/static/card-purchases.js) in the shared vm sandbox. No live network in any test."""

from __future__ import annotations

import ast
import json
import socket
import statistics
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ceres import card_combinables, card_market, client_limits
from ceres import purchase_history as ph
from ceres import routes_purchase_history as routes

from .vm_context import run_in_module_context

ROOT = Path(__file__).resolve().parents[2]
STATIC = ROOT / "ceres" / "static"
FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "ir9255_purchase_history.json").read_text(encoding="utf-8"))
PLAYER = "player_owner"
NOW = datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc)

DETAILS = {d["id"]: dict(d) for d in FIXTURE["details"]}
RATE_IDS = list(range(900, 912))  # the going-rate population: 12 synthetic Chaos rares
for i in RATE_IDS:
    DETAILS[i] = {"id": i, "name": f"Rate Card {i}", "type": "Monster", "rarity": 2, "tier": None, "no_pp": False, "color": "Red",
                  "secondary_color": None, "editions": "7"}
CHAOS_RARE_PP_PER_CC = card_market.normalise_print({"card_detail_id": 900, "edition": 7, "foil": 0}, DETAILS)["pp_per_cc"]
RATE_USD_PER_PP = [0.005 + 0.001 * k for k in range(12)]
# today's cheapest price per CC of the bought prints (value today); they are Land prints on sale, so in the going rate too
LOW_PER_CC = {(81, 3, 0): 0.15, (82, 3, 0): 2.0, (218, 3, 0): 0.03, (6, 0, 1): 30.0}


def _grouped() -> list[dict]:
    rows = [{"card_detail_id": i, "edition": 7, "foil": 0, "gold": False, "qty": 5, "low_price": 1.0,
             "low_price_bcx": usd * CHAOS_RARE_PP_PER_CC} for i, usd in zip(RATE_IDS, RATE_USD_PER_PP)]
    rows += [{"card_detail_id": k[0], "edition": k[1], "foil": k[2], "gold": k[2] == 1, "qty": 3, "low_price": v, "low_price_bcx": v}
             for k, v in LOW_PER_CC.items()]
    return rows


GOING_USD = statistics.median(it["usd_per_pp"] for it in (card_market.normalise_print(r, DETAILS) for r in _grouped()) if it and it["usd_per_pp"])


class Upstream:
    """A recording fake of the four public GETs; anything else fails the test."""

    def __init__(self, history=None, found=None):
        self.history = FIXTURE["history"] if history is None else history
        self.found = FIXTURE["cards_find"] if found is None else found
        self.urls: list[str] = []

    def __call__(self, url: str):
        self.urls.append(url)
        if url.startswith(ph.LAND_ORIGIN + ph.OTHER_PATH):
            return {"data": [
                {"buyer": PLAYER, "type": "purchase", "itemId": "I-001", "createdDate": "2026-10-14T12:00:00Z", "usdValue": 15, "price": 30000, "currency": "DEC"},
                {"buyer": PLAYER, "type": "purchase", "itemId": "P-001", "createdDate": "2026-10-12T12:00:00Z", "usdValue": 20, "price": 20000, "currency": "CREDITS"},
                {"buyer": PLAYER, "type": "purchase", "itemId": "C-001", "createdDate": "2026-10-14T12:00:00Z", "usdValue": 10, "price": 20000, "currency": "DEC"},
            ]}
        path, _, query = url.removeprefix(card_market.API_ORIGIN).partition("?")
        if path == ph.HISTORY_PATH:
            return self.history
        if path == ph.FIND_PATH:
            ids = urllib.parse.parse_qs(query)["ids"][0].split(",")
            return [c for c in self.found if c["uid"] in ids]
        if path == card_market.GROUPED_PATH:
            return _grouped()
        if path == card_market.SETTINGS_PATH:
            return {"dec_price": 0.0004935}
        raise AssertionError(f"unexpected upstream read {url}")


@pytest.fixture(autouse=True)
def _fresh():
    ph.CACHE.clear()
    card_market.CACHE.clear()
    card_combinables.clear_rate_cache()
    yield
    ph.CACHE.clear()
    card_market.CACHE.clear()
    card_combinables.clear_rate_cache()


def _build(upstream=None, **kw):
    upstream = upstream or Upstream()
    body = ph.build(PLAYER, DETAILS, getter=upstream, now=NOW, **kw)
    return body, upstream


def _by_name(body, name):
    return [p for p in body["purchases"] if p["name"] == name]


# parsing

def test_only_the_players_completed_purchases_newest_first():
    sale = dict(FIXTURE["history"][0], purchaser="player_0099", seller=PLAYER)
    open_listing = dict(FIXTURE["history"][1], status=0)
    rows = ph.purchase_rows([sale, open_listing, *FIXTURE["history"][2:]], PLAYER)
    assert len(rows) == 12
    dates = [r["completed_date"] for r in rows]
    assert dates == sorted(dates, reverse=True)


def test_payment_parsing():
    assert ph.parse_payment("18347.704 DEC") == (18347.704, "DEC")
    assert ph.parse_payment("9150 CREDITS") == (9150.0, "CREDITS")
    assert ph.parse_payment("") == (None, None)
    assert ph.parse_payment("12 dec extra") == (None, None)


def test_cc_from_xp_matches_the_games_bcx_for_every_unchanged_copy():
    found = {c["uid"]: c for c in FIXTURE["cards_find"]}
    checked = 0
    for row in FIXTURE["history"]:
        card = found[row["card_id"]]
        if card.get("combined_card_id") or card.get("bcx") is None:
            continue
        p = ph.normalise_purchase(row, DETAILS, 1)
        assert p["bcx"] == card["bcx"], (p["name"], row["xp"])
        checked += 1
    assert checked == 13  # Beta rare 4500 xp -> 61, Beta legendary 7500 -> 11, Beta common 7560 -> 505, Alpha gold rare 500 -> 1 ...


# PP per purchase

def test_pp_formula_equals_the_games_land_base_pp():
    found = {c["uid"]: c for c in FIXTURE["cards_find"]}
    land = 0
    for row in FIXTURE["history"]:
        p = ph.normalise_purchase(row, DETAILS, 1)
        game = card_market._number(found[row["card_id"]].get("land_base_pp"))
        if p["land"]:
            land += 1
            assert p["pp"] == pytest.approx(game, abs=0.001), p["name"]
        else:
            assert p["pp"] is None and p["pp_per_usd"] is None  # towers: no Land PP, money only
    assert land == 6


def test_game_pp_used_while_unchanged_and_combined_copies_marked():
    body, _ = _build()
    hob = _by_name(body, "Hobgoblin")
    assert {p["pp_source"] for p in hob} == {"game"} and sorted(p["pp"] for p in hob) == [2678.261, 2917.391]
    tower = _by_name(body, "Ivory Tower")[0]
    assert tower["combined"] is True and tower["land"] is False
    assert body["summary"]["combined"] == 1 and body["summary"]["looked_up"] == 14 and body["summary"]["not_owned"] == 0


def test_a_copy_that_grew_counts_as_combined_and_keeps_the_formula_pp():
    found = [dict(c) for c in FIXTURE["cards_find"]]
    gelatinous = next(c for c in found if c["card_detail_id"] == 218)
    gelatinous.update(bcx=600, land_base_pp="5000.000")
    body, _ = _build(Upstream(found=found))
    p = _by_name(body, "Gelatinous Cube")[0]
    assert p["combined"] is True and p["bcx"] == 505 and p["pp_source"] == "formula"


# averages, best / worst, going rate

def test_totals_and_weighted_average():
    body, _ = _build()
    s = body["summary"]
    land = [p for p in body["purchases"] if p["pp"]]
    assert s["count"] == 14 and s["transactions"] == 8 and s["land_count"] == 6
    assert s["usd"] == pytest.approx(sum(float(r["buy_price"]) for r in FIXTURE["history"]))
    assert s["paid"] == {"DEC": pytest.approx(sum(float(r["payment"].split()[0]) for r in FIXTURE["history"]), abs=0.001)}
    assert s["pp"] == pytest.approx(sum(p["pp"] for p in land), abs=0.01)
    assert s["pp_per_usd"] == pytest.approx(sum(p["pp"] for p in land) / sum(p["price_usd"] for p in land))
    assert s["pp_per_usd"] != pytest.approx(statistics.mean(p["pp_per_usd"] for p in land))  # weighted, not a mean of ratios


def test_groups_per_month_rarity_and_edition_add_up():
    body, _ = _build()
    s = body["summary"]
    for key in ("by_month", "by_rarity", "by_edition"):
        assert sum(g["count"] for g in s[key]) == s["count"], key
        assert sum(g["usd"] for g in s[key]) == pytest.approx(s["usd"]), key
        assert sum(g["pp"] for g in s[key]) == pytest.approx(s["pp"], abs=0.01), key
    assert [g["key"] for g in s["by_month"]] == ["2026-10", "2026-09"]
    assert [g["key"] for g in s["by_rarity"]] == ["common", "rare", "epic", "legendary"]
    beta = next(g for g in s["by_edition"] if g["key"] == "beta")
    assert beta["count"] == 4 and beta["pp_per_usd"] == pytest.approx(beta["pp"] / beta["usd"])


def test_best_and_worst_by_pp_per_usd_never_overlap():
    body, _ = _build()
    best, worst = body["summary"]["best"], body["summary"]["worst"]
    rates = sorted((p["pp_per_usd"] for p in body["purchases"] if p["pp_per_usd"]), reverse=True)
    assert [b["pp_per_usd"] for b in best] == rates[:3]
    assert [w["pp_per_usd"] for w in worst] == rates[3:][::-1]
    assert worst[0]["name"] == "Serpentine Soldier"
    few = ph.summarise([dict(p, _low_per_cc=None) for p in body["purchases"] if p["name"] == "Hobgoblin"], None)
    assert len(few["best"]) == 2 and few["worst"] == []


def test_each_purchase_against_todays_going_rate():
    body, _ = _build()
    going_pp_per_usd = 1 / GOING_USD
    assert body["going"]["usd_per_pp"] == pytest.approx(GOING_USD) and body["going"]["today"] is True
    for p in body["purchases"]:
        if p["pp_per_usd"]:
            assert p["vs_going"] == pytest.approx(p["pp_per_usd"] / going_pp_per_usd)
            assert p["tier"] == ph.rate_tier(p["pp_per_usd"], going_pp_per_usd)
        else:
            assert p["vs_going"] is None and p["tier"] is None
    s = body["summary"]
    land = [p for p in body["purchases"] if p["pp"]]
    assert s["value_at_going_usd"] == pytest.approx(sum(p["pp"] for p in land) * GOING_USD, abs=1e-5)
    hob = _by_name(body, "Hobgoblin")
    assert sorted(p["value_now_usd"] for p in hob) == pytest.approx([56 * 0.15, 61 * 0.15])
    assert s["value_now_count"] == 6 and s["value_now_paid_usd"] == pytest.approx(sum(p["price_usd"] for p in land))


@pytest.mark.parametrize("ratio,tier", [(1.2, "is-top"), (1.0, "is-top"), (0.99, "is-good"),
                                        (1 / 1.2, "is-good"), (1 / 1.4, "is-fair"), (0.7, "is-dear")])
def test_rate_tiers(ratio, tier):
    assert ph.rate_tier(100 * ratio, 100) == tier


def test_no_going_rate_no_tier():
    assert ph.rate_tier(100, None) is None and ph.rate_tier(None, 100) is None


# bounded requests, cache, coverage, no ids

def test_one_press_reads_history_once_and_cards_by_id_once():
    body, up = _build()
    paths = [u.removeprefix(card_market.API_ORIGIN).removeprefix(ph.LAND_ORIGIN).split("?")[0] for u in up.urls]
    assert paths.count(ph.HISTORY_PATH) == 1 and paths.count(ph.FIND_PATH) == 1
    assert sorted(set(paths)) == sorted({ph.HISTORY_PATH, ph.FIND_PATH, ph.OTHER_PATH, card_market.GROUPED_PATH, card_market.SETTINGS_PATH})
    assert len(up.urls) == 5
    assert body["summary"]["other_purchases"]["usd"] == 35
    assert body["summary"]["other_purchases"]["dec"] == 30000
    query = urllib.parse.parse_qs(up.urls[0].split("?", 1)[1])
    assert query == {"player": [PLAYER], "game": ["splinterlands"]}
    again = Upstream()
    ph.build(PLAYER, DETAILS, getter=again, now=NOW, days=7)  # a window change inside 60 s: no upstream read at all
    assert again.urls == []


def test_lookup_is_capped_at_one_call():
    template = FIXTURE["history"][6]  # a Beta common
    many = [dict(template, card_id=f"C3-218-X{i:04d}PSEUDO", transaction_id=f"{i:040x}") for i in range(150)]
    body, up = _build(Upstream(history=many, found=[]))
    finds = [u for u in up.urls if ph.FIND_PATH in u]
    assert len(finds) == 1
    assert len(urllib.parse.parse_qs(finds[0].split("?", 1)[1])["ids"][0].split(",")) == ph.FIND_IDS
    assert body["coverage"]["lookup_limit"] == ph.FIND_IDS


def test_window_filter_and_coverage():
    body, _ = _build(days=7)
    assert body["days"] == 7 and all(p["date"] >= "2026-10-08" for p in body["purchases"]) and body["summary"]["count"] == 4
    assert body["coverage"]["capped"] is False
    full = [dict(FIXTURE["history"][0], card_id=f"C3-81-Y{i:04d}PSEUDO", purchaser="player_0099") for i in range(ph.SOURCE_ROWS)]
    ph.CACHE.clear()
    capped, _ = _build(Upstream(history=full, found=[]))
    assert capped["coverage"]["capped"] is True and capped["coverage"]["rows_read"] == 200 and capped["purchases"] == []


def test_identical_copies_share_one_row_but_stats_count_each_copy():
    template = FIXTURE["history"][0]
    rows = [dict(template, card_id=f"C3-81-COPY{i:02d}PSEUDO") for i in range(17)]
    body, _ = _build(Upstream(history=rows, found=[]))
    assert body["summary"]["count"] == 17
    assert body["summary"]["transactions"] == 1
    assert len(body["purchases"]) == 1
    row = body["purchases"][0]
    assert row["quantity"] == 17
    assert row["price_usd"] == pytest.approx(17 * float(template["buy_price"]))
    assert row["pp"] == pytest.approx(body["summary"]["pp"])


def test_same_card_same_utc_day_merges_across_transactions_and_prices():
    template = FIXTURE["history"][0]
    rows = [dict(template, card_id="C3-81-DAY01PSEUDO", completed_date="2026-10-14T00:05:00Z",
                 buy_price="9.150", payment="18347.704 DEC", transaction_id="first-transaction"),
            dict(template, card_id="C3-81-DAY02PSEUDO", completed_date="2026-10-14T23:55:00Z",
                 buy_price="12.345", payment="25000.123 DEC", transaction_id="second-transaction")]
    body, _ = _build(Upstream(history=rows, found=[]))
    assert len(body["purchases"]) == 1
    row = body["purchases"][0]
    pp_each = ph.normalise_purchase(rows[0], DETAILS, 1)["pp"]
    assert row["quantity"] == 2
    assert row["paid_currency"] == "DEC"
    assert row["price_usd"] == pytest.approx(21.495)
    assert row["paid"] == pytest.approx(43347.827)
    assert row["pp"] == pytest.approx(2 * pp_each)
    assert row["value_now_usd"] == pytest.approx(2 * 61 * LOW_PER_CC[(81, 3, 0)])
    assert row["pp_per_usd"] == pytest.approx(2 * pp_each / 21.495)
    assert row["vs_going"] == pytest.approx(row["pp_per_usd"] / body["going"]["pp_per_usd"])
    assert row["tier"] == ph.rate_tier(row["pp_per_usd"], body["going"]["pp_per_usd"])
    assert body["summary"]["count"] == 2
    assert body["summary"]["transactions"] == 2
    assert len(body["summary"]["best"]) == 2
    assert body["summary"]["usd"] == pytest.approx(21.495)
    assert body["summary"]["paid"]["DEC"] == pytest.approx(43347.827)


def test_merged_rate_badge_uses_the_summed_pp_per_usd():
    template = FIXTURE["history"][0]
    rows = [dict(template, card_id="C3-81-RATE01PSEUDO", transaction_id="first-transaction"),
            dict(template, card_id="C3-81-RATE02PSEUDO", transaction_id="second-transaction",
                 buy_price="100.000", payment="200000 DEC")]
    body, _ = _build(Upstream(history=rows, found=[]))
    row = body["purchases"][0]
    assert len(body["purchases"]) == 1
    assert ph.rate_tier(ph.normalise_purchase(rows[0], DETAILS, 1)["pp_per_usd"], body["going"]["pp_per_usd"]) == "is-top"
    assert row["tier"] == ph.rate_tier(row["pp_per_usd"], body["going"]["pp_per_usd"]) == "is-dear"


@pytest.mark.parametrize("changed", [
    {"completed_date": "2026-10-13T23:55:00Z"},
    {"xp": 4125},  # a different CC and level
    {"payment": "25000.123 CREDITS"},
])
def test_collapse_keeps_different_day_cc_level_or_currency_separate(changed):
    template = FIXTURE["history"][0]
    rows = [dict(template, card_id="C3-81-SEP01PSEUDO", transaction_id="first-transaction"),
            dict(template, card_id="C3-81-SEP02PSEUDO", transaction_id="second-transaction", **changed)]
    body, _ = _build(Upstream(history=rows, found=[]))
    assert len(body["purchases"]) == 2
    assert [row["quantity"] for row in body["purchases"]] == [1, 1]
    assert body["summary"]["count"] == 2


def test_other_feed_totals_non_cards_only_and_filters_window():
    rows = [{"buyer": PLAYER, "type": "purchase", "itemId": f"I-{i}", "createdDate": "2026-10-14T00:00:00Z",
             "usdValue": 2, "price": 4000, "currency": "DEC"} for i in range(3)]
    rows += [{**rows[0], "buyer": "someone-else"}, {**rows[0], "type": "sale"},
             {**rows[0], "itemId": "C-001"}, {**rows[0], "itemId": "C3-001"},
             {**rows[0], "createdDate": "2026-09-01T00:00:00Z"}]
    got = ph.other_total({"data": rows}, PLAYER, NOW.replace(day=8))
    assert got["count"] == 3 and got["usd"] == 6 and got["dec"] == 12000


def test_unknown_window_falls_back_to_the_default():
    body, _ = _build(days=90)
    assert body["days"] == ph.DEFAULT_DAYS


def test_no_uid_transaction_id_or_seller_in_the_answer():
    body, _ = _build()
    text = json.dumps(body)
    for row in FIXTURE["history"]:
        assert row["card_id"] not in text and row["transaction_id"] not in text and row["seller"] not in text
    assert not any(k.startswith("_") for p in body["purchases"] for k in p)
    assert "uid" not in {k for p in body["purchases"] for k in p}


# no read without the press

ROUTE_PLAYER = "ownertest"  # a valid Hive name (player_owner is not one) for the HTTP route


def _route_upstream(**kw):
    history = [dict(r, purchaser=ROUTE_PLAYER) for r in FIXTURE["history"]]
    found = [dict(c, player=ROUTE_PLAYER) for c in FIXTURE["cards_find"]]
    return Upstream(history=history, found=found, **kw)


def _app(monkeypatch, upstream):
    monkeypatch.setattr(routes, "load_details_by_id", lambda: DETAILS)
    monkeypatch.setattr(ph, "_default_get", upstream)
    app = FastAPI()
    app.state.ceres_config = SimpleNamespace(player=ROUTE_PLAYER)
    app.include_router(routes.router)
    return app


def test_route_is_get_only_and_reads_on_call(monkeypatch):
    up = _route_upstream()
    client = TestClient(_app(monkeypatch, up))
    assert up.urls == []  # building the app reads nothing
    assert client.post("/api/cards/purchases").status_code == 405
    assert up.urls == []
    res = client.get("/api/cards/purchases", params={"player": ROUTE_PLAYER, "days": "30"})
    assert res.status_code == 200 and res.headers["cache-control"] == "no-store"
    assert res.json()["summary"]["count"] == 14 and len(up.urls) == 5
    assert client.get("/api/cards/purchases", params={"days": "90"}).status_code == 400
    assert client.get("/api/cards/purchases", params={"player": "bad name!"}).status_code == 422


def test_upstream_failure_is_a_502(monkeypatch):
    def broken(url):
        raise OSError("down")
    client = TestClient(_app(monkeypatch, broken))
    assert client.get("/api/cards/purchases").status_code == 502


def test_rate_limit_bucket():
    assert client_limits.BUCKETS["purchases"] == ((10, 60.0), (120, 3600.0))


def test_import_makes_no_network_call(monkeypatch):
    import importlib

    def refuse(*a, **k):
        raise AssertionError("network at import")
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket.socket, "connect", refuse)
    importlib.reload(ph)
    importlib.reload(routes)
    ph.CACHE.clear()


def test_no_background_work_in_the_server_modules():
    for name in ("purchase_history.py", "routes_purchase_history.py"):
        tree = ast.parse((ROOT / "ceres" / name).read_text(encoding="utf-8"))
        calls = {n.func.attr if isinstance(n.func, ast.Attribute) else getattr(n.func, "id", "") for n in ast.walk(tree) if isinstance(n, ast.Call)}
        assert not calls & {"Thread", "Timer", "create_task", "ensure_future", "on_event", "add_task", "schedule"}, name
        top = [n for n in tree.body if isinstance(n, ast.Expr) and isinstance(n.value, ast.Call)
               and not (isinstance(n.value.func, ast.Attribute) and n.value.func.attr == "setdefault")]
        assert top == [], name  # no module-level call statements (the rate-limit bucket registration aside)
    assert "@router.get(" in (ROOT / "ceres" / "routes_purchase_history.py").read_text() \
        and "@router.post(" not in (ROOT / "ceres" / "routes_purchase_history.py").read_text()


# the page (card-purchases.js)

def _js(body: str) -> dict:
    out = run_in_module_context(f"(async () => {{ {body} }})().catch((e) => {{ console.error(e); process.exit(1); }});",
                                entry=STATIC / "card-purchases.js").stdout
    return json.loads(out.strip().splitlines()[-1])


SAMPLE = json.dumps(ph.build(PLAYER, DETAILS, getter=Upstream(), now=NOW))


def test_page_mount_reads_nothing_until_the_press():
    got = _js(r"""
const calls = [];
const root = { innerHTML: '', dataset: {}, addEventListener: (t, fn) => { root.handler = fn; } };
const api = initPurchases(root, { player: () => 'player_owner', fetch: async (u) => { calls.push(u); return { ok: true, json: async () => (""" + SAMPLE + r""") }; } });
const before = calls.length;
const html = root.innerHTML;
const button = { dataset: { cpAction: 'load' }, disabled: false };
root.handler({ target: { closest: () => button } });
await new Promise((r) => setTimeout(r, 20));
console.log(JSON.stringify({ before, after: calls, hasLoad: html.includes('Load my purchases'), hasList: root.innerHTML.includes('cp-list'),
 ready: root.dataset.cpReady, leak: /C3-81-T|PSEUDO|player_0/.test(root.innerHTML) }));
""")
    assert got["before"] == 0 and got["hasLoad"]
    assert got["after"] == ["/api/cards/purchases?player=player_owner&days=30"]
    assert got["hasList"] and got["ready"] == "true" and got["leak"] is False


def test_page_window_button_before_load_only_selects():
    got = _js(r"""
const calls = [];
const root = { innerHTML: '', dataset: {}, addEventListener: (t, fn) => { root.handler = fn; } };
initPurchases(root, { player: () => 'p', fetch: async (u) => { calls.push(u); return { ok: true, json: async () => ({}) }; } });
root.handler({ target: { closest: () => ({ dataset: { cpDays: '7' }, disabled: false }) } });
await new Promise((r) => setTimeout(r, 10));
console.log(JSON.stringify({ calls, days: _cpForTests.days, pressed: root.innerHTML.includes('data-cp-days="7" aria-pressed="true"') }));
""")
    assert got == {"calls": [], "days": 7, "pressed": True}


def test_other_total_remains_visible_when_there_are_no_card_purchases():
    got = _js(r"""
const root = { innerHTML: '', dataset: {}, addEventListener: () => {} };
initPurchases(root, { player: () => 'p' });
_cpForTests.body = { days: 30, purchases: [], summary: { count: 0, transactions: 0, usd: 0, paid: {},
 other_purchases: { count: 1, usd: 12, dec: 24000, note: 'Limited feed' } }, going: {} };
renderPurchases();
console.log(JSON.stringify({ other: root.innerHTML.includes('Other purchases'), amount: root.innerHTML.includes('$12.00'),
 empty: root.innerHTML.includes('No card purchases') }));
""")
    assert got == {"other": True, "amount": True, "empty": True}


def test_page_helpers():
    got = _js(r"""
const rows = [{ name: 'b', date: '2026-10-02', pp_per_usd: 5 }, { name: 'a', date: '2026-10-03', pp_per_usd: null }, { name: 'c', date: '2026-10-01', pp_per_usd: 9 }];
console.log(JSON.stringify({
 rateDesc: cpSorted(rows, 'pp_per_usd', 'desc').map((r) => r.name),
 rateAsc: cpSorted(rows, 'pp_per_usd', 'asc').map((r) => r.name),
 name: cpSorted(rows, 'name', 'asc').map((r) => r.name),
 date: cpSorted(rows, 'date', 'desc').map((r) => r.name),
 url: cpUrl('player_owner', 90), usd: [cpUsd(9.15), cpUsd(0.048), cpUsd(null)], vs: [cpVsText(2.1), cpVsText(0.8), cpVsText(1)],
 month: cpMonth('2026-10'),
}));
""")
    assert got["rateDesc"] == ["c", "b", "a"] and got["rateAsc"] == ["b", "c", "a"]  # unknown last both ways
    assert got["name"] == ["a", "b", "c"] and got["date"] == ["a", "b", "c"]
    assert got["url"] == "/api/cards/purchases?player=player_owner&days=30"
    assert got["usd"] == ["$9.15", "$0.048", "—"]
    assert got["vs"][0].startswith("110 % more") and got["vs"][1].startswith("20 % less") and "same" in got["vs"][2]
    assert got["month"] == "Oct 2026"


def test_page_rows_colour_pp_per_usd_and_put_dec_after_the_amount():
    got = _js(r"""
const body = """ + SAMPLE + r"""const list = cpListHtml(body.purchases, 'pp_per_usd', 'desc');
const summary = cpSummaryHtml(body);
console.log(JSON.stringify({ tiers: (list.match(/cp-rate is-(top|good|fair|dear)/g) || []).length,
 dec: /\d<img class="cp-dec-icon"/.test(list), summaryDec: summary.includes('cp-dec-icon'), today: summary.includes('not the rate on the day you bought'),
 noLand: (list.match(/No Land PP/g) || []).length >= 8, combined: list.includes('Combined later') }));
""")
    assert got["tiers"] == 6 and got["dec"] and got["summaryDec"] and got["today"] and got["noLand"] and got["combined"]
