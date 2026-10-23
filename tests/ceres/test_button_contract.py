from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from tests.ceres.vm_context import ENTRY, _esm_evaluation_order, run_in_module_context


ROOT = Path(__file__).parents[2]
STATIC = ROOT / "ceres" / "static"
MANIFEST_PATH = Path(__file__).parent / "fixtures" / "ceres-button-contract.json"
BUTTONS_CSS = STATIC / "buttons.css"
CERES_CSS = STATIC / "ceres.css"
CANONICAL_CSS = STATIC / "canonical.css"
INDEX = STATIC / "index.html"

OWNED_PROPERTIES = {
    "appearance", "padding", "padding-block", "padding-inline", "padding-top",
    "padding-right", "padding-bottom", "padding-left", "width", "height",
    "min-width", "min-height", "max-width", "max-height", "inline-size",
    "block-size", "min-inline-size", "min-block-size", "max-inline-size",
    "max-block-size", "border", "border-color", "border-width", "border-style",
    "border-radius", "background", "background-color", "background-image",
    "color", "opacity", "filter", "box-shadow", "transform", "transition",
    "font", "font-family", "font-size", "font-weight", "font-style",
    "line-height", "text-align", "text-decoration", "text-decoration-color",
    "cursor", "fill", "stroke",
}

CONTROL_CONSTRUCTOR = re.compile(
    r"<button\b[^>]*>|<summary\b[^>]*>|"
    r"<(?:div|article|label|li|tr|th)\b[^>]*\brole=[\"'](?:button|combobox|option)[\"'][^>]*>|"
    r"<input\b[^>]*\btype=[\"'](?:submit|checkbox)[\"'][^>]*>|"
    r"createElement\([\"']button[\"']\)",
    re.IGNORECASE | re.DOTALL,
)


def _manifest() -> dict:
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def _strip_css_comments(text: str) -> str:
    return re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)


def _css_rules(text: str, context: tuple[str, ...] = ()):
    text = _strip_css_comments(text)
    index = 0
    while index < len(text):
        opening = text.find("{", index)
        if opening < 0:
            return
        prelude = " ".join(text[index:opening].strip().split())
        depth = 1
        cursor = opening + 1
        quote = None
        while cursor < len(text) and depth:
            char = text[cursor]
            if quote:
                if char == quote and text[cursor - 1] != "\\":
                    quote = None
            elif char in "'\"":
                quote = char
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
            cursor += 1
        if depth:
            raise AssertionError(f"unclosed CSS block after {prelude!r}")
        body = text[opening + 1:cursor - 1]
        if prelude.startswith(("@media", "@supports", "@layer", "@container")):
            yield from _css_rules(body, context + (prelude,))
        elif prelude and not prelude.startswith("@"):
            yield context, prelude, body
        index = cursor


def _declarations(body: str) -> dict[str, str]:
    declarations = {}
    for part in body.split(";"):
        if ":" not in part:
            continue
        name, value = part.split(":", 1)
        declarations[name.strip()] = value.strip()
    return declarations


def _control_markers(manifest: dict) -> set[str]:
    markers = set()
    for control in manifest["controls"]:
        markers.update(f".{name}" for name in re.findall(r"\.([A-Za-z_][A-Za-z0-9_-]*)", control["selector"]))
        markers.update(f"#{name}" for name in re.findall(r"#([A-Za-z_][A-Za-z0-9_-]*)", control["selector"]))
    markers.update({
        ".btn-close", ".harvest-bar-btn", ".picker-keep-open", ".picker-toggle",
        ".candidate-grid-card", ".candidate-row", ".candidate-card", ".fp-slot",
        ".plot-main-row", ".theme-picker", ".theme-tray",
    })
    return markers


def _direct_marker(selector: str, marker: str) -> bool:
    for match in re.finditer(re.escape(marker), selector):
        tail = selector[match.end():]
        boundaries = [point for point in (tail.find(","), tail.find(")")) if point >= 0]
        tail = tail[:min(boundaries or [len(tail)])]
        if not re.search(r"\s|>", tail):
            return True
    return False


def _is_control_rule(selector: str, properties: set[str], manifest: dict) -> bool:
    if re.search(
        r"(^|[\s>,:+~])(?:button|summary)\b|\[role=[\"']?(?:button|combobox|option)",
        selector,
    ):
        return True
    markers = _control_markers(manifest)
    if any(_direct_marker(selector, marker) for marker in markers):
        return True
    return bool(properties & {"fill", "stroke"}) and any(marker in selector for marker in markers)


def _legacy_snapshot(css: str, manifest: dict, source: str) -> list[dict]:
    snapshot = []
    for context, selector, body in _css_rules(css):
        properties = sorted(set(_declarations(body)) & OWNED_PROPERTIES)
        if not properties or not _is_control_rule(selector, set(properties), manifest):
            continue
        snapshot.append({
            "selector": selector,
            "properties": properties,
            "context": list(context),
            "source": source,
        })
    return snapshot


def _allowed_legacy_snapshot(manifest: dict) -> list[dict]:
    return [
        {
            "selector": entry["selector"],
            "properties": entry["properties"],
            "context": entry["context"],
            "source": entry.get("source", "ceres.css"),
        }
        for entry in manifest["legacy_allowlist"]
    ]


def _assert_style_ownership(css: str, manifest: dict) -> None:
    observed = _legacy_snapshot(css, manifest, "ceres.css")
    observed += _legacy_snapshot(CANONICAL_CSS.read_text(encoding="utf-8"), manifest, "canonical.css")
    allowed = _allowed_legacy_snapshot(manifest)
    assert observed == allowed, (
        "legacy button style ownership changed; remove/migrate an entry or record a reviewed case "
        f"instead of growing the allowlist silently\nobserved={observed!r}\nallowed={allowed!r}"
    )


def _selector_markers(selector: str) -> set[str]:
    markers = set(re.findall(r"[.#]([A-Za-z_][A-Za-z0-9_-]*)", selector))
    markers.update(re.findall(r"\[([A-Za-z_][A-Za-z0-9_-]*)", selector))
    if "summary" in selector:
        markers.add("summary")
    if "button" in selector:
        markers.add("button")
    return markers


def _production_sources(extra: dict[str, str] | None = None) -> dict[str, str]:
    sources = {"index.html": INDEX.read_text(encoding="utf-8")}
    sources.update({path.name: path.read_text(encoding="utf-8") for path in _esm_evaluation_order(ENTRY)})
    if extra:
        sources.update(extra)
    return sources


def _assert_control_coverage(sources: dict[str, str], manifest: dict) -> None:
    uncovered = []
    for renderer, source in sources.items():
        entries = [entry for entry in manifest["controls"] if renderer in entry["renderer"].split("|")]
        for match in CONTROL_CONSTRUCTOR.finditer(source):
            line_start = source.rfind("\n", 0, match.start()) + 1
            if source[line_start:match.start()].lstrip().startswith("//"):
                continue
            window = source[max(0, match.start() - 500):min(len(source), match.end() + 700)]
            if match.group(0).lower() == "<button>" and "return '<button>'" not in window:
                continue
            words = set(re.findall(r"[A-Za-z_][A-Za-z0-9_-]*", window))
            if not any(_selector_markers(entry["selector"]) & words for entry in entries):
                line = source.count("\n", 0, match.start()) + 1
                uncovered.append(f"{renderer}:{line}:{match.group(0)[:100]}")
    assert not uncovered, "unregistered production controls:\n" + "\n".join(uncovered)


def _assert_no_inline_control_style(sources: dict[str, str]) -> None:
    violations = []
    for renderer, source in sources.items():
        for match in CONTROL_CONSTRUCTOR.finditer(source):
            tag = match.group(0)
            style = re.search(r"\bstyle=[\"'][^\"']*(?:border-radius|padding|background|color|font|box-shadow|filter|transform)[^\"']*[\"']", tag, re.IGNORECASE)
            if style:
                violations.append(f"{renderer}:{style.group(0)}")
    assert not violations, "inline control styling is legacy ownership: " + ", ".join(violations)


def _assert_contract_cases(css: str, manifest: dict) -> None:
    registered = set(manifest["contract"]["cases"])
    used = set(re.findall(r'data-control-case~?=["\']([a-z-]+)', css))
    unknown = used - registered
    assert not unknown, f"unregistered button cases: {sorted(unknown)}"
    for entry in manifest["controls"]:
        cases = entry.get("case", "").split("+") if entry.get("case") else []
        cases += sorted(set(entry.get("case_by_action", {}).values()))
        cases = list(dict.fromkeys(cases))
        assert set(cases) <= registered
        if len(cases) > 1:
            assert "+".join(cases) in manifest["contract"]["allowed_case_combinations"]
    assert '[data-control-case~="signal"]' in css
    assert '[data-control-case~="destructive"]' in css
    assert '[data-control-case~="commit"]' in css


def _hex_rgb(value: str) -> tuple[float, float, float]:
    value = value.strip()
    assert re.fullmatch(r"#[0-9a-fA-F]{3}|#[0-9a-fA-F]{6}", value), value
    if len(value) == 4:
        value = "#" + "".join(char * 2 for char in value[1:])
    return tuple(int(value[index:index + 2], 16) / 255 for index in (1, 3, 5))


def _mix(first: tuple[float, ...], percent: float, second: tuple[float, ...]) -> tuple[float, ...]:
    weight = percent / 100
    return tuple(a * weight + b * (1 - weight) for a, b in zip(first, second))


def _luminance(rgb: tuple[float, ...]) -> float:
    def channel(value: float) -> float:
        return value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4
    red, green, blue = (channel(value) for value in rgb)
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def _contrast(first: tuple[float, ...], second: tuple[float, ...]) -> float:
    bright, dark = sorted((_luminance(first), _luminance(second)), reverse=True)
    return (bright + 0.05) / (dark + 0.05)


def _theme_tokens(canonical_css: str) -> dict[str, dict[str, tuple[float, ...]]]:
    themes = {}
    for _, selector, body in _css_rules(canonical_css):
        theme = None
        for name in ("alchemical", "dark", "light", "wood"):
            if f'[data-theme="{name}"]' in selector:
                theme = name
                break
        if not theme:
            continue
        values = {}
        for name, value in _declarations(body).items():
            if name.startswith("--") and re.fullmatch(r"#[0-9a-fA-F]{3}|#[0-9a-fA-F]{6}", value):
                values[name[2:]] = _hex_rgb(value)
        if {"bg2", "bg3", "heading", "primary", "accent", "shadow", "hi"} <= values.keys():
            themes[theme] = values
    assert themes.keys() == {"alchemical", "dark", "light", "wood"}
    return themes


def _choice_selected_percent(css: str) -> float:
    match = re.search(
        r"\.ceres-btn--choice:is\(\[aria-pressed=.*?\{.*?background:\s*color-mix\(in srgb, var\(--accent\)\s+(\d+(?:\.\d+)?)%,\s*var\(--bg3\)\)",
        css,
        re.DOTALL,
    )
    assert match, "choice selected recipe is missing"
    return float(match.group(1))


def _assert_recipe_contrast(buttons_css: str, canonical_css: str) -> None:
    selected_percent = _choice_selected_percent(buttons_css)
    for theme, token in _theme_tokens(canonical_css).items():
        enabled_pairs = [
            (token["heading"], token["bg3"], "secondary default"),
            (token["heading"], _mix(token["accent"], 10, token["bg3"]), "secondary hover"),
            (token["heading"], _mix(token["accent"], 18, token["bg3"]), "secondary press"),
            (token["heading"], _mix(token["accent"], selected_percent, token["bg3"]), "choice selected"),
            (token["heading"], _mix(token["primary"], 18, token["bg3"]), "signal"),
        ]
        enabled_pairs.extend(
            (token["hi"], _mix(token["primary"], percent, token["shadow"]), f"primary {percent}% stop")
            for percent in (42, 50, 60, 68)
        )
        for foreground, background, state in enabled_pairs:
            assert _contrast(foreground, background) >= 4.5, f"{theme} {state} text contrast"
        assert _contrast(token["heading"], token["bg3"]) >= 3, f"{theme} focus boundary contrast"


def _assert_palette(css: str) -> None:
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b|(?<!s)\brgba?\(|\bhsla?\(", css), "raw colour literal in buttons.css"
    allowed = {
        # Global palette tokens (canonical.css, same tier as danger/gold/hi/primary)
        # "ok" is the semantic status colour buttons.css's
        # selected-terrain-affinity/checked-choice treatments use; it was already a
        # defined canonical.css palette token when buttons.css started consuming it
        # just never registered here.
        "accent", "bg2", "bg3", "border-bright", "danger", "font-body", "gold", "gold-dim",
        "heading", "hi", "ok", "primary", "shadow",
        # Layout tokens local to buttons.css (declared and consumed only here, same
        # class as the existing ceres-control-* entries)
        "ceres-control-min-block", "ceres-control-inline-pad",
        "ceres-control-icon-size", "ceres-control-gap", "ceres-control-min-inline",
        "ceres-control-font-size",
        # both declared and consumed within buttons.css itself
        # (csel-leading-border: the leading-accent border width; ceres-chevron-art
        # the shared inline chevron mask image), never a global canonical.css token.
        "csel-leading-border", "ceres-chevron-art",
    }
    used = set(re.findall(r"var\(--([A-Za-z0-9_-]+)\)", css))
    assert used <= allowed, f"unregistered token aliases: {sorted(used - allowed)}"


def _assert_disabled_dispatch_guard(source: str) -> None:
    disabled = source.find(".disabled")
    aria = source.find("aria-disabled")
    activation = source.find("activate(")
    assert disabled >= 0 and aria >= 0 and activation >= 0
    assert disabled < activation and aria < activation


def test_manifest_schema_is_explicit_and_finite():
    manifest = _manifest()
    controls = manifest["controls"]
    assert len(controls) >= 50
    assert len({entry["id"] for entry in controls}) == len(controls)
    required = {"selector", "action", "renderer", "surface", "group", "treatment", "density", "status", "approval"}
    for entry in controls:
        assert required <= entry.keys(), entry["id"]
        assert entry["treatment"] in manifest["contract"]["treatments"]
        assert entry["density"] in manifest["contract"]["densities"]
        assert set(entry.get("exemptions", [])) <= set(manifest["contract"]["structural_exemptions"])
    for entry in manifest["legacy_allowlist"]:
        assert entry["selector"] and entry["properties"]
        assert entry["kind"] in {"temporary", "exception"}
        if entry["kind"] == "temporary":
            assert entry["expiry_phase"] in {2, 3, 4, 5, 6}
        else:
            assert entry["expiry_phase"] in {"E1", "E2", "E3"}


# updates the newly uncovered controls and the observed style-ownership snapshot
# incrementally; unrelated manifest approvals remain tied to their originating releases.
def test_production_controls_are_covered_by_manifest():
    _assert_control_coverage(_production_sources(), _manifest())


def test_legacy_style_ownership_matches_exact_allowlist():
    _assert_style_ownership(CERES_CSS.read_text(encoding="utf-8"), _manifest())


# (maintainer ruling on the third test): writing the ceres-btn class directly into
# shipped markup/scripts is accepted architecture, not a violation — this guard no longer checks
# for that. It keeps checking that the button stylesheet loads exactly once, after ceres.css, and
# that every rule it defines stays scoped to a registered button/shared-UI family: the anchor
# classes (contract.anchor_classes), a manifest control's own class/id markers (_control_markers)
# or — for a rule with no class at all, like a bare `details > summary` disclosure toggle — a
# manifest control whose own selector is that same bare tag/combinator phrase. A :root block is
# exempt only when every declaration in it is a custom property (a token block, not styling).
def _scope_markers(manifest: dict) -> list[str]:
    markers = [name.lstrip(".#") for name in _control_markers(manifest)]
    markers += list(manifest["contract"]["anchor_classes"])
    for control in manifest["controls"]:
        for part in control["selector"].split(","):
            phrase = " ".join(part.strip().split())
            if phrase and not re.search(r"[.#\[]", phrase):
                markers.append(phrase)
    return markers


def test_buttons_stylesheet_is_imported_once_and_scoped():
    index = INDEX.read_text(encoding="utf-8")
    assert index.count('/static/buttons.css?v={{CERES_VERSION}}') == 1
    assert index.index("/static/ceres.css") < index.index("/static/buttons.css")
    manifest = _manifest()
    markers = _scope_markers(manifest)
    css = BUTTONS_CSS.read_text(encoding="utf-8")
    unscoped = []
    for _, selector, body in _css_rules(css):
        if selector == ":root" and all(name.startswith("--") for name in _declarations(body)):
            continue
        normalized = " ".join(selector.split())
        if any(marker in normalized for marker in markers):
            continue
        unscoped.append(selector)
    assert not unscoped, f"unregistered/production-leaking selector(s) in buttons.css: {unscoped!r}"
    assert all(modifier in css for modifier in (".ceres-btn--primary", ".ceres-btn--secondary", ".ceres-btn--choice"))
    assert '.ceres-btn[hidden]' in css


def test_palette_cases_and_theme_contrast():
    css = BUTTONS_CSS.read_text(encoding="utf-8")
    manifest = _manifest()
    _assert_palette(css)
    _assert_contract_cases(css, manifest)
    _assert_recipe_contrast(css, CANONICAL_CSS.read_text(encoding="utf-8"))


def test_behaviour_and_safety_source_contracts():
    sources = _production_sources()
    picker = sources["picker-actions.js"]
    app = sources["app.js"]
    candidates = sources["candidates.js"]
    rows = sources["rows.js"]
    checkout = sources["checkout.js"]
    staging = sources["staging.js"]
    css = BUTTONS_CSS.read_text(encoding="utf-8")
    assert "worksiteAction.getAttribute('aria-disabled') === 'true'" in picker
    assert "checkoutAction.disabled || !checkoutSignableEntries().length" in picker
    assert "event.key !== 'Enter' && event.key !== ' '" in app
    assert "[role=\"button\"][data-picker-action=\"choose\"]" in app
    assert 'data-picker-action="choose"' in candidates
    assert 'role="button"${rowDisabled}' in candidates
    assert "if (event.target.closest('.slot-editor')) return" in rows
    assert "event.target.closest('[data-slot-action]')" in picker
    assert "event.target.closest('.editor-band[data-eos-detail-scaffold]')" in picker
    assert "el.dispatchEvent(new Event('change', { bubbles: true }))" in picker
    assert "input.type === 'checkbox' ? input.checked : input.value" in picker
    clear_body = picker[picker.index("function clearPickerFilters"):picker.index("export function syncPickerFilterChrome")]
    assert 'data-picker-action="sort"' not in clear_body
    assert "splinter_groups" not in clear_body
    assert "inFlightEntries.length > 0 || CERES_WORKSITE_SIGNING" in checkout
    assert "BOARD_GENERATION === context.generation && CURRENT_PLAYER === context.player" in staging
    assert '[aria-pressed="true"]' in css and ":focus" not in css[css.index('[aria-pressed="true"]'):css.index('[aria-pressed="true"]') + 150]
    assert "aria-label" in candidates and "aria-label" in sources["theme.js"]
    _assert_no_inline_control_style(sources)


def test_vm_aria_disabled_worksite_control_does_not_dispatch():
    body = r"""
let calls = 0;
const blocked = {
  dataset: { worksiteAction: 'stage' },
  getAttribute(name) { return name === 'aria-disabled' ? 'true' : null; },
};
const event = {
  target: { closest(selector) { return selector === '[data-worksite-action]' ? blocked : null; } },
  preventDefault() {}, stopPropagation() {},
};
stageThisWorksiteBuild = () => { calls += 1; };
Promise.resolve(handleSlotEditorClick(event)).then(() => {
  if (calls !== 0) throw new Error('aria-disabled worksite action dispatched');
});
"""
    run_in_module_context(body)


def test_negative_inline_radius_override_is_rejected():
    with pytest.raises(AssertionError, match="inline control styling"):
        _assert_no_inline_control_style({"injected.js": '<button style="border-radius:99px">Bad</button>'})


def test_negative_unregistered_case_is_rejected():
    css = BUTTONS_CSS.read_text(encoding="utf-8") + '\n.ceres-btn[data-control-case="glow-special"] { color: var(--heading); }'
    with pytest.raises(AssertionError, match="unregistered button cases"):
        _assert_contract_cases(css, _manifest())


def test_negative_dynamic_button_is_rejected():
    sources = _production_sources({"injected.js": "function renderInjected() { return '<button class=\"glow-special\">New</button>'; }"})
    with pytest.raises(AssertionError, match="unregistered production controls"):
        _assert_control_coverage(sources, _manifest())


def test_negative_generic_hover_rule_is_rejected():
    css = CERES_CSS.read_text(encoding="utf-8") + "\nbutton:hover { background: var(--primary); }"
    with pytest.raises(AssertionError, match="legacy button style ownership changed"):
        _assert_style_ownership(css, _manifest())


def test_negative_missing_disabled_guard_is_rejected():
    with pytest.raises(AssertionError):
        _assert_disabled_dispatch_guard("function click(target) { activate(target); }")


def test_negative_low_contrast_alchemical_selected_mix_is_rejected():
    css = BUTTONS_CSS.read_text(encoding="utf-8").replace("var(--accent) 18%", "var(--accent) 80%")
    with pytest.raises(AssertionError, match="alchemical choice selected text contrast"):
        _assert_recipe_contrast(css, CANONICAL_CSS.read_text(encoding="utf-8"))
