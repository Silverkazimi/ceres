""" Phase 4 -- the worksite-change op preview.

`ceres/worksite_swap.py` stays pure/read-only (enforced by
`tests/ceres/test_boundaries.py::test_worksite_swap_module_has_no_broadcast_surface`); this
module is the human-gated broadcast side, mirroring `ceres/op_preview.py`'s shape (`account`,
`key_tier`, `chunks`, `operations`, `notices`) closely enough that the frontend's existing
`.op-preview-review` rendering pattern applies with only a worksite-specific header. It builds
exactly ONE op for ONE plot -- the plan's "one op per confirmation" rule -- never a batch.
"""

from __future__ import annotations

import json
from typing import Any

from fides.op import Op, required_account, required_key_tier, requests_for_broadcast
from fides_land.ops import LandOpError, worksite_change_op, worksite_feed_op, worksite_cancel_op
from fides_land.render import allowlist_rules, evaluate_allowlist

from .notices import format_notice, notice_catalog
from .transaction_identity import app_identity
from .worksite_swap import ready_worksite_change, swap_control_available, swap_eligibility


def _ceres_app(app: str | None) -> str:
    expected = app_identity()
    if app not in (None, expected):
        raise LandOpError(f"Ceres worksite operations must use app {expected!r}")
    return expected


def _serialized_bytes(operations: list[list[Any]]) -> int:
    return len(json.dumps(operations, separators=(",", ":")).encode("utf-8"))


def build_worksite_change(
    report: dict[str, Any],
    deed_uid: str,
    target: str,
    *,
    account: str,
    time_crystals: int = 0,
    app: str | None = None,
    staged_power: bool = False,
    staged_worker_count: int | None = None,
) -> tuple[Op, bool, list[dict[str, Any]]]:
    if not isinstance(deed_uid, str) or not deed_uid:
        raise ValueError("deed_uid must be a non-empty string")
    app = _ceres_app(app)
    plots = {str(row.get("deed_uid")): row for row in report.get("plots", []) if row.get("deed_uid")}
    plot = plots.get(deed_uid)
    if plot is None:
        raise ValueError(f"unknown deed_uid {deed_uid!r}")
    facts = plot.get("facts") or {}
    sf = plot.get("staking_facts") or {}
    wf = plot.get("worksite_facts") or {}
    diagnostics = [item.get("type") for item in plot.get("diagnostics", [])]
    if not swap_control_available(
        plot_status=facts.get("status"), diagnostics=diagnostics, project_type=wf.get("project_type"),
        allow_first_clear=True,
    ):
        raise LandOpError(
            f"worksite change is not available on {deed_uid!r} right now (no worksite, "
            "Keep/Castle, or already mid-build/mid-change)"
        )
    first_clear = "no_worksite" in diagnostics
    live_power = sf.get("is_powered")
    if live_power is None:
        live_power = any(sf.get(key) is True for key in ("is_power_core_staked", "is_energized", "is_runi_staked"))
    effective_power = True if first_clear and staged_power else live_power
    effective_workers = sf.get("worker_count") if staged_worker_count is None else staged_worker_count
    eligibility = swap_eligibility(
        is_powered=effective_power, worker_count=effective_workers, first_clear=first_clear,
    )
    if not eligibility["eligible"]:
        raise LandOpError(f"{eligibility['reason']} {eligibility['remedy']}")
    if first_clear and time_crystals:
        raise LandOpError("a first clear is PP-scaled -- the 50 Time Crystal conversion does not apply; build it with 0 Time Crystals")
    op = worksite_change_op(
        deed_uid,
        facts.get("region"),
        target,
        account=account,
        eligible=True,
        terrain=facts.get("terrain"),
        current_resource=facts.get("resource"),
        time_crystals=time_crystals,
        app=app,
    )
    notices = [format_notice("worksite_swap_auto_harvest")]
    if first_clear:
        try:
            has_workers = float(effective_workers or 0) > 0
        except (TypeError, ValueError):
            has_workers = False
        if not has_workers:
            notices.append(format_notice("worksite_first_clear_no_workers"))
    if target == "aura":
        notices.append(format_notice("worksite_swap_aura_permit"))
    if time_crystals and report.get("tc_balance") is None:
        notices.append(format_notice("worksite_swap_tc_balance_unknown"))
    return op, first_clear, [notice for notice in notices if notice]


def build_worksite_preview(
    report: dict[str, Any],
    deed_uid: str,
    target: str,
    *,
    account: str,
    time_crystals: int = 0,
    app: str | None = None,
) -> dict[str, Any]:
    """Builds and allowlist-checks a single worksite-change op for `deed_uid`. Never
    broadcasts -- returns the same preview shape `renderOperationReview`-style frontend code
    already knows how to render. Raises `LandOpError`/`ValueError` on ANY refusal condition
    (H1 zero-PP, H5 illegal target, a suppressed control, an unknown deed) rather than
    building a doomed or illegal op; the caller (the `/worksite-preview` route) turns these
    into HTTP 400/409, exactly as `op_preview.py`'s own `build_preview` already does for card
    moves."""
    app = _ceres_app(app)
    op, first_clear, notices = build_worksite_change(
        report, deed_uid, target, account=account, time_crystals=time_crystals, app=app,
    )

    if required_key_tier([op]) != "Posting" or required_account([op]) != account:
        raise LandOpError("worksite-change preview produced an unexpected signing authority")

    operations = requests_for_broadcast([op])
    # Same allowlist source the CLI path and `op_preview.py`'s card-move preview both run
    # through -- a malformed or mis-generated op must be refused here, before Keychain ever
    # sees it, never only when written to a bundle file nobody opens.
    allowlist_check = evaluate_allowlist({
        "header": {"allowlist": allowlist_rules(account, app=app), "account": account},
        "waves": [{"chunks": [{"keychain_operations": operations}]}],
    })
    if not allowlist_check["ok"]:
        raise LandOpError("allowlist refused: " + "; ".join(allowlist_check["violations"]))

    chunk = {
        "index": 0,
        "operations": operations,
        "op_count": len(operations),
        "serialized_bytes": _serialized_bytes(operations),
    }
    return {
        "account": account,
        "key_tier": "Posting",
        "deed_uid": deed_uid,
        "target": target,
        "time_crystals": time_crystals,
        "first_clear": first_clear,
        "operation_count": 1,
        "chunk_count": 1,
        "chunks": [chunk],
        "operations": operations,
        "hazards": [],
        "notices": notices,
        "notice_catalog": notice_catalog(),
    }


def build_worksite_completion_preview(
    report: dict[str, Any], deed_uid: str, *, account: str, app: str | None = None,
    worksite_override: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Builds the Feed Worker op for a construction whose ready state is live.

    The current project row supplies both its id and its Grain requirement. This endpoint
    never repeats the worksite construction op and never derives a fee from PP or balance.

    : `report` is the stored board capture, which predates the request whenever the
    build's countdown finished while the page was already open ( forbids drawing that
    completion from a stale capture). `worksite_override` -- a fresh `/land/projects/deed/
    {deed}/active` read, fetched by the caller -- replaces the captured worksite facts for
    the readiness check when supplied; every other field (region, account) is unaffected.
    """
    app = _ceres_app(app)
    plots = {str(row.get("deed_uid")): row for row in report.get("plots", []) if row.get("deed_uid")}
    plot = plots.get(deed_uid)
    if plot is None:
        raise ValueError(f"unknown deed_uid {deed_uid!r}")
    facts = plot.get("facts") or {}
    sf = plot.get("staking_facts") or {}
    wf = worksite_override if worksite_override is not None else (plot.get("worksite_facts") or {})
    completion = ready_worksite_change(wf, sf.get("total_work_per_hour"))
    if completion is None:
        raise LandOpError(f"worksite completion is not ready on {deed_uid!r}")
    op = worksite_feed_op(
        deed_uid, facts.get("region"), completion["project_id"], account=account, app=app,
    )
    if required_key_tier([op]) != "Posting" or required_account([op]) != account:
        raise LandOpError("Feed Worker preview produced an unexpected signing authority")
    operations = requests_for_broadcast([op])
    allowlist_check = evaluate_allowlist({
        "header": {"allowlist": allowlist_rules(account, app=app, allow_feed_worker=True), "account": account},
        "waves": [{"chunks": [{"keychain_operations": operations}]}],
    })
    if not allowlist_check["ok"]:
        raise LandOpError("allowlist refused: " + "; ".join(allowlist_check["violations"]))
    notice = format_notice("worksite_completion_ready", context={"worksite": completion["to_label"], "grain_cost": _grain_text(completion["grain_cost"])})
    chunk = {"index": 0, "operations": operations, "op_count": 1, "serialized_bytes": _serialized_bytes(operations)}
    return {
        "kind": "worksite_completion", "account": account, "key_tier": "Posting", "deed_uid": deed_uid,
        "target": completion["to"], "grain_cost": completion["grain_cost"], "project_id": completion["project_id"],
        "operation_count": 1, "chunk_count": 1, "chunks": [chunk], "operations": operations, "hazards": [],
        "notices": [notice], "notice_catalog": notice_catalog(),
    }


def build_worksite_cancel_preview(
    report: dict[str, Any], deed_uid: str, *, account: str, app: str | None = None,
    worksite_override: dict[str, Any] | None = None,
) -> dict[str, Any]:
    app = _ceres_app(app)
    plots = {str(row.get("deed_uid")): row for row in report.get("plots", []) if row.get("deed_uid")}
    plot = plots.get(deed_uid)
    if plot is None:
        raise ValueError(f"unknown deed_uid {deed_uid!r}")
    facts = plot.get("facts") or {}
    wf = worksite_override if worksite_override is not None else (plot.get("worksite_facts") or {})
    if not isinstance(wf, dict) or not str(wf.get("project_type") or "").endswith("_construction"):
        raise LandOpError("this plot has no running construction to cancel")
    project_id = wf.get("project_id", wf.get("id"))
    op = worksite_cancel_op(deed_uid, facts.get("region"), project_id, account=account, app=app)
    if required_key_tier([op]) != "Posting" or required_account([op]) != account:
        raise LandOpError("cancel preview produced an unexpected signing authority")
    operations = requests_for_broadcast([op])
    allowlist_check = evaluate_allowlist({
        "header": {"allowlist": allowlist_rules(account, app=app, allow_cancel_construction=True), "account": account},
        "waves": [{"chunks": [{"keychain_operations": operations}]}],
    })
    if not allowlist_check["ok"]:
        raise LandOpError("allowlist refused: " + "; ".join(allowlist_check["violations"]))
    chunk = {"index": 0, "operations": operations, "op_count": 1, "serialized_bytes": _serialized_bytes(operations)}
    return {
        "kind": "worksite_cancel", "account": account, "key_tier": "Posting", "deed_uid": deed_uid,
        "project_id": project_id, "operation_count": 1, "chunk_count": 1, "chunks": [chunk],
        "operations": operations, "hazards": [], "notices": [], "notice_catalog": notice_catalog(),
    }


def _grain_text(value: float) -> str:
    numeric = float(value)
    return str(int(numeric)) if numeric.is_integer() else f"{numeric:g}"
