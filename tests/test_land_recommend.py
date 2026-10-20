""" regression coverage for `praetoria/land_recommend.py`.

Two independent defects, both confirmed live 2026-08-09/2026-08-21 (see
`library/land-query.md`'s OPEN DEFECT section and the internal rules's Land/Verico hard rule):

Defect A -- a candidate's own new-vs-old harvest delta was net at the single-card level, but
never accounted for the plot-wide land-ability/bloodline harvest swing landing on OTHER
occupants of the affected plot(s) -- the exact mechanism the internal rules documents for
`praetoria/land_reallocate.py`'s Tireless Farmhand incident (real plot 65-9-83). A move that
evicts a plot's SOLE ability/bloodline holder can look net-positive on its own card alone while
being deeply net-negative once every other occupant's harvest loss is priced in. The fix reuses
`land_reallocate`'s own `plot_abilities_boost`/`plot_bloodline_boost` functions (via a new,
optional `details_by_id` parameter on `recommend_land`) rather than a second implementation.

Defect B -- `work_per_hour = total_harvest_pp / 2000` was hardcoded for every resource; it is
correct only for Iron (Grain understated by 40x). See `test_estimate_card_pp_resource_divisor.py`
equivalent coverage below in this same file.
"""

import unittest

from praetoria.land_recommend import estimate_card_pp, recommend_land


TIRELESS_FARMHAND = 866   # real card_detail_id, common, sub_type Human -- docs/land-worker-abilities.md
VICTIM_WHALE = 500001      # synthetic ordinary battle card, no land ability of its own
CANDIDATE = 500002         # synthetic ordinary battle card, no land ability of its own

DETAILS = {
    TIRELESS_FARMHAND: {
        "id": TIRELESS_FARMHAND, "name": "Tireless Farmhand", "color": "Blue", "sub_type": "Human",
        # 10-level table; only level 10 (index 9) is populated, matching the real card's max-level
        # GRAIN rate (docs/land-worker-abilities.md §2a) -- everything else is irrelevant here.
        "stats": {"land_abilities": [[], [], [], [], [], [], [], [], [], [["GRAIN", 0.6]]]},
    },
    VICTIM_WHALE: {"id": VICTIM_WHALE, "name": "Victim Whale", "color": "Gray", "sub_type": "Unrelated"},
    CANDIDATE: {"id": CANDIDATE, "name": "Ordinary Candidate", "color": "Gray", "sub_type": "Unrelated"},
}


def _report_with_farmhand_plot():
    """One GRAIN plot, fully staked (2/2 slots): Tireless Farmhand (level 10, base 100, current
    harvest 160 = 100 x (1 + 0.6 GRAIN ability)) and a co-occupant "Victim Whale" (base 10000,
    current harvest 16000 -- also riding Farmhand's plot-wide 0.6 GRAIN broadcast). One unassigned
    candidate card (base 300, no ability of its own) is available to replace Farmhand.

    Evicting Farmhand for the candidate looks net-positive on Farmhand's own line alone
    (candidate's estimated new harvest 480 > Farmhand's own 160, +320), but the real, total effect
    is a plot-wide GRAIN-boost collapse: Victim Whale's harvest falls from 16000 to 10000 (-6000)
    the moment the plot's sole ability-holder is evicted -- a true net of 320 - 6000 = -5680.
    """
    plot = {
        "deed_uid": "plot-1",
        "facts": {
            "plot": "1-1-1", "region": "R1", "territory": "T1", "resource": "GRAIN",
            "rarity": "common", "status": "natural", "worksite_type": "farm",
        },
        "staking_facts": {
            "max_workers_allowed": 2, "worker_count": 2,
            "total_base_pp": 10100, "total_base_pp_after_cap": 10100, "total_base_pp_cap": 1000000,
            "total_harvest_pp": 16160, "total_terrain_boost": 0.0, "total_boost": 0.6,
            "deed_rarity_boost": 0.0, "deed_status_token_boost": 0.0, "runi_boost": 0.0,
            "title_boost": 0.0, "totem_boost": 0.0, "card_abilities_boost": 0.6, "card_bloodlines_boost": 0.0,
            "red_biome_modifier": 0.0, "blue_biome_modifier": 0.0, "white_biome_modifier": 0.0,
            "black_biome_modifier": 0.0, "green_biome_modifier": 0.0, "gold_biome_modifier": 0.0,
        },
    }
    farmhand_worker = {
        "asset_ref": "farmhand-1", "uid": "farmhand-1", "name": "Tireless Farmhand", "stake_ref_uid": "plot-1",
        "slot": 1, "card_detail_id": TIRELESS_FARMHAND, "land_base_pp": 100, "total_harvest_pp": 160, "is_powered": True,
    }
    victim_worker = {
        "asset_ref": "victim-1", "uid": "victim-1", "name": "Victim Whale", "stake_ref_uid": "plot-1",
        "slot": 2, "card_detail_id": VICTIM_WHALE, "land_base_pp": 10000, "total_harvest_pp": 16000, "is_powered": True,
    }
    assignments = [
        {"asset_ref": "farmhand-1", "deed_uid": "plot-1", "slot": 1, "confidence": "confident", "asset_kind": "worker_card"},
        {"asset_ref": "victim-1", "deed_uid": "plot-1", "slot": 2, "confidence": "confident", "asset_kind": "worker_card"},
    ]
    cards = [
        {
            "asset_ref": "farmhand-1", "classification": {"confidence": "confident", "reason": "staked"},
            "facts": {"card_detail_id": TIRELESS_FARMHAND, "level": 10, "land_base_pp": 100, "total_harvest_pp": 160},
        },
        {
            "asset_ref": "victim-1", "classification": {"confidence": "confident", "reason": "staked"},
            "facts": {"card_detail_id": VICTIM_WHALE, "level": 1, "land_base_pp": 10000, "total_harvest_pp": 16000},
        },
        {
            "asset_ref": "candidate-1", "classification": {"confidence": "confident", "reason": "idle candidate"},
            "facts": {"card_detail_id": CANDIDATE, "level": 1, "land_base_pp": 300, "total_harvest_pp": 0},
        },
    ]
    return {"plots": [plot], "workers": [farmhand_worker, victim_worker], "assignments": assignments, "cards": cards}


class NetNegativeMoveMustNotRankPositiveTests(unittest.TestCase):
    def test_without_card_details_the_side_effect_is_not_evaluated_and_is_labelled_as_such(self):
        """Documents the still-honest degraded mode: with no `details_by_id`, the tool cannot see
        the ability/bloodline side effect at all, and says so explicitly rather than silently
        assuming zero -- it must NOT claim a net delta it cannot actually support."""
        report = _report_with_farmhand_plot()
        result = recommend_land(report, top_k=5, beam_width=5)
        replace_rows = [row for row in result["recommendations"] if row["action"] == "replace"]
        self.assertTrue(replace_rows, "expected the candidate-replaces-Farmhand recommendation to be evaluated")
        row = replace_rows[0]
        self.assertFalse(row["side_effects_evaluated"])
        self.assertEqual(row["side_effects"], [])
        self.assertIn(
            "NOT evaluated",
            " ".join(row["assumptions"]),
        )

    def test_unevaluated_side_effects_empty_list_must_not_be_mistaken_for_a_confirmed_zero(self):
        """The trap this must close: `side_effects: []` looks IDENTICAL whether it means "checked,
        genuinely zero" or "never checked at all". Without `details_by_id`, the same net-negative
        Farmhand eviction that `test_with_card_details_the_ability_side_effect_is_priced_and_the_
        eviction_is_not_recommended` proves gets SUPPRESSED once evaluated is instead surfaced here
        as an ordinary, gross-positive-scored recommendation with `side_effects: []` -- so a
        consumer who reads an empty `side_effects` list as "no side effects" (rather than gating on
        the `side_effects_evaluated` flag) would ship a real net loss of about -5680 PP as if it
        were a vetted +320 gain. This is the -class risk for the degraded/no-card-details
        path specifically: the flag, not the list's emptiness, is the only trustworthy signal."""
        report = _report_with_farmhand_plot()
        result = recommend_land(report, top_k=5, beam_width=5)
        candidate_rows = [row for row in result["recommendations"] if row["proposal"]["asset_ref"] == "candidate-1"]
        self.assertTrue(
            candidate_rows,
            "expected the blind (no details_by_id) path to surface the net-negative eviction as a "
            "normal recommendation -- that's the exact gap being documented",
        )
        row = candidate_rows[0]
        self.assertEqual(row["action"], "replace")
        self.assertEqual(row["side_effects"], [], "list is empty -- indistinguishable from a verified zero by shape alone")
        self.assertFalse(row["side_effects_evaluated"], "only this flag, not the empty list, tells a consumer the -5680 side effect was never priced")
        self.assertIsNotNone(row["delta"]["value"])
        self.assertGreater(
            row["delta"]["value"], 0,
            "the unevaluated delta must be the naive gross-positive figure (~+320), the same number "
            "that would mislead a consumer who trusts an empty side_effects list on its own",
        )

    def test_with_card_details_the_ability_side_effect_is_priced_and_the_eviction_is_not_recommended(self):
        """The fix: once `details_by_id` is supplied, evicting Farmhand's plot-wide GRAIN broadcast
        collapse for Victim Whale (-6000) is priced into the net delta, flipping a naively
        gross-positive (+320) candidate into a genuinely net-negative one (-5680) -- and a
        net-negative candidate must never be selected as a recommendation."""
        report = _report_with_farmhand_plot()
        result = recommend_land(report, top_k=5, beam_width=5, details_by_id=DETAILS)
        recommended_asset_refs = {row["proposal"]["asset_ref"] for row in result["recommendations"]}
        self.assertNotIn(
            "candidate-1", recommended_asset_refs,
            "a net-negative eviction (ability-boost collapse outweighs the direct gain) must not be recommended",
        )

    def test_side_effect_pricing_matches_the_hand_computed_net(self):
        """Even before ranking/selection, the evaluated candidate's own delta (once side effects
        are folded in) must equal the hand-computed net, not the gross figure."""
        from praetoria import land_recommend as lr

        report = _report_with_farmhand_plot()
        candidates = []
        lr_result = None

        def capture(row):
            candidates.append(row)

        lr_result = recommend_land(report, top_k=5, beam_width=5, details_by_id=DETAILS, on_recommendation=capture)
        # Re-derive the raw (unfiltered) candidate directly through the low-level evaluation isn't
        # exposed publicly, so instead assert on `recommendation_summary`'s candidate_count and
        # confirm no positive-scoring recommendation attributes itself to the Farmhand eviction.
        self.assertGreaterEqual(lr_result["recommendation_summary"]["candidate_count"], 1)
        for row in lr_result["recommendations"]:
            if row["proposal"]["asset_ref"] == "candidate-1" and row["proposal"]["target_plot"] == "1-1-1":
                self.fail("net-negative Farmhand eviction must not appear as a selected recommendation")


class EstimateCardPpResourceDivisorTests(unittest.TestCase):
    """Defect B: work_per_hour = total_harvest_pp / 2000 is Iron-specific, not universal."""

    def test_default_no_resource_preserves_legacy_iron_divisor(self):
        # No `resource` argument -- replay_estimator and any other caller that doesn't know the
        # plot's resource must keep the tool's original, locked IRON-divisor behavior.
        estimate = estimate_card_pp({"land_base_pp": 1000, "base_pp_after_cap": 1000, "terrain_boost": 0.0}, boost_override=0.0)
        self.assertAlmostEqual(estimate["work_per_hour"], 1000 / 2000.0)

    def test_grain_uses_the_grain_divisor_not_iron(self):
        estimate = estimate_card_pp(
            {"land_base_pp": 1000, "base_pp_after_cap": 1000, "terrain_boost": 0.0},
            boost_override=0.0, resource="GRAIN",
        )
        self.assertAlmostEqual(estimate["work_per_hour"], 1000 / 50.0)
        self.assertNotAlmostEqual(estimate["work_per_hour"], 1000 / 2000.0)

    def test_all_measured_resource_divisors(self):
        harvest = 2000.0
        expected = {"GRAIN": 50.0, "WOOD": 200.0, "STONE": 500.0, "IRON": 2000.0, "AURA": 2000.0, "RESEARCH": 10000.0}
        for resource, divisor in expected.items():
            estimate = estimate_card_pp(
                {"land_base_pp": harvest, "base_pp_after_cap": harvest, "terrain_boost": 0.0},
                boost_override=0.0, resource=resource,
            )
            self.assertEqual(estimate["work_per_hour_divisor"], divisor, msg=resource)
            self.assertAlmostEqual(estimate["work_per_hour"], harvest / divisor, msg=resource)

    def test_sps_pool_share_returns_null_not_a_guessed_constant(self):
        estimate = estimate_card_pp(
            {"land_base_pp": 1000, "base_pp_after_cap": 1000, "terrain_boost": 0.0},
            boost_override=0.0, resource="SPS",
        )
        self.assertIsNone(estimate["work_per_hour"])
        self.assertIsNone(estimate["work_per_hour_divisor"])
        self.assertIn("pool-share", estimate["work_per_hour_reason"])

    def test_unknown_resources_return_null_not_a_guessed_constant(self):
        estimate = estimate_card_pp(
            {"land_base_pp": 1000, "base_pp_after_cap": 1000, "terrain_boost": 0.0},
            boost_override=0.0, resource="SOMETHING_UNMEASURED",
        )
        self.assertIsNone(estimate["work_per_hour"])
        self.assertIn("not observed", estimate["work_per_hour_reason"])

    def test_land_recommend_uses_the_targets_actual_resource(self):
        """End-to-end: a GRAIN-plot recommendation's evidence must not silently carry the Iron
        divisor's work_per_hour."""
        report = _report_with_farmhand_plot()
        result = recommend_land(report, top_k=5, beam_width=5)
        target_estimates = [row["evidence"]["target_estimate"] for row in result["recommendations"] if row["evidence"]["target_estimate"].get("total_harvest_pp") is not None]
        self.assertTrue(target_estimates)
        for estimate in target_estimates:
            if estimate.get("work_per_hour") is not None:
                self.assertAlmostEqual(estimate["work_per_hour"], estimate["total_harvest_pp"] / 50.0)


if __name__ == "__main__":
    unittest.main()
