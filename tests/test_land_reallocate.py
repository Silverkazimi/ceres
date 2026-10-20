"""Phase 1 (Fides plan: docs/plans/fides-batch-signing.md) -- additive, read-only enrichment of
land_reallocate.py: asset_ref always present, card_uid only with --include-asset-ids, slot carried
through for staked cards, stake_end_date carried through for idle cards."""

import json
import unittest
from datetime import datetime, timezone
from pathlib import Path

from praetoria.land import analyze_bundle
from praetoria.land_reallocate import (
    ReallocationCriteria, attach_colors, build_plots, gather_idle_cards, gather_staked_candidates,
    plan_reallocation, plan_to_dict, render_plan_text,
)


ROOT = Path(__file__).parent / "fixtures" / "land"
AS_OF = datetime(2026, 8, 9, tzinfo=timezone.utc)


def fixture(name):
    return json.loads((ROOT / name).read_text(encoding="utf-8"))


def collection_with_asset_roster():
    collection = fixture("collection_sample.json")
    known_uids = {card.get("uid") for card in collection["cards"]}
    for name in ("assets_active_above_cap.json", "assets_active_single_worker.json", "assets_active_with_runi.json"):
        for card in fixture(name)["data"]["cards"]:
            if card.get("uid") in known_uids:
                continue
            collection_card = {
                key: value for key, value in card.items()
                if key not in {"slot", "stake_type_uid", "is_powered", "base_pp_after_cap", "total_harvest_pp", "total_construction_pp", "work_per_hour", "terrain_boost", "card_abilities_boost", "card_bloodlines_boost"}
            }
            collection_card["stake_end_date"] = None
            collection["cards"].append(collection_card)
            known_uids.add(card.get("uid"))
    return collection


def build_report(include_asset_ids: bool):
    assets = {}
    for name in ("assets_active_above_cap.json", "assets_active_single_worker.json", "assets_active_with_runi.json"):
        data = fixture(name)["data"]
        if data["cards"]:
            assets[data["cards"][0]["stake_ref_uid"]] = data
    return analyze_bundle(
        "player0005", fixture("deeds_multi.json"), assets,
        collection_payload=collection_with_asset_roster(), as_of=AS_OF, include_asset_ids=include_asset_ids,
    )


DETAILS_BY_ID: dict[int, dict] = {}


def plan_from(report):
    # Forced onto the fixture's one open, positive-multiplier destination (epic plot 41-1-81) so at
    # least one staked-origin move actually happens -- the default criteria produce zero moves on
    # this small fixture, which would make the enrichment assertions below vacuous.
    criteria = ReallocationCriteria(
        min_card_pp=0.0,
        target_plots=frozenset({"41-1-81"}),
        require_nonnegative_terrain=False,
        source_rarities=frozenset({"common", "rare"}),
    )
    plan, plots = plan_reallocation(report, DETAILS_BY_ID, criteria, now=AS_OF.isoformat().replace("+00:00", "Z"))
    return plan, plots


class LandReallocateEnrichmentTests(unittest.TestCase):
    def test_asset_ref_always_present_card_uid_only_with_include_asset_ids(self):
        report_bare = build_report(include_asset_ids=False)
        report_full = build_report(include_asset_ids=True)

        # worker dicts (staked source data): asset_ref always present, uid only with the flag
        self.assertTrue(all("asset_ref" in w and w["asset_ref"] for w in report_bare["workers"]))
        self.assertTrue(all("uid" not in w for w in report_bare["workers"]))
        self.assertTrue(all("asset_ref" in w and w["asset_ref"] for w in report_full["workers"]))
        self.assertTrue(all("uid" in w and w["uid"] for w in report_full["workers"]))

        plan_bare, _ = plan_from(report_bare)
        plan_full, _ = plan_from(report_full)
        dict_bare = plan_to_dict(plan_bare)
        dict_full = plan_to_dict(plan_full)

        for move in dict_bare["moves"]:
            self.assertIn("asset_ref", move["card"])
            self.assertIsNotNone(move["card"]["asset_ref"])
            self.assertNotIn("card_uid", move["card"])
            if move["evicted"] is not None:
                self.assertIn("asset_ref", move["evicted"])
                self.assertNotIn("card_uid", move["evicted"])
        for row in dict_bare["backfill"]:
            self.assertIn("asset_ref", row["card"])
            self.assertNotIn("card_uid", row["card"])

        self.assertTrue(dict_full["moves"], "fixture plan produced no moves to actually test")
        saw_card_uid = False
        for move in dict_full["moves"]:
            self.assertIn("asset_ref", move["card"])
            self.assertIn("card_uid", move["card"])
            self.assertIsNotNone(move["card"]["card_uid"])
            saw_card_uid = True
            if move["evicted"] is not None:
                self.assertIn("card_uid", move["evicted"])
        for row in dict_full["backfill"]:
            self.assertIn("card_uid", row["card"])
            saw_card_uid = True
        self.assertTrue(saw_card_uid, "fixture plan produced no card_uid-bearing rows to actually test")

    def test_slot_carried_through_for_staked_cards(self):
        # Direct unit test of the enrichment function itself: no fixture plan in this small
        # dataset happens to be net-positive for a staked-origin move (the one available staked
        # common-plot card is already on a boosted-multiplier plot -- moving it anywhere else in
        # this fixture is a real loss, so plan_reallocation() correctly never proposes it). That's
        # a planner-economics fact, not a reason this enrichment goes untested.
        report = build_report(include_asset_ids=True)
        plots = build_plots(report)
        attach_colors(plots, DETAILS_BY_ID)
        staked = gather_staked_candidates(plots, source_rarities=frozenset({"common", "rare"}), min_pp=0.0, reserved=frozenset())
        self.assertTrue(staked, "fixture has no staked candidates to test slot carry-through")
        for card in staked:
            self.assertIsNotNone(card.source_slot)
            self.assertIsNotNone(card.asset_ref)
            self.assertIsNotNone(card.card_uid)

    def test_stake_end_date_carried_through_for_expired_idle_cards(self):
        report = build_report(include_asset_ids=True)
        expired_bucket_cards = [c for c in report["cards"] if c["classification"]["bucket"] == "stake_expired_or_historical"]
        if not expired_bucket_cards:
            self.skipTest("fixture has no stake_expired_or_historical cards")
        plan, _plots = plan_from(report)
        idle_moves = [m for m in plan.moves if m.card.origin == "idle" and m.card.bucket == "stake_expired_or_historical"]
        for move in idle_moves:
            self.assertIsNotNone(move.card.stake_end_date)

    def test_table_format_output_unaffected_by_enrichment(self):
        report_bare = build_report(include_asset_ids=False)
        report_full = build_report(include_asset_ids=True)
        plan_bare, plots_bare = plan_from(report_bare)
        plan_full, plots_full = plan_from(report_full)
        # The enrichment only adds fields consumed by plan_to_dict()/JSON output; the table
        # renderer reads none of them, so its output must be identical regardless of the flag.
        self.assertEqual(render_plan_text(plan_bare, plots_bare), render_plan_text(plan_full, plots_full))


if __name__ == "__main__":
    unittest.main()
