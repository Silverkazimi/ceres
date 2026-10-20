"""§6.6 test_boundaries.py -- the boundary enforcement the plan's §3.1 and the
2026-08-20 amendment both require: praetoria/fides/fides_land never import ceres,
ceres opens no HTTP itself, and ceres never references the deferred Plan design
(Plan/PlannedMove/Pin/planstore/revalidate -- Appendix A, explicitly out of scope for
Phase 1 and Phase 2)."""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

from .vm_context import ENTRY, _esm_evaluation_order

REPO_ROOT = Path(__file__).parent.parent.parent
DEFERRED_SYMBOLS = ("PlannedMove", "planstore", r"\brevalidate\s*\(", r"\bPlan\b", r"\bPin\b")
FIRST_PARTY_ROOTS = {"praetoria", "fides", "fides_land", "fides_cards"}
FORBIDDEN_PRAETORIA_SYMBOLS = {"allocation", "allocate", "apply", "execute", "wallet"}


def _py_files(*dirs: str) -> list[Path]:
    out = []
    for d in dirs:
        base = REPO_ROOT / d
        if base.exists():
            out.extend(base.rglob("*.py"))
    return out


def _imports(path: Path) -> list[tuple[str, str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend((alias.name.split(".", 1)[0], f"import {alias.name}") for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                root = path.parts[path.parts.index(path.parent.name)] if path.parent.name in FIRST_PARTY_ROOTS else "<relative>"
                module = "." * node.level + (node.module or "")
            else:
                root = (node.module or "").split(".", 1)[0]
                module = node.module or ""
            found.extend((root, f"from {module} import {alias.name}") for alias in node.names)
    return found


def _definition_symbols(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return [
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    ]


def _frontend_graph_source() -> str:
    return "\n".join(path.read_text(encoding="utf-8") for path in _esm_evaluation_order(ENTRY))


def test_no_praetoria_fides_module_imports_ceres():
    for path in _py_files("praetoria", "fides", "fides_land"):
        text = path.read_text(encoding="utf-8")
        assert "import ceres" not in text and "from ceres" not in text, path


def test_praetoria_fides_are_stdlib_or_first_party_only():
    stdlib = set(sys.stdlib_module_names) | {"__future__"}
    offenders = []
    for path in _py_files("praetoria", "fides", "fides_land"):
        for root, symbol in _imports(path):
            if root not in stdlib and root not in FIRST_PARTY_ROOTS:
                offenders.append(f"{path}: {symbol} (module root {root!r})")
    assert not offenders, "non-stdlib import(s):\n" + "\n".join(offenders)


def test_no_signing_or_mutation_surface_in_read_only_components():
    package_files = _py_files("praetoria", "fides", "fides_land")
    prohibition_path = REPO_ROOT / "fides" / "__init__.py"
    sign_tx_offenders = []
    for path in package_files:
        if "requestSignTx" in path.read_text(encoding="utf-8") and path != prohibition_path:
            sign_tx_offenders.append(f"{path}: requestSignTx")
    prohibition = prohibition_path.read_text(encoding="utf-8")
    if prohibition.count("requestSignTx") != 1 or "never calls `requestSignTx`" not in prohibition:
        sign_tx_offenders.append(f"{prohibition_path}: documented requestSignTx prohibition")
    assert not sign_tx_offenders, "requestSignTx outside the documented prohibition:\n" + "\n".join(sign_tx_offenders)

    offenders = []
    for path in _py_files("praetoria"):
        for name in _definition_symbols(path):
            if set(name.lower().split("_")) & FORBIDDEN_PRAETORIA_SYMBOLS:
                offenders.append(f"{path}: {name}")
    assert not offenders, "forbidden Praetoria mutation entry point(s):\n" + "\n".join(offenders)


def test_ceres_opens_no_http_itself():
    """Every HTTP-construction call in ceres/ must live in code that delegates to
 Praetoria (ceres/capture.py, which calls `LandCapture.capture` -- Praetoria owns
 the actual transport) -- ceres itself performs zero direct HTTP requests in
 Phase 1."""
    offenders = []
    for path in _py_files("ceres"):
        if path.name == "capture.py":
            continue
        text = path.read_text(encoding="utf-8")
        if "urlopen" in text or re.search(r"\bRequest\(", text):
            offenders.append(path)
    assert not offenders, offenders

    capture_text = (REPO_ROOT / "ceres" / "capture.py").read_text(encoding="utf-8")
    assert "urlopen" not in capture_text and not re.search(r"\bRequest\(", capture_text), (
        "capture.py must delegate to praetoria.land_analysis.LandCapture, not build requests itself"
    )


def test_ceres_never_references_the_deferred_plan_design():
    # DEFERRED_SYMBOLS survives for two reasons that stay substantive even
    # though this feature lifted the persistence-axis ban (plan §8.1/§8.3): (1) no
    # `Pin` -- planning entries never pin against the Praetoria recommender, and
    # `reserved_asset_refs` stays deleted; (2) no `Plan` container -- an entry is a
    # flat, independent, unversioned record, never a member of a modelled multi-move
    # object (plan §4.4).
    for path in _py_files("ceres"):
        text = path.read_text(encoding="utf-8")
        for pattern in DEFERRED_SYMBOLS:
            match = re.search(pattern, text)
            assert not match, f"{path} references deferred symbol matching {pattern!r}: {match.group(0)!r}"


def test_ceres_has_no_scheduler():
    """Mutation: adding `from sched import scheduler` to any file under `ceres/` made
 this guard fail red (verified against `ceres/capture.py` during the worker-table
 rebuild, after the planning ledger this docstring used to cite was deleted)."""
    paths = _py_files("ceres")
    assert paths
    offenders = []
    scanned_paths = []
    scheduler_terms = ("apscheduler", "crontab", "threading.Timer", "BackgroundTasks", "sched.scheduler")
    for path in paths:
        scanned_paths.append(path)
        text = path.read_text(encoding="utf-8")
        for term in scheduler_terms:
            if term in text:
                offenders.append(f"{path}: {term}")
        tree = ast.parse(text, filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "sched" or alias.name.startswith("apscheduler"):
                        offenders.append(f"{path}: import {alias.name}")
            elif isinstance(node, ast.ImportFrom) and node.module in {"sched", "apscheduler"}:
                offenders.append(f"{path}: from {node.module} import ...")
    assert set(scanned_paths) == set(paths)
    assert not offenders, offenders


def test_capture_freshness_module_imports_no_broadcast_authority():
    """`ceres/capture_freshness.py` (the standalone capture-freshness comparison that
 replaced the planning ledger's `require_restake_capture`) must stay a pure
 timestamp comparison -- never gaining the authority to build or sign anything.
 Mutation: adding `import ceres.op_preview` to it made this guard fail red."""
    path = REPO_ROOT / "ceres" / "capture_freshness.py"
    forbidden = {"op_preview", "fides", "fides_land"}
    offenders = []
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            imported = [node.module or ""] + [
                f"{node.module or ''}.{alias.name}" for alias in node.names
            ]
        else:
            continue
        if any(any(part in forbidden for part in name.split(".")) for name in imported):
            offenders.append(f"{path}: {', '.join(imported)}")
    assert not offenders, "capture_freshness.py imports broadcast authority " + "; ".join(offenders)


# Phase 1: the worksite-change modal is read-only

def test_worksite_swap_module_has_no_broadcast_surface():
    """`ceres/worksite_swap.py` (the pure logic behind swap_targets/swap_eligibility/
 pending_change) is a projection only -- no HTTP, no fides/fides_land, no op
 construction. Phase 3's op builder is a separate module, not this one."""
    src = (REPO_ROOT / "ceres" / "worksite_swap.py").read_text(encoding="utf-8")
    for term in ("fides", "requestBroadcast", "requestSignTx", "urlopen", "custom_json"):
        assert term not in src, f"worksite_swap.py must stay read-only: found {term!r}"


def test_worksite_modal_frontend_confirm_builds_no_op_client_side():
    """ Phase 4 made Confirm real: it now POSTs to the server-side op preview
 endpoint (`/api/plots/{deed}/worksite-preview`) and, once that review is shown,
 its own 'broadcast' button calls `window.hive_keychain.requestBroadcast` --
 `requestBroadcast` only, never `requestSignTx`, matching every other signing
 path in Ceres. What must still hold, and is asserted directly here: the browser
 NEVER constructs the `sm_land_operation` custom_json payload itself -- no
 `worksite_<target>_construction` string is ever assembled/concatenated in
 app.js, and no literal `"op":`/`"deed_uids":` JSON key is written by hand. The
 op is built exactly once, server-side (`ceres/worksite_broadcast.py`), and the
 browser only ever displays and re-broadcasts whatever that endpoint returned."""
    source = _frontend_graph_source()
    start = source.index("function ensureWorksiteModalOverlay")
    end = source.index("function mutateEditorSlot")
    modal_src = source[start:end]
    assert "requestSignTx" not in modal_src
    assert "data-worksite-action=\"confirm\"" in modal_src
    assert "data-worksite-action=\"broadcast\"" in modal_src

    # The browser never hand-builds the op payload: no `_construction` suffix is
    # ever concatenated onto a target string, and no `worksite_<target>_construction`
    # literal appears anywhere in the modal's own source (the only place that string
    # can legitimately appear is inside a *value* echoed back from the server's own
    # preview response and rendered read-only in the <pre> JSON dump -- which is
    # exactly what worksiteReviewHtml()'s `chunk.operations` does, never a string
    # app.js itself assembles).
    assert "_construction'" not in modal_src and '_construction"' not in modal_src
    assert "'sm_land_operation'" not in modal_src and '"sm_land_operation"' not in modal_src

    # Isolate the click-routing branch (worksite actions are their own namespace
    # separate from the card-move op-broadcast path's `data-op-action="confirm"`
    # branch a few lines further down the same function).
    branch_start = source.index("if (worksiteAction) {")
    branch_end = source.index("if (opAction) {", branch_start)
    worksite_branch = source[branch_start:branch_end]
    # Confirm's click now legitimately reaches the network (the op-preview fetch)
    # and, via the review section's own button, Keychain -- through the named
    # helper functions below, never inline. `requestSignTx` must still never appear.
    assert "requestWorksitePreview" in worksite_branch
    assert "confirmWorksiteBroadcast" in worksite_branch
    assert "requestSignTx" not in worksite_branch
    # The click branch itself never calls `fetch(`/`requestBroadcast` directly
    # both live inside the named helper functions it dispatches to, which is what
    # keeps the actual op construction and signing logic in ONE place, not
    # duplicated inline in the click router.
    assert "fetch(" not in worksite_branch
    assert "requestBroadcast(" not in worksite_branch


def test_worksite_preview_helper_is_the_only_op_builder_entry_point():
    """`requestWorksitePreview` must be the SOLE place app.js calls the worksite-
 preview endpoint -- proving there is exactly one op-construction entry point,
 not one per call site that could drift out of sync with the server-side
 refusal/allowlist logic."""
    source = _frontend_graph_source()
    # The trailing backtick (template-literal close) distinguishes the one real
    # fetch() call site from a prose comment mentioning the same endpoint path.
    assert source.count("/worksite-preview`") == 1


def test_worksite_broadcast_uses_posting_tier_and_request_broadcast_only():
    """`confirmWorksiteBroadcast` must sign with 'Posting' (never 'Active') and
 call `requestBroadcast` (never `requestSignTx`) -- the op family's declared
 tier (`fides_land/render.py`'s `sm_land_operation` family) and Ceres's one
 permitted Keychain call, matching every other broadcast path in this file."""
    source = _frontend_graph_source()
    start = source.index("export function confirmWorksiteBroadcast")
    end = source.index("export async function verifyWorksiteBroadcast", start)
    src = source[start:end]
    assert "requestSignTx" not in src
    assert "requestBroadcast(preview.payload.account, chunk.operations, 'Posting'" in src
