""" (browser side): the optional login and saved space -- the wallet signs one text, the session lives in page memory only, the merge never loses a route,
and a route the player removed stays removed on every device."""
from pathlib import Path

from tests.ceres.vm_context import run_in_module_context

STATIC = Path(__file__).parents[2] / "ceres" / "static"

STUBS = r"""
const sig = 'ab'.repeat(65);
const calls = [];
const server = { doc: null, etag: null, revision: 0 };
globalThis.fetch = async (url, init = {}) => {
  const path = String(url), method = init.method || 'GET', headers = init.headers || {};
  calls.push({ path, method, headers, body: init.body ? JSON.parse(init.body) : null });
  const reply = (status, data, etag) => ({ status, ok: status < 400, json: async () => data, headers: { get: name => (name === 'ETag' ? etag || null : null) } });
  if (path === '/api/auth/challenge') return reply(200, { challenge_id: 'cid', message: 'CERES OFF-CHAIN AUTH v1\nx' });
  if (path === '/api/auth/verify') return reply(200, { account: 'alice', token: 't'.repeat(43) });
  if (path === '/api/auth/logout') return reply(200, { ok: true });
  if (path === '/api/me/routing' && method === 'GET') {
    if (headers.Authorization !== 'Bearer ' + 't'.repeat(43)) return reply(401, {});
    return server.doc ? reply(200, server.doc, server.etag) : reply(200, { exists: false });
  }
  if (path === '/api/me/routing' && method === 'PUT') {
    if (headers.Authorization !== 'Bearer ' + 't'.repeat(43)) return reply(401, {});
    const expected = headers['If-Match'] || null;
    if (server.doc ? expected !== server.etag : expected) return reply(412, { current: server.doc });
    server.revision += 1; server.etag = '"g:' + server.revision + '"';
    server.doc = { revision: server.revision, payload: JSON.parse(init.body).payload };
    return reply(200, server.doc, server.etag);
  }
  return reply(404, {});
};
window.hive_keychain = { requestSignBuffer: (account, message, role, cb) => cb({ success: true, result: sig }), requestBroadcast() {} };
"""


def test_merge_keeps_every_route_of_both_sides_honours_removals_and_keeps_unknown_fields():
    run_in_module_context(r"""
const merged = mergeRouting(
  { rules: [{ id: 'a' }, { id: 'c', x: 1 }, 'odd'], deleted: [{ id: 'old' }], protected_wallet_dec: '5', mine: 1 },
  { rules: [{ id: 'a' }, { id: 'b' }, { id: 'old' }, { id: 'gone' }], deleted: [{ id: 'gone' }], protected_wallet_dec: '9', theirs: 2 });
const ids = merged.rules.map(r => r.id).filter(Boolean).sort().join(',');
if (ids !== 'a,b,c') throw Error('routes: ' + ids);
if (!merged.rules.includes('odd')) throw Error('an entry without an id was dropped');
if (merged.protected_wallet_dec !== '9' || merged.mine !== 1 || merged.theirs !== 2) throw Error('fields: ' + JSON.stringify(merged));
if (merged.deleted.map(t => t.id).sort().join(',') !== 'gone,old') throw Error('tombstones');
if (mergeRouting(null, null).rules.length !== 0) throw Error('empty merge');
""", entry=STATIC / "account-space.js")


def test_sign_in_signs_one_text_keeps_the_session_in_memory_only_and_a_401_ends_it():
    run_in_module_context("(async () => {\n" + STUBS + r"""
if (hasSession('alice')) throw Error('a session before sign-in');
const result = await player0014('alice');
if (!result.ok || !hasSession('alice')) throw Error('sign-in failed: ' + JSON.stringify(result));
const verify = calls.find(c => c.path === '/api/auth/verify');
if (verify.body.signatures.length !== 1 || !/^[0-9a-f]{64}$/.test(JSON.stringify(calls.find(c => c.path === '/api/auth/challenge').body.binding).slice(1, -1))) throw Error('wrong proof request');
if (localStorage.length) throw Error('something was stored in the browser');
const doc = await loadDocument('alice', 'routing');
if (doc.exists !== false) throw Error('expected an empty space');
await signOut('alice');
if (hasSession('alice')) throw Error('still signed in');
window.hive_keychain.requestSignBuffer = (a, m, r, cb) => cb({ success: false });
const declined = await player0014('alice');
if (declined.ok || declined.reason !== 'declined' || hasSession('alice')) throw Error('a declined wallet must not sign in');
})().catch(error => { console.error(error && error.stack || error); process.exit(1); });""", entry=STATIC / "account-space.js")


def test_a_stale_save_never_overwrites_and_sync_retries_with_the_current_version():
    run_in_module_context("(async () => {\n" + STUBS + r"""
await player0014('alice');
server.doc = { revision: 1, payload: { rules: [{ id: 'remote' }] } }; server.etag = '"g:1"'; server.revision = 1;
let local = { rules: [{ id: 'local' }] };
const result = await syncRouting('alice', { read: () => local, write: root => { local = root; } });
if (!result.ok) throw Error('sync failed ' + JSON.stringify(result));
const ids = server.doc.payload.rules.map(r => r.id).sort().join(',');
if (ids !== 'local,remote' || local.rules.length !== 2) throw Error('not merged both ways: ' + ids);
const stale = await saveDocument('alice', 'routing', { rules: [] }, '"g:0"');
if (!stale.conflict) throw Error('a stale save was accepted');
if (server.doc.payload.rules.length !== 2) throw Error('the saved copy was overwritten');
})().catch(error => { console.error(error && error.stack || error); process.exit(1); });""", entry=STATIC / "account-space.js")


def test_routing_setup_adopts_the_merged_routes_after_the_account_menu_signs_in_and_a_removed_route_stays_removed():
    run_in_module_context("(async () => {\n" + STUBS + r"""
navigator.locks = { request: async (_n, fn) => fn() };
const KEY = 'ceres:harvest-routing:v1:alice';
const rule = id => ({ id, kind: 'liquidity', dec_source: 'wallet', source_region: 'PR-LOP-60', symbol: id === 'b' ? 'GRAIN' : 'WOOD', quantity_mode: 'percent', quantity: '100' });
localStorage.setItem(KEY, JSON.stringify({ rules: [rule('a')] }));
server.doc = { revision: 1, payload: { rules: [rule('b')], deleted: [] } }; server.etag = '"g:1"'; server.revision = 1;
const listeners = {};
window.addEventListener = (type, fn) => { (listeners[type] ||= []).push(fn); };
window.removeEventListener = (type, fn) => { listeners[type] = (listeners[type] || []).filter(item => item !== fn); };
window.dispatchEvent = (event) => { for (const fn of listeners[event.type] || []) fn(event); return true; };
const setup = createRoutingSetup({ player: 'alice', scope: { kind: 'all' }, regions: [{ region_uid: 'PR-LOP-60' }], receipts: [], enabledKinds: ['lp_wallet'],
  signingEnabled: true, onRender: () => {}, isCurrent: () => true });
if (setup.html().includes('data-r3="space-player0014"')) throw Error('the sign-in control belongs to the account menu now');
const result = await connect('alice');                     // what the account menu does
if (!result.ok) throw Error('connect failed ' + JSON.stringify(result));
await new Promise(resolve => setTimeout(resolve, 30));
const shown = setup.html();
if (!shown.includes('<strong>2 routes</strong>') || !shown.includes('Saved across devices')) throw Error('not adopted: ' + shown.slice(0, 200));
if (JSON.parse(localStorage.getItem(KEY)).rules.length !== 2) throw Error('the saved route did not reach this device');
setup.action('remove', { dataset: { r3Index: '0' } });
await new Promise(resolve => setTimeout(resolve, 1200));
const saved = server.doc.payload;
if (saved.rules.length !== 1 || !saved.deleted.length) throw Error('the removal did not reach the saved copy: ' + JSON.stringify(saved));
if (!JSON.parse(localStorage.getItem(KEY)).deleted.length) throw Error('no tombstone on this device');
})().catch(error => { console.error(error && error.stack || error); process.exit(1); });""", entry=STATIC / "routing-setup.js")


def test_connect_never_overwrites_an_unreadable_local_setup_and_the_footer_always_has_the_wallet_chooser():
    run_in_module_context("(async () => {\n" + STUBS + r"""
navigator.locks = { request: async (_n, fn) => fn() };
localStorage.setItem('ceres:harvest-routing:v1:alice', '{not json');
server.doc = { revision: 1, payload: { rules: [{ id: 'remote' }] } }; server.etag = '"g:1"'; server.revision = 1;
const result = await connect('alice');
if (!result.ok) throw Error('connect failed');
if (localStorage.getItem('ceres:harvest-routing:v1:alice') !== '{not json') throw Error('an unreadable local record was overwritten');
for (const receipts of [[], ['attempt-1']]) {
  const setup = createRoutingSetup({ player: 'alice', scope: { kind: 'all' }, regions: [{ region_uid: 'PR-LOP-60' }], receipts, enabledKinds: ['lp_wallet'],
    signingEnabled: true, onRender: () => {}, isCurrent: () => true });
  const html = setup.html();
  const actions = html.slice(html.indexOf('<template class="r3-actions">'), html.indexOf('</template>'));
  if (!actions.includes('wallet-sign-pair') || !actions.includes('wallet-controls')) throw Error('no wallet chooser beside Sign');
  if (receipts.length && actions.indexOf('wallet-controls') > actions.indexOf('data-r3="start"')) throw Error('the chooser must come before Sign');
}
})().catch(error => { console.error(error && error.stack || error); process.exit(1); });""", entry=STATIC / "routing-setup.js")


def test_the_account_menu_has_one_sign_in_action_and_keeps_delete_controls_folded():
    run_in_module_context("(async () => {\n" + STUBS + r"""
const html = accountMenuHtml('alice');
if (!html.includes('data-account="player0014"') || html.includes('wallet-controls') || html.includes('data-account="signout"')) throw Error('the account menu must have one direct sign-in action');
if (!html.includes('ceres-btn--primary') || !html.includes('>Sign in</button>')) throw Error('the sign-in action is missing');
if (!html.includes('no transaction, no cost.')) throw Error('the reassurance line is missing');
await player0014('alice');
const after = accountMenuHtml('alice');
if (!after.includes('Signed in as alice') || !after.includes('data-account="signout"') || after.includes('data-account="player0014"')) throw Error('signed-in state: ' + after.slice(0, 300));
if (!after.includes('ceres-btn--secondary') || after.includes('wallet-controls') || !after.includes('Saved data options')) throw Error('signed-in controls');
if (!accountMenuHtml('').includes('Enter a player name first')) throw Error('empty account');
})().catch(error => { console.error(error && error.stack || error); process.exit(1); });""", entry=STATIC / "account-menu.js")


def test_the_header_button_is_primary_wears_the_wallet_logo_and_the_menu_uses_the_wallet_window_glass():
    index = (STATIC / "index.html").read_text(encoding="utf-8")
    start = index.index('id="account-menu-btn"')
    button = index[index.rfind("<button", 0, start):index.index("</button>", start)]
    assert "ceres-btn--primary" in button and "ceres-btn--secondary" not in button
    assert '<img class="wallet-logo"' in button and "<svg" not in button          # the selected wallet's logo, never a chevron
    css = (STATIC / "ceres.css").read_text(encoding="utf-8")
    block = css[css.index(".account-menu {"):css.index(".account-menu[hidden]")]
    assert "color-mix(in srgb, var(--shadow) 82%, transparent)" in block and "backdrop-filter: saturate(130%) blur(4px)" in block and "0 0 18px -3px var(--primary)" in block
    js = (STATIC / "account-menu.js").read_text(encoding="utf-8")
    assert "walletLogoHtml(selectedWallet(" in js


def test_the_routing_setup_always_says_where_the_routes_are_saved_and_when_they_are_not():
    run_in_module_context("(async () => {\n" + STUBS + r"""
navigator.locks = { request: async (_n, fn) => fn() };
const KEY = 'ceres:harvest-routing:v1:alice';
const make = () => createRoutingSetup({ player: 'alice', scope: { kind: 'all' }, regions: [{ region_uid: 'PR-LOP-60' }], receipts: [], enabledKinds: ['lp_wallet'],
  signingEnabled: true, onRender: () => {}, isCurrent: () => true });
const status = html => (/<span class="r3-save-state ([^"]*)"[^>]*>([^<]*)<\/span>/.exec(html) || []).slice(1);
// 1. the default: saved on this device
let setup = make(); let [tone, text] = status(setup.html());
if (text !== 'Saved on this device' || !tone.includes('is-warn')) throw Error('default status (orange): ' + text + ' / ' + tone);
// 2. a browser that refuses storage is said so at once
const realSet = localStorage.setItem.bind(localStorage), realRemove = localStorage.removeItem.bind(localStorage);
localStorage.setItem = () => { throw new Error('QuotaExceededError'); };
setup = make(); [tone, text] = status(setup.html());
if (!text.startsWith('Not saved on this device: this browser blocks storage.') || !tone.includes('is-bad') || !text.includes('lost when you close the page')) throw Error('blocked status: ' + text);
localStorage.setItem = realSet;
// 3. a save that fails after a change is said too
setup = make();
localStorage.setItem = (k, v) => { if (k === KEY) throw new Error('QuotaExceededError'); return realSet(k, v); };
setup.setting('protected_wallet_dec', '3');
[tone, text] = status(setup.html());
if (!tone.includes('is-bad')) throw Error('a failed save was not shown: ' + text);
localStorage.setItem = realSet;
// 4. an unreadable saved record is never overwritten and is said
localStorage.setItem(KEY, '{not json');
setup = make(); setup.setting('protected_wallet_dec', '4'); [tone, text] = status(setup.html());
if (!text.includes('could not be read') || localStorage.getItem(KEY) !== '{not json') throw Error('unreadable status: ' + text);
localStorage.removeItem(KEY);
// 5. signed in: saved across devices; the server unreachable: said
setup = make();
const result = await connect('alice');
if (!result.ok) throw Error('connect failed');
[tone, text] = status(setup.html());
if (text !== 'Saved across devices' || !tone.includes('is-ok')) throw Error('signed-in status: ' + text);
})().catch(error => { console.error(error && error.stack || error); process.exit(1); });""", entry=STATIC / "routing-setup.js")


def test_the_route_remove_button_wears_the_red_destructive_theme():
    run_in_module_context(r"""
navigator.locks = { request: async (_n, fn) => fn() };
localStorage.setItem('ceres:harvest-routing:v1:alice', JSON.stringify({ rules: [{ id: 'a', kind: 'liquidity', dec_source: 'wallet', source_region: 'PR-LOP-60', symbol: 'WOOD', quantity_mode: 'percent', quantity: '100' }] }));
const setup = createRoutingSetup({ player: 'alice', scope: { kind: 'all' }, regions: [{ region_uid: 'PR-LOP-60', active: true }], receipts: [], enabledKinds: ['lp_wallet'], signingEnabled: true, onRender: () => {}, isCurrent: () => true });
const html = setup.html();
if (!/data-control-case="destructive" data-r3="remove"/.test(html)) throw Error('the Remove button is not in the destructive theme');
""", entry=STATIC / "routing-setup.js")


def test_adding_a_route_and_removing_it_again_lands_back_on_saved_across_devices_even_after_the_session_ended():
    run_in_module_context("(async () => {\n" + STUBS + r"""
navigator.locks = { request: async (_n, fn) => fn() };
const KEY = 'ceres:harvest-routing:v1:alice';
const status = html => (/<span class="r3-save-state ([^"]*)"[^>]*>([^<]*)<\/span>/.exec(html) || []).slice(1);
const rule = id => ({ id, kind: 'liquidity', dec_source: 'wallet', source_region: 'PR-LOP-60', symbol: 'WOOD', quantity_mode: 'percent', quantity: '100' });
localStorage.setItem(KEY, JSON.stringify({ rules: [rule('a')] }));
const setup = createRoutingSetup({ player: 'alice', scope: { kind: 'all' }, regions: [{ region_uid: 'PR-LOP-60', active: true }], receipts: [], enabledKinds: ['lp_wallet'], signingEnabled: true, onRender: () => {}, isCurrent: () => true });
if (!status(setup.html())[0].includes('is-warn')) throw Error('before any sync it is orange');
await connect('alice');
await signOut('alice');                                                     // the session ended (restart, idle limit): nothing can sync now
let [tone, text] = status(setup.html());
if (text !== 'Saved across devices' || !tone.includes('is-ok')) throw Error('this device equals the saved copy: ' + text);
setup.action('add', {});
[tone, text] = status(setup.html());
if (!tone.includes('is-warn') || text !== 'Saved on this device') throw Error('a change that is not in the saved copy is orange: ' + text);
setup.action('remove', { dataset: { r3Index: '1' } });
[tone, text] = status(setup.html());
if (text !== 'Saved across devices' || !tone.includes('is-ok')) throw Error('removing the added route again is back to saved across devices: ' + text);
setup.action('remove', { dataset: { r3Index: '0' } });
[tone, text] = status(setup.html());
if (text !== 'Saved on this device') throw Error('removing a SAVED route is a real difference: ' + text);
})().catch(error => { console.error(error && error.stack || error); process.exit(1); });""", entry=STATIC / "routing-setup.js")


def test_the_signed_in_account_button_has_the_global_warn_border():
    css = (STATIC / "ceres.css").read_text(encoding="utf-8")
    assert ".account-menu-btn.is-signed-in { border-color: var(--warn); }" in css
    assert "inset 0 0 0 2px var(--ok)" not in css


def test_no_destructive_button_has_a_red_dot_but_all_keep_the_destructive_border_and_tint():
    buttons = (STATIC / "buttons.css").read_text(encoding="utf-8")
    assert 'data-control-case~="destructive"]:not([data-control-mode="icon"])::before' not in buttons       # the dot is gone everywhere
    assert "border-color: var(--danger);" in buttons and "color-mix(in srgb, var(--danger) 10%, var(--bg3))" in buttons   # the recipe stays: red border, red tint on hover
    assert '.ceres-btn[data-r3="remove"]::before' not in (STATIC / "ceres.css").read_text(encoding="utf-8")     # no per-button workaround is needed any more


# the one-time routing run lives in the page
RUN_STUBS = r"""
const calls = [];
globalThis.fetch = async (url, init = {}) => {
  const path = String(url); calls.push({ path, method: init.method || 'GET', body: init.body ? JSON.parse(init.body) : null });
  const reply = (status, data) => ({ status, ok: status < 400, json: async () => data, headers: { get: () => null } });
  if (path.includes('/safety/check')) return reply(200, { state: 'released', records: [] });
  if (path.includes('/safety')) return reply(200, { records: [{ id: 'run1:b1', tx_id: null, started: 1790000000, expires_at: 1790003900, expired: false, attempt_ids: ['x'] }] });
  if (path.includes('/discard')) return reply(200, { ok: true });
  return reply(404, {});
};
"""


def test_a_run_of_an_earlier_page_is_discarded_never_resumed_and_a_new_run_is_never_blocked():
    run_in_module_context("(async () => {\n" + RUN_STUBS + r"""
localStorage.setItem('ceres:routing-v2-run:alice', 'a'.repeat(32));
const run = createRoutingRun({ player: 'alice', onRender: () => {}, isCurrent: () => true, api: async (player, path, body, method) => {
  calls.push({ path, body, method }); if (path === 'run') return { run_id: 'b'.repeat(32), preview_id: 'p2', state: 'ready', bundles: { b1: { state: 'planned' } }, manifest: { bundles: [] } }; return { ok: true }; } });
const loaded = await run.load();
if (loaded !== null) throw Error('an earlier page run must not be resumed');
if (!calls.some(c => c.path === 'discard' && c.body.run_id === 'a'.repeat(32))) throw Error('the earlier run was not discarded on the server');
if (localStorage.getItem('ceres:routing-v2-run:alice') !== null) throw Error('the pointer to the earlier run must be forgotten');
const created = await run.create({ preview_id: 'p2', preview_digest: 'd' });
if (!created || created.preview_id !== 'p2') throw Error('a new run must be creatable');
// a finished run clears the pointer (the server deleted it)
const finished = { run_id: 'c'.repeat(32), preview_id: 'p3', state: 'complete', bundles: { b1: { state: 'confirmed' } }, manifest: { bundles: [] } };
const run2 = createRoutingRun({ player: 'alice', onRender: () => {}, isCurrent: () => true, api: async () => finished });
await run2.create({ preview_id: 'p3', preview_digest: 'd' });
if (localStorage.getItem('ceres:routing-v2-run:alice') !== null) throw Error('a finished run must leave no pointer');
})().catch(error => { console.error(error && error.stack || error); process.exit(1); });""", entry=STATIC / "routing-run.js")


def test_the_routing_screen_never_shows_or_asks_about_an_old_routing_even_if_the_server_still_lists_one():
    run_in_module_context("(async () => {\n" + RUN_STUBS + r"""
navigator.locks = { request: async (_n, fn) => fn() };
const setup = createRoutingSetup({ player: 'alice', scope: { kind: 'all' }, regions: [{ region_uid: 'PR-LOP-60' }], receipts: [], enabledKinds: ['lp_wallet'], signingEnabled: true, onRender: () => {}, isCurrent: () => true });
await new Promise(resolve => setTimeout(resolve, 30));
const html = setup.html();
if (html.includes('is not confirmed yet') || html.includes('safety-check') || html.includes('safety-notsent') || html.includes('r3-safety')) throw Error('an old routing must never be shown: ' + html.slice(0, 200));
await new Promise(resolve => setTimeout(resolve, 30));
if (calls.some(c => c.path.includes('/safety'))) throw Error('the page must not ask the server about an old routing');
})().catch(error => { console.error(error && error.stack || error); process.exit(1); });""", entry=STATIC / "routing-setup.js")


def test_staged_merge_unions_both_devices_and_a_removal_stays_removed_unless_staged_again():
    run_in_module_context(r"""
const t = (n) => new Date(Date.UTC(2026, 9, 1, 0, n)).toISOString();
const merged = mergeStaged(
  { plans: { 'a|w|0': { created_at: t(1) }, 'c|w|0': { created_at: t(9) } }, worksites: { d1: { created_at: t(2) } }, removed: { 'b|w|0': t(5) }, mine: 1 },
  { plans: { 'b|w|0': { created_at: t(1) }, 'a|w|0': { created_at: t(0), old: true }, 'c|w|0': { created_at: t(3) } }, worksites: { d2: { created_at: t(2) } }, removed: { 'w:d2': t(4) }, theirs: 2 });
const ids = Object.keys(merged.plans).sort().join(',');
if (ids !== 'a|w|0,c|w|0') throw Error('plans: ' + ids);   // b was removed after it was staged
if (merged.plans['a|w|0'].old) throw Error('the older copy won');
if (Object.keys(merged.worksites).join(',') !== 'd1') throw Error('worksites: ' + Object.keys(merged.worksites));   // d2 removed after staging
if (merged.mine !== 1 || merged.theirs !== 2) throw Error('fields were dropped');
const again = mergeStaged({ plans: { 'b|w|0': { created_at: t(8) } }, removed: { 'b|w|0': t(5) } }, null);
if (!again.plans['b|w|0']) throw Error('a change staged again after its removal was dropped');
if (Object.keys(mergeStaged(null, null).plans).length) throw Error('empty merge');
""", entry=STATIC / "account-space.js")


def test_staged_sync_saves_both_ways_and_only_writes_the_device_when_it_gained_something():
    run_in_module_context("(async () => {\n" + STUBS.replace("/api/me/routing", "/api/me/staged") + r"""
await player0014('alice');
let local = { plans: { 'a|w|0': { created_at: '2026-10-01T00:00:00Z' } }, worksites: {}, removed: {} };
let writes = 0;
const first = await syncStaged('alice', { read: () => local, write: m => { writes += 1; local = m; } });
if (!first.ok || first.changed || writes) throw Error('first sync: ' + JSON.stringify(first));
if (!server.doc.payload.plans['a|w|0']) throw Error('not saved');
server.doc = { revision: 5, payload: { plans: { 'z|w|1': { created_at: '2026-10-01T00:00:00Z' } }, worksites: {}, removed: {} } }; server.etag = '"g:5"';
const second = await syncStaged('alice', { read: () => local, write: m => { writes += 1; local = m; } });
if (!second.ok || !second.changed || writes !== 1 || !local.plans['z|w|1'] || !local.plans['a|w|0']) throw Error('second sync: ' + JSON.stringify(local));
if (!server.doc.payload.plans['a|w|0'] || !server.doc.payload.plans['z|w|1']) throw Error('server lost a plan');
})().catch(error => { console.error(error && error.stack || error); process.exit(1); });
""", entry=STATIC / "account-space.js")


def test_delete_saved_signs_the_servers_own_wording_and_ends_this_pages_session():
    run_in_module_context("(async () => {\n" + STUBS + r"""
await player0014('alice');
let signed = null;
const base = globalThis.fetch;
globalThis.fetch = async (url, init = {}) => {
  const path = String(url);
  if (path === '/api/me/delete') {
    calls.push({ path, method: 'POST', body: JSON.parse(init.body) });
    return { status: 200, ok: true, json: async () => ({ account: 'alice', scope: 'space', documents: 3, kept_safety_records: 1 }), headers: { get: () => null } };
  }
  return base(url, init);
};
const result = await deleteSaved('alice', 'space', { sign: async (account, message) => { signed = message; return sig; } });
if (!result.ok || result.documents !== 3 || result.kept_safety_records !== 1) throw Error('result: ' + JSON.stringify(result));
if (signed !== 'CERES OFF-CHAIN AUTH v1\nx') throw Error('the wallet did not sign the server text');
const challenge = calls.find(c => c.path === '/api/auth/challenge' && c.body.purpose === 'delete-all');
if (!challenge || challenge.body.scope !== 'space') throw Error('the challenge did not ask for a deletion of the space');
if (hasSession('alice')) throw Error('the page kept a session the server ended');
const declined = await deleteSaved('alice', 'cache', { sign: async () => { throw new Error('declined'); } });
if (declined.ok || declined.reason !== 'declined') throw Error('a declined signature must stop everything');
if (calls.filter(c => c.path === '/api/me/delete').length !== 1) throw Error('a declined signature still sent a deletion');
})().catch(error => { console.error(error && error.stack || error); process.exit(1); });""", entry=STATIC / "account-space.js")
