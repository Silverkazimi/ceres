"""Golden/snapshot coverage for `ceres.board.BADGE_META`'s label/tooltip strings.

`library/ceres.md` names `badge_meta` the frontend's single source of truth for
badge label/tooltip text (it ships verbatim to the browser via `/api/board`'s
`badge_meta` key and app.js's tooltip rendering) -- yet `tests/ceres/` had zero
coverage on it before this file. A CHANGELOG entry (v0.4.12, cbd5f2a) shipped a
factual error in the "unpowered" tooltip's rationale text (corrected 2026-08-24);
a bare `pytest tests/ceres -q` run stayed green through that whole regression
because nothing here asserted the actual string content.

This test freezes each key's label/tooltip against its current, verified-correct
text, so a future edit that silently reintroduces a wrong claim (or drops a key,
or adds an unreviewed one) fails loudly and names exactly which key drifted --
not just "some dict changed".

The "unpowered" tooltip specifically is cross-checked against the durable,
measured facts in this project's measured rules (6,903-plot sample, 0 exceptions):
`is_powered = power_core OR energized OR runi`; DEC does NOT substitute for a
power source; ENERGIZED substitutes for a Power Core in every rule; an unpowered
plot reads `max_workers_allowed: 0` (zero *capacity*, not reduced output). The
golden string below matches all of that -- if `BADGE_META["unpowered"]` ever
again claims DEC counts as power, or reframes this as reduced output rather than
zero capacity, this test must fail.
"""

from __future__ import annotations

import pytest

from ceres.board import BADGE_META

# Golden values, transcribed verbatim from ceres/board.py at the time this test
# was written. Update ONLY after confirming the new text against the sourced
# facts above (or, for a genuinely new badge, against whatever the project rules
# library/ceres.md fact backs it) -- never update this file just to silence a
# failure without checking why the string changed.
EXPECTED_BADGE_META = {
    "idle": {
        "label": "Idle",
        "kind": "problem",
        "tooltip": "No workers staked — produces nothing",
    },
    "unpowered": {
        "label": "Unpowered",
        "kind": "problem",
        "tooltip": (
            "This plot's worker slots have no power, so staked workers earn zero. A Power Core or Energized "
            "worker powers all five slots; a Runi powers four."
        ),
    },
    "unpowered_worker": {
        "label": "Unpowered worker",
        "kind": "problem",
        "tooltip": (
            "A worker in this unpowered slot earns zero. Recovery costs a 72-hour cooldown. "
            "A Power Core or an Energized worker would power the slot instead."
        ),
    },
    "redundant_power_core": {
        "label": "Redundant Power Core",
        "kind": "problem",
        "tooltip": (
            "This plot already has an Energized worker, so its Power Core is doing nothing here. It could power "
            "a different plot instead."
        ),
    },
    "dark_discount_wasted": {
        "label": "Wasted Dark Discount",
        "kind": "problem",
        "tooltip": (
            "Runi already removes this plot's DEC staking requirement, so Dark Discount does nothing here. It "
            "would reduce DEC elsewhere."
        ),
    },
    # Split from the single "incomplete" badge (2026-08-25 worksite ruling)
    # a worksite cannot be removed once built, so its total
    # absence means clearing has never finished (open-ended, depends on
    # rarity/PP) -- distinct from an existing worksite mid-build/mid-change
    # which always takes exactly 24 hours.
    "no_worksite": {
        "label": "Not cleared",
        "kind": "problem",
        "tooltip": (
            "No worksite has ever been built here. Clearing time depends on the plot's rarity and Base PP, and "
            "can take a long time."
        ),
    },
    "worksite_incomplete": {
        "label": "Worksite change",
        "kind": "problem",
        "tooltip": (
            "A worksite here is mid-build or mid-change. A worksite change always takes 24 hours."
        ),
    },
    "unstable_totem": {
        "label": "Unstable totem",
        "kind": "property",
        "tooltip": (
            "Not active yet: once Phase 2 goes live, activating this totem doubles this plot's production for 30 "
            "days. It has no effect on today's harvest."
        ),
    },
    "building_in_box": {
        "label": "Building in a Box",
        "kind": "property",
        "tooltip": (
            "Not active yet: on Phase 2 release, this becomes a token that instantly builds a structure on any "
            "owned plot at no construction cost. It has no effect on today's harvest."
        ),
    },
    "assets_not_loaded": {
        "label": "Partly loaded",
        "kind": "property",
        "tooltip": (
            "Partly loaded: this plot's numbers, workers, abilities, totem and title are live from the bulk read. "
            "Slot order, each card's own PP split and the exact staked items load when you open the plot."
        ),
    },
}


def test_badge_meta_has_exactly_the_expected_keys():
    """A key added or removed without updating this golden file is exactly the
    kind of silent drift this test exists to catch."""
    actual_keys = set(BADGE_META)
    expected_keys = set(EXPECTED_BADGE_META)
    missing = expected_keys - actual_keys
    unexpected = actual_keys - expected_keys
    assert not missing, f"BADGE_META lost key(s), never silently: {sorted(missing)}"
    assert not unexpected, (
        f"BADGE_META gained new, un-golden key(s): {sorted(unexpected)} -- "
        "add them to EXPECTED_BADGE_META in this test (after checking their "
        "text against a sourced fact) rather than leaving them uncovered."
    )


@pytest.mark.parametrize("badge_key", sorted(EXPECTED_BADGE_META))
def test_badge_meta_entry_matches_golden_text(badge_key):
    actual = BADGE_META[badge_key]
    expected = EXPECTED_BADGE_META[badge_key]
    assert actual.get("label") == expected["label"], (
        f"BADGE_META[{badge_key!r}]['label'] drifted:\n"
        f"  expected: {expected['label']!r}\n"
        f"  actual:   {actual.get('label')!r}"
    )
    assert actual.get("kind") == expected["kind"], (
        f"BADGE_META[{badge_key!r}]['kind'] drifted:\n"
        f"  expected: {expected['kind']!r}\n"
        f"  actual:   {actual.get('kind')!r}"
    )
    assert actual.get("tooltip") == expected["tooltip"], (
        f"BADGE_META[{badge_key!r}]['tooltip'] drifted:\n"
        f"  expected: {expected['tooltip']!r}\n"
        f"  actual:   {actual.get('tooltip')!r}"
    )


def test_unpowered_tooltip_names_worker_power_sources_and_no_dec():
    """Power Core/Energized power five slots; a Runi in the Power slot powers four.

    DEC is not a power source.
    """
    tooltip = BADGE_META["unpowered"]["tooltip"]
    for must_have in ("Power Core", "Energized", "Runi", "four"):
        assert must_have in tooltip, (
            f"'unpowered' tooltip is missing power source {must_have!r}: {tooltip!r}"
        )
    assert "DEC" not in tooltip, (
        "'unpowered' tooltip must never claim DEC substitutes for a power "
        f"source (project rule: DEC does NOT substitute): {tooltip!r}"
    )
