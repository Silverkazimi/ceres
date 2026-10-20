"""§6.6 test_boundaries.py -- the boundary enforcement the plan's §3.1 and the
2026-08-20 amendment both require: praetoria/fides/fides_land never import ceres,
ceres opens no HTTP itself, and ceres never references the deferred Plan design
(Plan/PlannedMove/Pin/planstore/revalidate -- Appendix A, explicitly out of scope for
Phase 1 and Phase 2)."""

from __future__ import annotations

import ast
import copy
import io
import re
import sys
import tokenize
from pathlib import Path

import pytest

from .vm_context import ENTRY, _esm_evaluation_order

REPO_ROOT = Path(__file__).parent.parent.parent
DEFERRED_SYMBOLS = ("PlannedMove", "planstore", r"\brevalidate\s*\(", r"\bPlan\b", r"\bPin\b")
FIRST_PARTY_ROOTS = {"praetoria", "fides", "fides_land", "fides_cards", "mnemosyne"}
FORBIDDEN_PRAETORIA_SYMBOLS = {"allocation", "allocate", "apply", "execute", "wallet"}
# Named exceptions. Each one is asserted by its own test below; none is a blanket allowance.
# orjson is the optional JSON codec: imported only by praetoria/fastjson.py, inside a
# try/except, so the stdlib codec takes over when it is missing.
OPTIONAL_THIRD_PARTY_IMPORTS = {("praetoria/fastjson.py", "orjson")}
# four private helpers whose names contain "allocation" (the PP cap arithmetic and an
# out-of-memory test on a decoder error); test_exempt_allocation_helpers_are_pure.
PURE_ALLOCATION_HELPERS = {
    ("praetoria/land_reallocate.py", "_cap_allocation"),
    ("praetoria/land_reallocate.py", "_scoring_allocation"),
    ("praetoria/land_reallocate.py", "_allocation_failure_reason"),
    ("praetoria/fastjson.py", "_is_allocation_failure"),
}
# the bounded artwork GETs (card art cache, listed static assets); test_artwork_downloads_are_bounded_image_gets.
ARTWORK_DOWNLOAD_MODULES = ("card_art_cache.py", "asset_sources.py")
# the fixture-mode urllib stand-in, which refuses unless a fixture set is installed.
FIXTURE_URLOPEN_MODULE = "fixture_upstream.py"
# the single threading.Timer: it kills a capture child process that ignored terminate().
CAPTURE_KILL_TIMER = ("capture.py", "_terminate_child_locked")
# Ceres must never call the game API or the Hive chain over HTTP itself.
GAME_OR_CHAIN_HOST_HINTS = ("splinterlands", "hive", "steemmonsters", "rpc", "api.")


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


def _rel(path: Path) -> str:
    return path.relative_to(REPO_ROOT).as_posix()


def _in_ceres(path: Path, name: str) -> bool:
    return path.parent == REPO_ROOT / "ceres" and path.name == name


def _code_names(text: str) -> list[tuple[str, str]]:
    """(identifier, next significant token) for the code only: comments, docstrings and other
    string literals are not scanned."""
    skip = (tokenize.COMMENT, tokenize.NL, tokenize.NEWLINE, tokenize.INDENT, tokenize.DEDENT)
    tokens = [tok for tok in tokenize.generate_tokens(io.StringIO(text).readline) if tok.type not in skip]
    return [
        (tok.string, tokens[i + 1].string if i + 1 < len(tokens) else "")
        for i, tok in enumerate(tokens)
        if tok.type == tokenize.NAME
    ]


def _deferred_identifier(text: str) -> str | None:
    for name, following in _code_names(text):
        if name in {"Plan", "Pin", "PlannedMove"} or "planstore" in name.lower():
            return name
        if name == "revalidate" and following == "(":
            return "revalidate("
    return None


def _timer_owners(tree: ast.AST) -> list[str]:
    """Name of the innermost enclosing function of every `threading.Timer(...)` call."""
    owners = []

    def visit(node: ast.AST, owner: str) -> None:
        for child in ast.iter_child_nodes(node):
            if (isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute) and child.func.attr == "Timer"
                    and isinstance(child.func.value, ast.Name) and child.func.value.id == "threading"):
                owners.append(owner)
            visit(child, child.name if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) else owner)

    visit(tree, "<module>")
    return owners


def _frontend_graph_source() -> str:
    return "\n".join(path.read_text(encoding="utf-8") for path in _esm_evaluation_order(ENTRY))


def test_no_praetoria_fides_module_imports_ceres():
    for path in _py_files("praetoria", "fides", "fides_land"):
        text = path.read_text(encoding="utf-8")
        assert "import ceres" not in text and "from ceres" not in text, path


def test_praetoria_fides_are_stdlib_or_first_party_only():
    stdlib = set(sys.stdlib_module_names) | {"__future__"}
    offenders = []
    for path in _py_files("praetoria", "fides", "fides_land", "mnemosyne"):
        for root, symbol in _imports(path):
            if root not in stdlib and root not in FIRST_PARTY_ROOTS and (_rel(path), root) not in OPTIONAL_THIRD_PARTY_IMPORTS:
                offenders.append(f"{path}: {symbol} (module root {root!r})")
    assert not offenders, "non-stdlib import(s):\n" + "\n".join(offenders)


def test_mnemosyne_never_imports_ceres():
    """mnemosyne is the first-party accounting package the read-only packages share; it must
 not become a way from them into ceres."""
    paths = _py_files("mnemosyne")
    assert paths
    for path in paths:
        assert "ceres" not in {root for root, _symbol in _imports(path)}, path


def test_orjson_is_optional_and_confined_to_fastjson():
    """orjson stays optional: its one import sits in a try whose handler falls back to the stdlib."""
    tree = ast.parse((REPO_ROOT / "praetoria" / "fastjson.py").read_text(encoding="utf-8"))
    imports = [n for n in ast.walk(tree) if isinstance(n, ast.Import) and any(a.name == "orjson" for a in n.names)]
    guarded = [
        child for node in ast.walk(tree) if isinstance(node, ast.Try) and node.handlers
        for child in node.body if child in imports
    ]
    assert len(imports) == 1 and guarded == imports, "orjson must be imported once, inside try/except"
    assert not any(isinstance(n, ast.ImportFrom) and (n.module or "").startswith("orjson") for n in ast.walk(tree))


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
            if set(name.lower().split("_")) & FORBIDDEN_PRAETORIA_SYMBOLS and (_rel(path), name) not in PURE_ALLOCATION_HELPERS:
                offenders.append(f"{path}: {name}")
    assert not offenders, "forbidden Praetoria mutation entry point(s):\n" + "\n".join(offenders)


_PURE_CALLS = {"float", "int", "str", "min", "max", "sum", "abs", "len", "sorted", "any", "all", "isfinite", "get", "values", "lower", "_cap_allocation"}


def test_exempt_allocation_helpers_are_pure():
    """The exempt helpers are private module-level functions that call only pure builtins (and
 each other), touch no global or nonlocal state, write only to containers they created, and
 leave their inputs unchanged."""
    for rel, name in sorted(PURE_ALLOCATION_HELPERS):
        assert name.startswith("_"), name
        tree = ast.parse((REPO_ROOT / rel).read_text(encoding="utf-8"))
        defs = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name]
        assert len(defs) == 1, f"{rel}: {name} must be one module-level function"
        params = {a.arg for a in defs[0].args.args + defs[0].args.kwonlyargs}
        own_locals: set[str] = set()
        for node in ast.walk(defs[0]):
            assert not isinstance(node, (ast.Global, ast.Nonlocal, ast.Yield, ast.YieldFrom, ast.Await)), f"{name}: {ast.dump(node)}"
            if isinstance(node, ast.Call):
                callee = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", None)
                if callee == "append":  # only onto a list the helper itself created
                    base = node.func.value
                    assert isinstance(base, ast.Name) and base.id in own_locals, f"{name} appends outside its own locals"
                    continue
                assert callee in _PURE_CALLS, f"{name} calls {callee!r}"
            if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
                for target in node.targets if isinstance(node, ast.Assign) else [node.target]:
                    if isinstance(target, ast.Name):
                        assert target.id not in params, f"{name} rebinds parameter {target.id}"
                        own_locals.add(target.id)
                    else:
                        base = getattr(target, "value", None)
                        assert isinstance(base, ast.Name) and base.id in own_locals, f"{name} writes outside its own locals"

    from praetoria import fastjson, land_reallocate

    cards = [
        {"asset_ref": "card-a", "base": 40.0, "slot": 1},
        {"asset_ref": "card-b", "base": 30.0, "slot": 2},
        {"asset_ref": "card-c", "base": 20.0, "slot": None},
    ]
    snapshot = copy.deepcopy(cards)
    for cap in (None, 50.0, 500.0):
        land_reallocate._cap_allocation(cards, cap)
        land_reallocate._scoring_allocation(cards, cap)
        land_reallocate._allocation_failure_reason(cards, cap)
    assert cards == snapshot
    assert fastjson._is_allocation_failure(ValueError("Not enough memory to allocate buffer")) is True


def test_ceres_opens_no_http_itself():
    """Every HTTP-construction call in ceres/ must live in code that delegates to
    Praetoria (ceres/capture.py, which calls `LandCapture.capture` -- Praetoria owns
    the actual transport) -- ceres itself performs zero direct HTTP requests in
    Phase 1."""
    offenders = []
    for path in _py_files("ceres"):
        if path.name == "capture.py":
            continue
        if any(_in_ceres(path, name) for name in ARTWORK_DOWNLOAD_MODULES):
            continue  # test_artwork_downloads_are_bounded_image_gets
        text = path.read_text(encoding="utf-8")
        if _in_ceres(path, FIXTURE_URLOPEN_MODULE):
            text = re.sub(r"\b(fixture_urlopen|FIXTURE_URLOPEN)\b", "", text)  # test_fixture_urlopen_fails_closed
        if "urlopen" in text or re.search(r"\bRequest\(", text):
            offenders.append(path)
    assert not offenders, offenders

    capture_text = (REPO_ROOT / "ceres" / "capture.py").read_text(encoding="utf-8")
    assert "urlopen" not in capture_text and not re.search(r"\bRequest\(", capture_text), (
        "capture.py must delegate to praetoria.land_analysis.LandCapture, not build requests itself"
    )


def test_artwork_downloads_are_bounded_image_gets():
    """The two artwork modules are the only places ceres opens HTTP itself: one GET each, to a
 pinned image host checked before the request, with a timeout, a byte cap and redirects
 refused. Neither can reach the game API or the chain."""
    from ceres import asset_sources, card_art_cache

    for host in set(asset_sources.ALLOWED_HOSTS) | {card_art_cache.CARD_CDN_HOST}:
        assert host.endswith(".cloudfront.net"), host
        assert not any(hint in host for hint in GAME_OR_CHAIN_HOST_HINTS), host
    for name in ARTWORK_DOWNLOAD_MODULES:
        text = (REPO_ROOT / "ceres" / name).read_text(encoding="utf-8")
        assert len(re.findall(r"\bRequest\(", text)) == 1, f"{name}: exactly one request construction"
        assert "urlopen" not in text, f"{name}: only the redirect-refusing opener may send it"
        assert "class _NoRedirect(urllib.request.HTTPRedirectHandler)" in text, f"{name}: redirects must be refused"
        assert "timeout=" in text, f"{name}: the request needs a timeout"
        assert re.search(r"\.read\((MAX_BYTES|MAX_IMAGE_BYTES) \+ 1\)", text), f"{name}: the body read must be capped"
        assert 'method="POST"' not in text and "data=" not in text, f"{name}: GET only"
    assert asset_sources.MAX_BYTES <= 16 * 1024 * 1024 and card_art_cache.MAX_IMAGE_BYTES <= 16 * 1024 * 1024
    with pytest.raises(ValueError):
        card_art_cache.fetch_card_art("https://api.example.invalid/cards/get_details", 1.0)


def test_fixture_urlopen_fails_closed():
    """The fixture-mode urllib stand-in answers only from an installed fixture set; otherwise it refuses."""
    from ceres import fixture_upstream

    text = (REPO_ROOT / "ceres" / FIXTURE_URLOPEN_MODULE).read_text(encoding="utf-8")
    assert "urllib.request.urlopen" not in text and not re.search(r"\bRequest\(", text)
    assert fixture_upstream._active is None
    with pytest.raises(RuntimeError):
        fixture_upstream.fixture_urlopen(object())


def test_ceres_never_references_the_deferred_plan_design():
    # DEFERRED_SYMBOLS survives for two reasons that stay substantive even
    # though this feature lifted the persistence-axis ban (plan §8.1/§8.3): (1) no
    # `Pin` -- planning entries never pin against the Praetoria recommender, and
    # `reserved_asset_refs` stays deleted; (2) no `Plan` container -- an entry is a
    # flat, independent, unversioned record, never a member of a modelled multi-move
    # object (plan §4.4).
    # Only code identifiers and definitions count (the names in DEFERRED_SYMBOLS, applied to
    # tokens): a comment or docstring using the English word "Plan" is not the Plan model.
    for path in _py_files("ceres"):
        found = _deferred_identifier(path.read_text(encoding="utf-8"))
        assert not found, f"{path} references deferred symbol {found!r}"


def test_deferred_plan_scan_still_catches_code():
    assert _deferred_identifier("class Plan:\n    pass\n") == "Plan"
    assert _deferred_identifier("x = Pin(1)\n") == "Pin"
    assert _deferred_identifier("from ceres import planstore\n") == "planstore"
    assert _deferred_identifier("moves: list[PlannedMove] = []\n") == "PlannedMove"
    assert _deferred_identifier("result = revalidate(entry)\n") == "revalidate("
    assert _deferred_identifier('def f():\n    """Plan donors first."""\n    return 1  # Pin\n') is None


def test_capture_kill_timer_is_the_one_named_exception():
    """The one permitted threading.Timer is a one-shot kill of a capture child process that
 ignored terminate(); it lives in the capture supervisor's _terminate_child_locked."""
    tree = ast.parse((REPO_ROOT / "ceres" / CAPTURE_KILL_TIMER[0]).read_text(encoding="utf-8"))
    assert _timer_owners(tree) == [CAPTURE_KILL_TIMER[1]]
    fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == CAPTURE_KILL_TIMER[1])
    body = ast.unparse(fn)
    assert "child.terminate()" in body and "child.kill()" in body
    assert _timer_owners(ast.parse("import threading\ndef poll():\n    threading.Timer(5, poll).start()\n")) == ["poll"]


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
        tree = ast.parse(text, filename=str(path))
        if _in_ceres(path, CAPTURE_KILL_TIMER[0]) and _timer_owners(tree) == [CAPTURE_KILL_TIMER[1]]:
            text = text.replace("threading.Timer", "", 1)  # test_capture_kill_timer_is_the_one_named_exception
        for term in scheduler_terms:
            if term in text:
                offenders.append(f"{path}: {term}")
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
    assert not offenders, "capture_freshness.py imports broadcast authority: " + "; ".join(offenders)


# Phase 1: the worksite-change modal is read-only

def test_worksite_swap_module_has_no_broadcast_surface():
    """`ceres/worksite_swap.py` (the pure logic behind swap_targets/swap_eligibility/
    pending_change) is a projection only -- no HTTP, no fides/fides_land, no op
    construction. Phase 3's op builder is a separate module, not this one."""
    src = (REPO_ROOT / "ceres" / "worksite_swap.py").read_text(encoding="utf-8")
    for term in ("fides", "requestBroadcast", "requestSignTx", "urlopen", "custom_json"):
        assert term not in src, f"worksite_swap.py must stay read-only: found {term!r}"


def _static(name: str) -> str:
    return (REPO_ROOT / "ceres" / "static" / name).read_text(encoding="utf-8")


def _function_source(source: str, header: str) -> str:
    """The text of one top-level JS function: from its header up to the next top-level declaration."""
    start = source.index(header)
    following = re.search(r"\n(?:export )?(?:async )?(?:function|const|let|class) ", source[start + len(header):])
    return source[start:start + len(header) + following.start()] if following else source[start:]


def _worksite_click_branch() -> str:
    source = _static("picker-actions.js")
    start = source.index("if (worksiteAction) {")
    end = source.index("\n  }\n", start)
    return source[start:end]


def test_worksite_modal_offers_stage_and_stage_and_sign_without_building_ops():
    """The worksite window has two ways out: "Stage change" (records the pick, no server call,
 no wallet) and "Stage and sign" (stages it, then hands the plot to the Changes signer). The
 browser never builds the `sm_land_operation` payload: no `worksite_<target>_construction`
 string is assembled, and no op family name or hand-written op JSON key appears in the
 worksite code. The op is built once, server-side, and the browser only shows and signs
 what the preview endpoint returned."""
    worksite = _static("worksite.js")
    assert 'data-worksite-action="stage"' in worksite
    assert 'data-worksite-action="stage-and-sign"' in worksite
    for forbidden in ("requestSignTx", "requestBroadcast(", "hive_keychain", "'sm_land_operation'", '"sm_land_operation"',
                      "_construction'", '_construction"', "_construction`", '"deed_uids":', "'deed_uids':"):
        assert forbidden not in worksite, f"worksite.js must not contain {forbidden!r}"
    assert not re.search(r"\bid:\s*['\"]sm_", worksite), "worksite.js must not write a custom_json id"

    branch = _worksite_click_branch()
    for forbidden in ("requestSignTx", "requestBroadcast", "hive_keychain", "fetch(", "Through" "Gate("):
        assert forbidden not in branch, f"the worksite click branch must not call {forbidden!r} inline"
    # Stage: records the pick only.
    stage = branch[branch.index("worksiteActionKind === 'stage')"):branch.index("worksiteActionKind === 'stage-and-sign'")]
    assert "stageThisWorksiteBuild(row, WORKSITE_MODAL_SELECTED)" in stage
    assert "confirmCheckoutWithWorksites" not in stage and "requestWorksitePreview" not in stage
    # Stage and sign: stages first, then the Changes signer, scoped to this one plot.
    stage_and_sign = branch[branch.index("worksiteActionKind === 'stage-and-sign'"):branch.index("worksiteActionKind === 'cancel-broadcast'")]
    assert stage_and_sign.index("stageThisWorksiteBuild(") < stage_and_sign.index("confirmCheckoutWithWorksites({ deedUid: row.deed_uid })")
    # Picking a tile asks the server for the preview; a sign button only signs that preview.
    assert "requestWorksitePreview(row, nextTarget" in branch
    assert "confirmWorksiteBroadcast(row, worksiteAction)" in branch

    staged = _function_source(worksite, "export function stageThisWorksiteBuild")
    for forbidden in ("fetch(", "requestWorksitePreview", "Gate(", "keychain"):
        assert forbidden not in staged, f"Stage change must not reach the server or the wallet: {forbidden!r}"


def test_worksite_ops_come_only_from_the_server_preview():
    """Every worksite signature signs `chunk.operations` of a preview the server returned
 (`fetchWorksitePreview` is the one request), never operations the browser assembled."""
    worksite = _static("worksite.js")
    fetcher = _function_source(worksite, "async function fetchWorksitePreview")
    assert "/worksite-preview`" in fetcher and "method: 'POST'" in fetcher
    prepare = _function_source(worksite, "export async function prepareStagedWorksiteBuild")
    assert "await requestWorksitePreview(row, target, timeCrystals)" in prepare
    assert "const chunk = preview.payload.chunks[0]" in prepare
    confirm = _function_source(worksite, "export function confirmWorksiteBroadcast")
    assert "const chunk = preview.payload.chunks[0]" in confirm
    assert "operations =" not in worksite and "operations.push(" not in worksite, "worksite.js never builds an operations list"


def test_worksite_signing_uses_posting_authority_through_the_shared_gate():
    """Worksite signatures go through the shared broadcast gate (broadcast-gate.js), with the
 op family's Posting authority (fides_land/render.py, `sm_land_operation`), never Active and
 never a direct wallet call."""
    worksite = _static("worksite.js")
    assert "import { requestBroadcastThroughGate, requestSignatureThroughGate } from './broadcast-gate.js';" in worksite
    assert "'Active'" not in worksite and '"Active"' not in worksite

    confirm = _function_source(worksite, "export function confirmWorksiteBroadcast")
    call = re.search(r"requestBroadcastThroughGate\(preview\.payload\.account, chunk\.operations, \(response\) => \{", confirm)
    assert call, "the worksite window signs the server preview through the gate"
    # No authority argument: the gate's default applies, and that default is Posting.
    gate = _static("broadcast-gate.js")
    assert "export async function requestBroadcastThroughGate(account, operations, onResponse, authority = 'Posting', metadata = {})" in gate
    assert "export async function requestSignatureThroughGate(account, operations, onResponse, authority = 'Posting', prepared = null, metadata = {})" in gate
    tail = confirm[call.end():]
    closing = tail.index("}).then(")
    assert "'Active'" not in tail[:closing + 8]

    staged = _function_source(worksite, "export async function signStagedWorksiteBuild")
    assert "requestSignatureThroughGate(plan.account, chunk.operations, () => {}, 'Posting', envelope)" in staged
    checkout = _function_source(_static("checkout.js"), "export async function confirmCheckoutWithWorksites")
    assert "prepareSigningBatch(worksitePlans[0].plan.account, worksitePlans.map(item => item.plan.chunk.operations), 'Posting'" in checkout
    assert "requestSignTx" not in checkout and "hive_keychain" not in checkout


def test_worksite_preview_helper_is_the_only_op_builder_entry_point():
    """`requestWorksitePreview` must be the SOLE place app.js calls the worksite-
    preview endpoint -- proving there is exactly one op-construction entry point,
    not one per call site that could drift out of sync with the server-side
    refusal/allowlist logic."""
    source = _frontend_graph_source()
    # The trailing backtick (template-literal close) distinguishes the one real
    # fetch() call site from a prose comment mentioning the same endpoint path.
    assert source.count("/worksite-preview`") == 1
