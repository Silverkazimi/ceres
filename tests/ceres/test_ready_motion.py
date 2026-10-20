"""  alert animation obeys the motion and theme contracts."""
from pathlib import Path
import re

ROOT = Path(__file__).parents[2]
CSS = (ROOT / 'ceres/static/ceres.css').read_text()
PULSE = (ROOT / 'ceres/static/pulse-sync.js').read_text()


def test_ready_keyframe_animates_opacity_only_and_syncs_with_stage_pulses():
    match = re.search(r'@keyframes\s+changes-ready-pulse\s*\{([^}]+)\}', CSS)
    assert match and 'opacity' in match.group(1)
    assert not re.search(r'\b(transform|box-shadow|filter|background|color)\s*:', match.group(1))
    assert "'changes-ready-pulse'" in PULSE
    assert re.search(r'prefers-reduced-motion:\s*reduce[^}]*\{[^}]*animation:\s*none;[^}]*opacity:\s*\.6', CSS)
    assert '--ready-alert: color-mix(in srgb, var(--ok) 70%, var(--hi))' in CSS
    assert '[data-theme="light"], [data-theme="wood"] { --ready-alert: var(--ok); }' in CSS
    assert 'transition: box-shadow' not in CSS[CSS.index('/*  : the fixed Changes controls'):]
