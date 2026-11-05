""" Start from live uses only an exact captured roster, never upstream reads."""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from ceres.board_plan_eval import live_placements_from_capture
from ceres import routes_board_plans
from ceres.op_preview import GROUP_STAKE_TYPES
from tests.ceres.test_in0267_board_plan_eval import HEADERS, _client, _report, no_network


def _complete_report():
    report = _report()
    for plot in report['plots']:
        plot['staking_facts']['is_power_core_staked'] = False
        plot['asset_fetch'] = {'status': 'retrieved'}
    return report


def test_live_copy_uses_stable_slots_and_card_names():
    report = _complete_report()
    before = repr(report)
    placements = live_placements_from_capture(report)
    assert [(p['deed_uid'], p['group_id'], p['slot_index'], p['asset_ref'], p['asset']['name']) for p in placements] == [
        ('plot-a', 'worker', 0, 'resident', 'resident'),
        ('plot-b', 'worker', 0, 'neighbour', 'neighbour'),
    ]
    assert repr(report) == before


def test_live_copy_refuses_missing_worker_or_item_identity():
    report = _complete_report()
    report['plots'][0]['staking_facts']['worker_count'] = 2
    with pytest.raises(ValueError, match='not loaded'):
        live_placements_from_capture(report)
    report = _complete_report()
    report['plots'][0]['staking_facts']['totem_boost'] = .25
    with pytest.raises(ValueError, match='Totem'):
        live_placements_from_capture(report)


def test_live_copy_includes_runi_and_all_item_groups():
    report = _complete_report()
    report['workers'].append({'asset_ref': 'runi-a', 'name': 'Runi', 'stake_ref_uid': 'plot-a',
                              'stake_type_uid': GROUP_STAKE_TYPES['runi'], 'slot': 0})
    report['plots'][0]['staking_facts']['worker_count'] = 2
    report['plots'][0]['staking_facts']['is_runi_staked'] = True
    for group, name in [('power_core', 'Core'), ('totem', 'Totem'), ('title', 'Title')]:
        report['bonus_assets'].append({'asset_ref': group + '-a', 'name': name, 'stake_ref_uid': 'plot-a',
                                       'stake_type_uid': GROUP_STAKE_TYPES[group]})
    report['plots'][0]['staking_facts'].update(is_power_core_staked=True, totem_boost=.25, title_boost=.1)
    placements = live_placements_from_capture(report)
    assert {p['group_id'] for p in placements} == {'worker', 'runi', 'power_core', 'totem', 'title'}
    assert all(p['slot_index'] == 0 for p in placements)


def test_live_copy_route_is_capture_bound_and_local(tmp_path, monkeypatch, no_network):
    report = _complete_report()
    client = _client(tmp_path, monkeypatch, report)
    url = '/api/board-plans/from-live?player=player0005'
    stale = client.post(url, json={'capture_generated_at': 'old'}, headers=HEADERS)
    assert stale.status_code == 409
    ok = client.post(url, json={'capture_generated_at': report['generated_at']}, headers=HEADERS)
    assert ok.status_code == 200, ok.text
    assert ok.json()['account'] == 'player0005'
    assert len(ok.json()['placements']) == 2
    assert no_network == []


def test_live_copy_route_direct_without_testclient(tmp_path, monkeypatch):
    report = _complete_report()
    monkeypatch.setattr(routes_board_plans, '_read_snapshot_or_503', lambda *_: (report, None))
    request = SimpleNamespace(headers={}, app=SimpleNamespace(state=SimpleNamespace(ceres_config=object())))
    async def body():
        return {'capture_generated_at': report['generated_at']}
    request.json = body
    response = asyncio.run(routes_board_plans.board_plan_from_live(request, player='player0005'))
    payload = json.loads(response.body)
    assert payload['account'] == 'player0005'
    assert len(payload['placements']) == 2
