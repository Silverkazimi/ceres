""" guard for the canonical harvest-stage aggregate label."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Mapping

import pytest


ROOT = Path(__file__).resolve().parents[2]
EDITOR_JS = ROOT / "ceres" / "static" / "editor.js"
ROWS_JS = ROOT / "ceres" / "static" / "rows.js"
WORKSITE_JS = ROOT / "ceres" / "static" / "worksite.js"
INDEX_HTML = ROOT / "ceres" / "static" / "index.html"
TEXTS_MD = ROOT / "work" / "tooltips" / "TEXTS.md"
TEXTS_JSON = ROOT / "work" / "tooltips" / "TEXTS.json"
TEXT_SUFFIXES = {".css", ".html", ".js", ".json", ".md", ".py", ".txt"}
STATIC_FILES = tuple(sorted(
    path for path in (ROOT / "ceres" / "static").rglob("*")
    if path.is_file() and path.suffix in TEXT_SUFFIXES
))
CERES_PY_FILES = tuple(sorted(path for path in (ROOT / "ceres").glob("*.py") if path.is_file()))

TOTAL_PP = "Total PP"
SECOND_NAME = re.compile(r"\bHarvest PP\b", re.IGNORECASE)


def _read_sources(overrides: Mapping[Path, str] | None = None) -> dict[Path, str]:
    paths = (*STATIC_FILES, *CERES_PY_FILES, TEXTS_MD, TEXTS_JSON)
    sources = {path: path.read_text(encoding="utf-8") for path in paths}
    if overrides:
        sources.update(overrides)
    return sources


def _one(pattern: str, source: str, name: str) -> str:
    matches = re.findall(pattern, source, flags=re.MULTILINE)
    assert len(matches) == 1, f"{name} must have exactly one content match; found {len(matches)}"
    value = matches[0]
    return value if isinstance(value, str) else value[0]


def _every(pattern: str, source: str, name: str) -> str:
    # v0.9.456 (0fa1cabc, phase D) and 85f1473c
    # (///) moved the plot-facts figure out of editor.js's
    # single `<span>...<b>` line into rows.js's mobile plot surfaces, which now
    # render it at TWO canonical sites (the mobile plot-card stats list and the
    # mobile plot sheet's own total-PP block) sharing the same markup -- both must
    # agree, so this asserts on every match rather than requiring exactly one.
    matches = re.findall(pattern, source, flags=re.MULTILINE)
    assert matches, f"{name} must have at least one content match; found none"
    values = {value if isinstance(value, str) else value[0] for value in matches}
    assert len(values) == 1, f"{name} sites disagree: {sorted(values)}"
    return next(iter(values))


def _label_values(sources: Mapping[Path, str]) -> dict[str, str]:
    editor = sources[EDITOR_JS]
    rows = sources[ROWS_JS]
    worksite = sources[WORKSITE_JS]
    index = sources[INDEX_HTML]
    return {
        "editor change tooltip": _one(
            r"title:\s*['\"]((?:Harvest|Total) PP after the change)['\"]", editor,
            "editor change tooltip",
        ),
        "editor change badge": _one(
            r'class="editor-change-badge [^\"]*"[^>]*>(Harvest|Total)\s+\$\{',
            editor, "editor change badge",
        ),
        "editor plot facts": _every(
            r"<dt>(Harvest PP|Total PP)</dt><dd>\$\{num\(row\.harvest_pp, 0\)\}</dd>",
            rows, "editor plot facts",
        ),
        # v0.9.60 (cf83a58e, "tooltips rebuilt to the
        # approved gate document") replaced this site's raw `lines.push(\`Total PP
        # ...\`)` template string with a structured `items.push({ name: 'Total'
        # effect: ... })` object (rendered under a "Production" group heading, so
        # the bare "Total" name is the guarded value here, not "Total PP").
        "editor card total": _one(
            r"items\.push\(\{ name: '(Harvest|Total)', effect: num\(total, 0\) \}\)",
            editor, "editor card total",
        ),
        "portfolio totals strip": _one(
            r"<span>(Harvest PP|Total PP):\s+<strong>\$\{num\(totals\.harvest_pp\)",
            rows, "portfolio totals strip",
        ),
        # this figure is now rendered at two agreeing call sites
        # in worksite.js, so every match must agree rather than there being exactly one.
        "projected worksite aggregate": _every(
            r"\$\{num\(harvestPp, 0\)\}\s+([A-Za-z]+ PP)",
            worksite, "projected worksite aggregate",
        ),
        # the header now carries a trailing `<span class="sort-chevron">`
        # (sort-affordance markup, -era) after the label text, so the match stops at
        # the first `<` instead of requiring `</th>` immediately after the label.
        "board aggregate column": _one(
            r'data-key="harvest_pp">([^<]+)<', index, "board aggregate column",
        ),
    }


def _tooltip_title_values(sources: Mapping[Path, str]) -> tuple[list[str], list[str]]:
    markdown_titles = [
        title
        for line in sources[TEXTS_MD].splitlines()
        if re.match(r"\|\s*49\s*\|", line)
        for title in re.findall(r"Title [`\"]([^`\"]+)[`\"]", line)
    ]
    data = json.loads(sources[TEXTS_JSON])
    json_row = next(row for row in data["rows"] if row.get("row") == 49)
    json_titles = [json_row["title"]]
    json_titles.extend(state["title"] for state in json_row["states"])
    return markdown_titles, json_titles


def _user_facing_label_values(sources: Mapping[Path, str]) -> list[tuple[str, str]]:
    patterns = (
        r"title:\s*['\"]([^'\"]*(?:Harvest|Total)\s+PP[^'\"]*)['\"]",
        r"aria-label\s*=\s*['\"]([^'\"]*(?:Harvest|Total)\s+PP[^'\"]*)['\"]",
        r"<(?:span|th|label)\b[^>]*>\s*([^<]*(?:Harvest|Total)\s+PP[^<]*)",
        r">\s*(Harvest|Total)\s+\$\{",
        r"\$\{[^`\n}]+\}\s+((?:Harvest|harvest|Total)\s+PP)",
        r"(?:title|label|aria_label)\s*[:=]\s*['\"]([^'\"]*(?:Harvest|Total)\s+PP[^'\"]*)['\"]",
    )
    values = []
    for path in (*STATIC_FILES, *CERES_PY_FILES):
        source = sources[path]
        for pattern in patterns:
            values.extend((str(path.relative_to(ROOT)), value) for value in re.findall(pattern, source))
    return values


def _assert_total_pp_contract(sources: Mapping[Path, str]) -> None:
    labels = _label_values(sources)
    expected = {
        "editor change tooltip": "Total PP after the change",
        "editor change badge": "Total",
        "editor plot facts": TOTAL_PP,
        "editor card total": "Total",
        "portfolio totals strip": f"{TOTAL_PP}",
        "projected worksite aggregate": TOTAL_PP,
        "board aggregate column": TOTAL_PP,
    }
    mismatches = [
        f"{name}: expected {expected[name]!r}, found {value!r}"
        for name, value in labels.items()
        if value != expected[name]
    ]

    markdown_titles, json_titles = _tooltip_title_values(sources)
    mismatches.extend(
        f"TEXTS.md row 49 title: expected a Total PP title, found {title!r}"
        for title in markdown_titles
        if not title.startswith(TOTAL_PP)
    )
    mismatches.extend(
        f"TEXTS.json row 49 title: expected a Total PP title, found {title!r}"
        for title in json_titles
        if not title.startswith(TOTAL_PP)
    )
    mismatches.extend(
        f"{path}: user-facing label uses the second name: {value!r}"
        for path, value in _user_facing_label_values(sources)
        if SECOND_NAME.search(value)
    )

    if mismatches:
        raise AssertionError("\n".join(mismatches))

    assert not any(SECOND_NAME.search(value) for value in labels.values())
    assert not any(SECOND_NAME.search(title) for title in markdown_titles + json_titles)


def test_harvest_stage_aggregate_uses_total_pp_at_every_label_site() -> None:
    """R-67 and R-104 keep this quantity on the Total PP name everywhere it is labelled."""
    _assert_total_pp_contract(_read_sources())


def test_total_pp_guard_rejects_a_second_name_injection() -> None:
    sources = _read_sources()
    canonical_rows = re.sub(
        r"<span>(?:Harvest PP|Total PP):\s+<strong>\$\{num\(totals\.harvest_pp\)",
        "<span>Total PP: <strong>${num(totals.harvest_pp)",
        sources[ROWS_JS], count=1,
    )
    assert "<span>Total PP: <strong>${num(totals.harvest_pp)" in canonical_rows
    injected_rows = canonical_rows.replace(
        "<span>Total PP: <strong>${num(totals.harvest_pp)",
        "<span>Harvest PP: <strong>${num(totals.harvest_pp)",
        1,
    )
    with pytest.raises(AssertionError, match="portfolio totals strip"):
        _assert_total_pp_contract({**sources, ROWS_JS: injected_rows})
