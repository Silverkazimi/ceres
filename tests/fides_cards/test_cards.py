import inspect
import json
import random
from itertools import permutations
from pathlib import Path

import pytest

from ceres import capture_freshness
from fides.bundle import build_bundle
from fides_cards.ops import CombineError, CombineIntent, build_combine_op, survivor_rule
from fides_cards.render import build_renderer, hazard_confirmation
from fides_cards.verify import CardVerificationSpec
from fides_land.render import allowlist_rules, evaluate_allowlist



TEST_APP = "ceres/0.0.0"

def records():
    return [
        {"uid": "C-LOW", "card_detail_id": 301, "name": "Card A", "edition": 6, "gold": False, "bcx": 1, "level": 1},
        {"uid": "C-HIGH", "card_detail_id": 301, "name": "Card A", "edition": 6, "gold": False, "bcx": 5, "level": 2},
    ]


def combine_bundle(op, collection=None):
    collection = collection or records()
    return build_bundle(
        [("combine", [op])],
        consumer="fides_cards",
        renderer=build_renderer(op.json_payload["cards"], collection, observed_at="2026-08-26T23:00:00Z"),
        allowlist=allowlist_rules("player0005", app=TEST_APP),
    )


def test_survivor_uses_highest_bcx_not_payload_position_and_tie_is_alphabetical():
    """A legal same-print tie is invariant under every input permutation.

 Mutation injection for independent observation: restore the ``(-bcx, print_id)``
 sort key in ``survivor_rule``. The permutation assertions must then go red because
 the result follows input order instead of the alphabetically first uid.
    """
    assert survivor_rule(records()) == "C-HIGH"
    tied = [
        {"uid": "C-Z", "card_detail_id": 301, "edition": 6, "gold": False, "bcx": 3, "level": 1},
        {"uid": "C-M", "card_detail_id": 301, "edition": 6, "gold": False, "bcx": 3, "level": 1},
        {"uid": "C-A", "card_detail_id": 301, "edition": 6, "gold": False, "bcx": 3, "level": 1},
    ]
    assert build_combine_op([row["uid"] for row in tied], tied, account="player0005", app=TEST_APP).json_payload["cards"] == [
        "C-Z", "C-M", "C-A"
    ]
    assert {survivor_rule(list(order)) for order in permutations(tied)} == {"C-A"}


def test_survivor_rule_is_total_on_real_collection_groups():
    """Every eligible real-data group has one deterministic survivor.

 Mutation injection for independent observation: remove ``uid`` from the survivor
 sort key. The guard must then go red on the first tied group whose input order is
 not its alphabetical order.
    """
    if not Path("work/combine-survivor-rule/player0005_collection.json").exists():
        pytest.skip("private work/ evidence file not included in this export")
    report = json.loads(Path("work/combine-survivor-rule/player0005_collection.json").read_text())
    groups = {}
    for row in report["cards"]:
        if row.get("market_listing_status") is not None or row.get("delegated_to") is not None:
            continue
        if row.get("uid") and row.get("bcx") is not None:
            groups.setdefault((row.get("card_detail_id"), row.get("edition"), row.get("gold")), []).append(row)
    eligible_groups = [group for group in groups.values() if len(group) > 1]

    for group in eligible_groups:
        expected = min(group, key=lambda row: (-row["bcx"], row["uid"]))["uid"]
        assert survivor_rule(group) == expected


def test_survivor_rule_matches_independent_reference_for_seeded_real_sets():
    """The shared rule agrees with an independent reference across 500 real sets.

 Mutation injection for independent observation: change the reference tie direction
 from ``min`` to ``max``. The guard must go red on the first tied sampled set.
    """
    if not Path("work/combine-survivor-rule/player0005_collection.json").exists():
        pytest.skip("private work/ evidence file not included in this export")
    report = json.loads(Path("work/combine-survivor-rule/player0005_collection.json").read_text())
    groups = {}
    for row in report["cards"]:
        if row.get("market_listing_status") is not None or row.get("delegated_to") is not None:
            continue
        if row.get("uid") and row.get("bcx") is not None:
            groups.setdefault((row.get("card_detail_id"), row.get("edition"), row.get("gold")), []).append(row)
    groups = [group for group in groups.values() if len(group) > 1]
    rng = random.Random(1887)

    for _ in range(500):
        group = rng.choice(groups)
        sample = rng.sample(group, min(len(group), rng.randint(2, 5)))
        rng.shuffle(sample)
        expected = min(sample, key=lambda row: (-row["bcx"], row["uid"]))["uid"]
        assert survivor_rule(sample) == expected


def test_combine_intent_has_only_uids_and_cannot_nominate_survivor():
    assert list(inspect.signature(CombineIntent).parameters) == ["uids"]
    assert list(inspect.signature(survivor_rule).parameters) == ["uids"]
    intent = CombineIntent(["C-LOW", "C-HIGH"])
    assert not hasattr(intent, "survivor")
    assert not hasattr(intent, "surviving_uid")


def test_builder_requires_live_records_and_matching_identity():
    with pytest.raises(CombineError, match="does not contain every selected"):
        build_combine_op(["C-LOW", "MISSING"], records(), account="player0005", app=TEST_APP)
    wrong = records()
    wrong[1]["edition"] = 7
    with pytest.raises(CombineError, match="same edition"):
        build_combine_op(["C-LOW", "C-HIGH"], wrong, account="player0005", app=TEST_APP)
    with pytest.raises(CombineError, match="sm_combine_all"):
        CombineIntent([{"id": 301, "gold": False, "edition": "6"}, {"id": 301, "gold": False, "edition": "6"}])


def test_builder_rejects_third_party_app_identifier():
    with pytest.raises(CombineError, match="third-party app identifier"):
        build_combine_op(["C-LOW", "C-HIGH"], records(), account="player0005", app="peakmonsters")


def test_ended_land_stake_does_not_block_a_combine():
    """stake_ref_uid persists after a stake ends -- presence alone must not block.

 Injection run: reverting `_land_stake_block` to bare
 `record.get("stake_ref_uid")` presence (no stake_end_date check) made this go
 red with CombineError "is Staked on Land". Restored after observing red.
    """
    ended = records()
    ended[0]["stake_ref_uid"] = "I-294-a2b30476977796"
    ended[0]["stake_end_date"] = "2026-02-25T00:00:00Z"
    op = build_combine_op(["C-LOW", "C-HIGH"], ended, account="player0005", app=TEST_APP)
    assert op.operation_id == "sm_combine_cards"


def test_live_land_stake_with_no_end_date_still_blocks():
    """A live stake (stake_ref_uid present, no stake_end_date) must still refuse.

 Injection run: dropping the `stake_ref_uid`-block branch from
 `_land_stake_block` entirely made this go red -- the live-staked card was
 silently accepted and no CombineError was raised. Restored after observing red.
    """
    staked = records()
    staked[0]["stake_ref_uid"] = "I-294-5d33fb0c6734d1"
    staked[0]["stake_end_date"] = None
    with pytest.raises(CombineError, match="Staked on Land"):
        build_combine_op(["C-LOW", "C-HIGH"], staked, account="player0005", app=TEST_APP)


def test_future_stake_end_date_blocks_as_unstaking():
    staked = records()
    staked[0]["stake_ref_uid"] = "I-294-5d33fb0c6734d1"
    staked[0]["stake_end_date"] = "2999-08-27T12:00:00Z"
    with pytest.raises(CombineError, match="Unstaking from Land"):
        build_combine_op(["C-LOW", "C-HIGH"], staked, account="player0005", app=TEST_APP)


def test_gold_foil_uses_the_same_flat_payload():
    gold = [dict(row, gold=True, uid="G" + row["uid"]) for row in records()]
    op = build_combine_op([row["uid"] for row in gold], gold, account="player0005", app=TEST_APP)
    assert op.operation_id == "sm_combine_cards"
    assert op.json_payload["cards"] == ["GC-LOW", "GC-HIGH"]
    assert op.json_payload["app"] == TEST_APP
    assert set(op.json_payload) == {"cards", "app", "n"}


def test_hazard_names_destroyed_cards_and_never_warns_on_a_skinned_copy():
    """A skin is card-level, not per-copy, so a destroyed copy never loses one.

 Injection run: reinstating the removed skin `hard_warnings.append(...)` branch
 (firing whenever a destroyed copy's record carries a truthy `skin`) made this go
 red -- `hazard["hard_warnings"]` came back non-empty for the three-gold-Lyanna
 input below, where every copy (survivor and destroyed alike) carries the same
 "splinterstorm" skin. Removed after observing red.
    """
    gold_lyanna = [
        {"uid": "G-TESTCARD01", "card_detail_id": 27, "name": "Lyanna Natura", "edition": 1, "gold": True, "bcx": 14, "level": 1, "skin": "splinterstorm"},
        {"uid": "G0-27-HB89SOFR8W", "card_detail_id": 27, "name": "Lyanna Natura", "edition": 0, "gold": True, "bcx": 2, "level": 1, "skin": "splinterstorm"},
        {"uid": "G-9TKP51WYN4", "card_detail_id": 27, "name": "Lyanna Natura", "edition": 0, "gold": True, "bcx": 1, "level": 1, "skin": "splinterstorm"},
    ]
    # editions differ across these three in reality, but the survivor/hazard logic
    # under test doesn't gate on edition -- build_combine_op does that separately.
    for row in gold_lyanna:
        row["edition"] = 1
    hazard = hazard_confirmation(
        ["G-TESTCARD01", "G0-27-HB89SOFR8W", "G-9TKP51WYN4"], gold_lyanna, observed_at="2026-08-29T00:00:00Z"
    )
    assert hazard["predicted_survivor_uid"] == "G-TESTCARD01"
    assert hazard["destroyed_uids"] == ["G0-27-HB89SOFR8W", "G-9TKP51WYN4"]
    assert "G0-27-HB89SOFR8W" in hazard["warning"]
    assert "G-9TKP51WYN4" in hazard["warning"]
    assert "permanently destroys" in hazard["warning"]
    assert hazard["requires_confirmation"] is True
    assert hazard["bypassable"] is False
    assert hazard["hard_warnings"] == []
    assert hazard["warnings"] == [hazard["warning"]]

    skinned = [dict(row) for row in records()]
    skinned[0]["skin"] = "skin-1"
    hazard = hazard_confirmation(["C-LOW", "C-HIGH"], skinned, observed_at="2026-08-26T23:00:00Z")
    assert "C-LOW" in hazard["warning"]
    assert "C-HIGH" in hazard["warning"]
    assert hazard["predicted_survivor_uid"] == "C-HIGH"
    assert hazard["destroyed_uids"] == ["C-LOW"]
    assert hazard["rows"][0] == {
        "card_id": "C-LOW",
        "name": "Card A",
        "edition": 6,
        "foil": "Regular Foil",
        "bcx": 1,
        "level": 1,
        "observed_at": "2026-08-26T23:00:00Z",
    }
    assert hazard["hard_warnings"] == []


def test_hazard_does_not_name_a_survivor_when_bcx_is_unavailable():
    incomplete = [dict(row) for row in records()]
    incomplete[1].pop("bcx")
    hazard = hazard_confirmation(["C-LOW", "C-HIGH"], incomplete, observed_at="2026-08-26T23:00:00Z")
    assert hazard["predicted_survivor_uid"] is None
    assert hazard["survivor"] is None
    assert "survivor cannot be named" in hazard["warning"]


def test_allowlist_requires_combine_fields_and_rejects_wrong_family_or_tier():
    op = build_combine_op(["C-LOW", "C-HIGH"], records(), account="player0005", app=TEST_APP)
    result = evaluate_allowlist(combine_bundle(op))
    assert result["ok"], result["violations"]
    for field in ("cards", "app", "n"):
        malformed = combine_bundle(op)
        payload = malformed["waves"][0]["chunks"][0]["keychain_operations"][0][1]
        parsed = json.loads(payload["json"])
        parsed.pop(field)
        payload["json"] = json.dumps(parsed)
        assert not evaluate_allowlist(malformed)["ok"]
    malformed = combine_bundle(op)
    del malformed["waves"][0]["chunks"][0]["keychain_operations"][0][1]["id"]
    assert not evaluate_allowlist(malformed)["ok"]
    wrong_tier = combine_bundle(op)
    body = wrong_tier["waves"][0]["chunks"][0]["keychain_operations"][0][1]
    body["required_auths"] = ["player0005"]
    body["required_posting_auths"] = []
    assert not evaluate_allowlist(wrong_tier)["ok"]


def test_allowlist_rejects_third_party_app_identifier():
    op = build_combine_op(["C-LOW", "C-HIGH"], records(), account="player0005", app=TEST_APP)
    bundle = combine_bundle(op)
    operation = bundle["waves"][0]["chunks"][0]["keychain_operations"][0][1]
    parsed = json.loads(operation["json"])
    parsed["app"] = "peakmonsters"
    operation["json"] = json.dumps(parsed)
    result = evaluate_allowlist(bundle)
    assert not result["ok"]
    assert any("every emitted app" in violation for violation in result["violations"])


def test_unpropagated_collection_read_is_pending_not_failure():
    before = records()
    op = build_combine_op(["C-LOW", "C-HIGH"], before, account="player0005", app=TEST_APP)
    result = CardVerificationSpec(lambda: before, expected_records=before).check_domain("combine", [op])
    assert result["status"] == "domain_pending"
    after = [dict(before[1], bcx=6)]
    verified = CardVerificationSpec(lambda: after, expected_records=before).check_domain("combine", [op])
    assert verified["status"] == "domain_verified"


def test_restake_requires_capture_newer_than_combine():
    """The Cooling/restake gate (`ceres/routes_op_preview.py::_preview`) refuses staging
 a stake from a capture that is not strictly newer than the combine it followed.
 The planning ledger this used to be keyed through is gone (worker-table rebuild);
 the client now sends `combine_verified_at` straight through and the server compares
 it with `capture_freshness.capture_is_newer_than` -- this is that primitive."""
    combine_at = "2026-08-26T22:00:00Z"
    assert capture_freshness.capture_is_newer_than("2026-08-26T22:00:00Z", combine_at) is False
    assert capture_freshness.capture_is_newer_than("2026-08-26T22:00:01Z", combine_at) is True
