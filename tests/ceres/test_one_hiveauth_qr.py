""" (diagnosis IN-1013): one HiveAuth connect request draws ONE request box (QR), not one per wallet-controls instance.

The SPS tab holds two wallet-controls instances (claim card and swap card) once the claim review is open. The request
box (QR) must be drawn only in the instance that was clicked; the other keeps its normal closed state. Runs the real
wallet.js module in the node sandbox (no browser, no network, no signing)."""
from __future__ import annotations

import json

from tests.ceres.vm_context import STATIC, run_in_module_context

# module-private state is reachable here: the sandbox publishes uniquely named private bindings
PRELUDE = """
const count = (html) => (html.match(/data-wallet-request/g) || []).length;
const qr = (html) => (html.match(/wallet-qr/g) || []).length;
const open = (html) => /wallet-drawer is-open/.test(html);
const asView = () => { requestView = { title: 'HiveAuth', account: 'alice', status: 'Scan', link: 'hiveauth://x', svg: '<svg></svg>' }; };
"""


def _run(body: str) -> dict:
    script = PRELUDE + body + "\nconsole.log('IR9558 ' + JSON.stringify(out));"
    result = run_in_module_context(script, entry=STATIC / "wallet.js", check=False)
    line = next((l for l in result.stdout.splitlines() if l.startswith("IR9558 ")), None)
    assert line, (result.stdout[-1500:], result.stderr[-3000:])
    return json.loads(line.split(" ", 1)[1])


def test_two_controls_one_connect_request_draw_exactly_one_qr_in_the_clicked_instance():
    out = _run("""
showDialog('Connect HiveAuth', 'alice', false, 'claim');
asView();
const claim = walletControlsHtml('alice', 'claim'), swap = walletControlsHtml('alice', 'swap');
const out = { claim: count(claim), swap: count(swap), claimQr: qr(claim), swapQr: qr(swap), swapOpen: open(swap), claimOpen: open(claim) };
""")
    assert out["claim"] == 1 and out["claimQr"] >= 1 and out["claimOpen"], out
    assert out["swap"] == 0 and out["swapQr"] == 0 and not out["swapOpen"], out


def test_the_other_instance_hosts_it_when_that_one_was_clicked():
    out = _run("""
showDialog('Connect HiveAuth', 'alice', false, 'swap');
asView();
const out = { claim: count(walletControlsHtml('alice', 'claim')), swap: count(walletControlsHtml('alice', 'swap')) };
""")
    assert out == {"claim": 0, "swap": 1}, out


def test_without_a_known_host_every_instance_still_renders_and_the_page_keeps_the_first_only():
    out = _run("""
showDialog('Connect HiveAuth', 'alice', false, null);
asView();
const out = { claim: count(walletControlsHtml('alice', 'claim')), swap: count(walletControlsHtml('alice', 'swap')) };
""")
    assert out == {"claim": 1, "swap": 1}, out   # notify() removes all but the first from the page (browser-only step)


def test_closing_the_request_clears_the_host():
    out = _run("""
showDialog('Connect HiveAuth', 'alice', false, 'claim');
closeDialog();
asView();
const out = { claim: count(walletControlsHtml('alice', 'claim')), swap: count(walletControlsHtml('alice', 'swap')) };
""")
    assert out == {"claim": 1, "swap": 1}, out
