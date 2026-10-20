from __future__ import annotations

import copy
import ast
import json
from pathlib import Path
import re
import subprocess
import urllib.parse
import urllib.request

import pytest
from playwright.sync_api import Browser

from ceres.board import BADGE_META
from ceres.candidates import CandidateRow, plot_removal_delta, serialize_candidate
from praetoria.land_reallocate import MovableCard
from ceres.notices import (
    NOTICE_REGISTRY,
    _BADGES,
    _EXCLUSIONS,
    badge_meta_projection,
    format_notice,
    verification_notice,
    validate_registry,
)
from ceres.op_preview import PreviewValidationError, build_preview

from .conftest import spawn_live_ceres, wait_for_board
from .test_op_preview import _plot, _report
from .test_removal_impact import _kinbound_fixture, DEED, KINBOUND_REF
from .vm_context import run_in_module_context


_R111_RETIRED_CONTROL_WORDS = re.compile(
    r"\b(?i:refresh|capture|stale|snapshot|freshness|reload)\w*\b",
)
_R111_RETIRED_CONTROL_ALLOWLIST = {
    "verification_in_flight_restored": {"reloaded"},
    "in_flight_store_unavailable": {"reload"},
}


def _notice_codes_at_head() -> set[str]:
    repo_root = Path(__file__).resolve().parents[2]
    source = subprocess.run(
        ["git", "show", "HEAD:ceres/notices.py"],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    tree = ast.parse(source)
    codes: set[str] = set()
    for node in tree.body:
        mapping = None
        if (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == "NOTICE_REGISTRY"
        ):
            mapping = node.value
        elif (
            isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Call)
            and isinstance(node.value.func, ast.Attribute)
            and isinstance(node.value.func.value, ast.Name)
            and node.value.func.value.id == "NOTICE_REGISTRY"
            and node.value.func.attr == "update"
            and node.value.args
        ):
            mapping = node.value.args[0]
        if isinstance(mapping, ast.Dict):
            codes.update(
                key.value
                for key in mapping.keys
                if isinstance(key, ast.Constant) and isinstance(key.value, str)
            )
    return codes


@pytest.fixture(scope="module")
def live_browser() -> tuple[Browser, str]:
    with spawn_live_ceres() as (browser, base_url):
        yield browser, base_url


def test_notice_registry_has_one_valid_shape_and_badge_projection() -> None:
    validate_registry()
    assert set(BADGE_META) == set(_BADGES)
    assert BADGE_META == badge_meta_projection()
    assert set(_EXCLUSIONS) <= set(NOTICE_REGISTRY)
    assert {"worker_count_unknown", "worker_count_mismatch", "worker_slot_unknown", "worker_slot_taken"} <= set(NOTICE_REGISTRY)
    assert NOTICE_REGISTRY["worker_count_unknown"]["blocking"] == "stage"
    assert NOTICE_REGISTRY["worker_count_mismatch"]["blocking"] == "stage"
    assert NOTICE_REGISTRY["worker_slot_unknown"]["blocking"] == "stage"
    assert NOTICE_REGISTRY["worker_slot_taken"]["blocking"] == "stage"
    assert all(spec["code"] == code for code, spec in NOTICE_REGISTRY.items())


def test_worker_slot_notice_contracts_render_distinct_refusal_messages() -> None:
    unknown = format_notice(
        "worker_slot_unknown",
        reason="worker slot occupancy is unknown after expanding the plot; refusing rather than guessing",
    )
    taken = format_notice(
        "worker_slot_taken",
        reason="worker slot(s) already occupied in the current snapshot: [1]",
    )
    assert unknown["code"] == "worker_slot_unknown"
    assert taken["code"] == "worker_slot_taken"
    assert unknown["message"] == "Ceres still does not know which worker slots are occupied. Expand this plot to load it, then try again."
    assert taken["message"] == "The worker slot you chose is already occupied. Choose a different slot."
    assert unknown["message"] != taken["message"]
    assert unknown["severity"] == taken["severity"] == "danger"


def test_worker_roster_refusal_notices_name_their_distinct_remedies() -> None:
    count = format_notice("worker_count_mismatch", context={"deed_uid": "deed-1"})
    slots = format_notice("worker_slot_unknown", context={"deed_uid": "deed-1"})

    assert count["code"] == "worker_count_mismatch"
    assert count["message"] == "Ceres received an incomplete worker roster. Expand this plot to load it, then try again."
    assert slots["code"] == "worker_slot_unknown"
    assert slots["message"] == "Ceres still does not know which worker slots are occupied. Expand this plot to load it, then try again."
    assert count["message"] != slots["message"]


def test_registry_messages_do_not_name_r111_retired_controls() -> None:
    actual = {
        code: {match.group(0).lower() for match in _R111_RETIRED_CONTROL_WORDS.finditer(spec["message"])}
        for code, spec in NOTICE_REGISTRY.items()
        if _R111_RETIRED_CONTROL_WORDS.search(spec["message"])
    }
    unexpected = {code: words for code, words in actual.items() if code not in _R111_RETIRED_CONTROL_ALLOWLIST}
    assert not unexpected
    assert actual == _R111_RETIRED_CONTROL_ALLOWLIST


def test_r111_allowlist_only_names_head_notice_codes() -> None:
    assert set(_R111_RETIRED_CONTROL_ALLOWLIST) <= _notice_codes_at_head()


def test_unknown_notice_is_visible_and_never_success() -> None:
    notice = format_notice("not-a-real-notice", reason="test evidence")
    assert notice["severity"] == "danger"
    assert notice["blocking"] == "repeat"
    assert notice["message"]
    assert notice["reason"] == "test evidence"


def test_power_and_sole_power_reachability_keep_distinct_axes() -> None:
    report = _report()
    report["plots"][0] = _plot("deed-1", powered=False, workers=0)
    report["plots"].append(_plot("powered-peer", powered=True, workers=0))
    report["workers"] = []
    report["unassigned_items"]["power_core"] = {"uids": ["core-ref"]}
    warning = build_preview(report, [{"deed_uid": "deed-1", "group_id": "worker", "action": "stake", "asset_ref": "new-ref", "slot_index": 0}], account="player0005")
    assert warning["notices"][0]["code"] == "workers_staged_without_power"
    assert warning["notices"][0]["blocking"] == "none"

    report["bonus_assets"] = [{
        "asset_ref": "core-ref", "uid": "ITEM-CORE", "stake_type_uid": "STK-LND-PCR",
        "stake_ref_uid": "deed-1", "asset_kind": "power_core",
    }]
    report["plots"][0] = _plot("deed-1", powered=True, workers=1)
    report["workers"] = [{
        "asset_ref": "old-ref", "uid": "CARD-OLD", "stake_type_uid": "STK-LND-WKR",
        "stake_ref_uid": "deed-1", "slot": 1, "asset_kind": "worker_card", "name": "Old",
    }]
    hazard = build_preview(report, [{"deed_uid": "deed-1", "group_id": "power_core", "action": "unstake", "asset_ref": "core-ref"}], account="player0005")
    assert hazard["notices"][0]["code"] == "sole_power_source_removed"
    assert hazard["notices"][0]["kind"] == "hazard"
    assert hazard["notices"][0]["blocking"] == "confirm"


def test_sole_power_warning_is_silent_for_empty_unpowered_plot() -> None:
    report = _report()
    report["plots"][0] = _plot("deed-1", powered=False, workers=0)
    report["workers"] = []
    report["bonus_assets"] = [{
        "asset_ref": "core-ref", "uid": "ITEM-CORE", "stake_type_uid": "STK-LND-PCR",
        "stake_ref_uid": "deed-1", "asset_kind": "power_core",
    }]
    result = build_preview(report, [{"deed_uid": "deed-1", "group_id": "power_core", "action": "unstake", "asset_ref": "core-ref"}], account="player0005")
    assert result["hazards"] == []
    assert result["notices"] == []


def test_removal_notice_is_server_priced_and_names_bloodline_subtype() -> None:
    report, details = _kinbound_fixture()
    result = plot_removal_delta(report, DEED, [KINBOUND_REF], details_by_id=details)
    bloodline = next(notice for notice in result["notices"] if notice["code"] == "bloodline_pair_deactivated")
    assert bloodline["context"]["sub_type"] == "Elf"
    assert "Elf" in bloodline["message"]
    assert bloodline["action"]["id"] == "open_bloodline_pairs"
    assert any(notice["code"] == "plot_ability_loss" for notice in result["notices"])


def test_rental_advisory_is_structured_and_visible_on_candidate() -> None:
    row = CandidateRow(
        MovableCard("Rented worker", "Red", 100, 0, "idle", None, asset_ref="rental-ref"),
        {}, {"asset_ref": "rental-ref"}, "idle", "STK-LND-WKR", {"ready": True},
        rental_kind="in", rental_expiry="2026-09-01T00:00:00Z",
    )
    result = serialize_candidate(row)
    assert result["rental"]["notice"]["code"] == "rental_expiry_warning"
    assert "force-harvests" in result["rental"]["notice"]["message"]


def test_cooldown_refusal_reaches_preview_error_with_structured_notice() -> None:
    report = _report()
    report["plots"].append(_plot("source-deed", powered=True, workers=1))
    report["workers"] = [{
        "asset_ref": "new-ref", "uid": "CARD-NEW", "stake_type_uid": "STK-LND-WKR",
        "stake_ref_uid": "source-deed", "slot": 1, "asset_kind": "worker_card",
        "stake_end_date": "2099-01-01T00:00:00Z", "name": "New",
    }]
    with pytest.raises(PreviewValidationError) as caught:
        build_preview(report, [{"deed_uid": "deed-1", "group_id": "worker", "action": "stake", "asset_ref": "new-ref", "slot_index": 0}], account="player0005")
    assert caught.value.errors[0]["notice"]["code"] == "cooldown_refusal"
    assert caught.value.errors[0]["notice"]["blocking"] == "stage"


@pytest.mark.parametrize("state, code, label", [
    ("domain-confirmed", "verification_confirmed", "Confirmed in game"),
    ("domain-mismatch", "verification_mismatch", "Game state does not match"),
    ("domain-not-applied", "verification_not_applied", "The transaction landed on chain, but the game did not apply this change. Nothing was changed in game. Prepare and sign again if you still want it."),
    ("chain_verified", "verification_chain_check", "On chain — checking game state…"),
])
def test_verification_states_use_plain_display_labels(state: str, code: str, label: str) -> None:
    notice = verification_notice(state)
    assert notice["code"] == code
    assert notice["message"] == label


def test_frontend_has_no_duplicate_bloodline_producer() -> None:
    """ (row 28): converted from `APP_JS.read_text()` substring
    checks to a `vm_context` whole-module load (no source-position coupling, the
    real module is fully evaluated). `bloodlineBreaksFromRemoval` must not exist
    as a module-scope symbol -- a second bloodline-break calculator alongside the
    real one; `renderNotices` and `NOTICE_CATALOG` must exist as module-scope
    symbols -- the notice-rendering surface and its catalog are present.

    Dropped, not retargeted: the structural "file still ends with a closed
    `ready(() => {...});`, not truncated" claim. It has no module-level
    equivalent -- a whole-module `vm.runInNewContext` load already fails outright
    (a parse error) on a truncated file, which every other test in this module
    already depends on implicitly, so the claim adds nothing beyond "the file
    parses". Once the campaign's split lands, "the file" is 20 files, so a
    single EOF-shape claim about one of them stops meaning what it means today
    (the plan's own §3.1 Batch B ruling gives row 23's "ready() once at EOF"
    claim the identical treatment -- retargeted at the post-split entry file,
    Phase 8's job, not this migration's)."""
    run_in_module_context(
        "if (typeof bloodlineBreaksFromRemoval !== 'undefined') { "
        "throw new Error('bloodlineBreaksFromRemoval must not exist as a module-scope symbol -- a second bloodline-break calculator alongside the real one'); }\n"
        "if (typeof renderNotices === 'undefined') { throw new Error('renderNotices missing -- the notice-rendering surface is gone'); }\n"
        "if (typeof NOTICE_CATALOG === 'undefined') { throw new Error('NOTICE_CATALOG missing -- the notice catalog is gone'); }"
    )
