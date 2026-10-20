"""The synthetic `board-basic` fixture set: one invented Land account, built here and written to
tests/fixtures/upstream/board-basic.

Every identifier is synthetic. The account is `sample01` (other accounts are `player0002`...), deed uids
are `I-<deed type>-<hash of a key in this file>`, card and item uids carry `SYN`, and transaction ids are
hashes of keys in this file. Nothing here was recorded from a real account. Card names and numbers come
from the public game catalogue shape already used by tests/fixtures/land; region names come from
ceres/data/praetoria_regions.json.

The upstream's derived figures (per-card after-cap, construction and harvest PP, the plot totals, boosts,
work per hour) are computed below with the game's rules as Ceres reads them, so a plot's totals always
agree with its cards:
- base PP is capped per plot at 100,000; the highest-slot worker absorbs the shortfall; a staked Runi is
  added outside the cap;
- construction PP = after-cap base PP x (1 + terrain boost); harvest PP = construction PP x (1 + total boost);
- work per hour = harvest PP x the worksite's per-PP rate (ceres/worksite_swap.py).

Regenerate with `python -m tests.ceres.board_basic_recording`; test_board_basic.py checks the files on disk
match this module. Tests name plots through the constants below (`PLOTS`, `OVERCAP`, ...), never by uid.
"""

from __future__ import annotations

import copy
import hashlib
import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
SET_DIR = ROOT / "tests" / "fixtures" / "upstream" / "board-basic"
LAND = ROOT / "tests" / "fixtures" / "land"
PLAYER = "sample01"
RENTER = "player0002"  # the synthetic maintainer of the rented card overlay
RECORDED_AT = "2026-10-15T12:00:00.000Z"
CAP = 100_000
VAPI = "vapi.splinterlands.com"
API = "api.splinterlands.com"


def _hex(key: str, size: int) -> str:
    return hashlib.sha256(f"ceres-synthetic:{key}".encode()).hexdigest()[:size]


def tx(key: str) -> str:
    """A synthetic 40-hex transaction id."""
    return _hex(f"tx:{key}", 40)


DEED_TYPE_ID = {"Forest": 294, "Mountain": 295, "Tundra": 296, "Canyon": 298, "Caldera": 303, "Plains": 290,
                "Swamp": 292, "Jungle": 293, "Lake": 300, "Desert": 297}
TERRAIN_HEX = {"Forest": "#494C07", "Mountain": "#4E3F14", "Tundra": "#DACDA1", "Canyon": "#A2701B", "Caldera": "#765542",
               "Plains": "#8DA34A", "Swamp": "#3F5A3A", "Jungle": "#2F6B2C", "Lake": "#3E6E8E", "Desert": "#D8B26A"}
LAND_RESOURCE = {"Forest": "wood", "Tundra": "wood", "Swamp": "wood", "Jungle": "wood", "Mountain": "ore", "Caldera": "ore",
                 "Canyon": "stone", "Desert": "stone", "Plains": "grain", "Lake": "grain"}
# card colour -> biome modifier, per terrain (favoured +0.1, penalised -0.5, else 0)
BIOME = {
    "Forest": {"green": 0.1, "white": 0.1, "red": -0.5, "black": 0, "blue": 0, "gold": -0.5},
    "Mountain": {"red": 0.1, "black": 0.1, "green": -0.5, "white": 0, "blue": -0.5, "gold": 0},
    "Tundra": {"blue": 0.1, "white": 0.1, "green": 0.1, "red": -0.5, "black": -0.5, "gold": 0},
    "Canyon": {"red": 0.1, "gold": 0.1, "blue": -0.5, "green": 0, "white": 0, "black": 0},
    "Caldera": {"red": 0.1, "black": 0.1, "blue": -0.5, "white": -0.5, "green": 0, "gold": 0},
    "Plains": {"white": 0.1, "gold": 0.1, "black": -0.5, "red": 0, "green": 0, "blue": 0},
    "Swamp": {"black": 0.1, "green": 0.1, "white": -0.5, "red": 0, "blue": 0.1, "gold": 0},
    "Jungle": {"green": 0.1, "red": 0.1, "white": -0.5, "black": 0, "blue": 0, "gold": 0},
    "Lake": {"blue": 0.1, "white": 0.1, "red": -0.5, "black": 0, "green": 0, "gold": 0},
}
RESOURCE = {  # worksite resource -> (project suffix, worksite name, token, resource id, per-PP rate)
    "grain": ("grain", "Grain Farm", "GRAIN", 1, 0.02), "wood": ("wood", "Logging Camp", "WOOD", 5, 0.005),
    "stone": ("stone", "Quarry", "STONE", 6, 0.002), "iron": ("iron", "Ore Mine", "IRON", 7, 0.0005),
    "research": ("research", "Research Hut", "RESEARCH", 3, 0.0001), "sps": ("sps", "Shard Mine", "SPS", 2, 0.00004),
    "aura": ("aura", "Aura Lab", "AURA", 4, 0.0005),
}
RARITY_BOOST = {"common": 0.0, "rare": 0.1, "epic": 0.4, "legendary": 1.0}
RARITY_SORT = {"common": 1, "rare": 2, "epic": 3, "legendary": 4}
TOTEM = {"common": 318, "rare": 319, "epic": 320, "legendary": 321}
ITEM_BOOST = {318: 0.1, 319: 0.25, 320: 0.5, 321: 1.0, 322: 0.0, 332: 0.1, 347: 0.1, 331: 0.25, 330: 0.5}
ITEM_NAME = {318: "Common Totem", 319: "Rare Totem", 320: "Epic Totem", 321: "Legendary Totem", 322: "Power Core",
             332: "The Proven", 347: "The Mystic", 331: "The Veteran", 330: "The Renowned"}
CARD_SET = {0: "alpha", 1: "beta", 2: "promo", 3: "reward", 4: "untamed", 6: "gladius", 7: "chaos", 9: "soulbound",
            10: "chaos", 12: "rebellion", 14: "rebellion", 19: "land", 20: "rebellion", 21: "land"}
REGIONS = {r["region_uid"].rsplit("-", 1)[1]: r for r in json.loads(
    (ROOT / "ceres" / "data" / "praetoria_regions.json").read_text(encoding="utf-8"))["regions"]}
TERRITORY = {"PNW": "Pristine Northwest", "BRL": "Broken Lands", "LOP": "Land of Prosperity", "CEF": "Central Fire",
             "WNE": "Wild Northeast", "GLL": "Great Lowlands", "SHC": "Shimmering Coasts"}


@dataclass
class Card:
    key: str
    cdid: int
    edition: int
    base_pp: float
    level: int = 1
    bcx: int = 1
    gold: bool = False
    slot: int | None = None       # set when staked
    dec: int | None = None        # land_dec_stake_needed
    xp: int | None = None

    @property
    def uid(self) -> str:
        return f"{'G' if self.gold else 'C'}{self.edition}-{self.cdid}-SYN{_hex('card:' + self.key, 7).upper()}"


@dataclass
class Plot:
    key: str
    region: int
    tract: int
    number: int
    terrain: str
    rarity: str = "common"
    status: str = "natural"           # natural | magical | occupied
    resource: str | None = None       # current worksite resource; None = never built
    building: str | None = None       # worksite under construction (resource); with `ready` past or future end
    ready: bool = False
    workers: list[Card] = field(default_factory=list)
    runi: Card | None = None
    totem: int | None = None          # item detail id
    title: int | None = None
    power_core: bool = False
    lock_days: int | None = None
    dec_short: bool = False           # stake less DEC than needed

    @property
    def uid(self) -> str:
        return f"I-{DEED_TYPE_ID[self.terrain]}-{_hex('deed:' + self.key, 14)}"

    @property
    def plot(self) -> str:
        """The board's `plot` field, e.g. "37-4-18"."""
        return f"{self.region}-{self.tract}-{self.number}"

    @property
    def label(self) -> str:
        return f"{self.region:03d}-{self.tract:02d}-{self.number:03d}"

    @property
    def plot_id(self) -> int:
        return self.region * 1000 + self.tract * 100 + self.number + 900_000

    @property
    def region_uid(self) -> str:
        return REGIONS[str(self.region)]["region_uid"]


def _details() -> dict[int, dict[str, Any]]:
    """The card catalogue: the same public catalogue rows tests/fixtures/land uses, with ids of upstream
    transactions replaced and print distributions dropped (Ceres reads neither)."""
    rows = json.loads((LAND / "card_details_sample.json").read_text(encoding="utf-8"))
    out = {}
    for row in rows:
        row = dict(row)
        row["last_update_tx"] = tx(f"details:{row['id']}")
        row["distribution"] = []
        out[int(row["id"])] = row
    return out


DETAILS = _details()


def _card(key, cdid, edition, base_pp, level=1, bcx=1, gold=False, slot=None, dec=None) -> Card:
    assert cdid in DETAILS, cdid
    return Card(key, cdid, edition, base_pp, level, bcx, gold, slot, dec)


def W(key, cdid, edition, base_pp, slot, level=3, bcx=5, gold=False) -> Card:
    return _card(key, cdid, edition, base_pp, level, bcx, gold, slot)


def _runi(key: str) -> Card:
    return _card(key, 505, 2, 1500.0, level=1, bcx=1, slot=0, dec=0)


# The board. Named plots carry the situations the browser tests look for; the filler makes the board tall
# enough to scroll and gives the filters a mix. Exactly four plots are legendary.

OVERCAP = Plot("overcap", 37, 4, 18, "Forest", "legendary", resource="wood", totem=321, title=330, power_core=True, workers=[
    W("oc-lyanna", 27, 1, 40000.0, 1, level=6, bcx=40), W("oc-paladin", 40, 1, 32941.176, 2, level=6, bcx=30),
    W("oc-spirit", 33, 1, 25000.0, 3, level=4, bcx=8), W("oc-rennyn", 112, 1, 7500.0, 4, level=2, bcx=3),
    W("oc-druid", 867, 19, 2200.0, 5, level=3, bcx=40)])
RUNI = Plot("runi", 37, 2, 7, "Mountain", "legendary", resource="iron", totem=320, title=347, power_core=True, runi=_runi("runi-runi"), workers=[
    W("ru-soldier", 6, 1, 21176.471, 1, level=4, bcx=10), W("ru-zintar", 49, 1, 15000.0, 2, level=3, bcx=5),
    W("ru-miner", 869, 19, 3000.0, 3, level=2, bcx=8), W("ru-malric", 5, 1, 12000.0, 4, level=3, bcx=6)])
ORDINARY = Plot("ordinary", 12, 3, 41, "Canyon", "rare", resource="stone", totem=319, title=332, power_core=True, workers=[
    W("or-giant", 60, 1, 500.0, 1, level=2, bcx=3), W("or-dwarf", 61, 1, 450.0, 2, level=2, bcx=3),
    W("or-archer", 79, 3, 300.0, 3, level=1, bcx=1)])
RUNI_SLOT5 = Plot("runi-slot5", 37, 6, 33, "Tundra", "epic", resource="wood", title=331, runi=_runi("r5-runi"), workers=[
    W("r5-lyanna", 27, 0, 32941.176, 1, level=5, bcx=20, gold=True), W("r5-paladin", 40, 0, 28235.294, 2, level=5, bcx=16),
    W("r5-spirit", 33, 0, 21176.471, 3, level=3, bcx=4), W("r5-roc", 2, 0, 10147.059, 4, level=4, bcx=12),
    W("r5-elf", 62, 1, 4000.0, 5, level=4, bcx=12)])
SOLO = Plot("solo", 12, 5, 9, "Lake", "common", status="occupied", resource="sps", power_core=True, workers=[
    W("so-phlegm", 998, 21, 150.0, 1, level=1, bcx=1)])
PENDING = Plot("pending", 63, 4, 2, "Forest", "common", status="occupied", resource="sps", building="wood", ready=True,
               power_core=True, workers=[W("pe-cutthroat", 62, 1, 400.0, 1, level=2, bcx=3)])
BUILDING = Plot("building", 63, 4, 3, "Canyon", "common", resource="stone", building="research", ready=False, power_core=True,
                workers=[W("bu-mercenary", 334, 3, 300.0, 1, level=1, bcx=1)])
LOCKED = Plot("locked", 63, 7, 14, "Plains", "common", resource="grain", power_core=True, workers=[
    W("lo-bandit", 337, 3, 300.0, 1, level=1, bcx=1)])
STAGED_LOCK = Plot("staged-lock", 63, 7, 15, "Plains", "common", resource="grain", lock_days=7, power_core=True, workers=[
    W("sl-troll", 159, 4, 600.0, 1, level=2, bcx=4)])
NEVER_BUILT = Plot("never-built", 63, 8, 50, "Lake", "common")
COMBINE = Plot("combine", 12, 1, 4, "Plains", "rare", resource="grain", power_core=True, totem=319, workers=[
    W("cb-archer", 79, 3, 1800.0, 1, level=3, bcx=5, gold=True), W("cb-armorsmith", 118, 2, 1200.0, 2, level=3, bcx=5),
    W("cb-mercenary", 334, 3, 700.0, 3, level=2, bcx=3)])
ABILITIES = Plot("abilities", 12, 1, 5, "Swamp", "rare", resource="wood", power_core=True, workers=[
    W("ab-druid", 867, 19, 2200.0, 1, level=4, bcx=60), W("ab-windseer", 726, 14, 5000.0, 2, level=3, bcx=4),
    W("ab-puppet", 526, 10, 600.0, 3, level=2, bcx=3)])
EMPTY_POWERED = Plot("empty-powered", 12, 2, 61, "Jungle", "common", resource="wood", power_core=True)
GAPPED = Plot("gapped", 12, 2, 62, "Jungle", "common", resource="wood", power_core=True, workers=[
    W("ga-grunt", 515, 10, 700.0, 2, level=2, bcx=3), W("ga-flinger", 514, 10, 650.0, 4, level=2, bcx=3)])
UNPOWERED = Plot("unpowered", 37, 9, 70, "Caldera", "common", resource="iron", workers=[
    W("un-kelan", 715, 2, 3500.0, 1, level=3, bcx=6)])
IDLE = Plot("idle", 37, 9, 71, "Caldera", "common", resource="iron", totem=318)
MAGICAL = Plot("magical", 12, 6, 88, "Canyon", "epic", status="magical", resource="research", power_core=True, workers=[
    W("ma-alchemist", 942, 20, 2500.0, 1, level=3, bcx=6), W("ma-stealthfire", 966, 20, 2400.0, 2, level=3, bcx=6)])
SOLE_CORE = Plot("sole-core", 12, 6, 89, "Desert", "common", resource="stone", power_core=True, dec_short=True, workers=[
    W("sc-sentry", 521, 10, 500.0, 1, level=2, bcx=3), W("sc-spitter", 520, 10, 480.0, 2, level=2, bcx=3)])

NAMED = [OVERCAP, RUNI, ORDINARY, RUNI_SLOT5, SOLO, PENDING, BUILDING, LOCKED, STAGED_LOCK, NEVER_BUILT, COMBINE,
         ABILITIES, EMPTY_POWERED, GAPPED, UNPOWERED, IDLE, MAGICAL, SOLE_CORE]

_FILLER_TERRAIN = ["Forest", "Plains", "Canyon", "Mountain", "Swamp", "Lake", "Tundra", "Jungle"]
_FILLER_RESOURCE = {"Forest": "wood", "Plains": "grain", "Canyon": "stone", "Mountain": "iron", "Swamp": "wood",
                    "Lake": "grain", "Tundra": "wood", "Jungle": "wood"}
_FILLER_WORKERS = [(60, 1, 500.0), (61, 1, 450.0), (63, 1, 520.0), (118, 2, 1200.0), (337, 3, 300.0), (382, 7, 650.0)]


def _filler() -> list[Plot]:
    plots = []
    for n in range(22):
        terrain = _FILLER_TERRAIN[n % len(_FILLER_TERRAIN)]
        rarity = "legendary" if n in (3, 15) else "rare" if n % 5 == 0 else "epic" if n % 7 == 3 else "common"
        count = n % 4  # 0..3 workers
        workers = [W(f"fi{n}-w{i}", *_FILLER_WORKERS[(n + i) % len(_FILLER_WORKERS)], i + 1, level=2, bcx=3) for i in range(count)]
        plots.append(Plot(f"filler-{n}", 88 + n // 6, 1 + n % 6, 10 + n, terrain, rarity, resource=_FILLER_RESOURCE[terrain],
                          power_core=count > 0, totem=TOTEM[rarity] if n % 3 == 0 else None, workers=workers))
    return plots


FILLER = _filler()
PLOTS: list[Plot] = NAMED + FILLER
# The conftest reads these plots' assets on every shared-server use, so they are `assets_loaded` on the board the
# tests inspect: combinable gold card, abilities, the over-cap cascade, a Runi plot, and a plot with every item.
PRIMED = (COMBINE, ABILITIES, OVERCAP, RUNI, ORDINARY)

# Idle cards in the collection: the worker picker's candidate pool (all stakeable, none cooling).
_IDLE_SPEC = [(2, 1, 900.0), (5, 1, 7000.0), (6, 1, 6000.0), (27, 1, 9000.0), (33, 1, 5000.0), (40, 1, 8000.0),
              (49, 1, 6500.0), (55, 1, 4000.0), (60, 1, 520.0), (61, 1, 480.0), (62, 1, 450.0), (63, 1, 510.0),
              (79, 3, 300.0), (112, 1, 3000.0), (114, 2, 1100.0), (118, 2, 1250.0), (159, 4, 600.0), (334, 3, 320.0),
              (337, 3, 310.0), (382, 7, 650.0), (514, 10, 640.0), (515, 10, 690.0), (520, 10, 470.0), (521, 10, 510.0),
              (526, 10, 600.0), (527, 10, 610.0), (715, 2, 3600.0), (726, 14, 5200.0), (738, 14, 5400.0),
              (750, 14, 5100.0), (942, 20, 2600.0), (966, 20, 2450.0), (867, 19, 2200.0), (869, 19, 3000.0)]
IDLE_CARDS = [_card(f"idle-{i}", cdid, ed, pp, level=2, bcx=3) for i, (cdid, ed, pp) in enumerate(_IDLE_SPEC)]
IDLE_CARDS.append(_card("idle-runi", 505, 2, 1500.0, level=1, bcx=1, dec=0))
# Two more ungilded copies of a staked gold Highland Archer would not combine; the browser-combine overlay
# adds the gold partners. These are the overlay's synthetic partner uids
COMBINE_WORKER = COMBINE.workers[0]
COMBINE_PARTNER_PREFIX = f"G{COMBINE_WORKER.edition}-{COMBINE_WORKER.cdid}-S8B-IDLE-"
COMBINE_PARTNERS = [f"{COMBINE_PARTNER_PREFIX}{n}" for n in (1, 2, 3)]
RENTED_CARD = "B14-765-SYNRENTED1"
SOULKEEP_CARD = "C11-10012-SAMPLE01"

UNASSIGNED_ITEMS = {"STK-LND-TOT": [f"I-318-SYNTOT{n:04d}" for n in range(1, 4)] + ["I-320-SYNTOT0010"],
                    "STK-LND-TTL": ["I-332-SYNTTL0001"],
                    "STK-LND-PCR": [f"I-322-SYNPCR{n:04d}" for n in range(1, 4)]}


# derived figures


def _r(value: float) -> float:
    return round(value + 0.0, 3)


def _colour(card: Card) -> str:
    return str(DETAILS[card.cdid].get("color") or "Gray").lower()


def _land_row(card: Card) -> list | None:
    table = (DETAILS[card.cdid].get("stats") or {}).get("land_abilities")
    if not table:
        return None
    return table[min(card.level, len(table)) - 1]


def _energized(plot: Plot) -> bool:
    return any(any(e[0] == "ENERGIZED" for e in (_land_row(w) or [])) for w in plot.workers)


def _bloodline(plot: Plot) -> float:
    best = 0.0
    for w in plot.workers:
        for entry in _land_row(w) or []:
            if entry[0] == "BLOODLINE":
                sub = entry[2]
                if any(o is not w and DETAILS[o.cdid].get("sub_type") == sub for o in plot.workers):
                    best = max(best, float(entry[1]))
    return best


def _abilities(plot: Plot) -> float:
    resource = (plot.resource or "").upper()
    best = 0.0
    for w in plot.workers:
        for entry in _land_row(w) or []:
            if entry[0] == resource and len(entry) > 1:
                best = max(best, float(entry[1]))
    return best


def _dd(plot: Plot) -> float:
    best = 0.0
    for w in plot.workers:
        for entry in _land_row(w) or []:
            if entry[0] == "DD":
                best = min(best, float(entry[1]))
    return best


def _status_boost(plot: Plot) -> float:
    target, status = plot.resource, plot.status
    return 1.0 if (target == "research" and status == "magical") or (target == "sps" and status == "occupied") \
        or (target == "aura" and status in ("magical", "occupied")) else 0.0


def _dec_needed(card: Card) -> int:
    return card.dec if card.dec is not None else int(round(card.base_pp / 4 + 250))


@dataclass
class Figures:
    cards: list[dict[str, Any]]
    staking: dict[str, Any]
    worksite: dict[str, Any] | None


def _figures(plot: Plot) -> Figures:
    biome = BIOME.get(plot.terrain, {})
    powered = plot.power_core or plot.runi is not None or _energized(plot)
    totem_boost = ITEM_BOOST[plot.totem] if plot.totem else 0.0
    title_boost = ITEM_BOOST[plot.title] if plot.title else 0.0
    runi_boost = 1.0 if plot.runi else 0.0
    rarity_boost = RARITY_BOOST[plot.rarity]
    status_boost = _status_boost(plot)
    ability_boost, bloodline_boost = _abilities(plot), _bloodline(plot)
    total_boost = round(rarity_boost + status_boost + runi_boost + title_boost + totem_boost + ability_boost + bloodline_boost, 3)
    building = plot.building is not None
    rate = RESOURCE[plot.building if building else plot.resource][4] if (plot.resource or plot.building) else 0.0
    running = 0.0
    rows: list[dict[str, Any]] = []
    for w in sorted(plot.workers, key=lambda c: c.slot):
        after = max(min(w.base_pp, CAP - running), 0.0)
        running += w.base_pp
        terrain = biome.get(_colour(w), 0.0)
        rows.append({"card": w, "after": _r(after), "terrain": terrain})
    if plot.runi:
        rows.insert(0, {"card": plot.runi, "after": plot.runi.base_pp, "terrain": 0.0})
    cards = []
    construction_total = harvest_total = 0.0
    for row in rows:
        w: Card = row["card"]
        is_runi = w is plot.runi
        construction = _r(row["after"] * (1 + row["terrain"]))
        harvest = _r(construction * (1 + total_boost)) if powered else 0.0
        construction_total += construction
        harvest_total += harvest
        detail = DETAILS[w.cdid]
        cards.append({
            "uid": w.uid, "card_detail_id": w.cdid, "player": PLAYER, "bcx": w.bcx, "gold": w.gold, "foil": 1 if w.gold else 0,
            "edition": w.edition, "card_set": CARD_SET.get(w.edition, "land"), "delegated_to": None, "rental_type": None,
            "collection_power": int(w.base_pp * 2) if not is_runi else 0, "land_base_pp": f"{w.base_pp:.3f}",
            "land_dec_stake_needed": 0 if plot.runi else _dec_needed(w), "name": detail["name"],
            "stake_start_date": "2026-06-03T18:00:42.000Z", "slot": w.slot,
            "stake_type_uid": "STK-LND-RUNI" if is_runi else "STK-LND-WKR", "stake_ref_uid": plot.uid,
            "base_pp_after_cap": f"{row['after']:.3f}", "base_pp_after_cap_percentage": f"{(row['after'] / w.base_pp if w.base_pp else 0):.3f}",
            "total_construction_pp": f"{construction:.3f}", "terrain_boost": f"{row['terrain']:.3f}",
            "terrain_boost_pp": f"{construction - row['after']:.3f}", "dec_stake_needed": 0,
            "deed_rarity_boost": f"{rarity_boost:.3f}", "deed_rarity_boost_pp": f"{construction * rarity_boost:.3f}",
            "deed_status_token_boost": f"{status_boost:.3f}", "deed_status_token_boost_pp": f"{construction * status_boost:.3f}",
            "runi_boost": f"{runi_boost:.3f}", "runi_boost_pp": f"{construction * runi_boost:.3f}",
            "title_boost": f"{title_boost:.3f}", "title_boost_pp": f"{construction * title_boost:.3f}",
            "totem_boost": f"{totem_boost:.3f}", "totem_boost_pp": f"{construction * totem_boost:.3f}",
            "boost": f"{total_boost:.3f}", "boost_pp": f"{construction * total_boost:.3f}",
            "total_harvest_pp": f"{harvest:.3f}", "work_per_hour": f"{harvest * rate:.10f}", "is_powered": powered,
            "card_abilities_boost": f"{ability_boost:.3f}", "card_abilities_boost_pp": f"{construction * ability_boost:.3f}",
            "card_bloodlines_boost": f"{bloodline_boost:.3f}", "card_bloodlines_boost_pp": f"{construction * bloodline_boost:.3f}",
        })
    base_total = sum(w.base_pp for w in plot.workers) + (plot.runi.base_pp if plot.runi else 0.0)
    after_total = min(sum(w.base_pp for w in plot.workers), CAP) + (plot.runi.base_pp if plot.runi else 0.0)
    dec_needed = 0 if plot.runi else sum(_dec_needed(w) for w in plot.workers)
    worker_count = len(plot.workers) + (1 if plot.runi else 0)
    max_workers = 5 if (plot.power_core or _energized(plot)) else 4 if plot.runi else 0
    work = _r(harvest_total * rate)
    staking = {
        "region_uid": plot.region_uid, "deed_uid": plot.uid, "is_powered": powered, "is_runi_staked": plot.runi is not None,
        "is_power_core_staked": plot.power_core, "is_energized": _energized(plot), "has_labors_luck": False,
        "max_workers_allowed": max_workers, "runi_boost": runi_boost, "title_boost": title_boost, "totem_boost": totem_boost,
        "deed_status_token_boost": status_boost, "deed_rarity_boost": rarity_boost, "card_abilities_boost": ability_boost,
        "card_bloodlines_boost": bloodline_boost, "grain_food_discount": 0, "lite_food_discount": 0,
        "dec_stake_needed_discount": _dd(plot), "total_boost": total_boost, "total_base_pp_cap": CAP,
        "total_base_pp": _r(base_total), "total_base_pp_after_cap": _r(after_total),
        "total_base_pp_after_cap_percentage": round(after_total / base_total, 3) if base_total else 0,
        "total_construction_pp": _r(construction_total), "total_terrain_boost": round((construction_total - after_total) / after_total, 3) if after_total else 0,
        "total_terrain_boost_pp": _r(construction_total - after_total), "total_dec_stake_needed": dec_needed,
        "total_dec_stake_in_use": dec_needed // 2 if plot.dec_short else dec_needed, "efficiency": 1,
        "total_deed_rarity_boost": rarity_boost, "total_deed_rarity_boost_pp": _r(construction_total * rarity_boost),
        "total_deed_status_token_boost": status_boost, "total_deed_status_token_boost_pp": _r(construction_total * status_boost),
        "total_runi_boost": runi_boost, "total_runi_boost_pp": _r(construction_total * runi_boost),
        "total_title_boost": title_boost, "total_title_boost_pp": _r(construction_total * title_boost),
        "total_totem_boost": totem_boost, "total_totem_boost_pp": _r(construction_total * totem_boost),
        "total_card_abilities_boost_pp": _r(construction_total * ability_boost),
        "total_card_bloodlines_boost_pp": _r(construction_total * bloodline_boost),
        "total_boost_pp": _r(construction_total * total_boost), "total_harvest_pp": _r(harvest_total),
        "total_work_per_hour": work, "powered_worker_count": worker_count if powered else 0, "worker_count": worker_count,
        "manager": PLAYER, "active_land_project_id": _project_id(plot) if (plot.resource or plot.building) else None,
        "has_completed_first_project": plot.resource is not None,
        **{f"{c}_biome_modifier": biome.get(c, 0) for c in ("red", "blue", "white", "black", "green", "gold")},
        "total_dec_staked": dec_needed // 2 if plot.dec_short else dec_needed,
    }
    return Figures(cards, staking, _worksite(plot, harvest_total, after_total, work))


def _project_id(plot: Plot) -> int:
    return 500_000 + int(_hex("project:" + plot.key, 5), 16) % 400_000


def _worksite(plot: Plot, harvest: float, after: float, work: float) -> dict[str, Any] | None:
    resource = plot.building or plot.resource
    if resource is None:
        return None
    suffix, name, token, resource_id, rate = RESOURCE[resource]
    building = plot.building is not None
    end = ("2026-10-15T08:00:00.000Z" if plot.ready else "2026-10-18T12:00:00.000Z") if building else None
    project = _project_id(plot)
    return {
        "id": project, "project_type": f"worksite_{suffix}_construction" if building else f"worksite_{suffix}",
        "project_number": 2, "deed_uid": plot.uid, "land_work_type_id": 10 + resource_id, "total_time_crystals_used": 0,
        "pp_balance": 0, "start_date": "2026-10-12T12:00:00.000Z" if building else "2025-03-18T23:22:24.000Z",
        "projected_hours": 72 if building else 0, "projected_end": end, "completed_date": None,
        "created_date": "2025-03-18T23:22:24.000Z", "last_updated_date": "2026-10-14T12:00:00.000Z", "trx_id": tx("project:" + plot.key),
        "block_num": 100_000_000 + project, "resource_id": resource_id, "token_symbol": token,
        "pp_required": 25000 if building else 0, "hours_to_completion": (0 if plot.ready else 72) if building else None,
        "elapsed_hours": 72 if building else 1000, "pp_spent": (25000 if plot.ready else 4000) if building else 0,
        "grain_required": 1200 if building else 0, "wood_required": 0, "stone_required": 0, "iron_required": 0,
        "is_active": True, "destroyed_date": None, "is_construction": building,
        "last_action_time": "2026-10-14T09:52:48.000Z", "hours_till_next_op": 0, "next_op_allowed_date": "2026-10-14T10:52:48.000Z",
        "pp_staked": _r(harvest), "projected_amount_received": _r(work * 24), "work_per_hour_per_one_pp": rate, "project_id": project,
        "is_harvesting": not building, "is_empty": False, "is_sps_work": resource == "sps", "sps_mining_reward_debt": 0,
        "latest_sps_reward_block": 0, "sps_tokens_per_block": 0, "accumulated_sps_rewards_per_share_of_pool": 0,
        "land_work_type_total_work_type_pp": 197393289.76, "captured_tax_rate": 0, "time_crystal_value": 1000,
        "project_created_date": "2025-03-18T23:22:24.000Z", "worksite_type": name if not building else "", "max_tax_rate": 0,
        "region_uid": plot.region_uid, "hours_since_last_op": 2.0, "site_efficiency": 1, "is_runi_staked": plot.runi is not None,
        "rewards_per_hour": work, "grain_req_per_hour": _r(after * 0.01), "segments": None, "estimated_totem_chance": 0.05,
        "resource_recipe": [], "resource_mint_rate": rate,
    }


def _deed(plot: Plot) -> dict[str, Any]:
    region = REGIONS[str(plot.region)]
    prefix = plot.region_uid.split("-")[1]
    name = plot.resource if plot.building is None else None
    worksite = RESOURCE[name][1] if name else ""
    resource = RESOURCE[name] if name else None
    return {
        "map_name": "Praetoria", "region_id": plot.region, "tract_id": plot.region * 10 + plot.tract, "plot_id": plot.plot_id,
        "region_number": plot.region, "tract_number": plot.tract, "plot_number": plot.number, "territory": TERRITORY[prefix],
        "region_uid": plot.region_uid, "resource_id": resource[3] if resource else None, "resource_symbol": resource[2] if resource else None,
        "magic_type": "dragon" if plot.status == "magical" else "", "stats": "{\"building_in_box\":true}", "deed_uid": plot.uid,
        "player": PLAYER, "created_date": "2023-04-07T23:02:02.902Z", "listed": False, "lock_days": plot.lock_days,
        "unlock_date": None, "in_use": bool(plot.workers or plot.runi), "deed_type": plot.terrain,
        "land_stats": json.dumps({"resources": [LAND_RESOURCE.get(plot.terrain, "grain")]}),
        "region_name": region["name"].split(" - ", 1)[1], "market_updated_date": None, "market_id": None, "listing_price": None,
        "market_listing_id": None, "market_listing_status_id": None, "castle": None, "keep": None, "rarity": plot.rarity,
        "plot_status": plot.status, "hex_code": TERRAIN_HEX[plot.terrain], "tax_rate": "0.10", "item_detail_id": DEED_TYPE_ID[plot.terrain],
        "created_block_num": 73_000_000 + plot.plot_id, "created_tx": tx("deed:" + plot.key), "worksite_type": worksite,
        "time_crystal_value": 1000, "rarity_sort_value": RARITY_SORT[plot.rarity], "is_construction": plot.building is not None,
    }


def _items(plot: Plot) -> list[dict[str, Any]]:
    items = []
    for kind, detail in (("STK-LND-TOT", plot.totem), ("STK-LND-TTL", plot.title), ("STK-LND-PCR", 322 if plot.power_core else None)):
        if detail is None:
            continue
        items.append({"uid": f"I-{detail}-SYN{_hex('item:' + plot.key + kind, 7).upper()}", "item_detail_id": detail,
                      "player": PLAYER, "stake_start_date": "2026-06-03T18:00:42.000Z", "stake_type_uid": kind,
                      "stake_ref_uid": plot.uid, "stake_ref_id": None, "name": ITEM_NAME[detail], "boost": f"{ITEM_BOOST[detail]:.3f}"})
    return items


_COLLECTION_TEMPLATE = {
    "market_id": None, "buy_price": None, "market_listing_type": None, "market_listing_status": None, "market_created_date": None,
    "rental_type": None, "rental_days": None, "rental_date": None, "next_rental_payment": None, "cancel_tx": None,
    "cancel_date": None, "cancel_player": None, "last_used_block": None, "last_used_player": None, "last_used_date": None,
    "last_transferred_block": None, "last_transferred_date": None, "alpha_xp": 0, "delegated_to": None, "delegation_tx": None,
    "skin": None, "delegated_to_display_name": None, "display_name": "", "lock_days": None, "unlock_date": None,
    "wagon_uid": None, "stake_end_date": None, "expiration_date": None, "last_buy_price": None, "last_buy_currency": None,
    "set_id": None, "bcx_unbound": 0, "renewal_tx": None, "renewal_date": None, "survival_date": None,
    "survival_mode_disabled": False, "mint": None, "card_deposited_date": None, "survival_bracket": None,
}


def collection_card(card: Card, plot: Plot | None = None) -> dict[str, Any]:
    detail = DETAILS[card.cdid]
    return {
        "player": PLAYER, "uid": card.uid, "card_detail_id": card.cdid, "xp": card.bcx, "gold": card.gold,
        "edition": card.edition, "card_set": CARD_SET.get(card.edition, "land"), "collection_power": int(card.base_pp * 2),
        **_COLLECTION_TEMPLATE,
        "stake_ref_uid": plot.uid if plot else None, "stake_start_date": "2026-06-03T18:00:42.000Z" if plot else None,
        "stake_plot": plot.plot_id if plot else None, "stake_region": plot.region if plot else None,
        "created_date": "2025-12-06T09:55:36.000Z", "created_block": 101_000_000 + int(_hex("blk:" + card.key, 5), 16) % 900_000,
        "created_tx": tx("card:" + card.key), "bcx": card.bcx, "land_base_pp": f"{card.base_pp:.3f}",
        "land_dec_stake_needed": _dec_needed(card), "foil": 1 if card.gold else 0, "level": card.level,
        "tier": detail.get("tier"),
    }


# entries


def entry(host: str, path: str, body: Any, *, query: str = "", method: str = "GET", status: int = 200) -> dict:
    return {"request": {"method": method, "host": host, "path": path, "query": query}, "status": status,
            "headers": {"Content-Type": "application/json"}, "body": body}


def _ok(data: Any) -> dict:
    return {"status": "success", "data": data}


def build() -> tuple[dict[str, Any], dict[str, list[dict]], dict[str, dict]]:
    """(manifest, entries by file stem, overlays by name)."""
    figures = {plot.key: _figures(plot) for plot in PLOTS}
    deeds = [_deed(plot) for plot in PLOTS]
    bundle = _ok({"deeds": deeds, "worksite_details": [f.worksite for f in figures.values() if f.worksite],
                  "staking_details": [figures[plot.key].staking for plot in PLOTS]})
    collection_cards = []
    for plot in PLOTS:
        for card in plot.workers + ([plot.runi] if plot.runi else []):
            collection_cards.append(collection_card(card, plot))
    collection_cards += [collection_card(card) for card in IDLE_CARDS]
    balances = [{"player": PLAYER, "token": token, "balance": amount} for token, amount in (
        ("DEC", 125000.5), ("SPS", 4210.25), ("VOUCHER", 12.0), ("CREDITS", 300.0), ("GRAIN", 52000.0), ("WOOD", 1800.0),
        ("STONE", 900.0), ("IRON", 120.0), ("RESEARCH", 15.5), ("AURA", 3.25), ("SPSP", 15000.0), ("DEC-B", 0.0))]

    entries: dict[str, list[dict]] = {}
    entries["account"] = [
        entry(VAPI, "/land/deeds", bundle, query=f"player={PLAYER}"),
        entry(API, f"/cards/collection/{PLAYER}", {"player": PLAYER, "cards": collection_cards}),
        entry(API, "/cards/get_details", [DETAILS[k] for k in sorted(DETAILS)]),
        entry(API, "/players/item_details", []),
        entry(API, "/players/balances", balances, query=f"username={PLAYER}"),
    ]
    for stake_type, uids in UNASSIGNED_ITEMS.items():
        for plot in PLOTS:
            entries.setdefault("available", []).append(entry(
                VAPI, f"/land/stake/items/{stake_type}/available", _ok({"ids": [{"uid": u} for u in uids]}),
                query=f"deedUid={plot.uid}&player={PLAYER}&offset=0&limit=100"))
    for plot in PLOTS:
        f = figures[plot.key]
        entries[f"plot-{plot.key}"] = [
            entry(VAPI, f"/land/stake/deeds/{plot.uid}/assets", _ok({"cards": f.cards, "items": _items(plot)})),
            entry(VAPI, f"/land/deeds/{plot.plot_id}", _ok(_deed(plot))),
            entry(VAPI, f"/land/projects/deed/{plot.uid}/active", _ok(f.worksite)),
            entry(VAPI, f"/land/stake/deed/details/{plot.uid}", _ok(f.staking)),
        ]
    manifest = {"format": 1, "set": "board-basic", "time_shift": True, "recorded_at": RECORDED_AT, "epoch_ms_keys": [],
                "overlays": [], "synthetic": True}
    return manifest, entries, _overlays(collection_cards)


def _overlays(collection_cards: list[dict]) -> dict[str, dict]:
    worker = next(c for c in collection_cards if c["uid"] == COMBINE_WORKER.uid)
    partners = []
    for n, uid in enumerate(COMBINE_PARTNERS, start=1):
        card = dict(worker, uid=uid, xp=1, bcx=1, level=1, stake_ref_uid=None, stake_start_date=None, stake_plot=None,
                    stake_region=None, created_tx=tx(f"partner:{n}"))
        partners.append(card)
    collection = {"method": "GET", "host": API, "path": f"/cards/collection/{PLAYER}"}
    deeds = {"method": "GET", "host": VAPI, "path": "/land/deeds"}
    rented = dict(collection_card(_card("rented", 62, 1, 450.0, level=2, bcx=3)), uid=RENTED_CARD, player=RENTER,
                  delegated_to=PLAYER, rental_type="season", rental_days=None, rental_date="2026-10-01T00:00:00.000Z",
                  delegation_tx=tx("rented"))
    soulkeep = dict(collection_card(_card("soulkeep", 63, 1, 510.0, level=1, bcx=1)), uid=SOULKEEP_CARD)
    finished = _worksite(dataclass_replace(NEVER_BUILT, building="grain", ready=True), 0.0, 0.0, 0.0)
    return {
        "browser-combine": {"edits": [{"match": collection, "select": "$.cards", "append": p} for p in partners]},
        "locked-plot": {"edits": [{"match": deeds, "select": f"$.data.deeds[?(@.deed_uid=='{LOCKED.uid}')]", "set": {"lock_days": 3}},
                                  {"match": {"method": "GET", "host": VAPI, "path": f"/land/deeds/{LOCKED.plot_id}"},
                                   "select": "$.data", "set": {"lock_days": 3}}]},
        "staged-lock": {"edits": [{"match": deeds, "select": f"$.data.deeds[?(@.deed_uid=='{STAGED_LOCK.uid}')]", "set": {"lock_days": None}},
                                  {"match": {"method": "GET", "host": VAPI, "path": f"/land/deeds/{STAGED_LOCK.plot_id}"},
                                   "select": "$.data", "set": {"lock_days": None}}]},
        "finished-construction": {"edits": [
            {"match": deeds, "select": f"$.data.deeds[?(@.deed_uid=='{NEVER_BUILT.uid}')]", "set": {"is_construction": True}},
            {"match": deeds, "select": "$.data.worksite_details", "append": finished},
            {"match": {"method": "GET", "host": VAPI, "path": f"/land/deeds/{NEVER_BUILT.plot_id}"}, "select": "$.data",
             "set": {"is_construction": True}},
            {"match": {"method": "GET", "host": VAPI, "path": f"/land/projects/deed/{NEVER_BUILT.uid}/active"}, "select": "$.data",
             "replace": finished},
            {"match": {"method": "GET", "host": VAPI, "path": f"/land/stake/deed/details/{NEVER_BUILT.uid}"}, "select": "$.data",
             "set": {"active_land_project_id": finished["project_id"]}},
        ]},
        "rented-card": {"edits": [{"match": collection, "select": "$.cards", "append": rented}]},
        "soulkeep-card": {"edits": [{"match": collection, "select": "$.cards", "append": soulkeep}]},
    }


def dataclass_replace(plot: Plot, **changes) -> Plot:
    clone = copy.copy(plot)
    for key, value in changes.items():
        setattr(clone, key, value)
    return clone


def render() -> dict[str, str]:
    """Relative path -> file text, exactly as written to SET_DIR."""
    manifest, entries, overlays = build()
    files = {"fixture-manifest.json": json.dumps(manifest, indent=1, sort_keys=True) + "\n"}
    for stem, rows in entries.items():
        for index, row in enumerate(rows):
            host, method = row["request"]["host"], row["request"]["method"]
            files[f"{host}/{method}/{stem}-{index:03d}.json"] = json.dumps(row, separators=(",", ":"), sort_keys=True) + "\n"
    for name, overlay in overlays.items():
        files[f"overlays/{name}.json"] = json.dumps(overlay, indent=1, sort_keys=True) + "\n"
    return files


def write(target: Path = SET_DIR) -> Path:
    if target.exists():
        shutil.rmtree(target)
    for relative, text in render().items():
        path = target / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return target


if __name__ == "__main__":
    print(write())
