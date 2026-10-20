"""Land/Verico card-ability and Bloodline ("Toil and Kin") coverage for `land_reallocate.py`,
fixing the defect flagged in the internal rules's Land/Verico hard rule (2026-08-19): the planner ignored
`card_abilities_boost` and `card_bloodlines_boost` entirely when evaluating a candidate move,
which could recommend evicting a Land/Verico worker a generic replacement card could never match.

Mechanism reference: docs/land-worker-abilities.md. One correction made DURING this fix, re-verified
live against `player0005`'s full account (238 staked workers / 107 plots, 0 exceptions both ways):
`card_abilities_boost` is -- like `card_bloodlines_boost` -- a PLOT-WIDE broadcast (the max
resource-matching table rate among the plot's ability-holders, applied to every occupant), not a
private per-card contribution as the doc's mechanism section and the internal rules's hard rule describe.
See `plot_abilities_boost`'s docstring in `praetoria/land_reallocate.py` for the live re-verification.
"""

import unittest

from praetoria.land_reallocate import (
    MovableCard, Plot, ReallocationCriteria, _harvest, _plot_roster, bloodline_holder,
    card_abilities_boost, plan_reallocation, plot_abilities_boost, plot_bloodline_boost,
)


# Synthetic card-detail universe. Real card_detail_ids are used for the two Land-set cards this
# bug was discovered against (866 Tireless Farmhand, 868 Toiling Ox) plus a Verico common (987 Xo
# Zhang Drover) so `is_land_or_verico()` classifies them correctly without a stub; everything else
# is an ordinary battle card (an id well outside both the edition-19 and Verico ranges) with no
# `land_abilities` table at all, matching a real `/cards/get_details` row for a non-Land card.

TIRELESS_FARMHAND = 866   # common, sub_type Human -- real per-level table, docs/land-worker-abilities.md §2a
TOILING_OX = 868           # epic, sub_type Mundane Beast
XO_ZHANG_DROVER = 987      # Verico common, sub_type Llamataur

ORDINARY_A = 40001          # ordinary battle card, sub_type Mundane Beast (companion, no ability of its own)
ORDINARY_B = 40002          # ordinary battle card, sub_type Llamataur (companion, no ability of its own)
ORDINARY_C = 40003          # ordinary battle card, unrelated sub_type, never a companion

DETAILS = {
    TIRELESS_FARMHAND: {
        "id": TIRELESS_FARMHAND, "name": "Tireless Farmhand", "color": "Blue", "sub_type": "Human",
        "stats": {"land_abilities": [
            [["GRAIN", 0.01]],                                            # level 1
            [], [], [], [], [], [], [], [],
            [["GRAIN", 0.6], ["ENERGIZED"], ["RATIONING", -0.3], ["DD", -0.5], ["LL"]],  # level 10 (max)
        ]},
    },
    TOILING_OX: {
        "id": TOILING_OX, "name": "Toiling Ox", "color": "White", "sub_type": "Mundane Beast",
        "stats": {"land_abilities": [
            [["DD", -0.15], ["BLOODLINE", 0.05, "Mundane Beast"], ["GRAIN", 0.05]],  # level 1
            [], [], [], [],
            [["DD", -0.8], ["BLOODLINE", 0.25, "Mundane Beast"], ["GRAIN", 0.8], ["STONE", 0.85], ["LL"]],  # level 6 (max)
        ]},
    },
    XO_ZHANG_DROVER: {
        "id": XO_ZHANG_DROVER, "name": "Xo Zhang Drover", "color": "Red", "sub_type": "Llamataur",
        "stats": {"land_abilities": [
            [["BLOODLINE", 0.01, "Llamataur"]],  # level 1
            [], [],
            [["BLOODLINE", 0.03, "Llamataur"], ["RATIONING_LITE", -0.03], ["ALTERATION"]],  # level 4
        ]},
    },
    # No "stats" key at all -- like a real ordinary battle card's /cards/get_details row.
    ORDINARY_A: {"id": ORDINARY_A, "name": "Furious Chicken", "color": "Gray", "sub_type": "Mundane Beast"},
    ORDINARY_B: {"id": ORDINARY_B, "name": "Second Llamataur Companion", "color": "Gray", "sub_type": "Llamataur"},
    ORDINARY_C: {"id": ORDINARY_C, "name": "Generic Whale", "color": "Gray", "sub_type": "Unrelated"},
}


def roster(*entries):
    """entries: (card_detail_id, level) pairs."""
    return [{"card_detail_id": cdid, "level": level} for cdid, level in entries]


class CardAbilitiesBoostTests(unittest.TestCase):
    def test_zero_when_no_entry_matches_plot_resource(self):
        # Toiling Ox level 6 has no IRON entry.
        self.assertEqual(card_abilities_boost(TOILING_OX, 6, "IRON", DETAILS), 0.0)

    def test_nonzero_matches_exact_table_value(self):
        self.assertAlmostEqual(card_abilities_boost(TOILING_OX, 6, "STONE", DETAILS), 0.85)
        self.assertAlmostEqual(card_abilities_boost(TIRELESS_FARMHAND, 10, "GRAIN", DETAILS), 0.6)

    def test_ordinary_battle_card_always_zero(self):
        self.assertEqual(card_abilities_boost(ORDINARY_A, 10, "GRAIN", DETAILS), 0.0)
        self.assertEqual(card_abilities_boost(ORDINARY_A, None, "GRAIN", DETAILS), 0.0)


class PlotAbilitiesBoostTests(unittest.TestCase):
    def test_plot_wide_broadcast_matches_the_holders_own_rate(self):
        # Corrected model: an ordinary companion on the SAME plot as the ability-holder receives
        # the identical broadcast value too -- confirmed live (Molten Ogre example, doc §3.3; and
        # the 0-mismatch card_abilities_boost re-verification during this fix).
        r = roster((TOILING_OX, 6), (ORDINARY_A, None), (ORDINARY_C, None))
        self.assertAlmostEqual(plot_abilities_boost(r, "STONE", DETAILS), 0.85)

    def test_zero_when_no_ability_holder_on_plot(self):
        r = roster((ORDINARY_A, None), (ORDINARY_B, None))
        self.assertEqual(plot_abilities_boost(r, "GRAIN", DETAILS), 0.0)

    def test_wrong_resource_still_zero_even_with_a_holder_present(self):
        r = roster((TOILING_OX, 6), (ORDINARY_A, None))
        self.assertEqual(plot_abilities_boost(r, "IRON", DETAILS), 0.0)


class BloodlineHolderTests(unittest.TestCase):
    def test_land_verico_card_is_a_holder(self):
        holder = bloodline_holder(TOILING_OX, 6, DETAILS)
        self.assertIsNotNone(holder)
        rate, sub_type = holder
        self.assertAlmostEqual(rate, 0.25)
        self.assertEqual(sub_type, "Mundane Beast")

    def test_ordinary_battle_card_never_a_holder(self):
        self.assertIsNone(bloodline_holder(ORDINARY_A, 10, DETAILS))


class PlotBloodlineBoostTests(unittest.TestCase):
    def test_solo_holder_no_companion_is_exactly_zero(self):
        r = roster((TOILING_OX, 6))
        self.assertEqual(plot_bloodline_boost(r, DETAILS), 0.0)

    def test_solo_holder_with_unrelated_companion_is_still_zero(self):
        r = roster((TOILING_OX, 6), (ORDINARY_C, None))
        self.assertEqual(plot_bloodline_boost(r, DETAILS), 0.0)

    def test_ordinary_companion_triggers_the_holders_bloodline(self):
        r = roster((TOILING_OX, 6), (ORDINARY_A, None))
        self.assertAlmostEqual(plot_bloodline_boost(r, DETAILS), 0.25)

    def test_two_distinct_bloodlines_do_not_stack_largest_applies(self):
        # Toil and Kin does not stack -- only the largest bonus applies.
        r = roster((TOILING_OX, 6), (ORDINARY_A, None), (XO_ZHANG_DROVER, 4), (ORDINARY_B, None))
        self.assertAlmostEqual(plot_bloodline_boost(r, DETAILS), 0.25)

    def test_same_bloodline_twice_does_not_double(self):
        # Two Mundane Beast companions for the same holder -- still a single 0.25, not 0.50.
        r = roster((TOILING_OX, 6), (ORDINARY_A, None), (ORDINARY_A, None))
        self.assertAlmostEqual(plot_bloodline_boost(r, DETAILS), 0.25)

    def test_two_holders_same_bloodline_still_caps_at_one_instance(self):
        # Two Toiling Oxen (contrived, but exercises the dedup-by-sub_type-not-by-holder rule)
        # sharing the same plot -- capped at the single (max) rate, never summed.
        r = roster((TOILING_OX, 6), (TOILING_OX, 6))
        self.assertAlmostEqual(plot_bloodline_boost(r, DETAILS), 0.25)

    def test_applies_uniformly_to_every_card_via_recompute(self):
        # The plot-wide value from plot_bloodline_boost is what `_harvest` uses for every card on
        # the plot, not just the holder -- proven directly via the harvest formula.
        plot_mult, terrain = 1.0, 0.0
        r = roster((TOILING_OX, 6), (ORDINARY_A, None))
        bl = plot_bloodline_boost(r, DETAILS)
        ab = plot_abilities_boost(r, None, DETAILS)
        holder_harvest = _harvest(2500.0, terrain, plot_mult, ab, bl)
        companion_harvest = _harvest(1000.0, terrain, plot_mult, ab, bl)
        self.assertAlmostEqual(holder_harvest, 2500.0 * (1 + bl))
        self.assertAlmostEqual(companion_harvest, 1000.0 * (1 + bl))


# End-to-end: a small synthetic plot roster reproducing the shape of the real bug (Tireless
# Farmhand / Toiling Ox on legendary plots getting evaluated as if they had no land abilities at
# all), built directly against `plan_reallocation`'s public `report`/`details_by_id` contract.

def _plot_row(deed_uid: str, plot_code: str, rarity: str, resource: str, *, mult_facts: dict, cap: float = 100000.0, max_workers: int = 5) -> dict:
    return {
        "deed_uid": deed_uid,
        "facts": {"plot": plot_code, "rarity": rarity, "status": "magical", "resource": resource, "territory": "T"},
        "staking_facts": {
            "max_workers_allowed": max_workers, "total_base_pp_cap": cap,
            "red_biome_modifier": 0, "blue_biome_modifier": 0, "white_biome_modifier": 0,
            "black_biome_modifier": 0, "green_biome_modifier": 0, "gold_biome_modifier": 0,
            **mult_facts,
        },
    }


def _worker_row(name: str, card_detail_id: int, base_pp: float, stake_ref_uid: str, asset_ref: str, cur_hint: float = 0.0) -> dict:
    return {
        "name": name, "card_detail_id": card_detail_id, "land_base_pp": str(base_pp),
        "total_harvest_pp": str(cur_hint), "asset_ref": asset_ref, "stake_ref_uid": stake_ref_uid,
        "asset_kind": "worker_card",
    }


def _collection_row(card_detail_id: int, base_pp: float, level: int | None, asset_ref: str, *, bucket: str) -> dict:
    return {
        "asset_ref": asset_ref,
        "classification": {"bucket": bucket},
        "facts": {
            "card_detail_id": card_detail_id, "land_base_pp": base_pp, "level": level,
            "market_id": None, "delegated_to": None, "rental_type": None, "unlock_date": None,
        },
    }


def _idle_card_row(card_detail_id: int, base_pp: float, level: int | None, asset_ref: str) -> dict:
    """A genuinely idle candidate -- eligible to `gather_idle_cards`."""
    return _collection_row(card_detail_id, base_pp, level, asset_ref, bucket="unstaked_apparent")


def _level_lookup_row(card_detail_id: int, base_pp: float, level: int | None, asset_ref: str) -> dict:
    """The collection-row twin of an already-staked worker -- present only so `_level_by_asset_ref`
 can resolve its level (mirrors how `report["cards"]` covers every staked asset_ref too, see
 `_level_by_asset_ref`'s docstring). Bucket is `assigned_active`, NOT one of `IDLE_BUCKETS`, so
 `gather_idle_cards` correctly excludes it -- exactly like Praetoria's real classifier would for
 a card that's simultaneously present in `workers[]`."""
    return _collection_row(card_detail_id, base_pp, level, asset_ref, bucket="assigned_active")


MANTICORE = 107   # a co-occupant of the example plot
NEB_SENI = 78
DETAILS[MANTICORE] = {"id": MANTICORE, "name": "Manticore", "color": "Gold", "sub_type": "Chimeric Beast"}
DETAILS[NEB_SENI] = {"id": NEB_SENI, "name": "Neb Seni", "color": "Gold", "sub_type": "Human"}


class EndToEndAbilityAwareEvictionTests(unittest.TestCase):
    """Reproduces the real 2026-08-19 incident using Tireless Farmhand's ACTUAL plot (65-9-83,
 a sample deed) and its co-occupants Manticore/Neb Seni (base PP, colors,
 and multiplier facts all taken verbatim from `~/.praetoria/snapshots/player0005-2026-08-19.json` and
 cross-checked to reproduce the exact live `total_harvest_pp` values -- Manticore 160,050.0 and
 Neb Seni 213,400.0 -- confirming the fixture matches the real API's own math).

 The real bug was never really about Farmhand's OWN `cur` value (that's always been correct --
 it's the server's own observed field, untouched by the planner's `_harvest` bug). It's that
 Farmhand is the PLOT's sole `card_abilities_boost` holder (0.6, GRAIN) -- evicting Farmhand
 doesn't just cost Farmhand's modest 4,850pp, it silently strips the SAME 0.6 broadcast from
 every other occupant sharing that plot. The old code never modeled the boost as a plot-wide
 broadcast, so it never priced that collateral loss at all -- `net = gain - lost - card.cur`
 with no side-effect term. This reproduces that exact shape of mistake."""

    DEED_UID = "I-290-d05b84f1965811"
    PLOT_CODE = "65-9-83"
    MULT = 1.0 + 1.0 + 1.0 + 0.25 + 1.0   # deed_rarity(1) + totem(1) + title(0.25) + runi(1); Runi IS staked on this plot

    def _report(self, *, replacement_base_pp: float) -> dict:
        # Plot at capacity (3/3) so an incoming idle card forces a real eviction choice -- Farmhand
        # with by far the smallest base PP of the three, is always the min-cur eviction target
        # exactly as in the live incident.
        plot = _plot_row(
            self.DEED_UID, self.PLOT_CODE, "legendary", "GRAIN", max_workers=3, cap=1000000.0,
            mult_facts={
                "deed_rarity_boost": 1.0, "totem_boost": 1.0, "title_boost": 0.25, "runi_boost": 1.0, "deed_status_token_boost": 0.0,
                "gold_biome_modifier": 0.1, "blue_biome_modifier": -0.5,
            },
        )
        farmhand_cur = _harvest(2000.0, -0.5, self.MULT, 0.6, 0.0)     # Farmhand's own color (Blue) is bad terrain here
        manticore_cur = _harvest(30000.0, 0.1, self.MULT, 0.6, 0.0)    # matches the live snapshot exactly: 160,050.0
        nebseni_cur = _harvest(40000.0, 0.1, self.MULT, 0.6, 0.0)      # matches the live snapshot exactly: 213,400.0
        self.assertAlmostEqual(manticore_cur, 160050.0)
        self.assertAlmostEqual(nebseni_cur, 213400.0)
        farmhand = _worker_row("Tireless Farmhand", TIRELESS_FARMHAND, 2000.0, self.DEED_UID, "farmhand-ref", cur_hint=farmhand_cur)
        manticore = _worker_row("Manticore", MANTICORE, 30000.0, self.DEED_UID, "manticore-ref", cur_hint=manticore_cur)
        nebseni = _worker_row("Neb Seni", NEB_SENI, 40000.0, self.DEED_UID, "nebseni-ref", cur_hint=nebseni_cur)
        farmhand_card = _level_lookup_row(TIRELESS_FARMHAND, 2000.0, 10, "farmhand-ref")
        replacement = _idle_card_row(ORDINARY_C, replacement_base_pp, None, "whale-ref")
        return {"plots": [plot], "workers": [farmhand, manticore, nebseni], "cards": [farmhand_card, replacement]}

    def test_old_formula_would_have_wrongly_evicted_farmhand(self):
        # Faithful replay of the OLD, pre-fix `net = gain - lost - card.current_harvest` (no side
        # effects on Manticore/Neb Seni at all): a 10,000pp ordinary idle card nets a strongly
        # positive (wrongly accepted) swap under the old formula.
        old_mult_no_ability = self.MULT  # the old formula never added card_abilities_boost anywhere
        gain_old = 10000.0 * (1 + 0.0) * old_mult_no_ability
        lost_old = _harvest(2000.0, -0.5, self.MULT, 0.6, 0.0)  # Farmhand's real observed cur, 4,850.0
        net_old = gain_old - lost_old - 0.0
        self.assertGreater(net_old, 0, "reproduces the exact defect: the old formula saw this as a winning eviction")

    def test_new_formula_rejects_the_same_move_once_the_collateral_loss_is_priced(self):
        report = self._report(replacement_base_pp=10000.0)  # the same "winning" swap under the old formula
        criteria = ReallocationCriteria(
            target_plots=frozenset({self.PLOT_CODE}), source_rarities=frozenset(), include_idle=True,
            min_card_pp=0.0, allow_eviction=True, require_nonnegative_terrain=False,
        )
        plan, plots = plan_reallocation(report, DETAILS, criteria, now="2026-08-19T00:00:00Z")
        evicted_names = {m.evicted["name"] for m in plan.moves if m.evicted}
        self.assertNotIn(
            "Tireless Farmhand", evicted_names,
            "the fix must reject an eviction that only looks profitable because it ignores the ability "
            "broadcast Manticore and Neb Seni would lose along with Farmhand",
        )
        # And the plot's real occupants (and their real harvest) are exactly as before -- untouched.
        remaining = {c["name"]: c["cur"] for c in plots[self.DEED_UID].cards}
        self.assertAlmostEqual(remaining["Manticore"], 160050.0)
        self.assertAlmostEqual(remaining["Neb Seni"], 213400.0)

    def test_a_genuinely_overwhelming_replacement_still_wins_on_true_net(self):
        # Proves the fix isn't a blanket "never evict a Land/Verico card" rule -- a replacement
        # large enough to outweigh BOTH Farmhand's own loss AND the collateral loss to Manticore
        # and Neb Seni is still, correctly, recommended.
        report = self._report(replacement_base_pp=200000.0)
        criteria = ReallocationCriteria(
            target_plots=frozenset({self.PLOT_CODE}), source_rarities=frozenset(), include_idle=True,
            min_card_pp=0.0, allow_eviction=True, require_nonnegative_terrain=False,
        )
        plan, plots = plan_reallocation(report, DETAILS, criteria, now="2026-08-19T00:00:00Z")
        evicted_names = {m.evicted["name"] for m in plan.moves if m.evicted}
        self.assertIn("Tireless Farmhand", evicted_names)


class SideEffectPropagationTests(unittest.TestCase):
    """A move's effect on OTHER cards left on the source/destination plot -- the architectural
 change this fix required beyond just adding the two boost terms to the mover's own harvest."""

    DEST_DEED = "I-298-6c3970d55b37f5"
    DEST_PLOT = "41-8-13"

    MULT = 1.0 + 1.0 + 0.5 + 0.1   # deed_rarity + totem + title (no runi/status token)

    def _report(self):
        plot = _plot_row(
            self.DEST_DEED, self.DEST_PLOT, "legendary", "STONE",
            mult_facts={"deed_rarity_boost": 1.0, "totem_boost": 0.5, "title_boost": 0.1, "runi_boost": 0.0, "deed_status_token_boost": 0.0},
        )
        # No Mundane Beast companion staked yet -- Toiling Ox's bloodline is dormant, but its own
        # STONE resource-match ability is already live (0.85), so `cur` reflects that.
        ox_cur = _harvest(2500.0, 0.0, self.MULT, 0.85, 0.0)
        bystander_cur = _harvest(20000.0, 0.0, self.MULT, 0.85, 0.0)
        ox = _worker_row("Toiling Ox", TOILING_OX, 2500.0, self.DEST_DEED, "ox-ref", cur_hint=ox_cur)
        bystander = _worker_row("Cyclops", 65, 20000.0, self.DEST_DEED, "cyclops-ref", cur_hint=bystander_cur)
        idle_companion = _idle_card_row(ORDINARY_A, 20000.0, None, "chicken-ref")
        ox_card = _level_lookup_row(TOILING_OX, 2500.0, 6, "ox-ref")
        return {"plots": [plot], "workers": [ox, bystander], "cards": [ox_card, idle_companion]}

    def test_adding_a_companion_raises_the_bystanders_harvest_too(self):
        report = self._report()
        criteria = ReallocationCriteria(
            target_plots=frozenset({self.DEST_PLOT}), source_rarities=frozenset(), include_idle=True,
            min_card_pp=0.0, allow_eviction=False, require_nonnegative_terrain=False,
        )
        plan, plots = plan_reallocation(report, DETAILS, criteria, now="2026-08-19T00:00:00Z")
        self.assertTrue(plan.moves, "expected the idle Mundane Beast companion to be staked")
        move = plan.moves[0]
        self.assertAlmostEqual(move.card_abilities_boost, 0.85)
        self.assertAlmostEqual(move.card_bloodlines_boost, 0.25)
        side_effect_names = {row["name"] for row in move.side_effects}
        self.assertIn("Cyclops", side_effect_names, "Cyclops's harvest must be recorded as a side effect of the companion arriving")
        cyclops_row = next(row for row in move.side_effects if row["name"] == "Cyclops")
        self.assertAlmostEqual(cyclops_row["before"], _harvest(20000.0, 0.0, self.MULT, 0.85, 0.0))
        self.assertAlmostEqual(cyclops_row["after"], _harvest(20000.0, 0.0, self.MULT, 0.85, 0.25))
        self.assertGreater(cyclops_row["after"], cyclops_row["before"])
        # Toiling Ox itself, still resident on the plot, must also show the new bloodline-inclusive cur.
        ox_row = next(c for c in plots[self.DEST_DEED].cards if c["name"] == "Toiling Ox")
        self.assertAlmostEqual(ox_row["cur"], _harvest(2500.0, 0.0, self.MULT, 0.85, 0.25))

    def test_removing_the_sole_companion_lowers_everyone_back_down(self):
        # Build a plot where Toiling Ox's bloodline IS already triggered (companion present), then
        # move the companion away to a far-more-lucrative destination -- the vacated plot's sole
        # remaining occupant (Toiling Ox) must drop back to zero bloodline. (A second ordinary
        # bystander is deliberately omitted here -- with it present, its OWN eligibility as a
        # candidate mover to the lucrative destination competes with the companion's move and
        # muddies which move the planner should prefer; Ox itself already proves the "remaining
        # occupant's harvest changes" side effect, since Ox stays behind and its `cur` must fall.)
        deed = self.DEST_DEED
        plot = _plot_row(
            deed, self.DEST_PLOT, "legendary", "STONE",
            mult_facts={"deed_rarity_boost": 1.0, "totem_boost": 0.5, "title_boost": 0.1, "runi_boost": 0.0, "deed_status_token_boost": 0.0},
        )
        ox_cur = _harvest(2500.0, 0.0, self.MULT, 0.85, 0.25)
        companion_cur = _harvest(20000.0, 0.0, self.MULT, 0.85, 0.25)
        ox = _worker_row("Toiling Ox", TOILING_OX, 2500.0, deed, "ox-ref", cur_hint=ox_cur)
        companion = _worker_row("Furious Chicken", ORDINARY_A, 20000.0, deed, "chicken-ref", cur_hint=companion_cur)

        elsewhere_deed = "I-elsewhere"
        elsewhere_mult = 1.0 + 2.0 + 2.0   # deliberately far richer than the source plot
        elsewhere = _plot_row(
            elsewhere_deed, "1-1-1", "legendary", "STONE", max_workers=1,   # room for exactly the companion, not both
            mult_facts={"deed_rarity_boost": 2.0, "totem_boost": 2.0, "title_boost": 0.0, "runi_boost": 0.0, "deed_status_token_boost": 0.0},
        )
        ox_card = _level_lookup_row(TOILING_OX, 2500.0, 6, "ox-ref")
        report = {"plots": [plot, elsewhere], "workers": [ox, companion], "cards": [ox_card]}
        criteria = ReallocationCriteria(
            target_plots=frozenset({"1-1-1"}), source_rarities=frozenset({"legendary"}), include_idle=False,
            min_card_pp=0.0, allow_eviction=False, require_nonnegative_terrain=False,
        )
        plan, plots = plan_reallocation(report, DETAILS, criteria, now="2026-08-19T00:00:00Z")
        moved_names = {m.card.name for m in plan.moves}
        self.assertIn("Furious Chicken", moved_names, "the far-more-lucrative elsewhere plot should pull the companion away")
        self.assertNotIn("Toiling Ox", moved_names, "moving the ability-holder itself away from its own bonus is a net loser and must not be proposed")
        remaining = {c["name"]: c for c in plots[deed].cards}
        self.assertIn("Toiling Ox", remaining)
        self.assertNotIn("Furious Chicken", remaining)
        # Bloodline is gone now that the sole Mundane Beast companion left; Ox's own resource
        # ability (it's still the plot's holder) is unaffected.
        self.assertAlmostEqual(remaining["Toiling Ox"]["cur"], _harvest(2500.0, 0.0, self.MULT, 0.85, 0.0))


if __name__ == "__main__":
    unittest.main()
