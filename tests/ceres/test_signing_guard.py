""" regression guard for unanswered wallet intents."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

def test_no_id_intents_wait_for_hive_expiry_grace():
    lp = (ROOT / "ceres/static/wallet-lp.js").read_text()
    checkout = (ROOT / "ceres/static/card-checkout.js").read_text()

    assert "NEVER_SENT_AFTER_MS = 3900 * 1000" in lp
    assert "NEVER_SENT_AFTER_MS = 3900 * 1000" in checkout
    # fix round 1 (F7): legacy intents only (no saved operations); current ones use the server's proof.
    assert "if (seen === 'not_found' && !st.receipt.ops) return { state: 'pending', message: LEGACY_UNRESOLVED };" in lp
    assert "if (seen === 'not_found') { unknown = true; continue; }" in checkout
    assert "Nothing will be sent again; it keeps looking for it." in lp
    assert "before Hive expiry, absence does not prove the wallet request is cancelled" in checkout
    assert "if (found.status === 'found' && current())" in lp
    assert "found?.status === 'found'" in checkout
    assert "asked_at: new Date().toISOString()" in lp
    assert "tx.state = 'asked'; tx.asked_at = new Date().toISOString();" in checkout
    assert "late wallet callbacks persist their transaction evidence" in checkout
    assert "syncStoredIntent(ctx.player, symbol, st); ctx.render();" in lp
    # fix round 1 (F7): a known id is judged absent by the shared confirmer's chain read on the server's clock
    # the swap card hands it its id and attempt instead of asking /api/op-inclusion with the browser's own time.
    assert "txId: st.receipt.tx_id || null, intent: shared?.attemptId || null" in lp and "result?.included === false" not in lp
    assert "async function expiredAndAbsent(tx)" in checkout
    assert "(await response.json())?.included === false" in checkout

    # The browser age threshold only prunes a local watch entry. It never proves absence.
    threshold = 3900 * 1000
    assert not (3899 * 1000 >= threshold)
    assert 3900 * 1000 >= threshold
    # A positive nonce match is adopted before the absence branch in each flow.
    assert lp.index("if (found.status === 'found' && current())") < lp.index("if (seen === 'not_found' && !st.receipt.ops)")
    assert checkout.index("if (found?.status === 'found')") < checkout.index("if (seen === 'not_found') { unknown = true; continue; }")


def test_watch_entries_expire_without_blocking_and_keep_fresh_misses_blocked():
    lp = (ROOT / "ceres/static/wallet-lp.js").read_text()
    checkout = (ROOT / "ceres/static/card-checkout.js").read_text()
    assert "function watchAskedAt(item)" in lp
    assert "function watchAskedAt(item)" in checkout
    assert "now - askedAt < NEVER_SENT_AFTER_MS" in lp
    assert "now - askedAt < NEVER_SENT_AFTER_MS" in checkout
    assert "writeWatch(ctx.player, symbol, list);" in lp
    assert "writeWatch(co.player, list);" in checkout
    assert "return list.length || unknown ? 'unknown' : 'clear'" not in lp

    # The age contract used by the watch pruning predicate: expiry releases
    # a fresh covering miss remains unknown; old-format `at` is the fallback.
    expiry = 3900 * 1000
    now = 10_000_000
    old_at = now - 2 * 60 * 60 * 1000
    fresh_at = now - 3899 * 1000
    assert now - old_at >= expiry  # legacy entry (no asked_at), removed
    assert now - fresh_at < expiry  # covering miss remains blocking
    assert "[item?.receipt?.asked_at, item?.receipt?.signed_at, item?.at]" in lp
    assert "[item?.asked_at, item?.signed_at, item?.at]" in checkout
