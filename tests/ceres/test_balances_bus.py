"""mocked confirmations and visible amount propagation; no wallet calls."""
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STATIC = ROOT / 'ceres/static'


def test_fixture_confirmations_update_amount_views(tmp_path):
    log = tmp_path / 'values.jsonl'
    subprocess.run(['node', str(ROOT / 'tests/ceres/fixtures/ir9317_flow.mjs'), str(log)], check=True, cwd=ROOT)
    rows = [json.loads(line) for line in log.read_text().splitlines()]
    assert len(rows) == 15
    assert {row['transaction'] for row in rows} == {
        'claim', 'regional DEC stake', 'regional DEC unstake', 'swap', 'transfer', 'LP add', 'LP withdraw',
        'harvest', 'harvest-all', 'harvest routing', 'market purchase', 'Buy & max', 'combine',
        'land stake', 'land unstake',
    }
    assert all(row['before'] != row['after'] for row in rows)
    assert all(max(row['reads']) == 1 for row in rows)  # duplicate confirmations never add reads


def test_each_real_producer_emits_only_in_confirmed_branch():
    sites = {
        'wallet-claim.js': ["result.status === 'confirmed') balancesChanged"],
        'regional-dec.js': ["result.status === 'confirmed') balancesChanged"],
        'wallet-lp.js': ["result.status === 'confirmed' && (intent.twoStep || deposit)", "result.status === 'confirmed' && isPoolKind", "result.status === 'settled'"],
        'harvest-modal.js': ['result.members.some(harvestMemberConfirmed)', 'members.some(harvestMemberConfirmed)'],
        'harvest-routing.js': ["step.state === 'confirmed'"],
        'card-checkout.js': ["result?.status === 'confirmed'", "tx.state === 'confirmed'"],
        'operations.js': ["result.state === 'domain-confirmed'"],
        'worksite.js': ["result.state === 'domain-confirmed'"],
    }
    for filename, guards in sites.items():
        source = (STATIC / filename).read_text()
        assert 'balancesChanged({' in source, filename
        assert all(guard in source for guard in guards), filename
    assert 'onBalancesChanged(' in (STATIC / 'regional-dec.js').read_text()
    for filename in ('board.js', 'card-checkout.js', 'harvest-modal.js', 'harvest-routing.js'):
        assert 'onBalancesChanged(' in (STATIC / filename).read_text(), filename


def test_each_consumer_filters_player_busy_run_and_debounces():
    subprocess.run(['node', str(ROOT / 'tests/ceres/fixtures/ir9317_consumers.mjs')], check=True, cwd=ROOT)


def test_shared_approval_reminder_has_sentence_space_in_all_callers():
    wallet = (STATIC / 'wallet.js').read_text()
    assert '${esc(text)} </span><small>${esc(WALLET_EXPIRY_TEXT)}' in wallet
    assert 'walletWaitHtml' in (STATIC / 'card-checkout.js').read_text()
    harvest = (STATIC / 'harvest-modal.js').read_text()
    assert 'walletSigningStatus' in harvest
    assert 'If the time runs out' not in harvest
