"""fides_land/render.py -- land's human-legible per-op row renderer and page-side allowlist
(plan §8), passed into `fides.bundle.build_bundle` at emit time.
"""

from __future__ import annotations

import json
from typing import Any

from fides.op import Op

from fides_cards.render import allowlist_family as combine_allowlist_family

from .ops import WORKSITE_SWAP_TARGETS


def _fmt_pp(value: Any) -> str:
    try:
        return f"{float(value):,.0f}"
    except (TypeError, ValueError):
        return "?"


def build_uid_index(plan_dict: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """`card_uid -> {name, base_pp, ...}`, built from the SAME plan snapshot record the uid
    itself came from -- this is the plan §8 uid-problem mitigation: a human can't verify a
    uid by eye, but the name/PP shown beside it is read from the identical record."""
    index: dict[str, dict[str, Any]] = {}

    def add(card: dict[str, Any]) -> None:
        uid = card.get("card_uid")
        if uid:
            index[uid] = {"name": card.get("name"), "base_pp": card.get("base_pp")}

    for move in plan_dict.get("moves", []):
        add(move["card"])
        if move.get("evicted"):
            add(move["evicted"])
    for row in plan_dict.get("backfill", []):
        add(row["card"])
    return index


def build_deed_labels(plots: dict[str, Any]) -> dict[str, str]:
    """deed_uid -> a pre-formatted label, e.g. "41-8-13 (common, x1.10)". `plots` is
    Praetoria's own `land_reallocate.Plot` objects (or any object/dict exposing the same
    `.plot`/`.rarity`/`.mult` attributes) -- kept generic so this module doesn't hard-depend
    on Praetoria's exact dataclass shape beyond duck typing three fields."""
    labels: dict[str, str] = {}
    for uid, plot in plots.items():
        code = getattr(plot, "plot", None) if not isinstance(plot, dict) else plot.get("plot")
        rarity = getattr(plot, "rarity", None) if not isinstance(plot, dict) else plot.get("rarity")
        mult = getattr(plot, "mult", None) if not isinstance(plot, dict) else plot.get("mult")
        mult_text = f"x{mult:.2f}" if isinstance(mult, (int, float)) else "x?"
        labels[uid] = f"{code} ({rarity}, {mult_text})"
    return labels


def _render_worksite_change(payload: dict[str, Any], deed_labels: dict[str, str]) -> str:
    """Row text for an `sm_land_operation` worksite-change op ( Phase 3) -- a
    completely different payload shape from `sm_stake_change`'s stake/unstake sides, so it
    is dispatched on separately rather than forced through the card renderer above."""
    op_name = str(payload.get("op") or "")
    target = op_name[len("worksite_") : -len("_construction")] if op_name.endswith("_construction") else "?"
    deed_uids = payload.get("deed_uids") or []
    labels = ", ".join(deed_labels.get(d, d) for d in deed_uids) if deed_uids else "?"
    tc = payload.get("time_crystals") or 0
    tc_note = f"  (Time Crystals: {tc}, instant)" if tc else "  (24h wait)"
    return f"WORKSITE CHANGE   -> {target:<10} on {labels}{tc_note}"


def _render_worksite_feed(payload: dict[str, Any], deed_labels: dict[str, str]) -> str:
    deed_uids = payload.get("deed_uids") or []
    labels = ", ".join(deed_labels.get(d, d) for d in deed_uids) if deed_uids else "?"
    return f"FEED WORKER   complete ready worksite on {labels}  (Grain required)"


def build_renderer(uid_index: dict[str, dict[str, Any]], deed_labels: dict[str, str]):
    """Returns a `fides.bundle.RowRenderer`: `Op -> {"text": "..."}` producing rows shaped
    like plan §8's worked example:
    `UNSTAKE   Tireless Farmhand    9,400 PP   from 41-8-13  (common, x1.10)`
    `STAKE     Sacred Unicorn      40,000 PP   into 65-9-83  (legendary + totem, x2.35)  slot 3`

    A worksite-change op (`payload["op"]` ending `_construction`) is a different family
    entirely and is rendered by `_render_worksite_change` instead.
    """

    def renderer(op: Op) -> dict[str, Any]:
        payload = op.json_payload
        if payload.get("op") == "update_worksite":
            return {"text": _render_worksite_feed(payload, deed_labels)}
        if str(payload.get("op") or "").endswith("_construction"):
            return {"text": _render_worksite_change(payload, deed_labels)}
        deed_uid = payload.get("deed_uid")
        label = deed_labels.get(deed_uid, deed_uid)
        lines: list[str] = []
        for entry in payload.get("unstake", {}).get("cards", []):
            info = uid_index.get(entry["card_uid"], {})
            lines.append(f"UNSTAKE   {str(info.get('name', '?')):<24} {_fmt_pp(info.get('base_pp')):>10} PP   from {label}")
        for entry in payload.get("stake", {}).get("cards", []):
            info = uid_index.get(entry["card_uid"], {})
            lines.append(f"STAKE     {str(info.get('name', '?')):<24} {_fmt_pp(info.get('base_pp')):>10} PP   into {label}  slot {entry.get('slot')}")
        return {"text": "\n".join(lines) if lines else f"(no worker changes) {label}"}

    return renderer


def wave_summarizer(wave_name: str, ops: list[Op]) -> dict[str, Any]:
    deeds = {op.json_payload.get("deed_uid") for op in ops}
    cards = sum(len(op.json_payload.get("stake", {}).get("cards", [])) + len(op.json_payload.get("unstake", {}).get("cards", [])) for op in ops)
    return {"deeds": len(deeds), "cards": cards}


def allowlist_rules(account: str, *, app: str, allow_feed_worker: bool = False, allow_cancel_construction: bool = False) -> dict[str, Any]:
    """Per-family land allowlist (plan §8, restructured for ). Rather than one flat rule
    list assumed to cover every op, each op FAMILY -- keyed by its custom_json `id` -- declares
    its own auth tier and its own field-shape rules; an op is validated only against the rules
    for ITS OWN family, and any op whose `id` names no known family is refused outright
    (fail-closed, never a silent pass). This is what lets a second family (e.g. a future card
    `sm_combine_cards` adapter) be added later by adding a new `families` entry, rather than by
    another restructure.

    `sm_stake_change` extends the original worker-only ruleset to legitimately permit
    STK-LND-PCR/STK-LND-TOT in `items[]` -- `fides_land/ops.py:intents_to_ops` already emits
    exactly that shape. STK-LND-RUNI is likewise permitted in `stake.cards[*]` (and
    `unstake.cards[*]`, which carries no type filter here): its payload is OBSERVED both
    directions -- a live stake and a live unstake capture -- per `ops.py`'s own
    `_UNVERIFIED_STAKE_TYPES`, which is now empty. STK-LND-TTL is permitted on the same
    footing: its stake and unstake payloads are both observed (an isolated Title-only swap,
    Posting tier). Its restake COOLDOWN duration is still unpublished, but a cooldown is a
    timing warning carried by the UI, not a payload-shape question -- the allowlist exists
    to refuse op shapes it cannot vouch for, and this one it can.

    `sm_land_operation` is  Phase 3's worksite-change family -- a structurally
    different payload (no `stake`/`unstake` sides at all; the target lives in `body.json.op`
    itself, per `fides_land.ops.worksite_change_op`). This is exactly the second family the
    per-family restructure above exists to add without another restructure.

    Auth tier is declared PER FAMILY; the family's custom_json id selects the expected tier,
    and the op's own `required_auths`/`required_posting_auths` must match it. A known id carrying
    a different tier is therefore refused rather than silently accepted."""
    worksite_op_names = sorted(f"worksite_{target}_construction" for target in WORKSITE_SWAP_TARGETS)
    if allow_feed_worker:
        worksite_op_names.append("update_worksite")
    if allow_cancel_construction:
        worksite_op_names = ["cancel_construction"]
    return {
        "common_rules": [
            {"label": "every operation must be custom_json", "scope": "op", "path": "name", "check": "eq", "value": "custom_json"},
            {"label": f"every emitted app must be {app}", "path": "body.json.app", "check": "eq", "value": app},
        ],
        "families": {
            "sm_stake_change": {
                "auth_tier": "Posting",
                "rules": [
                    {"label": "every stake.cards[*] stake_type_uid must be STK-LND-WKR or STK-LND-RUNI", "path": "body.json.stake.cards[*].stake_type_uid", "check": "in", "value": ["STK-LND-WKR", "STK-LND-RUNI"]},
                    {"label": "every stake.items[*] stake_type_uid must be STK-LND-PCR, STK-LND-TOT or STK-LND-TTL", "path": "body.json.stake.items[*].stake_type_uid", "check": "in", "value": ["STK-LND-PCR", "STK-LND-TOT", "STK-LND-TTL"]},
                    {"label": "every unstake.items[*] stake_type_uid (when present) must be STK-LND-PCR, STK-LND-TOT or STK-LND-TTL", "path": "body.json.unstake.items[*].stake_type_uid", "check": "in", "required": False, "value": ["STK-LND-PCR", "STK-LND-TOT", "STK-LND-TTL"]},
                    {"label": "the signing account must match the bundle header", "path": "body.required_posting_auths", "check": "array_equals_header_account"},
                ],
            },
            "sm_land_operation": {
                "auth_tier": "Posting",
                "rules": [
                    {"label": "op must be cancel_construction" if allow_cancel_construction else "op must be a known worksite construction or Feed Worker operation" if allow_feed_worker else "op must be one of the seven known worksite_<target>_construction targets", "path": "body.json.op", "check": "in", "value": worksite_op_names},
                    {"label": "deed_uids must contain at least one deed", "path": "body.json.deed_uids", "check": "array_nonempty"},
                    {"label": "time_crystals must match the worksite operation", "path": "body.json.time_crystals", "check": "in", "value": [0] if allow_cancel_construction else [0, 50]},
                    *([{"label": "worksite operation must carry its project id", "path": "body.json.project_id", "check": "present"}, {"label": "worksite operation must not auto-buy Grain", "path": "body.json.auto_buy_grain", "check": "eq", "value": False}] if allow_feed_worker or allow_cancel_construction else []),
                    {"label": "the signing account must match the bundle header", "path": "body.required_posting_auths", "check": "array_equals_header_account"},
                ],
            },
            "sm_combine_cards": combine_allowlist_family(app=app),
        },
    }


# a Python port of signer.js's rule evaluator, so Phase 3/4 tests can prove the allowlist
# actually refuses each violation without needing a browser. signer.js implements the exact
# same rule schema independently (defence in depth: the page must not trust that the Python
# side already checked).

def _resolve_path(root: Any, path: str) -> list[Any]:
    """Resolve dotted paths while retaining only values from present fields."""
    return _resolve_path_info(root, path).values


class _PathResolution:
    def __init__(self, values: list[Any], *, empty_wildcard: bool, missing: bool):
        self.values = values
        self.empty_wildcard = empty_wildcard
        self.missing = missing


def _resolve_path_info(root: Any, path: str) -> _PathResolution:
    current = [root]
    empty_wildcard = False
    missing = False
    for token in path.split("."):
        wildcard = token.endswith("[*]")
        key = token[: -3] if wildcard else token
        nxt: list[Any] = []
        for item in current:
            if item is None:
                missing = True
                continue
            if key == "":
                value, present = item, True
            elif isinstance(item, dict):
                present = key in item
                value = item.get(key)
            else:
                present = hasattr(item, key)
                value = getattr(item, key, None)
            if not present:
                missing = True
                continue
            if key == "json" and isinstance(value, str):
                try:
                    value = json.loads(value)
                except ValueError:
                    value = None
            if wildcard:
                if isinstance(value, list):
                    if value:
                        nxt.extend(value)
                    else:
                        empty_wildcard = True
                else:
                    missing = True
            else:
                nxt.append(value)
        current = nxt
    return _PathResolution(current, empty_wildcard=empty_wildcard, missing=missing)


def _refusal_result(reason: str, op_count: int) -> dict[str, Any]:
    return {
        "ok": False,
        "violations": [f"allowlist refused: {reason}"],
        "refusal_reason": reason,
        "op_count": op_count,
    }


_MISSING_INPUT = object()


def _collect_operations(bundle_body: Any) -> tuple[list[dict[str, Any]], str | None]:
    if not isinstance(bundle_body, dict):
        return [], "operation set is null or absent"
    waves = bundle_body.get("waves", _MISSING_INPUT)
    if waves is _MISSING_INPUT or waves is None:
        return [], "operation set is null or absent"
    if not isinstance(waves, list):
        return [], "operation set is malformed"
    all_ops: list[dict[str, Any]] = []
    for wave in waves:
        if not isinstance(wave, dict):
            return [], "operation set is null or absent" if wave is None else "operation set is malformed"
        chunks = wave.get("chunks", _MISSING_INPUT)
        if chunks is _MISSING_INPUT or chunks is None:
            return [], "operation set is null or absent"
        if not isinstance(chunks, list):
            return [], "operation set is malformed"
        for chunk in chunks:
            if not isinstance(chunk, dict):
                return [], "operation set is null or absent" if chunk is None else "operation set is malformed"
            operations = chunk.get("keychain_operations", _MISSING_INPUT)
            if operations is _MISSING_INPUT or operations is None:
                return [], "operation set is null or absent"
            if not isinstance(operations, list):
                return [], "operation set is malformed"
            for operation in operations:
                if isinstance(operation, (list, tuple)) and len(operation) >= 2:
                    all_ops.append({"name": operation[0], "body": operation[1]})
                else:
                    all_ops.append({"name": None, "body": None})
    return all_ops, None if all_ops else "operation set is empty"


def evaluate_allowlist(bundle_body: dict[str, Any]) -> dict[str, Any]:
    """Fail-closed, per-family evaluation (): `common_rules` run against every op
    regardless of family; each op is then looked up by its own `body.id` in `families` and
    checked ONLY against that family's `auth_tier` and `rules` -- an op whose id names no known
    family is refused outright rather than silently passing (or silently being checked against
    the wrong family's rules)."""
    all_ops, operation_reason = _collect_operations(bundle_body)
    if operation_reason:
        return _refusal_result(operation_reason, len(all_ops))
    header = bundle_body.get("header") if isinstance(bundle_body, dict) else None
    if not isinstance(header, dict):
        return _refusal_result("bundle header is null or absent", len(all_ops))
    allowlist = header.get("allowlist") or {}
    common_rules = allowlist.get("common_rules", allowlist.get("rules", []))
    families = allowlist.get("families", {})
    violations: list[str] = []
    account = header.get("account")

    for rule in common_rules:
        targets = [bundle_body["header"]] if rule.get("scope") == "header" else all_ops
        for target in targets:
            resolution = _resolve_path_info(target, rule["path"])
            if not _rule_passes(rule, resolution, account):
                label = rule.get("label", rule["path"])
                if rule.get("scope") == "header":
                    violations.append(label)
                else:
                    op_id_values = _resolve_path(target, "body.id")
                    op_id = op_id_values[0] if op_id_values else None
                    violations.append(f"{label} (op id={op_id})")

    for op in all_ops:
        op_id_values = _resolve_path(op, "body.id")
        op_id = op_id_values[0] if op_id_values else None
        family = families.get(op_id)
        if family is None:
            violations.append(f"op id {op_id!r} is not an allowlisted family (expected one of: {sorted(families)})")
            continue
        if not _check_auth_tier(op, family.get("auth_tier")):
            violations.append(f"op id {op_id!r} auth tier must be {family.get('auth_tier')!r} (required_auths/required_posting_auths mismatch)")
        for rule in family.get("rules", []):
            resolution = _resolve_path_info(op, rule["path"])
            if not _rule_passes(rule, resolution, account):
                violations.append(f"{rule.get('label', rule['path'])} (op id={op_id})")
    return {
        "ok": not violations,
        "violations": violations,
        "refusal_reason": "operation member is forbidden" if violations else None,
        "op_count": len(all_ops),
    }


def _check_auth_tier(op: dict[str, Any], declared_tier: Any) -> bool:
    """Compare the op's auth arrays with the tier declared by its selected family."""
    active = _resolve_path(op, "body.required_auths")
    posting = _resolve_path(op, "body.required_posting_auths")
    active_list = active[0] if active else None
    posting_list = posting[0] if posting else None
    if not (isinstance(active_list, list) and isinstance(posting_list, list)):
        return False
    if declared_tier == "Posting":
        return len(active_list) == 0 and len(posting_list) > 0
    if declared_tier == "Active":
        return len(posting_list) == 0 and len(active_list) > 0
    return False


def _check(rule: dict[str, Any], values: list[Any], account: str) -> bool:
    check = rule["check"]
    if check == "eq":
        return len(values) > 0 and all(v == rule["value"] for v in values)
    if check == "in":
        return len(values) > 0 and all(v in rule["value"] for v in values)  # Absent fields fail closed.
    if check == "array_nonempty":
        return len(values) > 0 and all(isinstance(v, list) and len(v) > 0 for v in values)
    if check == "array_empty":
        return len(values) > 0 and all(isinstance(v, list) and len(v) == 0 for v in values)
    if check == "array_equals_header_account":
        return len(values) > 0 and all(isinstance(v, list) and v == [account] for v in values)
    if check == "present":
        return len(values) > 0 and all(v is not None for v in values)
    if check == "array_strings_nonempty":
        return len(values) > 0 and all(isinstance(v, list) and len(v) > 0 and all(isinstance(item, str) and item for item in v) for v in values)
    return False


def _rule_passes(rule: dict[str, Any], resolution: _PathResolution, account: str) -> bool:
    if resolution.missing:
        if rule.get("required", True):
            return False
        if not resolution.values:
            return True
    if not resolution.values and resolution.empty_wildcard and rule["check"] == "in":
        return True
    return _check(rule, resolution.values, account)
