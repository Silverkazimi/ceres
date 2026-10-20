"""Offline guards for the first Posting-only routing controller release."""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ceres.app import create_app
from ceres.config import CeresConfig
from tests.ceres.test_routing_v2_counts import _fixture, _preview
from tests.ceres.vm_context import run_in_module_context


STATIC = Path(__file__).parents[2] / "ceres" / "static"


def test_disabled_switch_leaves_modal_without_routing_controls(tmp_path, monkeypatch):
    monkeypatch.setenv("CERES_ROUTING_SALE_DEC", "0")
    config = CeresConfig(root=tmp_path / ".ceres", player="fixtureowner")
    client = TestClient(create_app(config))
    response = client.get("/api/harvest/routing/v2/kinds?player=fixtureowner")
    assert response.status_code == 200
    assert response.json()["enabled_kinds"] == []
    modal = (STATIC / "harvest-modal.js").read_text()
    assert "const HARVEST_ROUTING_ENABLED = false;" in modal
    assert "if (M.v2Kinds.length) return" in modal
    assert "if (!HARVEST_ROUTING_ENABLED) return '';" in modal
    assert "M.v2Kinds = result.enabled_kinds" in modal


def test_sale_dec_switch_exposes_only_the_released_kind(tmp_path, monkeypatch):
    monkeypatch.setenv("CERES_ROUTING_SALE_DEC", "1")
    config = CeresConfig(root=tmp_path / ".ceres", player="fixtureowner")
    response = TestClient(create_app(config)).get("/api/harvest/routing/v2/kinds?player=fixtureowner")
    assert response.status_code == 200
    assert response.json()["enabled_kinds"] == ["sale_dec"]


@pytest.mark.parametrize("name", ["a", "b", "c"])
def test_sale_only_approval_numbers_use_compiler_manifest(tmp_path, monkeypatch, name):
    fixture = _fixture(name)
    fixture["model"]["routes"] = [route for route in fixture["model"]["routes"] if route["kind"] == "sale_dec"]
    config = CeresConfig(root=tmp_path / ".ceres", player="fixtureowner")
    config.routing_kinds = {"sale_dec"}
    manifest = _preview(TestClient(create_app(config)), monkeypatch, fixture)["manifest"]
    expected = len(manifest["bundles"])
    assert expected > 0
    run_in_module_context(f"""
const manifest = {json.dumps(manifest)};
const counts = routingApprovalCounts(manifest);
if (counts.fixed !== {expected} || counts.conditional !== 0 || counts.maximum !== manifest.approval_counts.routing)
  throw Error(JSON.stringify(counts));
for (const [index, bundle] of manifest.bundles.entries())
  if (bundle.approval !== index + 1) throw Error('approval number changed');
""", entry=STATIC / "routing-window.js")


def test_uncertain_bundle_cannot_request_second_signature():
    run_in_module_context(r"""
walletReady = () => true;
navigator.locks = { request: async (_name, fn) => fn() };
const bundle = { id: 'bundle-1', wave: 0, approval: 1, operations: [] };
const record = { run_id: 'a'.repeat(32), state: 'ready', manifest: { bundles: [bundle], approval_counts: { harvest: 1 } },
  bundles: { 'bundle-1': { state: 'planned' } }, operations: {}, call_budget: { stopped: false, total: 0 } };
let signatures = 0;
const api = async (_player, path, body) => {
  if (path === 'run' || path === 'state') return record;
  if (path === 'claim') { record.bundles['bundle-1'].state = 'claimed'; return {
    authority: 'Posting', member_nonces: ['member-1'], operations: [['custom_json', { id: 'sm_land_operation', json: '{"n":"member-1"}' }]] }; }
  if (path === 'head') return { head_block_number: 1, head_block_id: '0'.repeat(40), time: '2026-09-24T00:00:00' };
  if (path === 'attach') { record.bundles['bundle-1'] = { state: 'outcome_unknown', tx_id: body.tx_id }; return record; }
  throw Error('unexpected endpoint ' + path);
};
const run = createRoutingRun({ player: 'alice', api, prepare: async () => [{ txId: 'b'.repeat(40) }],
  sign: async (_player, _ops, _callback, authority) => {
    if (authority !== 'Posting') throw Error('wrong authority');
    signatures += 1;
    const error = Error('wallet outcome uncertain'); error.txId = 'b'.repeat(40); throw error;
  } });
(async () => {
  await run.create({ preview_id: 'preview', preview_digest: 'digest' });
  await run.action('sign');
  await run.action('sign');
  if (signatures !== 1 || record.bundles['bundle-1'].state !== 'outcome_unknown') throw Error('uncertain bundle was signed again');
})().then(() => process.exit(0), error => { console.error(error); process.exit(1); });
""", entry=STATIC / "routing-run.js")


def test_r3_requests_posting_authority_only():
    source = "\n".join((STATIC / name).read_text() for name in
                       ("routing-run.js", "routing-setup.js", "routing-window.js"))
    assert "'Active'" not in source and '"Active"' not in source
    assert "claim.authority !== 'Posting'" in source
    assert "sign(player, handoff.operations, null, 'Posting'" in source
