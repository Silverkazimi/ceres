"""The shared browser harness supplies a real board without maintainer data or network."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.request

from . import board_basic_recording as recording
from .conftest import LIVE_PLAYER, prime_all_fixture_plots, spawn_live_ceres


def test_child_guard_refuses_external_connections_before_opening_a_socket():
    script = """
import runpy, socket
from tests.ceres.offline_server import main

def exercise(*args, **kwargs):
    try:
        socket.getaddrinfo('s8b.invalid', 443)
    except OSError as exc:
        assert 'refused external DNS lookup' in str(exc), str(exc)
    else:
        raise AssertionError('DNS lookup escaped the guard')
    for operation in ('connect', 'connect_ex'):
        with socket.socket() as sock:
            try:
                getattr(sock, operation)(('203.0.113.1', 443))
            except OSError as exc:
                assert 'refused external connection' in str(exc), str(exc)
            else:
                raise AssertionError('external connection escaped the guard')
runpy.run_module = exercise
main()
"""
    subprocess.run([sys.executable, "-c", script], check=True, timeout=10,
                   env={**os.environ, "CERES_FIXTURE_UPSTREAM": "required-by-guard"})


def test_shared_server_has_recorded_workers_synthetic_partners_and_offline_pages():
    with spawn_live_ceres() as (browser, base):
        with urllib.request.urlopen(f"{base}/api/health") as response:
            assert json.load(response)["fixture_upstream"] == "board-basic"
        prime_all_fixture_plots(base)
        with urllib.request.urlopen(f"{base}/api/idle-cards") as response:
            cards = json.load(response)["cards"]
        partners = [c for c in cards if c["uid"].startswith(recording.COMBINE_PARTNER_PREFIX)]
        assert len(partners) == 3
        assert all(c["available"] and c["bcx"] == 1 and c["bcx_max"] > 1 for c in partners)
        context = browser.new_context(viewport={"width": 1600, "height": 1000})
        try:
            page = context.new_page()
            page.goto(f"{base}/?player={LIVE_PLAYER}")
            page.wait_for_selector("#plot-tbody > tr.plot-row")
            assert page.evaluate("document.documentElement.classList.contains('ceres-lw-le-1279')") is False
            # Both an unrecorded API request and an unknown host must fail locally.
            assert page.evaluate("fetch('https://api.splinterlands.com/s8b-unrecorded').then(r => r.status)") == 599
            assert page.evaluate("fetch('https://s8b.invalid/').then(() => false, () => true)") is True
            assert page.evaluate("""() => new Promise((resolve, reject) => {
 const timer = setTimeout(() => reject(new Error('Offline WebSocket did not open locally')), 3000);
 const ws = new WebSocket('wss://s8b.invalid/');
 ws.onopen = () => { clearTimeout(timer); ws.close(); resolve(true); };
 ws.onerror = () => { clearTimeout(timer); reject(new Error('WebSocket escaped the local mock')); };
 })""") is True
        finally:
            context.close()

        # This page performed a fresh quick capture. A later shared-server caller
        # must still receive usable worker slots, regardless of that prior page.
        with spawn_live_ceres() as (_same_browser, same_base):
            assert same_base == base
            with urllib.request.urlopen(f"{base}/api/board") as response:
                plots = json.load(response)["plots"]
            assert len(plots) == len(recording.PLOTS)
            loaded = {p["deed_uid"] for p in plots if p["assets_loaded"]}
            assert {plot.uid for plot in recording.PRIMED} <= loaded
