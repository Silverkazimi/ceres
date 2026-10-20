from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_terrain_penalty_notice_names_the_picker_splinter_icons() -> None:
    notice_source = (ROOT / "ceres/notices.py").read_text()
    notice_match = re.search(r'"terrain_penalty":\s*"([^"]+)"', notice_source)
    assert notice_match is not None
    notice = notice_match.group(1)

    assert "Include terrain-penalised" not in notice
    assert re.search(r"checkbox", notice, re.IGNORECASE) is None
    assert "penalised Splinter icons" in notice

    affinity_source = (ROOT / "ceres/static/terrainAffinity.js").read_text()
    assert 'data-affinity-state="${peer.state}"' in affinity_source
    assert 'aria-label="Filter ${esc(peer.splinter)} cards"' in affinity_source

    candidates_source = (ROOT / "ceres/static/candidates.js").read_text()
    assert re.search(
        r"if \(Number\(excluded\.terrain_penalty\) > 0\) \{\s*"
        r"return `<p class=\"picker-empty\">\$\{esc\(noticeMessage\(noticeFor\('terrain_penalty'\)\)\)\}</p>`;",
        candidates_source,
    ) is not None
