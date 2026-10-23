""" source and VM guards for the net-first regional harvest preview."""
from __future__ import annotations

import re
from pathlib import Path

from .vm_context import read_entry_source, run_in_module_context


ROOT = Path(__file__).parents[2]
MODAL = ROOT / "ceres" / "static" / "harvest-modal.js"
CSS = ROOT / "ceres" / "static" / "ceres.css"
BUTTONS_CSS = ROOT / "ceres" / "static" / "buttons.css"
LEDGER = ROOT / "ceres" / "static" / "harvest-ledger.js"


def _modal_source_with_state() -> str:
    source = read_entry_source(MODAL)
    return source.rsplit("}", 1)[0] + "\nObject.defineProperty(globalThis, 'M', {get: () => M});\n}\n"


def test_batch_preview_default_and_cost_disclosure_use_shared_net_math() -> None:
    run_in_module_context(
        r"""
M.mode = 'batch'; M.scopeKind = 'all'; M.selectedRegions = new Set(); M.costsOpen = new Set();
M.regionLayout = {resources: ['GRAIN', 'WOOD'], rows: [{
  region_uid: 'PR-CEF-41', region_number: '041', plots: 2, resources: ['GRAIN', 'WOOD']
}]};
M.preview = null;
const skeleton = batchBodyHtml();
if (!skeleton.includes('Total · net after tax and costs')) throw Error('missing Total');
if (!skeleton.includes('Show costs') || skeleton.includes('− costs</span>')) throw Error('costs visible at open');
if (!skeleton.includes('class="hm-batch-head-status"') || skeleton.includes('class="hm-batch-status"')) throw Error('status is outside the fixed Total header slot');
if (!skeleton.includes('class="hm-batch-grid"') || skeleton.includes('hm-batch-grid-scroll')) throw Error('whole ledger scrolls');
if (skeleton.includes('hm-batch-scrollbar') || skeleton.includes('data-hm-batch-scroll')) throw Error('batch horizontal scroller remains');
M.preview = {regions: [{region_uid: 'PR-CEF-41', region_number: '041', status: 'included',
  net_by_token: {GRAIN: 100, WOOD: 20}, cost_now: {GRAIN: 15, WOOD: 5}}], issues: []};
const loaded = batchBodyHtml();
const badgeSlots = html => (html.match(/class="hm-batch-badge/g) || []).length;
const regionFor = html => {
  const regionStart = html.match(/<div\b(?=[^>]*class="[^"]*\bhm-batch-region\b[^"]*")(?=[^>]*\bdata-region="PR-CEF-41")[^>]*>/)?.index ?? -1;
  if (regionStart < 0) return '';
  const regionRows = /<div\b[^>]*class="hm-batch-region(?:\s[^"]*)?"/g;
  regionRows.lastIndex = regionStart + 1;
  const nextRegion = regionRows.exec(html)?.index ?? -1;
  const noProduction = html.indexOf('<div class="hm-batch-no-production"', regionStart);
  const sectionEnd = html.indexOf('</section>', regionStart);
  const regionEnd = [nextRegion, noProduction, sectionEnd].filter(index => index >= 0).reduce((end, index) => Math.min(end, index), html.length);
  return html.slice(regionStart, regionEnd);
};
if (badgeSlots(skeleton) !== badgeSlots(loaded)) throw Error('skeleton changed slot count');
if (loaded.includes('data-hm-batch-scroll')) throw Error('loaded view added a batch scroller');
if (!loaded.includes('class="hm-batch-name" title="Grain">Grain</span>')) throw Error('resource name has no full-name title');
if (skeleton.includes('--hm-batch-width') || loaded.includes('--hm-batch-width')) throw Error('amounts drive badge widths');
if (!loaded.includes('+85.00') || !loaded.includes('+15.00')) throw Error('collapsed amounts are not net');
if (loaded.includes('− costs</span>')) throw Error('cost row visible while collapsed');
const region = regionFor(loaded);
const closedRegionToggle = region.match(/<button[^>]*class="hm-batch-row-toggle"[^>]*>[\s\S]*?<\/button>/)?.[0] || '';
if (!closedRegionToggle.includes('aria-expanded="false"') || !closedRegionToggle.includes('aria-label="Show costs for region 041"') || !closedRegionToggle.includes('class="ceres-chevron"')) throw Error('closed region costs toggle is missing its shared chevron state or label');
if (closedRegionToggle.includes('±')) throw Error('region costs toggle still uses the plus-minus symbol');
if (region.includes('Region 041') || region.includes('region name')) throw Error('region name repeated');
if (!region.includes('>041</span>')) throw Error('region plaque missing');
if (region.includes('hm-batch-region-status') || region.includes('Ready</span>')) throw Error('region status added a second line');
if (!region.includes('hm-batch-state-dot') || !region.includes('data-tip="Ready"')) throw Error('region state dot or tooltip missing');
for (const [status, label] of [['short', 'Needs funding'], ['unknown', 'Balance unavailable'], ['unavailable', 'Quote unavailable'], ['nothing', 'Cooldown']]) {
  M.preview.regions[0] = {region_uid: 'PR-CEF-41', region_number: '041', status, cooldown_seconds: 30,
    next_op_at: new Date(Date.now() + 60_000).toISOString(), net_by_token: {GRAIN: 100, WOOD: 20}, cost_now: {GRAIN: 15, WOOD: 5}};
  const blocked = batchBodyHtml();
  const blockedRegion = regionFor(blocked);
  if (!blockedRegion.includes(`hm-batch-state-label`) || !blockedRegion.includes(label) || blockedRegion.includes('hm-batch-region-status') || blocked.includes('Region 041')) throw Error(`blocking state not compact in toggle column: ${label}`);
}
M.preview.regions[0] = {region_uid: 'PR-CEF-41', region_number: '041', status: 'nothing', cooldown_seconds: 30,
  next_op_at: new Date(Date.now() + 60_000).toISOString(),
  net_by_token: {GRAIN: 0, WOOD: 0}, cost_now: {GRAIN: 0, WOOD: 0}};
const idleRegionHtml = regionFor(batchBodyHtml());
if (idleRegionHtml.includes('hm-batch-viewport') || idleRegionHtml.includes('hm-batch-slots')) throw Error('idle region renders an empty resource strip');
const idleLabel = idleRegionHtml.match(/<span\b[^>]*class="[^"]*\bhm-batch-idle-status\b[^"]*"[^>]*>([^<]*)<\/span>/);
const idlePlaqueEnd = idleRegionHtml.indexOf('</span>', idleRegionHtml.indexOf('hm-batch-plaque'));
if (!idleLabel || !/^Cooldown · \d/.test(idleLabel[1]) || idlePlaqueEnd < 0 || idleRegionHtml.indexOf(idleLabel[0]) < idlePlaqueEnd) throw Error('idle region does not show its full state label after the plaque');
M.preview.regions[0] = {region_uid: 'PR-CEF-41', region_number: '041', status: 'nothing',
  net_by_token: {GRAIN: 0, WOOD: 0}, cost_now: {GRAIN: 0, WOOD: 0}};
const noCooldownIdleRegion = regionFor(batchBodyHtml());
const noCooldownIdleLabel = noCooldownIdleRegion.match(/<span\b[^>]*class="[^"]*\bhm-batch-idle-status\b[^"]*"[^>]*>([^<]*)<\/span>/);
if (!noCooldownIdleLabel || noCooldownIdleLabel[1] !== 'Nothing to harvest') throw Error('idle region without cooldown does not show its full state label');
M.preview.regions[0] = {region_uid: 'PR-CEF-41', region_number: '041', status: 'included',
  net_by_token: {GRAIN: 100, WOOD: 20}, cost_now: {GRAIN: 15, WOOD: 5}};
M.regionLayout.rows.push(
  {region_uid: 'PR-CEF-1', region_number: '001', plots: 1, resources: []},
  {region_uid: 'PR-CEF-8', region_number: '008', plots: 1, resources: []}
);
const idle = batchBodyHtml();
if ((idle.match(/class="hm-batch-no-production"/g) || []).length !== 1 || !idle.includes('No production:') || !idle.includes('>001</span>') || !idle.includes('>008</span>')) throw Error('no-production regions are not grouped in one line');
const totalSection = idle.slice(idle.indexOf('<section class="hm-batch-total"'), idle.indexOf('</section>', idle.indexOf('<section class="hm-batch-total"')));
const regionsSection = idle.slice(idle.indexOf('<section class="hm-batch-regions"'), idle.indexOf('</section>', idle.indexOf('<section class="hm-batch-regions"')));
if (totalSection.includes('hm-batch-no-production')) throw Error('no-production line is inside Total');
if (!regionsSection.includes('hm-batch-no-production') || regionsSection.indexOf('hm-batch-region-list') > regionsSection.indexOf('hm-batch-no-production')) throw Error('no-production line is not after the region rows');
if (idle.includes('hm-batch-region is-idle') || idle.includes('No production this region')) throw Error('idle regions render separate rows');
M.preview.regions[0] = {region_uid: 'PR-CEF-41', region_number: '041', status: 'included',
  net_by_token: {GRAIN: 100, WOOD: 20}, cost_now: {GRAIN: 15, WOOD: 5}};
M.costsOpen = new Set();
handleAction({dataset: {hm: 'show-costs', region: 'PR-CEF-41'}}, {});
handleAction({dataset: {hm: 'show-costs'}}, {});
const expanded = batchBodyHtml();
const expandedRegionToggle = regionFor(expanded).match(/<button[^>]*class="hm-batch-row-toggle"[^>]*>[\s\S]*?<\/button>/)?.[0] || '';
if (!expandedRegionToggle.includes('aria-expanded="true"') || !expandedRegionToggle.includes('aria-label="Hide costs for region 041"') || !expandedRegionToggle.includes('class="ceres-chevron"')) throw Error('open region costs toggle is missing its shared chevron state or label');
const expandedRegion = regionFor(expanded);
const expandedTotal = expanded.slice(expanded.indexOf('<section class="hm-batch-total"'), expanded.indexOf('</section>', expanded.indexOf('<section class="hm-batch-total"')));
if ((expanded.match(/>received<\/span>/g) || []).length !== 1) throw Error('Total received label missing or duplicated in region rows');
if ((expanded.match(/>− costs<\/span>/g) || []).length !== 1) throw Error('Total costs label missing or duplicated in region rows');
if ((expanded.match(/>= net<\/span>/g) || []).length !== 1) throw Error('Total net label missing or duplicated in region rows');
if (!expandedTotal.includes('>received</span>') || !expandedTotal.includes('>− costs</span>') || !expandedTotal.includes('>= net</span>')) throw Error('Total ledger labels are missing');
if (expandedRegion.includes('>received</span>') || expandedRegion.includes('>− costs</span>') || expandedRegion.includes('>= net</span>')) throw Error('expanded region contains redundant row labels');
const firstExpandedLine = expandedRegion.slice(0, expandedRegion.indexOf('</div>') + 6);
if (!firstExpandedLine.includes('>041</span>') || firstExpandedLine.indexOf('hm-batch-plaque') > firstExpandedLine.indexOf('hm-batch-viewport')) throw Error('expanded plaque does not lead the received row');
if (!expandedRegion.includes('<span aria-hidden="true"></span><div class="hm-batch-viewport">')) throw Error('expanded cost/net rows do not preserve the shared badge column');
if (!expanded.includes('+100.00') || !expanded.includes('−15.00') || !expanded.includes('+85.00')) throw Error('received minus costs does not equal net');
if (!expanded.includes('is-cost') || !expanded.includes('is-net')) throw Error('badge semantics missing');
const unknown = batchBadgeHtml('FUTURE_TOKEN', 12.5, 'net');
if (!unknown.includes('hm-batch-icon-fallback') || !unknown.includes('FUTURE_TOKEN')) throw Error('unknown resource has no generic icon or symbol label');
if (tokenIcon('FUTURE_TOKEN') !== '' || tokenLabel('FUTURE_TOKEN') !== 'FUTURE_TOKEN') throw Error('unknown token icon/label fallback changed');
const unknownKeys = resourceKeys({FUTURE_TOKEN: 1, GRAIN: 1});
if (unknownKeys.join(',') !== 'GRAIN,FUTURE_TOKEN') throw Error('unknown resource key is not retained after known keys');
M.regionLayout = {resources: Array.from({length: 50}, (_, i) => `RX${String(i).padStart(2, '0')}`),
  rows: [{region_uid: 'PR-CEF-41', region_number: '041', plots: 50,
    resources: Array.from({length: 50}, (_, i) => `RX${String(i).padStart(2, '0')}`)}]};
M.costsOpen = new Set(); M.preview = null;
const wideOpen = batchBodyHtml();
M.preview = {regions: [{region_uid: 'PR-CEF-41', region_number: '041', status: 'included',
  net_by_token: {RX00: 9999999.99}, cost_now: {}}], issues: []};
const wideLoaded = batchBodyHtml();
if (!wideLoaded.includes('+9,999,999.99')) throw Error('reserved 13-character amount changed');
if (badgeSlots(wideOpen) !== badgeSlots(wideLoaded)) throw Error('50-resource badges change at load');
""",
        entry=MODAL,
        source=_modal_source_with_state(),
    )


def test_batch_source_keeps_fixed_slots_legible_and_uses_existing_endpoints() -> None:
    modal = MODAL.read_text(encoding="utf-8")
    ledger = LEDGER.read_text(encoding="utf-8")
    css = CSS.read_text(encoding="utf-8")
    buttons_css = BUTTONS_CSS.read_text(encoding="utf-8")
    assert "netAfterCosts({ token: symbol" in modal
    assert "M.regionLayout = batchRegionLayout()" in modal
    assert "data-hm-batch-scroll" not in modal
    assert 'class="hm-batch-grid"' in modal
    assert 'class="hm-batch-grid-scroll"' not in modal
    assert 'class="hm-batch-slots">' in ledger
    # The batch slots never scroll-sync. The modal's single scroll listener belongs to the routing
    # window's pointer lines and acts only on scrolls inside the routing surfaces.
    assert "addEventListener('scroll'" not in ledger
    assert modal.count("addEventListener('scroll'") == 1
    assert "el.addEventListener('scroll', redrawPointers, true);" in modal
    assert "const redrawPointers = (event) => { if (!event || event.target?.closest?.('.r3-window, .r3-setup')) {" in modal
    assert "regionScroll" not in modal and "slot.scrollLeft" not in modal
    assert "recordWidth" not in modal and "numberChars" not in modal
    assert "${esc(row.name)}" not in modal[modal.index("function batchBodyHtml()"):modal.index("function batchNothingText")]
    assert 'class="hm-batch-name" title="${esc(name)}"' in ledger
    batch_font_size_rules = [
        (match.group(1), match.group(2))
        for match in re.finditer(r"([^{}]+)\{([^{}]*)\}", css)
        if re.search(r"font-size\s*:", match.group(2))
        and re.search(r"\.hm-batch-(?:name|number)(?![\w-])", match.group(1))
    ]
    assert len(batch_font_size_rules) == 1
    assert re.search(r":is\(#harvest-modal-overlay, \.harvest-receipt-ledger\) \.hm-batch-name,\s*:is\(#harvest-modal-overlay, \.harvest-receipt-ledger\) \.hm-batch-number", batch_font_size_rules[0][0])
    assert re.search(r"font-size\s*:\s*\.9rem\s*(?:;|$)", batch_font_size_rules[0][1])
    assert re.search(r"(?m)^:is\(#harvest-modal-overlay, \.harvest-receipt-ledger\) \.hm-batch-number \{[^}]*text-align:right", css)
    assert "grid-template-columns:1.1rem minmax(0,1fr) max-content" in css
    assert "hm-batch-head-status" in ledger and "hm-batch-status" not in modal and "hm-batch-status" not in ledger
    assert "hm-batch-region-actions" in modal and "hm-batch-state-dot" in modal
    assert 'class="ceres-chevron" aria-hidden="true"></span></button>`' in modal
    assert "[aria-expanded=\"true\"] > .ceres-chevron" in buttons_css
    assert "transition: transform .18s ease" in buttons_css
    assert "@media (prefers-reduced-motion: reduce)" in buttons_css
    assert ".ceres-chevron, details > summary::before { transition: none; }" in buttons_css
    assert "hm-batch-idle-status hm-batch-state-label" in modal
    assert "const idle = fresh && row.preview?.status === 'nothing'" in modal
    assert 'class="hm-batch-region-actions">${state.blocking && !idle ?' in modal
    assert "${open ? plaque : ''}" not in modal
    assert "@keyframes hm-batch-shimmer" in css and "background-size:200% 100%" in css
    assert "@media (prefers-reduced-motion: reduce) { :is(#harvest-modal-overlay, .harvest-receipt-ledger) .hm-batch-badge.is-skeleton { animation:none; } }" in css
    assert "hm-batch-no-production" in modal and "hm-batch-region is-idle" not in modal
    grid = "grid-template-columns:5.75rem minmax(0,1fr) 2.6rem"
    region_grid = re.search(r":is\(#harvest-modal-overlay, \.harvest-receipt-ledger\) \.hm-batch-region-line,\s*:is\(#harvest-modal-overlay, \.harvest-receipt-ledger\) \.hm-batch-ledger-line,\s*:is\(#harvest-modal-overlay, \.harvest-receipt-ledger\) \.hm-batch-scrollbar-row\s*\{([^}]*)\}", css)
    assert region_grid and grid in region_grid.group(1)
    assert len(re.findall(r"grid-template-columns:5\.75rem minmax\(0,1fr\) 2\.6rem", css)) == 1
    assert len(grid.split()) == 3
    # the base badge rule (scoped overrides such as the wallet drawer's receipt come earlier in the file)
    badge = re.search(r"(?m)^:is\(#harvest-modal-overlay, \.harvest-receipt-ledger\) \.hm-batch-badge\s*\{([^}]*)\}", css)
    assert badge and "padding-inline:10px" in badge.group(1)
    assert "inline-size:auto" in badge.group(1) and "flex:none" in badge.group(1)
    number = re.search(r"(?m)^:is\(#harvest-modal-overlay, \.harvest-receipt-ledger\) \.hm-batch-number \{([^}]*)\}", css)
    assert number and "justify-self:end" in number.group(1) and "overflow:visible" in number.group(1)
    assert not re.search(r"overflow\s*:\s*hidden|text-overflow\s*:\s*ellipsis", number.group(1))
    name = re.search(r"\.hm-batch-name\s*\{([^}]*)\}", css)
    assert name and "overflow:hidden" in name.group(1) and "text-overflow:ellipsis" in name.group(1)
    slot_rule = re.search(r"(?m)^:is\(#harvest-modal-overlay, \.harvest-receipt-ledger\) \.hm-batch-slots\s*\{([^}]*)\}", css)
    assert slot_rule and "display:grid" in slot_rule.group(1)
    assert "grid-template-columns:repeat(6,minmax(0,1fr))" in slot_rule.group(1)
    assert "inline-size:100%" in slot_rule.group(1) and "overflow:visible" in slot_rule.group(1)
    assert not re.search(r"overflow-x\s*:", slot_rule.group(1))
    grid_rule = re.search(r"\.hm-batch-grid\s*\{([^}]*)\}", css)
    assert grid_rule and "inline-size:100%" in grid_rule.group(1) and "overflow-x" not in grid_rule.group(1)
    assert ".hm-batch-viewport.is-overflowing::after { display:none; }" in css
    assert ":is(#harvest-modal-overlay, .harvest-receipt-ledger) .hm-batch-scrollbar-row { display:none; }" in css
    assert "justify-content:flex-start" in re.search(r"\.hm-batch-region-actions\s*\{([^}]*)\}", css).group(1)
    assert ".harvest-panel.is-batch:has(.hm-batch-grid) { inline-size:min(96rem" in css
    assert "scrollbar-gutter:stable" in css
    assert "${spacer}" in ledger[ledger.index("export function batchLedgerRows"):ledger.index("export function batchSlotsHtml")]
    idle_status = re.search(r"\.hm-batch-idle-status\s*\{([^}]*)\}", css)
    assert idle_status and "min-block-size:1.75rem" in idle_status.group(1)
    assert "max-inline-size:none" in idle_status.group(1) and "overflow:visible" in idle_status.group(1)
    assert "text-overflow:clip" in idle_status.group(1)
    assert not re.search(r"max-inline-size\s*:\s*(?!none\b)|overflow\s*:\s*hidden|text-overflow\s*:\s*ellipsis", idle_status.group(1))
    assert ".hm-batch-leading.is-expanded" not in css
    assert "batchRegionLedgerHtml({ leading, received: rowSlots('received', received), costs: rowSlots('cost', costs), net: rowSlots('net', net), actions: stateColumn, open: open && fresh })" in modal
    assert "if (!open) return line(leading, net, actions);" in ledger
    assert "return line(expandedLeading, received, actions)" in ledger
    assert "line('<span aria-hidden=\"true\"></span>', costs)" in ledger
    assert "line('<span aria-hidden=\"true\"></span>', net)" in ledger
    assert "${routingEntryHtml()}<div class=\"hm-batch-grid\"" in modal
    assert "M.v2?.action" in modal and "M.v2?.change" in modal and "M.v2?.record" in modal
    batch_css = css[css.index("/* known resource slots"):css.index("/* reserve balances")]
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", batch_css)
    endpoints = set(re.findall(r"['\"](/api/[^'\"?]+)", modal))
    assert endpoints == {
        "/api/harvest/routing/stop", "/api/harvest/state", "/api/harvest/preview",
        "/api/harvest/attempt", "/api/harvest/verify",
        "/api/harvest/routing/harvest-transaction", "/api/harvest/routing/harvest-plan",
        "/api/harvest/preflight", "/api/harvest/prepare",
    }
