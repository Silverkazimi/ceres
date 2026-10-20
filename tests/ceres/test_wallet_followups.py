"""a sale left at the SPS transfer continues by itself after a reload. : the SPS claim cooldown is a timer."""
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ceres.routes_wallet import next_claim_at

STATIC = Path(__file__).parents[2] / "ceres" / "static"
SETTINGS = {"reward_pools": {"claim_limit": 1, "claim_limit_time": 82800000}}


def test_next_claim_is_last_claim_plus_the_games_limit():
    last = datetime.now(timezone.utc) - timedelta(hours=3)
    ready = next_claim_at(last.isoformat().replace("+00:00", "Z"), SETTINGS)
    assert abs(datetime.fromisoformat(ready.replace("Z", "+00:00")) - (last + timedelta(hours=23))) < timedelta(seconds=1)
    assert next_claim_at((datetime.now(timezone.utc) - timedelta(hours=24)).isoformat(), SETTINGS) is None  # allowed now
    assert next_claim_at(last.isoformat(), {}) is None and next_claim_at(None, SETTINGS) is None
    assert next_claim_at(last.isoformat(), {"reward_pools": {"claim_limit": 2, "claim_limit_time": 82800000}}) is None


def test_wallet_shows_the_timer_and_holds_the_claim_button():
    resources = (STATIC / "wallet-resources.js").read_text()
    assert "data-countdown-until=\"${esc(rewards.next_claim_at)}\"" in resources and "Next claim in" in resources
    claim = (STATIC / "wallet-claim.js").read_text()
    assert "|| waiting)}" in claim
    countdown = (STATIC / "countdown.js").read_text()
    assert ".dec-drawer [data-countdown-until]" in countdown and "ceres:wallet-countdown-ended" in countdown
    assert "ceres:wallet-countdown-ended" in (STATIC / "regional-dec.js").read_text()


def test_pending_sps_transfer_resumes_once_per_page():
    lp = (STATIC / "wallet-lp.js").read_text()
    assert "st.stage === 'funding' && st.receipt?.tx_id && !st.busy && !st.autoFunded" in lp and "st.autoFunded = true; fund(ctx, st);" in lp
    assert "is waiting for your" in lp


def test_percent_buttons_are_disabled_while_the_card_is_not_editable():
    # outside the edit stage the buttons did nothing; they now look disabled
    lp = (STATIC / "wallet-lp.js").read_text()
    assert "btn(`pct:${pct}`, `${pct}%`, 'secondary', 'compact', st.stage !== 'edit')" in lp


def test_claim_button_sits_inside_the_rewards_card_with_a_warning_edge():
    # the waiting rewards card keeps a warning top edge; : the plain Claim button is the card's last row
    css = (STATIC / "ceres.css").read_text()
    # cooldown = neutral box; claimable now = warning box (border and top edge)
    assert ".wallet-card.is-claim-ready:not(.is-confirmed):not(.is-failed) { border-color: var(--warn); border-block-start-color: var(--warn); }" in css
    assert "is-claim-waiting" not in css.split("")[1][:600]
    assert "claimable ? ' is-claim-ready' : ''" in (STATIC / "wallet-resources.js").read_text()
    dec = (STATIC / "regional-dec.js").read_text()
    assert "withClaimInRewards(walletResourceHtml(D.tab, D.wallet, D.walletBusy), D.tab === 'SPS' ? claimHtml(D) : '')" in dec
    assert "html.slice(0, -'</section>'.length) + claim + '</section>'" in dec


def test_hiveauth_request_is_visible_while_the_sps_transfer_waits():
    # the HiveAuth request (status, phone link, close) renders only inside .wallet-controls; the card shows them during the transfer
    lp = (STATIC / "wallet-lp.js").read_text()
    assert lp.count("walletWaitHtml(player, 'Active') + `<div class=\"wallet-fund-controls\">${walletControlsHtml(player, null, { compactRequest: true })}</div>`") == 2


def test_balances_are_read_again_when_a_swap_is_done():
    lp = (STATIC / "wallet-lp.js").read_text()
    assert "balancesChanged({ player: ctx.player" in lp
    assert "if (st.stage === 'done') ctx.reload()" not in lp
    assert "onBalancesChanged(({ assets })" in (STATIC / 'regional-dec.js').read_text()
