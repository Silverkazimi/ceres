""": the Ceres-wide UI standard (`library/ceres-ui-standards.md`) as a failing test.

Owner, 2026-09-27: "This standard must be written down and byte by byte followed by each new
integration. Nothing must ever deviate from that, unless specificaly instructed and then
imemdiately added to the ceres wide standard".

Each check below names the rule of the written standard it enforces. Everything that already
deviated on master when this guard was recorded (, 2026-09-28) is listed in the dated
baseline `fixtures/ceres-ui-standard-baseline.json`. The baseline is a shrinking ratchet, like
: a NEW deviation fails; a baseline entry that no longer exists also fails, so the fix that
removes a deviation must also remove its entry. An maintainer-approved new exception is added to the
standard (the doc) AND to the baseline in the same commit, with its ticket.

Scope: desktop presentation code. Phone rules (`html.ceres-lw-le-1279 …`, `mobile-plot.css`) are
a separate design (, rule P-0) and are not constrained here beyond P-0 itself. The vendored
Iris stylesheet `canonical.css` is pull-only and excluded from the value ratchets.

Regenerate the baseline (only when the maintainer approved the change it records):
    python3 -m tests.ceres.test_ui_standard --write-baseline
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path

from tests.ceres import test_colour_tokens as s9310


ROOT = Path(__file__).parents[2]
STATIC = ROOT / "ceres" / "static"
STANDARD_DOC = ROOT / "library" / "ceres-ui-standards.md"
BASELINE_PATH = Path(__file__).parent / "fixtures" / "ceres-ui-standard-baseline.json"

CSS_FILES = ("buttons.css", "badge-colours.css", "ceres.css", "wallet.css", "mobile-plot.css", "canonical.css")
VENDORED_CSS = {"canonical.css"}          # Iris, pull-only (standard §Frozen surfaces)
PHONE_CSS = {"mobile-plot.css"}
DEV_ONLY_PAGES = {"assets-preview.html", "pill-preview.html"}  # source_exclusions
PHONE_MARKER = "ceres-lw-le-1279"

# T-3 — the type scale. New desktop `font-size` values must be one of these (spelling-normalised).
FONT_SIZE_SCALE = {
    ".8rem",                                            # fs-control ( control text)
    "var(--ceres-control-font-size)",                   # the control token itself
    "clamp(.875rem,.75rem+.15625cqi,.9375rem)",         # fs-table (, frozen)
    ".9rem",                                            # fs-ledger (, Harvest all)
    "1rem",                                             # fs-body
    ".75rem",                                           # fs-label
    ".7rem",                                            # fs-caption, the readable minimum (11.2px)
    "1.25rem",                                          # fs-h-panel
    "2rem", "var(--step-2)",                            # fs-h-display
    "inherit",
}
# E-1 — radii. New desktop radius values must be one of these.
RADIUS_SCALE = {
    "8px",                                              # controls ()
    "var(--radius)",                                    # panels / tiles (Iris, per theme)
    "calc(var(--radius)-3px)",                          # nested inside a panel
    "5px 0 0 5px",                                      # Iris edge chips
    "999px",                                            # pills, scrollbar thumbs
    "50%",                                              # avatars, dots
    "0", "inherit",
}
RADIUS_PROPERTY = re.compile(r"^border(?:-[a-z]+)*-radius$")

# M-4 — fixed/sticky chrome. Roots whose descendants are chrome too (edge tabs, wallet/DEC drawer
# badge rail and flag, Iris, scroll-to-top, page header, filter row, sticky table head).
FIXED_CHROME_ROOTS = (
    ".theme-picker", ".theme-picker-dock", ".theme-toggle", ".theme-tray", ".theme-opt",
    ".edge-tabs", ".edge-picker", ".edge-toggle", ".edge-tray", ".edge-opt",
    ".dec-drawer", ".dec-drawer-panel", ".wallet-tabs", ".wallet-tab",
    ".badge-bar", ".badge-bar-head", ".badge-flag", "#badge-flag",
    ".scroll-top", "#header", ".board-header", ".controls-row", "#table-head-sticky",
)
M4_FORBIDDEN = {"box-shadow", "filter", "backdrop-filter", "-webkit-backdrop-filter", "all"}
TIME = re.compile(r"^-?[\d.]+m?s$")
EASING = re.compile(r"^(?:ease(?:-in|-out|-in-out)?|linear|step-start|step-end|steps\(.*\)|cubic-bezier\(.*\)|linear\(.*\))$")

# K-2 / K-3 — a text input takes its geometry and type from the control tokens; a surface may set
# its width, never its own text size, weight, height, vertical padding or corner radius.
INPUT_OWNED = {
    "font", "font-size", "font-weight", "font-family", "line-height", "letter-spacing",
    "padding", "padding-block", "padding-top", "padding-bottom", "padding-inline",
    "padding-left", "padding-right", "block-size", "height", "min-block-size", "min-height",
    "max-block-size", "max-height", "border-radius", "border-start-start-radius",
    "border-start-end-radius", "border-end-start-radius", "border-end-end-radius",
}
INPUT_DEFINITION_SELECTORS = {".ceres-wide-input"}   # the one shared definition (ceres.css, )


# CSS parsing

def _blank(match: re.Match[str]) -> str:
    return re.sub(r"[^\n]", " ", match.group())


def _strip_comments(text: str) -> str:
    return re.sub(r"/\*.*?\*/", _blank, text, flags=re.DOTALL)


def _block_end(text: str, opening: int) -> int:
    depth, quote, cursor = 1, "", opening + 1
    while cursor < len(text):
        char = text[cursor]
        if quote:
            if char == quote and text[cursor - 1] != "\\":
                quote = ""
        elif char in "\"'":
            quote = char
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return cursor
        cursor += 1
    raise AssertionError(f"unclosed CSS block at offset {opening}")


def _has_block(body: str) -> bool:
    quote = ""
    for index, char in enumerate(body):
        if quote:
            if char == quote and body[index - 1] != "\\":
                quote = ""
        elif char in "\"'":
            quote = char
        elif char == "{":
            return True
    return False


def _norm(value: str) -> str:
    return " ".join(value.split())


def css_items(text: str, context: tuple[str, ...] = (), offset: int = 0):
    """Yield ("rule", context, selector, body, offset) and ("keyframes", context, name, body, offset)."""
    cursor = 0
    while cursor < len(text):
        opening = text.find("{", cursor)
        if opening < 0:
            return
        prelude = _norm(text[cursor:opening].split(";")[-1] if text[cursor:opening].lstrip().startswith("@import") else text[cursor:opening])
        closing = _block_end(text, opening)
        body = text[opening + 1:closing]
        if prelude.startswith("@keyframes") or prelude.startswith("@-webkit-keyframes"):
            yield "keyframes", context, prelude.split()[1], body, offset + opening
        elif prelude.startswith("@"):
            yield from css_items(body, context + (prelude,), offset + opening + 1)
        elif _has_block(body):  # CSS nesting: the outer selector becomes context
            yield "rule", context, prelude, re.sub(r"[^;{}]*\{[^{}]*\}", " ", body), offset + opening
            yield from css_items(body, context + (prelude,), offset + opening + 1)
        elif prelude:
            yield "rule", context, prelude, body, offset + opening
        cursor = closing + 1


def declarations(body: str) -> list[tuple[str, str]]:
    result, start, depth, quote = [], 0, 0, ""
    for index, char in enumerate(body + ";"):
        if quote:
            if char == quote and body[index - 1] != "\\":
                quote = ""
        elif char in "\"'":
            quote = char
        elif char == "(":
            depth += 1
        elif char == ")":
            depth = max(0, depth - 1)
        elif char == ";" and depth == 0:
            part = body[start:index]
            if ":" in part:
                name, value = part.split(":", 1)
                name = name.strip().lower()
                if re.fullmatch(r"-?-?[a-z][a-z0-9-]*", name):
                    result.append((name, _norm(value.replace("!important", "")).strip()))
            start = index + 1
    return result


def split_selectors(selector: str) -> list[str]:
    parts, depth, start = [], 0, 0
    for index, char in enumerate(selector):
        if char in "([":
            depth += 1
        elif char in ")]":
            depth -= 1
        elif char == "," and depth == 0:
            parts.append(selector[start:index].strip())
            start = index + 1
    parts.append(selector[start:].strip())
    return [part for part in parts if part]


def _is_phone(file: str, context: tuple[str, ...], selector: str) -> bool:
    if file in PHONE_CSS:
        return True
    if any("max-width" in item for item in context):
        return True
    parts = split_selectors(selector)
    return bool(parts) and all(PHONE_MARKER in part for part in parts)


def _line(source: str, offset: int) -> int:
    return source.count("\n", 0, offset) + 1


def stylesheets():
    for name in CSS_FILES:
        source = (STATIC / name).read_text(encoding="utf-8")
        yield name, source, _strip_comments(source)


def all_rules():
    for name, source, text in stylesheets():
        for kind, context, head, body, offset in css_items(text):
            yield name, source, kind, context, head, body, offset


def value_key(value: str) -> str:
    value = _norm(value).lower()
    value = re.sub(r"(?<![\w.])0+(\.\d)", r"\1", value)
    value = re.sub(r"\(\s+", "(", value)
    value = re.sub(r"\s+\)", ")", value)
    value = re.sub(r"\s*,\s*", ",", value)
    return re.sub(r"\s+([+*/-])\s+", r"\1", value)


# observations

def observed_raw_colours_beyond_s9310() -> list[dict]:
    """C-1: the  scanner, minus its own reviewed allowlist."""
    observed = Counter((item.file, item.match_key, item.value) for item in s9310._observed_raw_colours())
    allowed = Counter((e["file"], e["match_key"], e["value"]) for e in s9310._allowlist()["legacy_allowlist"])
    extra = observed - allowed
    return [
        {"file": file, "match_key": key, "value": value, "count": count}
        for (file, key, value), count in sorted(extra.items())
    ]


def _transition_properties(name: str, value: str) -> set[str]:
    if name == "transition-property":
        return {item.strip() for item in value.split(",")}
    props = set()
    depth, start, items = 0, 0, []
    for index, char in enumerate(value + ","):
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        elif char == "," and depth == 0:
            items.append(value[start:index].strip())
            start = index + 1
    for item in items:
        tokens = re.findall(r"[a-z-]+\([^)]*\)|[^\s]+", item)
        named = [t for t in tokens if not TIME.match(t) and not EASING.match(t)
                 and t not in {"allow-discrete", "normal"}]
        if not tokens or named == ["none"]:
            continue
        props.add(named[0] if named else "all")
    return props


def _selector_tokens(part: str) -> set[str]:
    part = re.sub(r":not\((?:[^()]|\([^()]*\))*\)", " ", part)
    return set(re.findall(r"[.#][A-Za-z_][A-Za-z0-9_-]*", part))


def _fixed_chrome_tokens(rules: list) -> set[str]:
    tokens = set(FIXED_CHROME_ROOTS)
    for name, _, kind, context, selector, body, _ in rules:
        if kind != "rule" or name in VENDORED_CSS or _is_phone(name, context, selector):
            continue
        if any(prop == "position" and value in {"fixed", "sticky"} for prop, value in declarations(body)):
            for part in split_selectors(selector):
                if PHONE_MARKER in part:
                    continue
                compounds = re.split(r"\s+|>|\+|~", part.strip())
                tokens |= _selector_tokens(compounds[-1])
    return tokens


def observed_m4_violations() -> list[dict]:
    """M-4: fixed/sticky chrome transitions only opacity/transform ()."""
    rules = list(all_rules())
    chrome = _fixed_chrome_tokens(rules)
    keyframe_props = {}
    for _, _, kind, _, head, body, _ in rules:
        if kind == "keyframes":
            found = set()
            for _, _, _, frame_body, _ in css_items(body):
                found |= {prop for prop, _ in declarations(frame_body)}
            keyframe_props[head] = found
    hits = []
    for name, _, kind, context, selector, body, _ in rules:
        if kind != "rule" or _is_phone(name, context, selector):
            continue
        parts = [p for p in split_selectors(selector) if PHONE_MARKER not in p]
        if not any(_selector_tokens(part) & chrome for part in parts):
            continue
        for prop, value in declarations(body):
            if prop in {"transition", "transition-property"}:
                bad = _transition_properties(prop, value) & M4_FORBIDDEN
                if bad:
                    hits.append({"file": name, "selector": _norm(selector), "declaration": f"{prop}: {value}",
                                 "forbidden": sorted(bad)})
            elif prop in {"animation", "animation-name"}:
                for keyframe, animated in keyframe_props.items():
                    if re.search(rf"(?<![\w-]){re.escape(keyframe)}(?![\w-])", value) and animated & M4_FORBIDDEN:
                        hits.append({"file": name, "selector": _norm(selector), "declaration": f"{prop}: {value}",
                                     "forbidden": sorted(animated & M4_FORBIDDEN)})
    return sorted(hits, key=lambda h: (h["file"], h["selector"], h["declaration"]))


def observed_motion_gaps() -> dict[str, list[dict]]:
    """M-5: every keyframe that is used has a prefers-reduced-motion answer; no dead keyframes."""
    rules = list(all_rules())
    keyframes = {}
    users: dict[str, list[tuple[str, tuple, str]]] = {}
    reduce_rules = []
    for name, _, kind, context, head, body, _ in rules:
        if kind == "keyframes":
            keyframes.setdefault(head, name)
            continue
        in_reduce = any("prefers-reduced-motion: reduce" in c or "prefers-reduced-motion:reduce" in c for c in context)
        if in_reduce:
            reduce_rules.append((head, body))
        for prop, value in declarations(body):
            if prop in {"animation", "animation-name"}:
                for token in re.findall(r"[A-Za-z_][\w-]*", value):
                    users.setdefault(token, []).append((name, context, head))
    unused, gaps = [], []
    for keyframe, file in sorted(keyframes.items()):
        uses = users.get(keyframe, [])
        if not uses:
            unused.append({"file": file, "keyframes": keyframe})
            continue
        covered = True
        for _, context, selector in uses:
            if any("prefers-reduced-motion" in c for c in context):
                continue  # declared under no-preference, or a reduce-specific animation
            tokens = set().union(*(_selector_tokens(p) for p in split_selectors(selector))) or {selector}
            ok = False
            for reduce_selector, reduce_body in reduce_rules:
                decls = declarations(reduce_body)
                props = {prop for prop, _ in decls}
                if keyframe in reduce_body:
                    ok = True
                elif props & {"animation", "animation-name", "animation-duration", "animation-play-state",
                              "animation-iteration-count"} or ("display", "none") in decls:
                    reduce_tokens = set().union(*(_selector_tokens(p) for p in split_selectors(reduce_selector)))
                    if reduce_tokens & tokens or reduce_selector.strip() in {"*", "*, *::before, *::after"}:
                        ok = True
                if ok:
                    break
            if not ok:
                covered = False
                break
        if not covered:
            gaps.append({"file": file, "keyframes": keyframe})
    return {"keyframes_without_reduced_motion": gaps, "unused_keyframes": unused}


def _value_counts(prop_match) -> dict[str, dict[str, int]]:
    counts: dict[str, Counter] = {}
    for name, _, kind, context, selector, body, _ in all_rules():
        if kind != "rule" or name in VENDORED_CSS or _is_phone(name, context, selector):
            continue
        for prop, value in declarations(body):
            if prop_match(prop):
                counts.setdefault(name, Counter())[value_key(value)] += 1
    return counts


def observed_font_sizes() -> dict[str, dict[str, int]]:
    """T-3: desktop font sizes off the scale."""
    allowed = {value_key(v) for v in FONT_SIZE_SCALE}
    counts = _value_counts(lambda prop: prop == "font-size")
    return {file: dict(sorted((v, n) for v, n in c.items() if v not in allowed)) for file, c in sorted(counts.items())
            if any(v not in allowed for v in c)}


def observed_radii() -> dict[str, dict[str, int]]:
    """E-1: desktop corner radii off the scale."""
    allowed = {value_key(v) for v in RADIUS_SCALE}
    counts = _value_counts(lambda prop: bool(RADIUS_PROPERTY.match(prop)))
    return {file: dict(sorted((v, n) for v, n in c.items() if v not in allowed)) for file, c in sorted(counts.items())
            if any(v not in allowed for v in c)}


def observed_input_overrides() -> list[dict]:
    """K-2/K-3: no surface re-sizes the shared input's text, height, vertical padding or radius."""
    hits = []
    for name, _, kind, context, selector, body, _ in all_rules():
        if kind != "rule" or name in VENDORED_CSS or _is_phone(name, context, selector):
            continue
        parts = [p for p in split_selectors(selector) if PHONE_MARKER not in p]
        targets = [p for p in parts if ".ceres-wide-input" in re.sub(r":not\((?:[^()]|\([^()]*\))*\)", "", p)]
        if not targets or all(_norm(p) in INPUT_DEFINITION_SELECTORS for p in targets):
            continue
        owned = sorted({prop for prop, value in declarations(body) if prop in INPUT_OWNED and value != "inherit"})
        if owned:
            hits.append({"file": name, "selector": _norm(selector), "properties": owned})
    return sorted(hits, key=lambda h: (h["file"], h["selector"]))


BUTTON_TAG = re.compile(r"<button\b[^>]*>", re.DOTALL | re.IGNORECASE)
CREATE_BUTTON = re.compile(r"createElement\(\s*['\"]button['\"]\s*\)")


def observed_buttons_without_ceres_btn() -> list[dict]:
    """K-1: every button is a `.ceres-btn` unless it is a registered special family (K-7)."""
    counts: Counter = Counter()
    for path in sorted(STATIC.glob("*.js")) + sorted(STATIC.glob("*.html")):
        if path.name in DEV_ONLY_PAGES:
            continue
        source = s9310._blank_js_comments(path.read_text(encoding="utf-8")) if path.suffix == ".js" \
            else re.sub(r"(?s)", _blank, path.read_text(encoding="utf-8"))
        for match in BUTTON_TAG.finditer(source):
            tag = match.group(0)
            if "ceres-btn" in tag:
                continue
            klass = re.search(r"\bclass\s*=\s*([\"'])(.*?)\1", tag, re.DOTALL)
            counts[(path.name, _norm(klass.group(2)) if klass else "(no class)")] += 1
        for match in CREATE_BUTTON.finditer(source):
            if "ceres-btn" not in source[match.end():match.end() + 600]:
                counts[(path.name, "createElement('button')")] += 1
    return [{"file": f, "class": c, "count": n} for (f, c), n in sorted(counts.items())]


def observed_phone_rules_outside_scope() -> list[dict]:
    """P-0: every rule in the phone stylesheet is phone-scoped, so it can never change a desktop value."""
    hits = []
    for name, _, kind, context, selector, body, _ in all_rules():
        if name not in PHONE_CSS or kind != "rule":
            continue
        if any("max-width" in c for c in context):
            continue
        loose = [p for p in split_selectors(selector) if PHONE_MARKER not in p]
        if loose and declarations(body):
            hits.append({"file": name, "selector": _norm(selector)})
    return hits


def observe() -> dict:
    motion = observed_motion_gaps()
    return {
        "raw_colour_beyond_s9310_allowlist": observed_raw_colours_beyond_s9310(),
        "m4_fixed_chrome_shadow_filter_motion": observed_m4_violations(),
        "keyframes_without_reduced_motion": motion["keyframes_without_reduced_motion"],
        "unused_keyframes": motion["unused_keyframes"],
        "font_sizes_off_scale": observed_font_sizes(),
        "radii_off_scale": observed_radii(),
        "input_overrides": observed_input_overrides(),
        "buttons_without_ceres_btn": observed_buttons_without_ceres_btn(),
        "phone_rules_outside_phone_scope": observed_phone_rules_outside_scope(),
    }


# tests

def _baseline() -> dict:
    return json.loads(BASELINE_PATH.read_text(encoding="utf-8"))


def _diff_lists(observed: list[dict], allowed: list[dict]) -> tuple[list, list]:
    key = lambda item: json.dumps(item, sort_keys=True)
    obs, allw = Counter(map(key, observed)), Counter(map(key, allowed))
    return sorted((obs - allw).elements()), sorted((allw - obs).elements())


def _diff_counts(observed: dict, allowed: dict) -> tuple[list, list]:
    new, stale = [], []
    for file in sorted(set(observed) | set(allowed)):
        o, a = observed.get(file, {}), allowed.get(file, {})
        for value in sorted(set(o) | set(a)):
            if o.get(value, 0) > a.get(value, 0):
                new.append(f"{file}: {value} ×{o.get(value, 0) - a.get(value, 0)}")
            elif o.get(value, 0) < a.get(value, 0):
                stale.append(f"{file}: {value} ×{a.get(value, 0) - o.get(value, 0)}")
    return new, stale


RULE_OF = {
    "raw_colour_beyond_s9310_allowlist": "C-1 (use var(--token) / color-mix of tokens)",
    "m4_fixed_chrome_shadow_filter_motion": "M-4 (fixed chrome animates only opacity/transform; a glow is a pseudo-element whose opacity fades)",
    "keyframes_without_reduced_motion": "M-5 (add a prefers-reduced-motion: reduce answer)",
    "unused_keyframes": "M-5 (delete dead keyframes)",
    "font_sizes_off_scale": "T-3 (use a size from the type scale; a new size is added to the standard first)",
    "radii_off_scale": "E-1 (use a radius from the radius scale)",
    "input_overrides": "K-2/K-3 (inputs take control tokens; a surface sets width only)",
    "buttons_without_ceres_btn": "K-1/K-7 (use .ceres-btn or register a special family)",
    "phone_rules_outside_phone_scope": "P-0 (phone rules only under html.ceres-lw-le-1279)",
}


def test_baseline_is_dated_and_complete():
    baseline = _baseline()
    assert baseline["rule"] == ""
    assert baseline["recorded_on"] == "2026-09-28"
    assert baseline["recorded_at_version"] == "1.0.0"
    assert set(baseline["baseline"]) == set(RULE_OF)


def test_ui_standard_holds_against_dated_baseline():
    observed = observe()
    allowed = _baseline()["baseline"]
    messages = []
    for section, rule in RULE_OF.items():
        if isinstance(observed[section], dict):
            new, stale = _diff_counts(observed[section], allowed[section])
        else:
            new, stale = _diff_lists(observed[section], allowed[section])
        if new:
            messages.append(f"NEW deviation from {rule}:\n  " + "\n  ".join(map(str, new)))
        if stale:
            messages.append(f"fixed deviation still in the  baseline ({section}); remove it:\n  "
                            + "\n  ".join(map(str, stale)))
    assert not messages, "\n\n".join(messages)


def test_standard_document_carries_every_rule_the_guard_enforces():
    doc = STANDARD_DOC.read_text(encoding="utf-8")
    for rule_id in ("C-1", "C-4", "T-3", "K-1", "K-2", "K-3", "K-7", "E-1", "M-4", "M-5", "P-0", "W-2"):
        assert re.search(rf"\*\*{re.escape(rule_id)}\b", doc), f"{rule_id} missing from {STANDARD_DOC.name}"
    for heading in ("## Frozen surfaces", "## Defaults used (maintainer may overrule)", "## How the standard is enforced"):
        assert heading in doc, f"{heading!r} missing from {STANDARD_DOC.name}"
    assert "test_ui_standard.py" in doc


def test_scales_in_guard_match_the_written_standard():
    doc = STANDARD_DOC.read_text(encoding="utf-8")
    for value in (".8rem", ".9rem", ".75rem", ".7rem", "1.25rem", "clamp(.875rem, .75rem + .15625cqi, .9375rem)"):
        assert f"`{value}`" in doc, f"T-3 value {value} not written in the standard"
    for value in ("8px", "var(--radius)", "5px 0 0 5px", "999px", "50%"):
        assert f"`{value}`" in doc, f"E-1 value {value} not written in the standard"


def test_m4_parser_flags_shadow_and_implicit_all_transitions():
    assert _transition_properties("transition", "background .3s ease, box-shadow .3s ease") == {"background", "box-shadow"}
    assert _transition_properties("transition", ".2s") == {"all"}
    assert _transition_properties("transition", "none") == set()
    assert _transition_properties("transition", "opacity 180ms cubic-bezier(.2, .85, .3, 1), transform 180ms") == {"opacity", "transform"}
    assert _transition_properties("transition-property", "opacity, filter") == {"opacity", "filter"}


def test_value_keys_normalise_spelling():
    assert value_key("0.8rem") == ".8rem"
    assert value_key("clamp(.875rem, .75rem + .15625cqi, .9375rem)") == value_key("clamp(0.875rem,0.75rem + 0.15625cqi,0.9375rem)")
    assert value_key("calc(var(--radius) - 3px)") == "calc(var(--radius)-3px)"


def _write_baseline() -> None:
    payload = {
        "schema_version": 1,
        "rule": "",
        "recorded_on": "2026-09-28",
        "recorded_at_version": "1.0.0",
        "note": ("Deviations from library/ceres-ui-standards.md present on master  when the guard was "
                 "recorded. Shrinking ratchet: remove an entry when its deviation is fixed; add one only with "
                 "an maintainer-approved exception that is also written into the standard."),
        "baseline": observe(),
    }
    BASELINE_PATH.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


if __name__ == "__main__":
    if "--write-baseline" in sys.argv:
        _write_baseline()
    else:
        print(json.dumps(observe(), indent=2, ensure_ascii=False))
