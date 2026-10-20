""": the wallet pool swap card (option B) -- where it renders, what it never shows."""
import re
from pathlib import Path

from .vm_context import STATIC, run_in_module_context

ROOT = Path(__file__).parents[2]


def test_swap_card_renders_on_every_tier_for_the_four_resources_with_a_balance():
    body_js = """
const wallet = { account: { DEC: 12, SPS: 1, SPSP: 0, VOUCHER: 0 },
  swap: { symbols: ['GRAIN', 'WOOD', 'STONE', 'IRON'], directions: ['swap_sell'], slippage: '2.5', signing_enabled: false },
  regions: [{ region_uid: 'PR-CEF-65', plots_owned: 3, balances: { GRAIN: 2500, WOOD: 0, STONE: 7, IRON: 1, RESEARCH: 5, AURA: 5 } },
            { region_uid: 'PR-GLL-41', plots_owned: 2, balances: { GRAIN: 9000, WOOD: 0, STONE: 0, IRON: 0, RESEARCH: 0, AURA: 0 } }] };
const grain = swapHtml('GRAIN', wallet, 'player0005', true);
if (!grain.includes('data-lp-symbol="GRAIN"') || !grain.includes('You pay') || !grain.includes('You receive')) throw new Error('grain card missing: ' + grain);
// The richest region is the default source; the shared dropdown lists every region with a balance.
if (!grain.includes('Region 41') || !grain.includes('data-lp-region')) throw new Error('region choice missing: ' + grain);
// A server that lists only SELL (a gated BUY) shows no flip button and no buy side.
if (grain.includes('lp:flip') || grain.includes('Receive into')) throw new Error('buy direction must stay hidden: ' + grain);
// No quote yet: no receive figure, Review not clickable, no illustrative text, no slippage field.
if (!/data-dec="lp:swap"[^>]*disabled/.test(grain)) throw new Error('Swap must wait for a live quote: ' + grain);
if (/Illustrative|unavailable|expired|Custom/i.test(grain)) throw new Error('forbidden text: ' + grain);
if (!grain.includes('ceres-wide-input')) throw new Error('amount must use the shared input: ' + grain);
if (swapHtml('WOOD', wallet, 'player0005', true) !== '') throw new Error('no card without a balance to sell');
for (const tab of ['DEC', 'SPS', 'VOUCHER', 'RESEARCH', 'AURA'])
  if (swapHtml(tab, wallet, 'player0005', true) !== '') throw new Error(tab + ' must not get a swap card');
// : the phone shows the same swap card (was hidden by ).
if (!swapHtml('GRAIN', wallet, 'player0005').includes('class="wallet-card wallet-swap"')) throw new Error('phone gets the swap card');
if (swapHtml('GRAIN', { ...wallet, swap: null }, 'player0005', true) !== '') throw new Error('unreadable pools hide the card silently');

// Amount handling: capped at the live balance, three decimals at most.
if (capAmount('99999', 2500) !== '2500') throw new Error('cap ' + capAmount('99999', 2500));
if (capAmount('1.23456', 2500) !== '1.234') throw new Error('decimals ' + capAmount('1.23456', 2500));
if (parseAmount('1,000.5') !== '1000.5' || parseAmount('0') !== null || parseAmount('') !== null) throw new Error('parse');

// A live quote fills the card; with signing off, Review has no Sign button.
const st = swapState('player0005', 'STONE');
st.amount = '5'; st.quoted = '5';
st.quote = { kind: 'swap_sell', input: '5', output: '0.4', minimum: '0.39', pool_cost: '0.5', slippage: '2.5', resource_symbol: 'STONE' };
const stone = swapHtml('STONE', wallet, 'player0005', true);
//  (maintainer-approved simplification): the big receive figure; fee, minimum and balances-after sit behind Details.
if (!stone.includes('0.400 DEC') || !stone.includes('Swap 5.000 Stone → 0.400 DEC')) throw new Error('receive figure / action: ' + stone);
st.moreOpen = true;
const stoneOpen = swapHtml('STONE', wallet, 'player0005', true);
if (!stoneOpen.includes('Pool fee · 10%') || !stoneOpen.includes('0.500 Stone') || !stoneOpen.includes('You get at least') || !stoneOpen.includes('0.390 DEC')) throw new Error('details: ' + stoneOpen);
if (!stoneOpen.includes('Wallet DEC after') || !stoneOpen.includes('12.400') || !stoneOpen.includes('Region 65 Stone after') || !stoneOpen.includes('2.000')) throw new Error('after rows: ' + stoneOpen);
// : one Swap button, no Review/Edit anywhere.
if (/lp:review|lp:edit|lp:sign|Check and sign/.test(stoneOpen)) throw new Error('no review stage: ' + stoneOpen);
// No wallet ready: the chooser sits left of Swap (OM:6100) and Swap waits for it.
const noWallet = swapHtml('STONE', { ...wallet, swap: { ...wallet.swap, signing_enabled: true } }, 'player0005', true);
const pair = noWallet.indexOf('wallet-swap-sign'), chooser = noWallet.indexOf('wallet-controls'), swapAt = noWallet.indexOf('data-dec="lp:swap"');
if (pair < 0 || chooser < pair || swapAt < chooser || !/data-dec="lp:swap"[^>]*disabled/.test(noWallet)) throw new Error('chooser left of Swap: ' + noWallet);
// Keychain present: just the Swap button, ready; with signing switched off it stays disabled.
window.hive_keychain = { requestBroadcast() { throw new Error('never broadcast in tests'); } };
const ready = swapHtml('STONE', { ...wallet, swap: { ...wallet.swap, signing_enabled: true } }, 'player0005', true);
if (ready.includes('wallet-controls') || /data-dec="lp:swap"[^>]*disabled/.test(ready) || !ready.includes('Swap 5.000 Stone → 0.400 DEC')) throw new Error('ready Swap: ' + ready);
if (!/data-dec="lp:swap"[^>]*disabled/.test(swapHtml('STONE', wallet, 'player0005', true))) throw new Error('signing off keeps Swap disabled');
if (swapLocked('player0005') !== null) throw new Error('an idle card locks nothing');
st.stage = 'confirming'; st.receipt = { region: 'PR-CEF-65', input: 5, output: 0.4, nonce: 'a', tx_id: null };
if (swapLocked('player0005') !== null) throw new Error(': a swap awaiting the game never freezes the tabs');
st.stage = 'signing';
if (swapLocked('player0005') !== 'STONE') throw new Error('an open wallet prompt keeps its tab');
"""
    run_in_module_context(body_js, entry=STATIC / "wallet-lp.js")


def test_swap_css_is_scoped_and_uses_shared_controls():
    css = (ROOT / "ceres" / "static" / "ceres.css").read_text()
    start = css.index("/* : the wallet pool swap card")
    block = css[start:css.index(".wallet-swap .dec-receipt-amount > img", start)]
    for line in block.splitlines():
        if line.startswith(".") or line.startswith("#"):
            assert line.startswith(".wallet-swap"), line
    # only the approved on-scale sizes -- the receive figure (1.25rem) and the one small line (.75rem)
    # the amount input keeps the shared control size ().
    assert set(re.findall(r"font-size:\s*([^;]+);", block)) <= {"1.25rem", ".75rem"}
    assert "wallet-swap-amount" not in "".join(l for l in block.splitlines() if "font-size" in l)
    js = (ROOT / "ceres" / "static" / "wallet-lp.js").read_text()
    assert 'class="ceres-wide-input wallet-swap-amount" data-control-density="compact"' in js
    assert "initCustomSelect(" in js  # the shared dropdown for Change region


def test_regional_dec_wires_the_swap_card_after_the_wallet_resource_view():
    js = (ROOT / "ceres" / "static" / "regional-dec.js").read_text()
    # the pool position card closes the tab, after the swap card
    assert "withClaimInRewards(walletResourceHtml(D.tab, D.wallet, D.walletBusy), D.tab === 'SPS' ? claimHtml(D) : '') + (D.walletBusy ? '' : swapHtml(D.tab, D.wallet, D.player) + positionHtml(D.tab, D.wallet, D.player))" in js  # the Claim button sits inside the rewards card
    assert "if (action.startsWith('lp:')) { await swapAction(swapContext(), action.slice(3), target); return; }" in js
    assert "|| !!swapLocked(D.player);" in js


def test_flip_turns_the_card_into_dec_to_resource_with_receive_into_a_region():
    body_js = """
const wallet = { account: { DEC: 12, SPS: 1, SPSP: 0, VOUCHER: 0 },
  swap: { symbols: ['GRAIN', 'WOOD', 'STONE', 'IRON'], directions: ['swap_sell', 'swap_buy'], slippage: '2.5', signing_enabled: false,
    rates: { GRAIN: { swap_sell: { unit: '0.008', inverse: '128.643' }, swap_buy: { unit: '104.201', inverse: '0.010' } },
             WOOD: { swap_sell: { unit: '0.1', inverse: '10' }, swap_buy: { unit: '8', inverse: '0.125' } } } },
  regions: [{ region_uid: 'PR-CEF-65', plots_owned: 3, balances: { GRAIN: 2500, WOOD: 0, STONE: 7, IRON: 1, RESEARCH: 5, AURA: 5 } },
            { region_uid: 'PR-BRL-41', plots_owned: 2, balances: { GRAIN: 9000, WOOD: 0, STONE: 0, IRON: 0, RESEARCH: 0, AURA: 0 } }] };
const sell = swapHtml('GRAIN', wallet, 'player0005', true);
if (!sell.includes('data-lp-direction="sell"') || !sell.includes('Grain</span>→<span>') || !sell.includes('data-dec="lp:flip"')) throw new Error('sell card with flip: ' + sell);
// The game's per-unit lines, exactly as the Trade Hub words them.
if (!sell.includes('1 Grain = 0.008 DEC')) throw new Error('rate line: ' + sell);
if (!sell.includes('of 9,000.000') || !sell.includes('Region 41')) throw new Error('sell source: ' + sell);
// Flip: the state the ⇅ button sets (swapAction 'flip').
const st = swapState('player0005', 'GRAIN');
st.direction = 'buy';
const buy = swapHtml('GRAIN', wallet, 'player0005', true);
if (!buy.includes('data-lp-direction="buy"') || !buy.includes('DEC</span>→<span>')) throw new Error('flipped header: ' + buy);
if (!buy.includes('of 12.000 in your wallet') || !buy.includes('aria-label="You pay, DEC"')) throw new Error('buy pays DEC: ' + buy);
const receiveBox = buy.slice(buy.indexOf('wallet-swap-receive'));
if (!receiveBox.includes('Region 41') || !receiveBox.includes('data-lp-region')) throw new Error('buy lands in a region: ' + buy);
if (!buy.includes('1 DEC = 104.201 Grain')) throw new Error('buy rate line: ' + buy);
// A quote for the other direction is never shown on this side.
st.amount = '1'; st.quoted = '1';
st.quote = { kind: 'swap_sell', input: '1', output: '0.008', minimum: '0.007', pool_cost: '0.1', slippage: '2.5', resource_symbol: 'GRAIN' };
if (/value="0\.008"/.test(swapHtml('GRAIN', wallet, 'player0005', true))) throw new Error('sell quote leaked into buy side');
st.quote = { kind: 'swap_buy', input: '1', output: '104.201', minimum: '101.595', pool_cost: '0.1', slippage: '2.5', resource_symbol: 'GRAIN',
  rates: { unit: '104.201', inverse: '0.010' } };
const quoted = swapHtml('GRAIN', wallet, 'player0005', true);
st.moreOpen = true;
const quotedOpen = swapHtml('GRAIN', wallet, 'player0005', true);
if (!quoted.includes('data-lp-receive="GRAIN"') || !quoted.includes('value="104.201"') ||  // : "You receive" is an input
    !quotedOpen.includes('101.595 Grain') || !quotedOpen.includes('0.100 DEC')) throw new Error('buy figures: ' + quotedOpen);
if (!quotedOpen.includes('11.000') || !quotedOpen.includes('Region 41 Grain after') || !quotedOpen.includes('9,104.201')) throw new Error('buy after rows: ' + quotedOpen);
// No resource anywhere: the card still offers the buy side (nothing to sell, so no flip).
const wood = swapHtml('WOOD', wallet, 'player0005', true);
if (!wood.includes('DEC</span>→<span>') || wood.includes('lp:flip')) throw new Error('wood buy-only: ' + wood);
// The preview body per direction is the maintainer's proven shape (sell: resource_amount, buy: dec_amount).
const b = previewBody('GRAIN', 'buy', 'PR-BRL-41', '1');
if (JSON.stringify(b) !== JSON.stringify({ kind: 'swap_buy', resource_symbol: 'GRAIN', region_uid: 'PR-BRL-41', dec_amount: '1' })) throw new Error(JSON.stringify(b));
const s2 = previewBody('GRAIN', 'sell', 'PR-CEF-65', '1');
if (JSON.stringify(s2) !== JSON.stringify({ kind: 'swap_sell', resource_symbol: 'GRAIN', region_uid: 'PR-CEF-65', resource_amount: '1' })) throw new Error(JSON.stringify(s2));
"""
    run_in_module_context(body_js, entry=STATIC / "wallet-lp.js")
