const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const root = path.resolve(__dirname, '..', '..');
const staticDir = path.join(root, 'ceres', 'static');
const source = fs.readFileSync(path.join(staticDir, 'harvest-resources.js'), 'utf8')
  .replace(/^import .*;$/gm, '')
  .replace(/^export /gm, '');
const context = {
  module: { exports: {} },
  Number,
  Object,
  Set,
  String,
  esc: value => String(value),
  fmt2: value => Number(value).toFixed(2),
  tokenIcon: () => '',
  tokenLabel: key => ({ GRAIN: 'Grain', WOOD: 'Wood', STONE: 'Stone', IRON: 'Iron' }[key] || key),
};
vm.runInNewContext(`${source}\nmodule.exports = { harvestResourceColumns, resourcePairStartsVisualRow };`, context);
const { harvestResourceColumns, resourcePairStartsVisualRow } = context.module.exports;

function check(id, fn) {
  fn();
  process.stdout.write(`PASS ${id}\n`);
}

function pair(html, key, nextKey = null) {
  const start = html.indexOf(`data-resource="${key}"`);
  assert.notEqual(start, -1, `${key} pair missing`);
  const end = nextKey ? html.indexOf(`data-resource="${nextKey}"`, start) : html.length;
  return html.slice(start, end);
}

check('IR8030-shared-keys-do-not-materialize-impossible-grain', () => {
  const html = harvestResourceColumns({ IRON: 5, GRAIN: 0 }, {}, { sharedKeys: ['GRAIN', 'IRON'] });
  assert.match(html, /data-resource="IRON"/);
  assert.doesNotMatch(html, /data-resource="GRAIN"/);
  assert.doesNotMatch(html, /Grain/);
});

check('S15990-cost-only-resource-has-debit-without-received-zero', () => {
  const html = harvestResourceColumns({ IRON: 5, GRAIN: 0 }, { GRAIN: 3, WOOD: 0 }, { sharedKeys: ['GRAIN', 'WOOD', 'IRON'] });
  const grain = pair(html, 'GRAIN', 'IRON');
  assert.match(grain, /hm-resource-debit/);
  assert.doesNotMatch(grain, /hm-resource-credit/);
  assert.doesNotMatch(html, /data-resource="WOOD"/);
});

check('IR8030-pending-known-zero-keeps-skeleton-semantics', () => {
  const html = harvestResourceColumns({ GRAIN: 0 }, { GRAIN: 0 }, { pending: true });
  assert.match(html, /data-resource="GRAIN"/);
  assert.match(html, /hm-resource-credit/);
  assert.match(html, /hm-resource-debit/);
  assert.equal((html.match(/hm-skel is-num/g) || []).length, 2);
});

check('S15960-wrapped-row-start-is-geometry-based', () => {
  assert.equal(resourcePairStartsVisualRow(null, 20), true);
  assert.equal(resourcePairStartsVisualRow(20, 20), false);
  assert.equal(resourcePairStartsVisualRow(20, 20.9), false);
  assert.equal(resourcePairStartsVisualRow(20, 21.1), true);
});

check('S15960-shared-helper-is-mounted-and-reflow-synced-at-every-render-site', () => {
  const css = fs.readFileSync(path.join(staticDir, 'ceres.css'), 'utf8');
  const modal = fs.readFileSync(path.join(staticDir, 'harvest-modal.js'), 'utf8');
  const dec = fs.readFileSync(path.join(staticDir, 'regional-dec.js'), 'utf8');
  const receipt = fs.readFileSync(path.join(staticDir, 'harvest-results.js'), 'utf8');
  assert.match(css, /hm-resource-pair\.is-row-start \{ border-inline-start:0; \}/);
  for (const renderedSurface of [modal, dec]) {
    assert.match(renderedSurface, /syncHarvestResourceDividers\(panel\);/);
    assert.match(renderedSurface, /observeHarvestResourceDividers\(panel\);/);
  }
  assert.match(receipt, /harvestResourceColumns\(member\.received_by_token, member\.costs_by_token, \{ receipt: true \}\)/);
});

function receiptDomContext() {
  const context = {
    module: { exports: {} },
    console,
    Date,
    JSON,
    Map,
    Number,
    Object,
    Set,
    String,
    Array,
    Math,
    localStorage: (() => {
      const values = new Map();
      return { getItem: key => values.get(key) || null, setItem: (key, value) => values.set(key, String(value)) };
    })(),
    document: { readyState: 'loading', addEventListener() {} },
    window: { addEventListener() {}, removeEventListener() {} },
    signedQueueReceipts: () => [],
    plotBadgeHtml: () => '',
    taxWorksiteArtPath: () => '',
    terrainEmblemHtml: () => '',
    territorySlug: value => value || '',
    finishedWorksiteOverlayPath: () => '',
    worksiteTileArtPath: () => '',
    esc: value => String(value ?? ''),
    formatPlotLabel: value => String(value ?? ''),
    humanizeInstant: () => null,
    HIVE_TX_EXPLORER: value => `https://hivehub.dev/tx/${value}`,
    fmt2: value => Number(value).toFixed(2),
    tokenIcon: () => '',
    tokenLabel: value => String(value),
    rewardCelebrations: () => '',
    editorOccupant: () => '',
    renderPreviewTile: () => '',
    S: {},
    BOARD: { plots: [] },
    CURRENT_PLAYER: '',
    netCalls: [],
    syncCalls: [],
    observeCalls: [],
  };
  const voidTags = new Set(['img', 'br', 'hr', 'input', 'meta', 'link']);
  const decode = value => value.replace(/&quot;/g, '"').replace(/&#39;/g, "'").replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&amp;/g, '&');
  const attrsFor = source => {
    const attrs = {};
    const attr = /([a-zA-Z_:][-a-zA-Z0-9_:.]*)(?:\s*=\s*"([^"]*)")?/g;
    for (let match; (match = attr.exec(source));) attrs[match[1]] = match[2] === undefined ? '' : decode(match[2]);
    return attrs;
  };
  const selector = query => {
    const not = query.match(/^(.*):not\(\[([a-zA-Z0-9-]+)\]\)$/);
    const base = not ? not[1] : query;
    const excluded = not?.[2];
    let matches;
    if (base.startsWith('.')) {
      const className = base.slice(1);
      matches = element => element.classList.contains(className);
    } else if (base.startsWith('[')) {
      const match = base.match(/^\[([a-zA-Z0-9-]+)(?:="([^"]*)")?\]$/);
      const [, name, value] = match;
      matches = element => value === undefined ? name in element.attrs : element.attrs[name] === value;
    } else {
      matches = element => element.tagName === base.toUpperCase();
    }
    return element => matches(element) && (!excluded || !(excluded in element.attrs));
  };
  const descendants = (root, query) => {
    const matches = selector(query);
    const found = [];
    const visit = element => element.children.forEach(child => { if (matches(child)) found.push(child); visit(child); });
    visit(root);
    return found;
  };
  const element = (tag, attrs = {}) => {
    const node = { tagName: String(tag).toUpperCase(), attrs, children: [], parentNode: null, textContent: '', disabled: false };
    node.dataset = new Proxy({}, { get: (_, key) => node.attrs[`data-${String(key).replace(/[A-Z]/g, letter => `-${letter.toLowerCase()}`)}`], set: (_, key, value) => { node.attrs[`data-${String(key).replace(/[A-Z]/g, letter => `-${letter.toLowerCase()}`)}`] = String(value); return true; } });
    Object.defineProperty(node, 'hidden', { get: () => 'hidden' in node.attrs, set: value => { if (value) node.attrs.hidden = ''; else delete node.attrs.hidden; } });
    Object.defineProperty(node, 'innerHTML', { get: () => node._html || '', set: html => { node._html = html; node.children = parse(html).children; node.children.forEach(child => { child.parentNode = node; }); } });
    node.classList = { contains: className => (node.attrs.class || '').split(/\s+/).includes(className), add() {}, remove() {}, toggle() {} };
    node.setAttribute = (name, value) => { node.attrs[name] = String(value); };
    node.removeAttribute = name => { delete node.attrs[name]; };
    node.getAttribute = name => node.attrs[name] ?? null;
    node.appendChild = child => { child.parentNode = node; node.children.push(child); return child; };
    node.insertBefore = (child, reference) => { const present = node.children.indexOf(child); if (present >= 0) node.children.splice(present, 1); const at = reference == null ? -1 : node.children.indexOf(reference); if (at < 0) node.children.push(child); else node.children.splice(at, 0, child); child.parentNode = node; };
    node.remove = () => { const at = node.parentNode?.children.indexOf(node); if (at >= 0) node.parentNode.children.splice(at, 1); node.parentNode = null; };
    node.replaceWith = child => { const at = node.parentNode?.children.indexOf(node); if (at >= 0) node.parentNode.children[at] = child; child.parentNode = node.parentNode; node.parentNode = null; };
    node.querySelector = query => descendants(node, query)[0] || null;
    node.querySelectorAll = query => descendants(node, query);
    node.getBoundingClientRect = () => ({ top: 0, bottom: 0 });
    node.focus = () => {};
    return node;
  };
  const parse = html => {
    const root = element('root');
    const stack = [root];
    const tags = /<(\/?)([a-zA-Z][a-zA-Z0-9-]*)([^>]*)>/g;
    for (let match; (match = tags.exec(html));) {
      const closing = match[1].trim();
      const tag = match[2];
      if (closing) {
        for (let index = stack.length - 1; index > 0; index -= 1) if (stack[index].tagName === tag.toUpperCase()) { stack.length = index; break; }
      } else {
        const child = element(tag, attrsFor(match[3]));
        stack.at(-1).appendChild(child);
        if (!voidTags.has(tag.toLowerCase()) && !/\/\s*$/.test(match[3])) stack.push(child);
      }
    }
    return root;
  };
  context.document.createElement = tag => {
    if (tag !== 'template') return element(tag);
    const template = { _html: '' };
    Object.defineProperty(template, 'innerHTML', { get: () => template._html, set: html => { template._html = html; } });
    Object.defineProperty(template, 'content', { get: () => ({ firstElementChild: parse(template._html).children[0] || null }) });
    return template;
  };
  context.__panel = () => element('section');
  context.harvestResourceColumns = (received, costs, options = {}) => {
    context.netCalls.push({ received, costs, options });
    return '<div class="hm-totals hm-totals--net"><div class="hm-resource-pair" data-resource="GRAIN"></div></div>';
  };
  context.syncHarvestResourceDividers = panel => context.syncCalls.push(panel);
  context.observeHarvestResourceDividers = panel => context.observeCalls.push(panel);
  return context;
}

check('IR8160-net-receipt-render-mounts-divider-sync-and-observer', () => {
  const receiptSource = fs.readFileSync(path.join(staticDir, 'receipts.js'), 'utf8')
    .replace(/^import .*;$/gm, '')
    .replace(/^export /gm, '');
  const context = receiptDomContext();
  vm.runInNewContext(receiptSource, context, { filename: 'receipts.js' });
  context.CURRENT_PLAYER = 'ir8160';
  context.BOARD = { player: 'ir8160', plots: [] };
  context.localStorage.setItem('ceres:receipts:v1:ir8160', JSON.stringify([{
    txId: 'r'.repeat(40), at: '2026-09-22T10:00:00Z', state: 'is-confirmed', family: 'harvest', headline: 'Harvest', notBroadcast: true,
    regions: [{ region: 'Praetoria', scope: 'every plot', amounts_available: true, received: { GRAIN: 8, WOOD: 3 }, costs: { GRAIN: 2 } }],
  }]));
  const panel = context.__panel();
  context.renderReceipts(panel);
  const harvestList = panel.querySelector('[data-history-list="harvest"]');
  const rendered = harvestList?.children[0]?._receiptHtml || '';
  assert.match(rendered, /hm-resource-summary--compact rc-harvest-net/);
  assert.match(rendered, /hm-totals--net/);
  assert.equal(context.netCalls.length, 1, 'receipt render must build its net resource tiles');
  assert.equal(context.netCalls[0].options.net, true);
  assert.equal(context.syncCalls.length, 1, 'net/compact receipt render must sync resource dividers after mount');
  assert.equal(context.syncCalls[0], panel);
  assert.equal(context.observeCalls.length, 1, 'net/compact receipt render must observe resource-divider reflow after mount');
  assert.equal(context.observeCalls[0], panel);
});
