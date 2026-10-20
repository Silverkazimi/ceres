"""the combine dialog never shows a Card ID (maintainer rule: screens show plot labels and card names, never UIDs).

Runs the real combine.js render in the shared vm sandbox; no network, no browser."""

from __future__ import annotations

import json
import re

from .vm_context import ENTRY, run_in_module_context

UID = re.compile(r"\b[A-Z]{1,3}\d*-\d+-[A-Za-z0-9]+\b")

JS = r"""
const copy = (uid, bcx, level, over = {}) => ({ uid, asset_ref: uid, name: 'Cerberus', bcx, level, available: true, ...over });
const copies = [
 copy('C13-700-AAAA1', 12, 3),
 copy('C13-700-BBBB2', 4, 2, { plot: '065-09-083', status: 'staked', available: false, blocked_reason: 'card is Staked on Land' }),
 copy('C13-700-CCCC3', 2, 1, { status: 'cooling', available: false, blocked_reason: 'card is cooling down' }),
 copy('C13-700-DDDD4', 1, 1, { available: false, blocked_reason: 'card is Listed on the market' }),
 copy('C13-700-EEEE5', 1, 1),
];
const dialog = { copies, blocked: new Map(copies.filter((c) => !c.available).map((c) => [c.uid, c.blocked_reason])) };
COMBINE_SELECTION.clear();
[copies[0], copies[4]].forEach((c) => COMBINE_SELECTION.set(c.uid, c));
const html = combineCopyRowsHtml(dialog, { hazard: { destroyed_uids: ['C13-700-EEEE5'], rows: [] }, uids: ['C13-700-AAAA1', 'C13-700-EEEE5'] }, 'C13-700-AAAA1');
const reason = combineReason({ detail: {
 message: 'cannot combine selected Card IDs: C13-700-DDDD4 card is Listed',
 reasons: [{ uid: 'C13-700-DDDD4', reason: 'card is Listed' }], missing_uids: ['C13-700-ZZZZ9'] } });
console.log(JSON.stringify({ html, reason }));
"""


def _run() -> dict:
    out = run_in_module_context(JS, entry=ENTRY).stdout
    return json.loads(out.strip().splitlines()[-1])


def _visible(html: str) -> str:
    """The HTML with every data-* attribute removed (those may carry the id for signing)."""
    return re.sub(r'\sdata-[a-z-]+="[^"]*"', "", html)


def test_copy_rows_show_level_cc_and_where_never_a_card_id():
    html = _run()["html"]
    assert UID.search(_visible(html)) is None, _visible(html)
    texts = re.sub(r"<[^>]+>", "|", html)
    assert "Idle" in texts and "065-09-083" in texts and "Cooling" in texts and "Listed" in texts
    assert "12 CC · Lv 3" in texts
    assert 'data-uid="C13-700-AAAA1"' in html  # kept for signing and the browser tests


def test_refusal_text_names_no_card_id():
    reason = _run()["reason"]
    assert UID.search(reason) is None, reason
    assert "Card ID" not in reason
