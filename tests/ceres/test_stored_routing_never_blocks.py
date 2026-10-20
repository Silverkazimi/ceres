""" (incident,, then "we only
dropped broken routes from 1 single harvest ... I never wanted to delete root routing setup"): a user's saved routes are their ROOT setup and are never
deleted. Saved routes that fit this harvest come back; routes that do not fit it, or are damaged, are only left out of the screen (and stay stored);
damaged, hostile or oversized stored values are ignored without an error and can never block the screen."""
import json
from pathlib import Path

from tests.ceres.vm_context import run_in_module_context

STATIC = Path(__file__).parents[2] / "ceres" / "static"
KEY = "ceres:harvest-routing:v1:alice"

CALL = r"""
navigator.locks = { request: async (_name, fn) => fn() };
const setup = createRoutingSetup({ player: 'alice', scope: { kind: 'all' }, regions: [{ region_uid: 'PR-LOP-60' }], receipts: [],
 enabledKinds: ['sale_dec', 'lp_wallet'], signingEnabled: true, onRender: () => {}, isCurrent: () => true });
const html = setup.html();
"""


def _run(body):
    return run_in_module_context(body, entry=STATIC / "routing-setup.js")


def test_saved_routes_that_fit_this_harvest_come_back_and_the_root_setup_is_never_deleted():
    _run(f"""
localStorage.setItem('{KEY}', JSON.stringify({{ protected_wallet_dec: '12.5', slippage: '9', reserves: [{{ region_uid: 'x', symbol: 'DEC' }}], rules: [
 {{ id: 'r1', kind: 'liquidity', dec_source: 'wallet', source_region: 'PR-LOP-60', symbol: 'WOOD', quantity_mode: 'percent', quantity: '100' }},
 {{ id: 'r2', kind: 'liquidity', dec_source: 'wallet', source_region: 'PR-OTHER-1', symbol: 'GRAIN', quantity_mode: 'percent', quantity: '100' }},
 'broken', null, {{ id: 'r3', kind: 'liquidity', dec_source: 'wallet', source_region: 5, symbol: 7 }}] }}));
{CALL}
if (!html.includes('<strong>1 route</strong>')) throw Error('the saved route that fits this harvest did not come back: ' + html.slice(0, 300));
setup.setting('protected_wallet_dec', '3'); // any save must keep the routes that do not fit this harvest
const left = JSON.parse(localStorage.getItem('{KEY}'));
if (!left.rules.some(rule => rule.id === 'r2')) throw Error('a saved route for another region was deleted: ' + JSON.stringify(left));
if (!left.rules.some(rule => rule.id === 'r1')) throw Error('the route shown here was deleted');
if (left.reserves === undefined || left.slippage === undefined) throw Error('older saved values were deleted');
""")


def test_damaged_hostile_or_oversized_storage_never_breaks_the_screen():
    for value in ("'null'", "'[]'", "'42'", "'\"text\"'", "'{not json'", "'{\"rules\":\"' + 'x'.repeat(200000) + '\"}'"):
        _run(f"localStorage.setItem('{KEY}', {value});\n{CALL}\nif (!html.includes('r3-setup')) throw Error('screen did not render');")


def test_storage_that_refuses_reads_or_writes_never_breaks_the_screen():
    _run(f"""
const realGet = localStorage.getItem.bind(localStorage), realSet = localStorage.setItem.bind(localStorage);
localStorage.setItem('{KEY}', JSON.stringify({{ rules: [] }}));
localStorage.setItem = () => {{ throw Error('QuotaExceededError'); }};
{{ {CALL}
if (!html.includes('r3-setup')) throw Error('write failure broke the screen'); }}
localStorage.setItem = realSet;
localStorage.getItem = () => {{ throw Error('SecurityError'); }};
{{ {CALL}
if (!html.includes('r3-setup')) throw Error('read failure broke the screen'); }}
""")


def test_routes_of_this_visit_are_kept_only_if_this_harvest_produced_them():
    _run(f"""
const rules = [{{ id: 'a', kind: 'liquidity', dec_source: 'wallet', source_region: 'PR-LOP-60', symbol: 'WOOD', quantity_mode: 'percent', quantity: '25' }},
 {{ id: 'b', kind: 'liquidity', dec_source: 'wallet', source_region: 'PR-LOP-60', symbol: 'GRAIN', quantity_mode: 'percent', quantity: '25' }}];
const setup = createRoutingSetup({{ player: 'alice', scope: {{ kind: 'all' }}, regions: [{{ region_uid: 'PR-LOP-60' }}], receipts: [], initialRules: rules,
 produced: new Set(['PR-LOP-60|WOOD']), enabledKinds: ['lp_wallet'], signingEnabled: true, onRender: () => {{}}, isCurrent: () => true }});
if (!setup.html().includes('<strong>1 route</strong>')) throw Error('a route the harvest did not produce was kept');
""")


def test_this_visits_routes_are_handed_to_the_caller_and_saved_as_the_root_setup():
    _run(f"""
navigator.locks = {{ request: async (_n, fn) => fn() }};
let handed = null;
const rule = {{ id: 'a', kind: 'liquidity', dec_source: 'wallet', source_region: 'PR-LOP-60', symbol: 'WOOD', quantity_mode: 'percent', quantity: '25' }};
const setup = createRoutingSetup({{ player: 'alice', scope: {{ kind: 'all' }}, regions: [{{ region_uid: 'PR-LOP-60' }}], receipts: [], initialRules: [rule],
 enabledKinds: ['lp_wallet'], signingEnabled: true, onRender: () => {{}}, isCurrent: () => true, onRulesChange: rules => {{ handed = rules; }} }});
setup.setting('protected_wallet_dec', '3');
if (!handed || handed.length !== 1 || handed[0].id !== 'a') throw Error('routes were not handed to the caller');
if (JSON.parse(localStorage.getItem('{KEY}')).rules.length !== 1) throw Error('the route was not saved to the root setup');
""")


def test_pre_harvest_setup_has_one_clear_way_forward():
    """before the harvest is confirmed the setup says what comes next and offers 'Continue to harvest'; after it, the notice is gone."""
    base = lambda receipts: f"""
navigator.locks = {{ request: async (_n, fn) => fn() }};
const setup = createRoutingSetup({{ player: 'alice', scope: {{ kind: 'all' }}, regions: [{{ region_uid: 'PR-LOP-60' }}], receipts: {receipts},
 enabledKinds: ['sale_dec'], signingEnabled: true, onRender: () => {{}}, isCurrent: () => true }});
const html = setup.html();
"""
    _run(base("[]") + "if (!html.includes('r3-pre-harvest') || !html.includes('Continue to harvest') || !html.includes('data-hm=\"routing-back\"')) throw Error('no way forward');")
    _run(base("['attempt-1']") + "if (html.includes('r3-pre-harvest')) throw Error('notice shown after the harvest');")


MAKE = r"""
navigator.locks = { request: async (_n, fn) => fn() };
const make = (extra = {}) => createRoutingSetup({ player: 'alice', scope: { kind: 'all' }, regions: [{ region_uid: 'PR-LOP-60' }], receipts: [],
 enabledKinds: ['lp_wallet'], signingEnabled: true, onRender: () => {}, isCurrent: () => true, ...extra });
const rule = (id, region = 'PR-LOP-60', symbol = 'WOOD') => ({ id, kind: 'liquidity', dec_source: 'wallet', source_region: region, symbol, quantity_mode: 'percent', quantity: '100' });
const read = () => JSON.parse(localStorage.getItem('ceres:harvest-routing:v1:alice'));
"""


def test_a_whale_root_setup_survives_load_and_save_without_any_cap():
    _run(MAKE + """
const many = []; for (let i = 0; i < 1100; i++) many.push(rule('r' + i, i % 10 === 0 ? 'PR-LOP-60' : 'PR-OTHER-' + (i % 7), i % 2 ? 'WOOD' : 'GRAIN'));
many.push('broken', null, 42);
localStorage.setItem('ceres:harvest-routing:v1:alice', JSON.stringify({ protected_wallet_dec: '5', extra_field: { keep: true }, rules: many }));
const setup = make();
setup.setting('protected_wallet_dec', '7');
const after = read();
if (after.rules.length !== many.length) throw Error('routes were lost: ' + after.rules.length + ' of ' + many.length);
if (!after.extra_field || after.extra_field.keep !== true) throw Error('an unknown field was lost');
if (!after.rules.includes('broken') || !after.rules.includes(42)) throw Error('an entry this screen cannot read was dropped');
""")


def test_another_tabs_additions_and_an_unreadable_record_are_never_overwritten():
    _run(MAKE + """
localStorage.setItem('ceres:harvest-routing:v1:alice', JSON.stringify({ rules: [rule('a')] }));
const setup = make();
const current = read(); current.rules.push(rule('from-other-tab', 'PR-LOP-60', 'IRON')); localStorage.setItem('ceres:harvest-routing:v1:alice', JSON.stringify(current));
setup.setting('protected_wallet_dec', '3');
if (!read().rules.some(r => r.id === 'from-other-tab')) throw Error("another tab's route was overwritten");
localStorage.setItem('ceres:harvest-routing:v1:alice', '{not json');
setup.setting('protected_wallet_dec', '4');
if (localStorage.getItem('ceres:harvest-routing:v1:alice') !== '{not json') throw Error('an unreadable record was overwritten');
""")


def test_only_an_explicit_remove_deletes_a_route_and_nothing_is_ever_restored():
    _run(MAKE + """
let asked = 0; globalThis.fetch = async (url) => { if (String(url).includes('/safety')) return { ok: true, json: async () => ({ records: [] }) }; asked += 1; return { ok: true, json: async () => ({ rules: [rule('x')] }) }; };
localStorage.setItem('ceres:harvest-routing:v1:alice', JSON.stringify({ rules: [rule('a'), rule('b', 'PR-LOP-60', 'GRAIN'), rule('c', 'PR-ELSEWHERE-1')] }));
const setup = make();
setup.action('remove', { dataset: { r3Index: '0' } });
const left = read().rules.map(r => r.id).sort().join(',');
if (left !== 'b,c') throw Error('expected only the removed route to go, got ' + left);
if (asked !== 0) throw Error('the screen asked the server for routes'); // (the safety list is not a route source)
""")
