""" (maintainer): a stalled worksite build has no timer and nothing to sign; it is a plot state with its own Stalled badge and never a Changes row or count."""
import subprocess
from pathlib import Path

STATIC = Path(__file__).parents[2] / "ceres" / "static"


def test_constructionItems_skips_stalled_builds_but_keeps_building_and_feed_rows():
    source = (STATIC / "feed-changes.js").read_text(encoding="utf-8")
    start = source.index("export function constructionItems(")
    body = source[start:source.index("\n}\n", start) + 3].replace("export function", "function", 1)
    script = f"""
const BOARD = null; const FEED_STATE = new Map(); const SENT_HOLD_MS = 600000;
{body}
const now = Date.parse('2026-10-02T12:00:00Z');
const plots = [
 {{ deed_uid: 'stalled', pending_change: {{ kind: 'worksite_change', stalled: true, ready_at: null, to_label: 'Logging Camp' }} }},
 {{ deed_uid: 'building', pending_change: {{ kind: 'worksite_change', stalled: false, ready_at: '2026-10-03T12:00:00Z', to_label: 'Mine' }} }},
 {{ deed_uid: 'ready', pending_change: {{ kind: 'worksite_change', stalled: false, ready_at: '2026-10-01T12:00:00Z', to_label: 'Farm' }} }},
 {{ deed_uid: 'done', worksite_completion: {{ ready_at: '2026-10-01T12:00:00Z', to_label: 'Quarry', grain_cost: 5 }}, pending_change: {{ kind: 'worksite_change', stalled: true }} }},
 {{ deed_uid: 'plain' }},
];
const out = constructionItems(plots, now).map(item => item.deedUid + ':' + item.kind).join(',');
if (out !== 'building:building,ready:feed,done:feed') throw new Error('unexpected items: ' + out);
"""
    subprocess.run(["node", "-e", script], check=True, capture_output=True, text=True)
