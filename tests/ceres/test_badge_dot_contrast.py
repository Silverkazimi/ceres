"""The badge status glyphs use semantic theme tokens in every theme."""
from __future__ import annotations

import re
from pathlib import Path

CSS = (Path(__file__).resolve().parents[2] / "ceres" / "static" / "ceres.css").read_text(encoding="utf-8")


def test_status_glyph_uses_semantic_critical_and_warning_tokens():
    assert ".badge-status-glyph.is-critical { color: var(--danger); }" in CSS
    assert ".badge-status-glyph.is-warning { color: var(--warn); }" in CSS
    assert ".badge-strip.is-pending { color: var(--gold); }" in CSS


def test_loading_and_clean_glyphs_keep_their_semantic_tokens():
    assert ".badge-strip.is-loading { color: var(--meta); }" in CSS
    assert ".badge-strip.is-clean { color: var(--ok); }" in CSS


def test_status_glyph_has_no_shadow_ring():
    rule = re.search(r"\.badge-status-glyph\s*\{([^}]*)\}", CSS)
    assert rule and "box-shadow" not in rule.group(1)
