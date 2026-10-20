from __future__ import annotations

import re
import subprocess

import pytest

from .vm_context import STATIC, read_entry_source, run_in_module_context


EOS_SOURCE = (STATIC / "eos.js").read_text(encoding="utf-8")
EDITOR_SOURCE = (STATIC / "editor.js").read_text(encoding="utf-8")
CSS_SOURCE = (STATIC / "ceres.css").read_text(encoding="utf-8")
HEAD_CSS_SOURCE = subprocess.check_output(
    ["git", "show", "HEAD:ceres/static/ceres.css"],
    cwd=STATIC.parents[1],
    text=True,
)
ROWS_SOURCE = (STATIC / "rows.js").read_text(encoding="utf-8")


def _function_body(source: str, name: str, end: str) -> str:
    start = source.index(name)
    return source[start:source.index(end, start)]


def _css_blocks(source: str) -> list[tuple[str, str]]:
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    return [(match.group(1).strip(), match.group(2)) for match in re.finditer(r"(?s)([^{}]+)\{([^{}]*)\}", source)]


def _css_transform_rules(source: str) -> set[tuple[tuple[str, ...], str, str]]:
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    stack: list[tuple[str, int, tuple[str, ...]]] = []
    cursor = 0
    rules: set[tuple[tuple[str, ...], str, str]] = set()
    for index, character in enumerate(source):
        if character == "{":
            selector = source[cursor:index].strip()
            stack.append((selector, index + 1, tuple(item[0] for item in stack)))
            cursor = index + 1
        elif character == "}" and stack:
            selector, start, ancestors = stack.pop()
            declarations = source[start:index]
            cursor = index + 1
            if "{" in declarations:
                continue
            match = re.search(r"(?:^|;)\s*transform\s*:\s*([^;]+)", declarations)
            if not match:
                continue
            value = re.sub(r"\s+", "", match.group(1))
            for item in selector.split(","):
                rules.add((ancestors, re.sub(r"\s+", " ", item.strip()), value))
    return rules


def _css_declarations(source: str, selector: str) -> str:
    for selectors, declarations in _css_blocks(source):
        if selector in selectors.split(','):
            return declarations
    raise AssertionError(f"missing CSS rule: {selector}")


def _css_property(declarations: str, name: str) -> str:
    match = re.search(rf"(?:^|;)\s*{re.escape(name)}\s*:\s*([^;]+)", declarations)
    if not match:
        raise AssertionError(f"missing CSS property: {name}")
    return match.group(1).strip()


def _keyframe_declarations(source: str, name: str) -> dict[str, str]:
    start = source.index(f"@keyframes {name}")
    end = source.find("@keyframes ", start + 1)
    blocks = _css_blocks(source[start:] if end < 0 else source[start:end])
    return {selectors: declarations for selectors, declarations in blocks if selectors in {"0%", "75%", "100%"}}


def _css_functions(value: str, name: str) -> list[str]:
    return [re.sub(r"\s+", "", item) for item in re.findall(rf"{re.escape(name)}\([^)]*\)", value)]


def _assert_data_watchdog(source: str) -> None:
    workers = _function_body(source, "function prepareWorkers", "export function beginPlotOpening")
    assert "if (session.dataWatchdogRevision !== revision)" in workers
    assert "session.dataWatchdogRevision = revision;" in workers
    assert "hooks.redraw(session.uid)" in workers


def test_eos_p5_first_paint_scene_and_empty_frame_guard() -> None:
    """Check scaffold markup only; geometry is proven only by the browser guard."""
    mount = _function_body(EOS_SOURCE, "export function mountPlotPresentation", "function cancelCentre")
    assert "frame.querySelectorAll('[data-eos-permanent-frame]')" in mount
    assert "frame.querySelector('.eos-worker-cover')?.setAttribute('hidden', '')" in mount
    assert "const detailScaffold = frame.hasAttribute('data-eos-detail-scaffold');" in mount
    assert "node.inert = detailScaffold" in mount
    assert "prepareWorkers(session)" not in mount
    assert "function renderEmptyEditorFrame" in EDITOR_SOURCE
    assert "data-eos-detail-scaffold" in EDITOR_SOURCE
    assert "editor-band[data-eos-detail-scaffold]" in (STATIC / "picker-actions.js").read_text(encoding="utf-8")
    empty_frame = _function_body(EDITOR_SOURCE, "function renderEmptyEditorFrame", "Renders the `.editor-facts`")
    assert "data-reveal-group" not in empty_frame and "data-slot-action" not in empty_frame
    assert "data-eos-permanent-frame" in EDITOR_SOURCE
    assert "requestAnimationFrame(() => requestAnimationFrame(() => {" in EOS_SOURCE
    assert "session.portraitEligible = true;" in EOS_SOURCE
    assert "--scene-terrain" in EOS_SOURCE and "--scene-worksite" in EOS_SOURCE


def test_eos_p5_decode_ownership_and_overtaking_guard() -> None:
    worker = _function_body(EOS_SOURCE, "function prepareWorkers", "export function beginPlotOpening")
    decode = _function_body(EOS_SOURCE, "function decodeImage", "function setStatus")
    owned = _function_body(EOS_SOURCE, "function ownDecodedNode", "function queueArtDecode")
    assert "const apertureValid =" in worker
    assert "outcome = await queueArtDecode(session, image, source, apertureValid" in worker
    assert "if (!apertureValid(outcome.identity)) return;" in worker
    assert worker.count("settlePortraitFill(session, image)") == 1
    assert "onDecoded?.(image)" in decode
    assert "await image.decode()" in owned and "onDecoded?.(image)" in owned
    assert "record.inFlightPromise.then((outcome) => outcome.kind === 'decoded'" in EOS_SOURCE
    assert "ownDecodedNode(image, outcome.url, valid, { ...outcome.identity, aperture }, session.artCleanups, onDecoded)" in EOS_SOURCE
    assert "{ plotUid: session.uid, rank }" in EOS_SOURCE


def test_eos_p5_recovery_attempts_advance_revision_and_self_drive() -> None:
    recovery = _function_body(EOS_SOURCE, "function scheduleArtRecovery", "function rememberArtFailure")
    assert "record.retryRevision = session.artRevision + 1;" in recovery
    assert "session.artRevision = record.retryRevision;" in recovery
    assert "prepareWorkers(session);" in recovery


def test_eos_p5_rows_use_one_returned_handoff_band() -> None:
    assert ROWS_SOURCE.count("const handoff = handoffPlotContent(row, previousBand, nextBand") == 2
    assert ROWS_SOURCE.count("if (nextBand && handoff && handoff !== nextBand) nextBand.replaceWith(handoff);") == 2
    assert "spliceDecodedArt(row.deed_uid, previousBand, nextBand);" in ROWS_SOURCE


def test_eos_p5_portrait_only_pop_in_contract() -> None:
    fill = _css_declarations(CSS_SOURCE, ".eos-portrait-fill")
    pop = _css_declarations(CSS_SOURCE, ".eos-portrait-fill.eos-portrait-pop-in")
    normalized = lambda value: re.sub(r"\s+", "", value)
    assert normalized(_css_property(fill, "transform-origin")) == "50%50%"
    assert normalized(_css_property(fill, "transform")) == "scale(1)"
    # a soft fade-in -- opacity only, no scale bounce.
    assert normalized(_css_property(pop, "animation-name")) == "eos-portrait-pop-opacity"
    assert normalized(_css_property(pop, "animation-duration")) == "420ms"
    assert normalized(_css_property(pop, "animation-delay")) == "0ms"
    assert _css_functions(_css_property(pop, "animation-timing-function"), "cubic-bezier") == ["cubic-bezier(0.25,0.1,0.25,1)"]
    assert "eos-portrait-pop-scale" not in CSS_SOURCE
    fade = _keyframe_declarations(CSS_SOURCE, "eos-portrait-pop-opacity")
    assert normalized(_css_property(fade["0%"], "opacity")) == "0"
    assert normalized(_css_property(fade["100%"], "opacity")) == "1"
    added_transforms = _css_transform_rules(CSS_SOURCE) - _css_transform_rules(HEAD_CSS_SOURCE)
    for ancestors, selector, _ in added_transforms:
        keyframes = [item for item in ancestors if item.startswith("@keyframes ")]
        if keyframes:
            assert len(keyframes) == 1 and re.fullmatch(r"@keyframes (?:eos-portrait-pop-[\w-]+|badge-clock-pulse)", keyframes[0])
        else:
            assert selector in {".eos-portrait-fill", ".eos-portrait-fill.eos-portrait-pop-in"}
    assert "data-eos-permanent-frame" in EDITOR_SOURCE
    assert "class=\"eos-portrait-fill\"" in EDITOR_SOURCE
    # (v0.9.92, landed before this guard) added the freshPick exemption to the same clause.
    assert "if (reducedMotion() || forcedColors() || (!session.animateAllowed && !freshPick)" in EOS_SOURCE


def test_eos_p5_reveal_scope_and_readiness_guard() -> None:
    assert "[data-reveal-group]:not([data-eos-permanent-frame])" in EOS_SOURCE
    assert "const groups = revealNodes(frame);" in EOS_SOURCE
    assert "session.sceneReleased && session.scenePending?.size" in EOS_SOURCE
    assert "session.phase = session.pendingArt.size || session.scenePending?.size ? 'pending-art'" in EOS_SOURCE
    assert "session.phase === 'revealing'" not in EOS_SOURCE
    assert "reveal-complete" not in EOS_SOURCE
    assert ".finished" not in EOS_SOURCE
    assert ".eos-portrait-fill.eos-portrait-pop-in { animation: none !important; }" in CSS_SOURCE
    assert "function applyScene(session)" in EOS_SOURCE
    assert "if (veil) veil.hidden = true;" in EOS_SOURCE


def test_eos_p5_mutation_guards() -> None:
    _assert_data_watchdog(EOS_SOURCE)
    watchdog_removed = EOS_SOURCE.replace("if (session.dataWatchdogRevision !== revision)", "if (false)", 1)
    assert watchdog_removed != EOS_SOURCE
    with pytest.raises(AssertionError):
        _assert_data_watchdog(watchdog_removed)
    queue_decode = _function_body(EOS_SOURCE, "function queueArtDecode", "function scheduleArtRecovery")
    assert "settlePortraitFill" not in queue_decode
    pop = _css_declarations(CSS_SOURCE, ".eos-portrait-fill.eos-portrait-pop-in")
    assert re.search(r"animation-delay\s*:\s*0ms\s*;", pop)
    assert "transform: translate" not in pop


def test_eos_p5_vm_reveal_selector_excludes_permanent_frames() -> None:
    run_in_module_context(r"""
      const semantic = {};
 const permanent = {};
 const frame = { querySelectorAll(selector) {
 if (selector !== '[data-reveal-group]:not([data-eos-permanent-frame])') throw new Error(selector);
 return [semantic];
 }};
 const nodes = revealNodes(frame);
 if (nodes.length !== 1 || nodes[0] !== semantic || nodes.includes(permanent)) throw new Error('R-161 reveal scope');
    """, entry=STATIC / "eos.js")
