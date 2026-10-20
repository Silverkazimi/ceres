from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).parents[2]
STATIC = ROOT / "ceres" / "static"
CERES_CSS = STATIC / "ceres.css"

_SURFACE = re.compile(r"\boverflow(?:-x|-y)?\s*:\s*(?:auto|scroll|overlay)\b")
_JS_SURFACE = re.compile(r"\boverflow(?:-x|-y|X|Y)?\s*(?::|=)\s*[\"'`]?\s*(?:auto|scroll|overlay)\b")


def _without_comments(text: str) -> str:
    return re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)


def _inner_rules(text: str):
    for match in re.finditer(r"([^{}]+)\{([^{}]*)\}", text):
        yield " ".join(match.group(1).split()), match.group(2)


def test_global_scrollbar_contract_covers_every_css_scroll_surface() -> None:
    css = _without_comments(CERES_CSS.read_text(encoding="utf-8"))
    surfaces = []
    for path in sorted(STATIC.glob("*.css")):
        source = _without_comments(path.read_text(encoding="utf-8"))
        for selector, body in _inner_rules(source):
            if _SURFACE.search(body):
                surfaces.append(f"{path.name}: {selector}")
    for path in sorted(STATIC.glob("*.js")):
        source = path.read_text(encoding="utf-8")
        if _JS_SURFACE.search(source):
            surfaces.append(f"{path.name}: JavaScript-created overflow")

    assert surfaces
    assert css.count("where(*), html {") == 1
    assert re.search(r":where\(\*\),\s*html\s*\{[^}]*scrollbar-width:\s*thin", css, re.DOTALL)
    assert re.search(
        r":where\(\*\),\s*html\s*\{[^}]*scrollbar-color:\s*var\(--border-bright\)\s+transparent",
        css,
        re.DOTALL,
    )
    assert re.search(r":where\(\*\)::\-webkit-scrollbar,\s*html::\-webkit-scrollbar\s*\{[^}]*width:\s*0\.5rem[^}]*height:\s*0\.5rem", css)
    assert "where(*)::-webkit-scrollbar-track, html::-webkit-scrollbar-track { background: transparent; }" in css
    assert re.search(
        r":where\(\*\)::\-webkit-scrollbar-thumb,\s*html::\-webkit-scrollbar-thumb\s*\{[^}]*background:\s*var\(--border-bright\)[^}]*border-radius:\s*999px",
        css,
        re.DOTALL,
    )
    assert "where(*)::-webkit-scrollbar-thumb:hover, html::-webkit-scrollbar-thumb:hover { background: var(--accent); }" in css


def test_scrollbar_contract_has_no_local_native_reset() -> None:
    local_rules = []
    for path in sorted(STATIC.glob("*.css")):
        source = _without_comments(path.read_text(encoding="utf-8"))
        for selector, body in _inner_rules(source):
            if "scrollbar-width: thin" in body or "scrollbar-color:" in body:
                if ":where(*)" not in selector:
                    local_rules.append(f"{path.name}: {selector}")
            if re.search(r"scrollbar-width\s*:\s*auto\b", body):
                local_rules.append(f"{path.name}: {selector} resets scrollbar-width to auto")
    assert not local_rules, "scrolling surfaces must use the intrinsic global contract " + "; ".join(local_rules)
