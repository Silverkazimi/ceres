import copy
import io
import json
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from praetoria import cli
from praetoria.land import analyze_bundle, estimate_card_pp, recommend_land, render_land_analysis, replay_estimator


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


def fixture_report():
    assets = {}
    for name in ("assets_active_above_cap.json", "assets_active_single_worker.json", "assets_active_with_runi.json"):
        data = fixture(name)["data"]
        if data["cards"]:
            assets[data["cards"][0]["stake_ref_uid"]] = data
    return analyze_bundle("player0005", fixture("deeds_multi.json"), assets, collection_payload=collection_with_asset_roster(), as_of=AS_OF)


def full_scale_synthetic_report():
    report = fixture_report()
    plots = copy.deepcopy(report["plots"])
    while len(plots) < 161:
        index = len(plots)
        plot = copy.deepcopy(plots[index % len(report["plots"])])
        deed_uid = f"synthetic-deed-{index:03d}"
        plot["deed_uid"] = deed_uid
        plot["facts"]["deed_uid"] = deed_uid
        plot["facts"]["plot"] = f"{100 + index // 10}-{index % 10 + 1}-{index + 1}"
        plot["facts"]["is_construction"] = index % 11 == 0
        plot["staking_facts"]["max_workers_allowed"] = 5
        plot["staking_facts"]["worker_count"] = 0 if index % 4 == 0 else 2
        plot["staking_facts"]["total_base_pp"] = 110000 if index % 13 == 0 else 1000 + index * 17
        plot["staking_facts"]["total_base_pp_after_cap"] = 100000 if index % 13 == 0 else plot["staking_facts"]["total_base_pp"]
        plot["staking_facts"]["total_base_pp_cap"] = 100000
        plot["staking_facts"]["total_harvest_pp"] = 10000 + index * 101
        if index % 9 == 0 and plot["worksite_facts"]:
            plot["worksite_facts"]["is_construction"] = True
            plot["worksite_facts"]["is_harvesting"] = False
        plots.append(plot)
    report["plots"] = plots
    report["workers"] = []
    report["assignments"] = []
    worker_source = report["cards"][1]["facts"]
    for index in range(248):
        deed_uid = plots[index % len(plots)]["deed_uid"]
        asset_ref = f"synthetic-worker-{index:04d}"
        worker = copy.deepcopy(worker_source)
        worker.update({"asset_ref": asset_ref, "uid": asset_ref, "land_base_pp": str(100 + (index % 100) * 10), "total_harvest_pp": str(200 + (index % 100) * 20), "is_powered": index % 3 != 0})
        report["workers"].append(worker)
        report["assignments"].append({"asset_ref": asset_ref, "deed_uid": deed_uid, "slot": (index % 5) + 1, "confidence": "confident", "asset_kind": "worker_card"})
    cards = []
    for index in range(7776):
        card = copy.deepcopy(report["cards"][index % len(report["cards"])])
        card["asset_ref"] = f"synthetic-card-{index:05d}"
        card["facts"]["land_base_pp"] = str(100 + (index % 1000) * 10)
        card["facts"]["total_harvest_pp"] = str(200 + (index % 1000) * 20)
        confidence = "blocked" if index % 19 == 0 else "uncertain" if index % 7 == 0 else "confident"
        card["classification"] = {"confidence": confidence, "reason": "synthetic fixture candidate"}
        cards.append(card)
    report["cards"] = cards
    return report


class LandPhase2Tests(unittest.TestCase):
    def test_estimator_replays_every_real_fixture_card(self):
        cards = fixture("assets_active_above_cap.json")["data"]["cards"] + fixture("assets_active_single_worker.json")["data"]["cards"] + fixture("assets_active_with_runi.json")["data"]["cards"]
        estimates = replay_estimator(cards)
        self.assertTrue(all(row["estimate_class"] == "validated_model" for row in estimates))
        for card, estimate in zip(cards, estimates):
            self.assertAlmostEqual(estimate["total_construction_pp"], float(card["total_construction_pp"]), places=2)
            self.assertAlmostEqual(estimate["total_harvest_pp"], float(card["total_harvest_pp"]), places=2)
            self.assertAlmostEqual(estimate["work_per_hour"], float(card["total_harvest_pp"]) / 2000, places=4)
        mismatches = [card["uid"] for card, estimate in zip(cards, estimates) if not estimate["work_per_hour_observation_matches_formula"]]
        self.assertEqual(mismatches, ["4C87D3EC16C4A063", "E750D28F90", "A67D024E24", "3FA727626EDF19", "14F4667FBB6EF36", "F751235274E8FAD2", "40219A8F7B0C849"])

    def test_estimator_preserves_the_two_stage_formula(self):
        card = fixture("assets_active_above_cap.json")["data"]["cards"][0]
        estimate = estimate_card_pp(card)
        terrain_pp = float(card["base_pp_after_cap"]) * float(card["terrain_boost"])
        boost_pp = float(card["total_construction_pp"]) * sum(float(card[name]) for name in ("deed_rarity_boost", "deed_status_token_boost", "runi_boost", "title_boost", "totem_boost", "card_abilities_boost", "card_bloodlines_boost"))
        self.assertAlmostEqual(estimate["total_construction_pp"], float(card["base_pp_after_cap"]) + terrain_pp, places=2)
        self.assertAlmostEqual(estimate["total_harvest_pp"], float(card["total_construction_pp"]) + boost_pp, places=2)

    def test_above_cap_recommendations_have_clipped_point_delta(self):
        report = recommend_land(fixture_report())
        above_plots = {"41-2-27", "41-9-56"}
        touched = [row for row in report["recommendations"] if row["proposal"]["target_plot"] in above_plots or row["proposal"].get("source_plot") in above_plots]
        self.assertTrue(touched)
        self.assertTrue(all(row["delta"]["value"] is not None for row in touched))
        self.assertTrue(all(row["delta"]["low"] is not None for row in touched))

    def test_selected_recommendations_do_not_double_use_cards_or_slots(self):
        report = recommend_land(fixture_report())
        cards = [row["proposal"]["asset_ref"] for row in report["recommendations"]]
        self.assertEqual(len(cards), len(set(cards)))
        slots = []
        for row in report["recommendations"]:
            proposal = row["proposal"]
            slots.append((proposal["target_plot"], proposal["slot"]))
            if proposal.get("source_plot") and row["action"] == "move":
                source = row["current"].get("source") or {}
                slots.append((proposal["source_plot"], source.get("slot")))
        self.assertEqual(len(slots), len(set(slots)))

    def test_every_recommendation_has_appendix_a_explanation(self):
        required = {"before", "after", "current", "proposal", "delta", "assumptions", "conflicts", "opportunity_cost", "confidence", "estimate_class", "read_only_notice"}
        for row in recommend_land(fixture_report())["recommendations"]:
            self.assertTrue(required.issubset(row))
            self.assertIsInstance(row["assumptions"], list)
            self.assertIsInstance(row["conflicts"], list)
            self.assertIn("value", row["delta"])
            self.assertIn("reason", row["opportunity_cost"])
            self.assertIn("grade", row["confidence"])
            self.assertIn("unverified", row["proposal"]["eligibility"])

    def test_opportunity_costs_are_observed_or_explicitly_labelled(self):
        result = recommend_land(fixture_report())
        replacements = [row for row in result["recommendations"] if row["action"] == "replace"]
        self.assertTrue(replacements)
        for row in replacements:
            self.assertIsNotNone(row["opportunity_cost"]["value"])
            self.assertEqual(row["opportunity_cost"]["reason"], "observed displaced worker production")
        additions = [row for row in result["recommendations"] if row["action"] == "add"]
        self.assertTrue(additions)
        self.assertTrue(all(row["opportunity_cost"] == {"source_plot": None, "value": 0.0, "unit": "harvest_pp", "reason": "none (card unassigned)"} for row in additions))
        table = render_land_analysis(result, "table", view="recommend", show_all=True)
        csv = render_land_analysis(result, "csv", view="recommend")
        payload = json.loads(render_land_analysis(result, "json", view="recommend"))
        self.assertIn("none (card unassigned)", table)
        self.assertIn("opportunity_cost_reason", csv.splitlines()[0])
        self.assertIn("7350.000", csv)
        # Contract: recommendations are ranked output, so their list position is not an
        # identity. Consumers locate the replacement by its action and target plot.
        replacement = next(
            row for row in payload["recommendations"]
            if row["action"] == "replace" and row["proposal"]["target_plot"] == "41-2-27"
        )
        self.assertEqual(replacement["opportunity_cost"]["value"], "7350.000")

    def test_full_scale_pruning_diagnosis_accounts_for_every_stage(self):
        result = recommend_land(full_scale_synthetic_report())
        summary = result["recommendation_summary"]
        diagnosis = summary["pruning_diagnosis"]
        self.assertEqual(diagnosis["input_cards"], 7776)
        self.assertEqual(diagnosis["retained_cards"], 256)
        self.assertGreater(diagnosis["retention_pruned_cards"], 0)
        self.assertGreater(diagnosis["action_opportunities_pruned_by_per_plot_ceiling"], 0)
        self.assertFalse(summary["candidate_evaluation_ceiling_hit"])
        self.assertEqual(diagnosis["dominance_rules_pruned_candidates"], 0)
        self.assertGreater(diagnosis["conflict_graph_edges"], 0)
        self.assertIn("global evaluation ceiling is not binding", diagnosis["conclusion"])

    def test_blocked_cards_are_never_recommended_and_uncertain_grades_are_visible(self):
        source = fixture_report()
        blocked = {row["asset_ref"] for row in source["cards"] if row["classification"]["confidence"] == "blocked"}
        result = recommend_land(source)
        recommended = {row["proposal"]["asset_ref"] for row in result["recommendations"]}
        self.assertTrue(blocked.isdisjoint(recommended))
        uncertain = {row["asset_ref"] for row in source["cards"] if row["classification"]["confidence"] == "uncertain"}
        for row in result["recommendations"]:
            if row["proposal"]["asset_ref"] in uncertain:
                self.assertEqual(row["confidence_grade"], "uncertain")
                self.assertTrue(row["confidence_reason"])

    def test_search_discloses_pruning_and_idle_worklist_is_separate(self):
        result = recommend_land(fixture_report())
        search = result["recommendation_summary"]["search"]
        self.assertGreater(search["top_k_pruned_candidates"], 0)
        self.assertEqual(search["optimality"], "good bounded result, not proven optimal")
        self.assertGreater(result["recommendation_summary"]["idle_plot_worklist_count"], 0)
        self.assertNotIn("idle_multiplier", json.dumps(result["recommendations"]))

    def test_full_scale_recommendation_completes_within_60_seconds(self):
        report = full_scale_synthetic_report()
        started = time.perf_counter()
        result = recommend_land(report)
        elapsed = time.perf_counter() - started
        self.assertEqual((len(report["plots"]), len(report["assignments"]), len(report["cards"])), (161, 248, 7776))
        self.assertLess(elapsed, 60.0, f"full-scale recommendation took {elapsed:.3f}s")
        search = result["recommendation_summary"]
        self.assertEqual(search["global_candidate_pool"]["retained_cards"], 256)
        self.assertGreater(search["global_candidate_pool"]["pruned_cards"], 0)
        self.assertLessEqual(search["candidate_evaluations"], 161 * 24)
        self.assertIn("optimality", search["search"])

    def test_runi_display_and_diagnostic_plot_truncation_are_explicit(self):
        report = fixture_report()
        plots = render_land_analysis(report, "table", view="plots", limit=11)
        self.assertIn("5/5+R", plots)
        expanded = copy.deepcopy(report)
        expanded["findings"] = [{"type": "many_plots", "deed_uid": row["deed_uid"], "message": "fixture"} for row in expanded["plots"]]
        diagnostics = render_land_analysis(expanded, "table", view="diagnostics", limit=8)
        self.assertIn("true count 11", diagnostics)
        self.assertIn("use --all or JSON", diagnostics)
        self.assertNotIn("true count 11", render_land_analysis(expanded, "table", view="diagnostics", limit=8, show_all=True))
        self.assertEqual(len(json.loads(render_land_analysis(expanded, "json", view="diagnostics", limit=25))["diagnostic_groups"][-1]["plots"]), 11)

    def test_recommend_cli_uses_snapshot_and_supports_csv(self):
        # no-card-abilities: this test is about CSV/table rendering, not 's default-on
        # side-effect pricing (covered separately below); the flag keeps it network-free.
        report = fixture_report()
        with patch.object(cli, "load_snapshot", return_value=report), patch.object(cli, "_require_praetoria_storage_path"):
            stream = io.StringIO()
            with redirect_stdout(stream):
                self.assertEqual(cli.main(["land", "recommend", "--load-snapshot", "fixture.json", "--limit", "2", "--no-card-abilities"]), 0)
            self.assertIn("Recommendations", stream.getvalue())
            self.assertIn("showing 2 of", stream.getvalue())
            stream = io.StringIO()
            with redirect_stdout(stream):
                self.assertEqual(cli.main(["land", "recommend", "--load-snapshot", "fixture.json", "--csv", "--all", "--no-card-abilities"]), 0)
            self.assertIn("confidence", stream.getvalue().splitlines()[0])

    def test_recommend_cli_emits_header_before_computation(self):
        report = fixture_report()
        observed = []
        stream = io.StringIO()

        def delayed_recommendation(source, **kwargs):
            observed.append(stream.getvalue())
            return recommend_land(source)

        with patch.object(cli, "load_snapshot", return_value=report), patch.object(cli, "recommend_land", side_effect=delayed_recommendation), patch.object(cli, "_require_praetoria_storage_path"):
            with redirect_stdout(stream):
                self.assertEqual(cli.main(["land", "recommend", "--load-snapshot", "fixture.json", "--limit", "2", "--no-card-abilities"]), 0)
        self.assertTrue(observed)
        self.assertIn("Coverage:", observed[0])
        self.assertIn("Deeds:", observed[0])

    def test_recommend_cli_defaults_to_pricing_card_ability_side_effects(self):
        """ flipped default: `land recommend` prices plot-wide land-ability/bloodline side
 effects UNLESS `--no-card-abilities` is passed. Reuses the Tireless-Farmhand-shaped fixture
 from `test_land_recommend.py` (a net-negative eviction that only looks positive
 without side-effect pricing) end-to-end through the CLI, with the card_details fetch
 mocked so the test stays network-free."""
        from tests.test_land_recommend import DETAILS, _report_with_farmhand_plot

        report = _report_with_farmhand_plot()
        with patch.object(cli, "load_snapshot", return_value=report), patch.object(cli, "_require_praetoria_storage_path"), \
                patch.object(cli.JsonCache, "get_or_fetch", return_value=(list(DETAILS.values()), None)):
            stream = io.StringIO()
            err = io.StringIO()
            with redirect_stdout(stream), redirect_stderr(err):
                self.assertEqual(cli.main(["land", "recommend", "--load-snapshot", "fixture.json", "--all", "--json"]), 0)
            payload = json.loads(stream.getvalue())
        recommended_asset_refs = {row["proposal"]["asset_ref"] for row in payload["recommendations"]}
        self.assertNotIn("candidate-1", recommended_asset_refs)

    def test_no_card_abilities_flag_opts_out_and_leaves_side_effects_unevaluated(self):
        """The escape hatch: with `--no-card-abilities`, the same net-negative candidate the
 default path excludes is still gross-ranked positive and labelled unevaluated -- proving
 the flag genuinely disables pricing rather than merely renaming a dead option."""
        from tests.test_land_recommend import _report_with_farmhand_plot

        report = _report_with_farmhand_plot()
        with patch.object(cli, "load_snapshot", return_value=report), patch.object(cli, "_require_praetoria_storage_path"):
            stream = io.StringIO()
            with redirect_stdout(stream):
                self.assertEqual(cli.main(["land", "recommend", "--load-snapshot", "fixture.json", "--all", "--json", "--no-card-abilities"]), 0)
            payload = json.loads(stream.getvalue())
        replace_rows = [row for row in payload["recommendations"] if row["action"] == "replace"]
        self.assertTrue(replace_rows)
        self.assertFalse(replace_rows[0]["side_effects_evaluated"])


if __name__ == "__main__":
    unittest.main()
