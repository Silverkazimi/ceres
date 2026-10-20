"""13870/13900: wallet sections fail independently, unknown is never zero, LP math."""
from decimal import Decimal

from ceres import routes_wallet as wallet
from ceres.harvest_routing import Pool
from .vm_context import STATIC, run_in_module_context


def rows(*items):
    return [{"player": "player0005", "token": t, "balance": b} for t, b in items]


def pools():
    return {"GRAIN": Pool(1, "GRAIN", Decimal(1000), Decimal(500), Decimal(100)),
            "SPS": Pool(100, "SPS", Decimal(2000), Decimal(4000), Decimal(200), True)}


def regions_payload():
    return {"status": "success", "data": [
        {"uid": "PR-CEF-65", "name": "065 - Mandar Camden", "plots_owned": 102, "grain": 5.5, "wood": 0, "stone": 1, "iron": 2, "research": 3, "aura": 4},
        {"uid": "PR-PNW-1", "name": "001 - Viritas", "plots_owned": 3, "grain": 0, "wood": 0, "stone": 0, "iron": 0, "research": 2.623, "aura": 0}]}


def test_all_sections_present_and_swap_lists_only_internal_resource_pools():
    data = wallet.build_wallet("player0005", lambda p: rows(("DEC", 10), ("SPS", 1.5), ("SPSP", 400), ("VOUCHER", 2), ("DEC-GRAIN", 25)),
                               lambda p: regions_payload(), pools)
    assert data["issues"] == []
    assert data["account"] == {"DEC": 10, "SPS": 1.5, "SPSP": 400, "VOUCHER": 2}
    assert [r["region_uid"] for r in data["regions"]] == ["PR-PNW-1", "PR-CEF-65"]
    assert data["regions"][1]["balances"] == {"GRAIN": 5.5, "WOOD": 0, "STONE": 1, "IRON": 2, "RESEARCH": 3, "AURA": 4}
    # no LP position/reward data any more; every non-soulbound token with a DEC pool swaps both
    # ways (SPS too, its pool is external), fixed 2.5 % tolerance, signing off by default.
    assert "positions" not in data and "liquidity" not in data and "lp_fees" not in data
    swap = data["swap"]
    assert {k: swap[k] for k in ("symbols", "directions", "slippage", "signing_enabled")} == \
        {"symbols": ["GRAIN", "SPS"], "directions": ["swap_sell", "swap_buy"], "slippage": "2.5", "signing_enabled": False}
    # The game's per-unit lines from the same pool: 1,000 GRAIN / 500 DEC, 10 % fee.
    assert swap["rates"]["GRAIN"]["swap_sell"] == {"unit": "0.450", "inverse": "2.224"}
    assert swap["rates"]["GRAIN"]["swap_buy"] == {"unit": "1.797", "inverse": "0.557"}
    assert wallet.build_wallet("player0005", lambda p: rows(("DEC", 1)), lambda p: regions_payload(), pools,
                               signing_enabled=True)["swap"]["signing_enabled"] is True

def test_failed_sections_are_null_never_zero():
    def boom(*_):
        raise RuntimeError("upstream down")
    data = wallet.build_wallet("player0005", boom, lambda p: regions_payload(), pools)
    assert data["account"] is None and data["swap"] is not None
    assert data["regions"] is not None
    assert "Account balances are unavailable." in data["issues"]
    data = wallet.build_wallet("player0005", lambda p: rows(("DEC", 1)), boom, boom)
    assert data["regions"] is None and data["swap"] is None and data["account"]["DEC"] == 1
    # an unreadable pools read only hides the swap card -- it adds no issue text.
    assert data["issues"] == ["Regional land resources are unavailable."]

def test_malformed_regional_row_invalidates_only_regions():
    bad = {"status": "success", "data": [{"uid": "PR-CEF-65", "grain": "many"}]}
    data = wallet.build_wallet("player0005", lambda p: rows(("DEC", 1)), lambda p: bad, pools)
    assert data["regions"] is None and data["account"] is not None and data["swap"] is not None
    assert any("unavailable" in issue for issue in data["issues"])


def test_no_tab_renders_an_lp_position_or_pool_reward_card():
    # the two LP boxes ("unusable and 100% noise") are gone from every tab, whatever the data.
    from .vm_context import STATIC, run_in_module_context
    body_js = """
const wallet = { account: { DEC: 1, SPS: 1, SPSP: 1, VOUCHER: 1 }, soulbound: ['AURA', 'RESEARCH'], resources: ['GRAIN', 'WOOD', 'STONE', 'IRON', 'RESEARCH', 'AURA'],
 regions: [{ region_uid: 'PR-CEF-65', plots_owned: 3, balances: { GRAIN: 5, WOOD: 5, STONE: 5, IRON: 5, RESEARCH: 5, AURA: 5 } }],
 positions: { GRAIN: { pool_id: 1, shares: '25.000', share_percent: '25' }, SPS: { pool_id: 100, shares: '1', share_percent: '1' } },
 liquidity: [{ symbol: 'GRAIN', pool_id: 1, pool_reward_total_dec: '1', pool_reward_total_resource: '1' }], rewards: null };
for (const tab of ['SPS', 'VOUCHER', 'GRAIN', 'WOOD', 'STONE', 'IRON', 'RESEARCH', 'AURA']) {
 const html = walletResourceHtml(tab, wallet, false);
 for (const noise of ['liquidity pool', 'Liquidity <small>', 'Pool data unavailable', 'No position in this pool', 'Pool rewards', 'Pool holds'])
 if (html.includes(noise)) throw new Error(tab + ' still renders LP noise (' + noise + '): ' + html);
}
"""
    run_in_module_context(body_js, entry=STATIC / "wallet-resources.js")

def test_wallet_tabs_use_global_tab_family_and_regions_sort_by_holding():
    from pathlib import Path
    js = (Path(__file__).parents[2] / "ceres" / "static" / "wallet-resources.js").read_text()
    # the tab row is now icon-only (.wallet-tabs--icons); still the global tab family.
    assert 'class="ceres-tabs wallet-tabs wallet-tabs--icons" role="tablist"' in js and 'role="tab" aria-selected=' in js
    assert "aria-pressed" not in js
    assert ".sort((a, b) => Number(b.balances[symbol]) - Number(a.balances[symbol])" in js
    assert "class=\"is-zero\"" in js
    css = (Path(__file__).parents[2] / "ceres" / "static" / "ceres.css").read_text()
    assert ".wallet-regions li.is-zero { opacity: .45; }" in css
    assert 'is(.receipts-family-nav, .ceres-tabs) [role="tab"][aria-selected="true"]' in css


def test_wallet_tabs_allowed_list_exempts_dec_and_sps_from_busy():
    # (a): while only a pending DEC receipt/claim blocks tabs, DEC/SPS stay enabled.
    from .vm_context import STATIC, run_in_module_context
    body_js = """
const html = walletTabsHtml('DEC', true, ['DEC', 'SPS']);
const decMatch = html.match(/data-dec="tab:DEC"[^>]*>/)[0];
const spsMatch = html.match(/data-dec="tab:SPS"[^>]*>/)[0];
const grainMatch = html.match(/data-dec="tab:GRAIN"[^>]*>/)[0];
if (decMatch.includes('disabled')) throw new Error('DEC tab must stay enabled: ' + decMatch);
if (spsMatch.includes('disabled')) throw new Error('SPS tab must stay enabled: ' + spsMatch);
if (!grainMatch.includes('disabled')) throw new Error('GRAIN tab must stay disabled: ' + grainMatch);

const allBusy = walletTabsHtml('DEC', true);
if (!allBusy.match(/data-dec="tab:DEC"[^>]*>/)[0].includes('disabled')) throw new Error('no allowed list must disable every tab');
"""
    run_in_module_context(body_js, entry=STATIC / "wallet-resources.js")


def test_claim_controls_disabled_freezes_on_drawer_busy_too():
    # (b): claim buttons freeze while D.busy (the wallet drawer's own load), not only claim.busy.
    from .vm_context import STATIC, run_in_module_context
    body_js = """
if (claimControlsDisabled({ claim: { busy: false }, busy: false }) !== false) throw new Error('neither busy should not disable');
if (claimControlsDisabled({ claim: { busy: true }, busy: false }) !== true) throw new Error('claim.busy should disable');
if (claimControlsDisabled({ claim: { busy: false }, busy: true }) !== true) throw new Error('D.busy should disable too');

const D = { claim: { busy: false }, busy: true, player: 'player0005', rewards: { total: 5 } };
const html = claimHtml(D);
const reviewButton = html.match(/data-dec="claim:review"[^>]*>/)[0];
if (!reviewButton.includes('disabled')) throw new Error('claim review button must be disabled while D.busy: ' + reviewButton);
"""
    run_in_module_context(body_js, entry=STATIC / "wallet-claim.js")


def test_claim_action_clears_busy_when_drawer_generation_changes():
    body_js = """
const D = { player: 'player0005', generation: 0, error: '', signing: false,
 claim: { busy: false, review: null, pending: null, checking: false } };
let renders = 0;
const render = () => { renders += 1; if (renders === 1) D.generation += 1; };
(async () => {
 await claimAction(D, 'review', render, () => 'player0005', async () => {});
 if (D.claim.busy) throw new Error('claim.busy must clear after a stale-generation exit');
 if (D.signing) throw new Error('D.signing must clear after a stale-generation exit');
})().then(() => process.exit(0), error => { console.error(error); process.exit(1); });
"""
    extra_context_js = """
context.fetch = () => Promise.resolve({ ok: true, json: async () => ({ total: '1', rows: [], operations: [], signing_enabled: true }) });
context.window.fetch = context.fetch;
"""
    run_in_module_context(body_js, entry=STATIC / "wallet-claim.js", extra_context_js=extra_context_js)


def test_reward_rows_sort_largest_first_and_label_sources():
    payload = {"unclaimed_balances": [
        {"player": "player0005", "token": "SPS", "type": "wild", "balance": "0.003"},
        {"player": "player0005", "token": "SPS", "type": "land_mining", "balance": "516.968"},
        {"player": "player0005", "token": "SPS", "type": "brawl", "balance": "0.000"}], "last_claim_date": "2026-09-19T07:59:40.042Z"}
    data = wallet.reward_rows(payload, "player0005")
    assert [r["type"] for r in data["rows"]] == ["land_mining", "wild", "brawl"]
    assert data["rows"][0]["source"] == "land" and data["rows"][1]["source"] == "battle"
    assert data["total"] == "516.971" and data["last_claim_date"].startswith("2026-09-19")


def test_reward_rows_refuse_foreign_or_malformed_rows():
    import pytest
    with pytest.raises(ValueError):
        wallet.reward_rows({"unclaimed_balances": [{"player": "other", "token": "SPS", "type": "wild", "balance": "1"}]}, "player0005")
    with pytest.raises(ValueError):
        wallet.reward_rows({"unclaimed_balances": [{"player": "player0005", "token": "SPS", "type": "wild", "balance": "many"}]}, "player0005")
    with pytest.raises(ValueError):
        wallet.reward_rows({"error": "player and token are required"}, "player0005")


def test_claim_operation_is_one_posting_custom_json():
    import json
    import re
    operation, nonce = wallet.claim_operation("player0005")
    op_id, body = operation
    assert op_id == "custom_json"
    assert body["id"] == "sm_claim_rewards"
    assert body["required_posting_auths"] == ["player0005"]
    assert body["required_auths"] == []
    payload = json.loads(body["json"])
    assert set(payload.keys()) == {"app", "n"}
    from ceres.transaction_identity import app_identity
    assert payload["app"] == app_identity()
    assert payload["n"] == nonce
    assert re.fullmatch(r"[a-f0-9]{16}", nonce)


def test_require_posting_claim_refuses_active_key_claims():
    import pytest
    wallet.require_posting_claim({"active_auth_ops": ["claim_staking_rewards"]})
    with pytest.raises(ValueError):
        wallet.require_posting_claim({"active_auth_ops": ["claim_rewards"]})
    with pytest.raises(ValueError):
        wallet.require_posting_claim({})


def test_claim_result_matrix():
    import json
    import pytest

    tx = "a" * 40
    nonce = "b" * 16

    def info(**overrides):
        base = {"id": tx, "player": "player0005", "type": "claim_rewards", "data": json.dumps({"app": "x", "n": nonce}),
                "success": True, "result": json.dumps({"balances": [
                    {"type": "land_mining", "token": "SPS", "balance": "516.968"},
                    {"type": "wild", "token": "SPS", "balance": "0.003"}]})}
        base.update(overrides)
        return base

    verdict = wallet.claim_result(info(), "player0005", nonce, tx)
    assert verdict["status"] == "confirmed"
    assert verdict["total"] == "516.971"
    labels = {row["label"] for row in verdict["claimed"]}
    assert labels == {"Land mining", "Wild battles"}

    assert wallet.claim_result(None, "player0005", nonce, tx) == {"status": "not_found"}

    failed = wallet.claim_result(info(success=False, error="nope"), "player0005", nonce, tx)
    assert failed == {"status": "failed", "message": "nope"}

    with pytest.raises(ValueError):
        wallet.claim_result(info(data=json.dumps({"app": "x", "n": "c" * 16})), "player0005", nonce, tx)

    pending = wallet.claim_result(info(result=json.dumps({"no_balances": True})), "player0005", nonce, tx)
    assert pending == {"status": "pending"}


def test_claim_ui_is_wired():
    from pathlib import Path
    regional = (Path(__file__).parents[2] / "ceres" / "static" / "regional-dec.js").read_text()
    assert "claimAction(D, action.slice(6), render, account, () => Promise.all([loadWallet({ quiet: true }), loadRewards()]))" in regional
    assert "withClaimInRewards(walletResourceHtml(D.tab, D.wallet, D.walletBusy), D.tab === 'SPS' ? claimHtml(D) : '')" in regional  # the Claim button closes the rewards card; the swap card follows
    claim = (Path(__file__).parents[2] / "ceres" / "static" / "wallet-claim.js").read_text()
    assert "walletSignLabelHtml('Claim and sign with')" in claim
    assert "requestBroadcastThroughGate(D.player, fresh.operations" in claim


def test_claim_result_rejects_other_player_or_type():
    import json, pytest
    nonce = "a" * 16; tx = "b" * 40
    base = {"id": tx, "player": "player0005", "type": "claim_rewards", "data": json.dumps({"app": "x", "n": nonce}), "success": True,
            "result": json.dumps({"balances": [{"type": "wild", "token": "SPS", "balance": "1.000"}]})}
    assert wallet.claim_result(base, "player0005", nonce, tx)["status"] == "confirmed"
    with pytest.raises(ValueError):
        wallet.claim_result({**base, "player": "someone"}, "player0005", nonce, tx)
    with pytest.raises(ValueError):
        wallet.claim_result({**base, "type": "claim_staking_rewards"}, "player0005", nonce, tx)
    with pytest.raises(ValueError):
        wallet.claim_result({**base, "id": "c" * 40}, "player0005", nonce, tx)


def test_claim_pending_checks_automatically_and_keeps_queue_tx_id():
    from pathlib import Path
    js = (Path(__file__).parents[2] / "ceres" / "static" / "wallet-claim.js").read_text()
    assert "Discard this claim" not in js and "Change transaction ID" not in js
    assert "claim.retryTimer = setTimeout" in js
    assert "error.txId" in js and "tx_id: error.txId" in js
    dec = (Path(__file__).parents[2] / "ceres" / "static" / "regional-dec.js").read_text()
    assert dec.index("if (D.busy || D.closing) return;") < dec.index("if (action.startsWith('claim:'))")
    assert "if (D.pending) D.tab = 'DEC'; else if (D.claim.pending) D.tab = 'SPS';" in dec


def test_tab_guard_allows_dec_and_sps_while_only_pending_blocks():
    # (a): a pending DEC receipt / claim alone no longer refuses tab:DEC / tab:SPS -- only
    # D.review, D.claim.review, or D.busy still refuse every tab switch.
    from pathlib import Path
    js = (Path(__file__).parents[2] / "ceres" / "static" / "regional-dec.js").read_text()
    assert "export function tabSwitchAllowed(D, tab) {" in js
    assert "if (!WALLET_TABS.includes(tab) || D.review || D.claim.review || D.busy) return false;" in js
    assert "if ((D.pending || D.claim.pending) && !['DEC', 'SPS'].includes(tab)) return false;" in js
    assert "if (!tabSwitchAllowed(D, tab) || swapLocked(D.player)) return;" in js  # a swap in flight keeps its tab


# staked DEC.
def dec_overall_payload(value=250.5):
    return {"status": "success", "data": value}


def dec_staked_payload():
    # PR-BRL-3 (review fix ): a zero-stake row the upstream still lists for an account
    # that has touched the region before -- must not appear in the per-region output/tooltip.
    return {"status": "success", "data": [
        {"region_uid": "PR-CEF-65", "player": "player0005", "amount": 200},
        {"region_uid": "PR-BRL-3", "player": "player0005", "amount": 0},
        {"region_uid": "PR-PNW-1", "player": "player0005", "amount": 50.5}]}


def test_staked_dec_sections_and_wallet_open_makes_five_reads():
    calls = []
    def track(name, value):
        def read(*_):
            calls.append(name)
            return value() if callable(value) else value
        return read
    data = wallet.build_wallet("player0005", track("balances", rows(("DEC", 10))), track("regions", regions_payload()), track("pools", pools),
                               dec_overall_read=track("dec_overall", dec_overall_payload()), dec_staked_read=track("dec_staked", dec_staked_payload()),
                               signing_enabled=True)
    assert data["staked_dec"] == {"overall": "250.500", "by_region": [
        {"region_uid": "PR-PNW-1", "amount": "50.500"}, {"region_uid": "PR-CEF-65", "amount": "200.000"}]}
    assert data["issues"] == []
    # allrewards and all-no-vesting are no longer read, even with signing on.
    assert sorted(calls) == ["balances", "dec_overall", "dec_staked", "pools", "regions"]

def test_staked_dec_sections_degrade_independently_never_zero():
    def boom(*_):
        raise RuntimeError("upstream down")
    data = wallet.build_wallet("player0005", lambda p: rows(("DEC", 10)), lambda p: regions_payload(), pools,
                               dec_overall_read=boom, dec_staked_read=lambda p: dec_staked_payload())
    assert data["staked_dec"]["overall"] is None
    assert data["staked_dec"]["by_region"] == [{"region_uid": "PR-PNW-1", "amount": "50.500"}, {"region_uid": "PR-CEF-65", "amount": "200.000"}]
    assert data["issues"] == ["Staked DEC total is unavailable."]


def test_stake_regions_section_drops_zero_amount_rows():
    # Review fix: the upstream lists every region the account has ever touched, most at 0
    # only rows with a real staked amount should reach the wallet payload / tooltip.
    data = wallet.stake_regions_section(dec_staked_payload())
    assert data == [{"region_uid": "PR-PNW-1", "amount": "50.500"}, {"region_uid": "PR-CEF-65", "amount": "200.000"}]
    assert all(row["region_uid"] != "PR-BRL-3" for row in data)


def test_stake_overall_rejects_non_scalar_or_malformed_payload():
    import pytest
    with pytest.raises(ValueError):
        wallet.stake_overall_section({"status": "success", "data": "not-a-number"})
    with pytest.raises(ValueError):
        wallet.stake_overall_section({"status": "error"})


def test_pool_number_error_messages_are_family_specific():
    # Review fix: the stake families name their own malformed figures.
    import pytest
    with pytest.raises(ValueError, match="^Staked DEC total is unavailable$"):
        wallet.stake_overall_section({"status": "success", "data": "nope"})
    with pytest.raises(ValueError, match="^Staked DEC figure is unavailable$"):
        wallet.stake_regions_section({"status": "success", "data": [{"region_uid": "PR-CEF-65", "amount": "nope"}]})


def test_staked_dec_line_source_guard_and_removed_evp_call():
    from pathlib import Path
    js = (Path(__file__).parents[2] / "ceres" / "static" / "regional-dec.js").read_text()
    assert "function stakedDecHtml(wallet)" in js
    assert "Staked DEC " in js
    assert "EVP" not in js
    assert "${stakedDecHtml(D.wallet)}" in js
    assert "regionLabel(r.region_uid)" in js  # per-region tooltip text
    route = (Path(__file__).parents[2] / "ceres" / "routes_wallet.py").read_text()
    assert "stake_evp_pending" not in route
    assert "evp_pending" not in route
    data = wallet.build_wallet("player0005", lambda p: rows(("DEC", 10)), lambda p: regions_payload(), pools)
    assert "evp_pending" not in data


def test_tab_switch_allowed_pure_function_executed():
    # (a): executed VM test for the extracted tabSwitchAllowed(D, tab) predicate.
    body_js = """
const baseline = { pending: null, review: null, busy: false, claim: { review: null, pending: null } };

// Pending DEC + pending claim -> DEC/SPS allowed, GRAIN refused.
const pendingBoth = { ...baseline, pending: { tx_id: 'a' }, claim: { review: null, pending: { tx_id: 'b' } } };
if (tabSwitchAllowed(pendingBoth, 'DEC') !== true) throw new Error('DEC should be allowed with both pending');
if (tabSwitchAllowed(pendingBoth, 'SPS') !== true) throw new Error('SPS should be allowed with both pending');
if (tabSwitchAllowed(pendingBoth, 'GRAIN') !== false) throw new Error('GRAIN should be refused with both pending');

// Review open -> all refused.
const reviewOpen = { ...baseline, review: { region_uid: 'PR-CEF-65' } };
if (tabSwitchAllowed(reviewOpen, 'DEC') !== false) throw new Error('DEC should be refused with review open');
if (tabSwitchAllowed(reviewOpen, 'SPS') !== false) throw new Error('SPS should be refused with review open');
if (tabSwitchAllowed(reviewOpen, 'GRAIN') !== false) throw new Error('GRAIN should be refused with review open');
const claimReviewOpen = { ...baseline, claim: { review: { tx_id: 'x' }, pending: null } };
if (tabSwitchAllowed(claimReviewOpen, 'DEC') !== false) throw new Error('DEC should be refused with claim review open');

// Busy -> all refused.
const busy = { ...baseline, busy: true };
if (tabSwitchAllowed(busy, 'DEC') !== false) throw new Error('DEC should be refused while busy');
if (tabSwitchAllowed(busy, 'SPS') !== false) throw new Error('SPS should be refused while busy');
if (tabSwitchAllowed(busy, 'GRAIN') !== false) throw new Error('GRAIN should be refused while busy');
"""
    run_in_module_context(body_js)
