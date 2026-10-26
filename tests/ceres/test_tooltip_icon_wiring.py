"""Sandbox-runnable direct guards for tooltip icon helper wiring.

/: v0.9.60 (cf83a58e,  "tooltips rebuilt to the approved gate
document") deliberately dropped the tooltip icon from `terrainCellHtml()` ("Gate
document #70: no tooltip -- the terrain name is printed beside the emblem").
`terrain` moved out of the required "positive" (has-icon) set into `gaps`
(text-led, no icon attributes), matching every other intentional icon gap this
file already covers.
"""

from __future__ import annotations

import json
import hashlib
import re
import subprocess
from html.parser import HTMLParser

import pytest

from .vm_context import STATIC, read_entry_source, run_in_module_context


_MARKER = "__TOOLTIP_ICON_RECORDS__"
_WIRED_KINDS = {
    "rarity",
    "splinter",
    "set",
    "type",
    "totem",
    "named title",
    "individual ability",
    "resource",
    "DEC",
}
_ICON_PATH = re.compile(
    re.search(r"const ICON_PATH = /(.+)/i;", (STATIC / "tooltip.js").read_text(encoding="utf-8")).group(1),
    re.IGNORECASE,
)
# The public tree carries no game artwork: an icon counts when it is on disk (a maintainer checkout or an earlier
# download) or on the reviewed, hash-pinned list Ceres downloads it from on first use (ceres/asset_sources.py).
_SOURCES = STATIC / "assets" / "sources.json"
_DOWNLOADABLE = frozenset(
    row["path"] for row in (json.loads(_SOURCES.read_text(encoding="utf-8")) if _SOURCES.is_file() else [])
)
_ASSET_INDEX = {
    row["path"]: row for row in json.loads(
        (STATIC.parents[1] / "tests/ceres/fixtures/export_boundary/asset-index.json").read_text(encoding="utf-8")
    )
}


class _FragmentParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.elements: list[dict[str, object]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.elements.append({"tag": tag, "attrs": dict(attrs)})

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)


_BODY_JS = r"""
const positive = [
  {kind: 'rarity', html: rarityGemHtml('rare')},
  {kind: 'splinter', html: splinterIconHtml('Fire')},
  {kind: 'terrain-affinity', html: terrainAffinityHtml({terrain: 'Mountain', plot: '1-1-1', biome: {red: 0.1, blue: 0, green: -0.1}}, 'editor')},
  {kind: 'set', html: setIconOnlyHtml('chaos')},
  {kind: 'type', html: typeMarkHtml('natural')},
  {kind: 'totem', html: totemIconHtml('rare')},
  {kind: 'named title', html: titleIconHtml('The Proven', 'The Proven')},
  {kind: 'individual ability', html: abilityIconHtml('GRAIN')},
  {kind: 'ability fallback', html: abilityIconHtml('RESEARCH')},
  {kind: 'resource', html: resourceIconHtml('grain')},
  {kind: 'DEC', html: resourceIconHtml('dec')},
];
const gaps = [
  {kind: 'mythic rarity gap', html: rarityGemHtml('mythic')},
  {kind: 'unknown totem gap', html: totemIconHtml('mythic')},
  {kind: 'static charge ability gap', html: abilityIconHtml('STATIC_CHARGE')},
];
// Not an asset gap (it renders its own emblem <img>) -- a deliberate no-tooltip
// design (Gate document #70) -- checked separately below, never lumped in with
// the text-led asset-gap fallbacks above.
const terrainCell = terrainCellHtml('Mountain').html;
for (const record of positive) {
  const paths = [...record.html.matchAll(/data-tip-icon="([^"]*)"/g)].map((match) => match[1]);
  if (!paths.length || paths.some((path) => !ICON_PATH.test(path))) {
    throw new Error(`required tooltip icon missing or invalid for ${record.kind}`);
  }
}
console.log('__TOOLTIP_ICON_RECORDS__' + JSON.stringify({positive, gaps, terrainCell}));
"""


def _run_helper_harness(source: str | None = None) -> dict[str, list[dict[str, str]]]:
    result = run_in_module_context(_BODY_JS, source=source)
    output = result.stdout.split(_MARKER, 1)
    assert len(output) == 2, f"VM harness did not return {_MARKER}: {result.stdout!r}"
    return json.loads(output[1].splitlines()[0])


def _elements(html: str) -> list[dict[str, object]]:
    parser = _FragmentParser()
    parser.feed(html)
    return parser.elements


def _assert_icon_contract(record: dict[str, str]) -> int:
    elements = _elements(record["html"])
    tipped = [element for element in elements if "data-tip-icon" in element["attrs"]]
    assert tipped, f"{record['kind']} emitted no data-tip-icon: {record['html']}"
    for element in tipped:
        attrs = element["attrs"]
        icon = str(attrs["data-tip-icon"] or "")
        assert icon, f"{record['kind']} emitted an empty data-tip-icon"
        assert _ICON_PATH.fullmatch(icon), f"{record['kind']} emitted unsafe icon path {icon!r}"
        asset = STATIC / "assets" / icon
        assert asset.is_file() or icon in _DOWNLOADABLE or icon in _ASSET_INDEX, (
            f"{record['kind']} icon has no vetted asset record: {icon}")
        if asset.is_file() and icon in _ASSET_INDEX:
            assert hashlib.sha256(asset.read_bytes()).hexdigest() == _ASSET_INDEX[icon]["sha256"]
        src = attrs.get("src")
        if src:
            assert str(src).endswith(f"/assets/{icon}"), f"trigger src and tooltip icon differ: {src!r} != {icon!r}"
    return len(tipped)


def test_direct_tooltip_icon_helpers_emit_valid_local_icons() -> None:
    records = _run_helper_harness()["positive"]
    assert {record["kind"] for record in records} >= _WIRED_KINDS
    total_icons = sum(_assert_icon_contract(record) for record in records)
    assert total_icons >= len(_WIRED_KINDS)


def test_asset_gaps_remain_text_led_without_icon_attributes() -> None:
    result = _run_helper_harness()
    for record in result["gaps"]:
        elements = _elements(record["html"])
        assert all("data-tip-icon" not in element["attrs"] for element in elements), record
        assert not any(element["tag"] == "img" for element in elements), record
    # Terrain cells are a deliberate no-tooltip design (, Gate document #70
    # "no tooltip -- the terrain name is printed beside the emblem"), distinct from
    # an asset gap: they still render their own emblem <img>, just no tooltip icon.
    terrain_elements = _elements(result["terrainCell"])
    assert all("data-tip-icon" not in element["attrs"] for element in terrain_elements)
    assert any(element["tag"] == "img" for element in terrain_elements)


def test_tooltip_icon_guard_goes_red_when_rarity_helper_drops_attribute() -> None:
    source = read_entry_source()
    needle = "icon: tooltipIconPath('rarity', key), peer: 'icons_identity'"
    assert source.count(needle) == 1
    mutated = source.replace(needle, "peer: 'icons_identity'", 1)
    with pytest.raises(subprocess.CalledProcessError):
        _run_helper_harness(mutated)


# (maintainer: "there iss abug with thetooltip of the wallet appearing without
# hover, especially when changing tabs and then returning to the browser but also
# while surfing elsewhere"): the wallet edge control keeps focus after the drawer
# closes; a window/tab refocus re-fires `focusin` on it with no real user action
# behind it, popping the tooltip. Source-string guards (the fix is event-wiring, not a
# pure function suited to the VM harness above).
_TOOLTIP_SOURCE = (STATIC / "tooltip.js").read_text(encoding="utf-8")


def test_focusin_only_shows_for_keyboard_focus() -> None:
    focusin_match = re.search(r"document\.addEventListener\('focusin', \(event\) => \{.*?\n  \}\);", _TOOLTIP_SOURCE, re.S)
    assert focusin_match, "focusin handler not found in its expected shape"
    body = focusin_match.group(0)
    assert "trigger.matches(':focus-visible')" in body
    assert "try {" in body and "catch (_)" in body, "unsupported :focus-visible must fall through and show, not throw"


def test_focusin_ignores_a_window_refocus_window() -> None:
    assert "let WINDOW_REFOCUSED_AT = 0;" in _TOOLTIP_SOURCE
    assert "const WINDOW_REFOCUS_IGNORE_MS = 400;" in _TOOLTIP_SOURCE
    focusin_match = re.search(r"document\.addEventListener\('focusin', \(event\) => \{.*?\n  \}\);", _TOOLTIP_SOURCE, re.S)
    assert focusin_match
    assert "Date.now() - WINDOW_REFOCUSED_AT < WINDOW_REFOCUS_IGNORE_MS" in focusin_match.group(0)


def test_window_focus_and_visibility_restore_stamp_the_refocus_window() -> None:
    assert "window.addEventListener('focus', () => { WINDOW_REFOCUSED_AT = Date.now(); });" in _TOOLTIP_SOURCE
    visibility_match = re.search(r"document\.addEventListener\('visibilitychange', \(\) => \{.*?\n  \}\);", _TOOLTIP_SOURCE, re.S)
    assert visibility_match, "visibilitychange handler not found in its expected shape"
    body = visibility_match.group(0)
    assert "document.visibilityState === 'visible'" in body
    assert "WINDOW_REFOCUSED_AT = Date.now();" in body


def test_window_blur_and_hidden_visibility_hide_the_tooltip() -> None:
    visibility_match = re.search(r"document\.addEventListener\('visibilitychange', \(\) => \{.*?\n  \}\);", _TOOLTIP_SOURCE, re.S)
    blur_match = re.search(r"window\.addEventListener\('blur', \(\) => \{.*?\n  \}\);", _TOOLTIP_SOURCE, re.S)
    assert visibility_match and blur_match, "visibilitychange/blur handlers not found in their expected shape"
    for body in (visibility_match.group(0), blur_match.group(0)):
        assert "hideTooltip();" in body
        assert "TOOLTIP_OPEN_TIMER" in body and "clearTimeout(TOOLTIP_OPEN_TIMER)" in body


def test_node_check_tooltip_js() -> None:
    result = subprocess.run(["node", "--check", str(STATIC / "tooltip.js")], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
