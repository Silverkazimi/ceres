from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
STATIC = ROOT / "ceres" / "static"
BADGES = (STATIC / "badges.js").read_text(encoding="utf-8")
OVERLAY = (STATIC / "badge-overlay.js").read_text(encoding="utf-8")
CERES_CSS = (STATIC / "ceres.css").read_text(encoding="utf-8")
BADGE_COLOURS = (STATIC / "badge-colours.css").read_text(encoding="utf-8")
INDEX = (STATIC / "index.html").read_text(encoding="utf-8")


def test_badge_strip_projects_one_prioritized_status_glyph():
    assert "export function badgeStripStatus(" in BADGES
    assert "pending_change_stalled" in BADGES
    assert "staged_changes" in BADGES and "staged_cooldown" in BADGES
    assert "badge-status-glyph" in BADGES
    assert "aria-label=" in BADGES
    assert "<i class=\"badge-dot" not in BADGES


def test_badge_surface_has_no_glow_or_shadow_transition():
    css = re.sub(r"/\*.*?\*/", "", CERES_CSS, flags=re.S)
    badge_rules = re.findall(
        r"([^{}]*?(?:badge-bar|badge-overlay-row|badge-bar-row|badge-flag)[^{}]*?)\{([^{}]*)\}",
        css,
        re.S,
    )
    assert badge_rules
    # and (the slide-outs'
    # left-edge glow) put static glows back; what still holds is that a glow is never animated ( M-4).
    assert all(not re.search(r"transition\s*:[^;{}]*box-shadow", body) for _, body in badge_rules)
    hover_rules = [body for selector, body in badge_rules if ":hover" in selector or ":focus-visible" in selector]
    assert all(not re.search(r"transition\s*:[^;{}]*box-shadow", body) for body in hover_rules)
    assert "staged-changes-pulse" not in BADGE_COLOURS
    assert "badge-golden-pulse" not in CERES_CSS


def test_only_pending_clock_pulses_and_reduced_motion_is_static():
    assert "@keyframes badge-clock-pulse" in CERES_CSS
    assert re.search(r"\.badge-status-glyph\.is-pending\s*\{[^}]*animation:\s*badge-clock-pulse", CERES_CSS)
    assert re.search(r"@media\s*\(prefers-reduced-motion:\s*reduce\)[^{]*\{[^}]*\.badge-status-glyph\.is-pending[^}]*animation:\s*none", CERES_CSS, re.S)
    animation_refs = re.findall(r"animation:\s*([\w-]+)", CERES_CSS + BADGE_COLOURS)
    assert animation_refs.count("badge-clock-pulse") == 1


def test_exact_chip_faces_and_separate_stalled_filter():
    for code, face in {
        "idle": "Idle",
        "unpowered": "No power",
        "unpowered_worker": "Unpowered",
        "redundant_power_core": "Spare core",
        "dark_discount_wasted": "DD wasted",
        "no_worksite": "Not cleared",
        "worksite_incomplete": "Worksite change",
        "unstable_totem": "UT",
        "building_in_box": "BiB",
    }.items():
        assert re.search(rf"{re.escape(code)}:\s*['\"]{re.escape(face)}['\"]", BADGES)
    assert "pending_change_stalled'" in BADGES and "label: 'Stalled'" in BADGES
    assert "['pending_change_stalled', { kind: 'problem', label: 'Stalled', group: 'critical'" in BADGES
    assert "label: 'Building'" in BADGES
    assert "return badgeChips(row).some((chip) => chip.key === key);" in BADGES
    assert "SOLO" in BADGES and "PAIRED" in BADGES and "RUNI" in BADGES and "LL" in BADGES
    assert "Loading" in BADGES
    assert "Over Cap" in BADGES and "Region DEC" in BADGES and "Waiting" in BADGES


def test_badge_sidebar_does_not_restore_open_state_and_flag_tracks_state():
    assert "localStorage" not in OVERLAY
    assert "overlayOpenState" not in OVERLAY
    assert "aria-expanded" in OVERLAY
    assert "aria-expanded" in INDEX
    assert 'data-state="unused"' in INDEX
    assert "flag.dataset.state = allOpen ? 'used' : 'unused';" in OVERLAY
    # The flag's used/unused state is shown on its icon stroke (the former
    # before marker is gone); the two states must stay visually distinct.
    assert '.badge-bar-head[data-state="used"]::before' not in CERES_CSS
    assert '#badge-flag[data-state="unused"] .hdr-ic .a.h { stroke: var(--meta);' in CERES_CSS
    assert '#badge-flag[data-state="used"] .hdr-ic .a.h { stroke: var(--gold);' in CERES_CSS
    assert '#badge-flag[data-state="unused"] .hdr-ic .a.h' in CERES_CSS
    assert '#badge-flag[data-state="used"] .hdr-ic .a.h' in CERES_CSS
