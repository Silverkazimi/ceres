""".

1. `/api/board?plots=<label>` -- the same lean board from the same publication, its `plots` cut to the named rows,
 every board-wide field unchanged, `partial: true`; unknown label 404, malformed 400; the filter is part of the
 cache key (own bytes-cache entry, own ETag).
2. The page with `?plot=<label>` draws that plot first, then the full board; board-wide values are never shown from
 the one row, and the opened plot does not move when the rest arrives.
3. `window.__ceresReady` / `ceres:ready` states.
"""

from __future__ import annotations

import json
import re
import urllib.request
from pathlib import Path
from types import SimpleNamespace

import pytest

from ceres import capture as capture_mod
from ceres.config import CeresConfig
from ceres.routes_board import _board_cache_key, _parse_plot_filter, _plot_label_key, get_board
from . import board_basic_recording as recording

STATIC = Path(__file__).resolve().parents[2] / "ceres" / "static"


def _request(config: CeresConfig, headers: dict | None = None):
    state = SimpleNamespace(ceres_config=config)
    hdrs = {k.lower(): v for k, v in (headers or {}).items()}
    return SimpleNamespace(
        app=SimpleNamespace(state=state),
        headers=SimpleNamespace(get=lambda key, default="": hdrs.get(key.lower(), default)),
    )


def _rows():
    return [
        {"deed_uid": "I-1-a", "plot": "65-9-83", "region_number": 65, "base_pp": 10},
        {"deed_uid": "I-1-b", "plot": "65-9-84", "region_number": 65, "base_pp": 20},
        {"deed_uid": "I-1-c", "plot": "7-1-2", "region_number": 7, "base_pp": 30},
    ]


BOARD = {
    "schema": "ceres.board.v1", "player": "player0005", "plots": _rows(),
    "totals": {"plots": 3, "base_pp": 60, "plots_idle": 1}, "notices": [], "idle_plots": ["I-1-c"],
    "badge_meta": {"x": 1}, "pad": "x" * 2000,
}


def _clear_board_caches():
    import ceres.routes_board as rb

    rb._board_cache.clear()
    rb._board_cache_bytes.clear()
    rb._board_bytes_cache.clear()
    rb._board_bytes_cache_sizes.clear()
    rb._board_cache_total_bytes = 0


def _fallback(monkeypatch, report, tmp_path, *, token="tok-1"):
    config = CeresConfig(root=tmp_path / "ceres", player="player0005")
    ref = capture_mod.SnapshotRef(path=str(tmp_path / "snap.json"), generated_at="t", player="player0005", token=token)
    monkeypatch.setattr(capture_mod, "latest_snapshot", lambda config, player: (report, ref))
    monkeypatch.setattr(capture_mod, "freshness", lambda config, player: capture_mod.CaptureStatus(state="idle", age_seconds=1, stale=False))
    monkeypatch.setattr("ceres.routes_board.load_details_by_id", lambda: {})
    monkeypatch.setattr("ceres.routes_board.details_cache_mtime", lambda: 111)
    monkeypatch.setattr("ceres.routes_board.project_board", lambda *a, **k: json.loads(json.dumps(BOARD)))
    return config


def _body(response):
    return json.loads(response.body)


def test_plot_label_key_and_filter_parse():
    assert _plot_label_key("065-09-083") == _plot_label_key("65-9-83") == "65-9-83"
    assert _plot_label_key("I-1-a") is None and _plot_label_key("65-9") is None
    assert _parse_plot_filter(None) is None
    assert _parse_plot_filter("065-09-083,65-9-83, 7-1-2") == ("65-9-83", "7-1-2")
    for bad in ("", "abc", "65-9-83,../x", ",".join(f"1-1-{n}" for n in range(21))):
        with pytest.raises(Exception) as caught:
            _parse_plot_filter(bad)
        assert getattr(caught.value, "status_code", None) == 400


def test_filtered_read_returns_one_plot_with_full_board_values(monkeypatch, report, tmp_path):
    _clear_board_caches()
    config = _fallback(monkeypatch, report, tmp_path)
    full = _body(get_board(_request(config)))
    response = get_board(_request(config), plots="065-09-083")
    assert response.status_code == 200
    one = _body(response)
    assert [row["deed_uid"] for row in one["plots"]] == ["I-1-a"]
    assert one["partial"] is True and one["plots_filter"] == ["65-9-83"]
    # every board-wide field is the FULL board's value -- totals, idle plots, badge meta, notices
    for key in ("totals", "idle_plots", "badge_meta", "notices", "player", "schema"):
        assert one[key] == full[key], key
    assert "partial" not in full and len(full["plots"]) == 3


def test_unknown_label_is_404_and_malformed_is_400(monkeypatch, report, tmp_path):
    _clear_board_caches()
    config = _fallback(monkeypatch, report, tmp_path)
    missing = get_board(_request(config), plots="999-99-999")
    assert missing.status_code == 404
    assert _body(missing)["plots_filter"] == ["999-99-999"]
    with pytest.raises(Exception) as caught:
        get_board(_request(config), plots="not-a-label")
    assert getattr(caught.value, "status_code", None) == 400


def test_filter_is_part_of_the_cache_key(monkeypatch, report, tmp_path):
    _clear_board_caches()
    config = _fallback(monkeypatch, report, tmp_path)
    ref = capture_mod.SnapshotRef(path="p", generated_at="t", player="player0005", token="tok")
    plain, _, _ = _board_cache_key(ref, "player0005", True)
    one, _, _ = _board_cache_key(ref, "player0005", True, plots=("65-9-83",))
    two, _, _ = _board_cache_key(ref, "player0005", True, plots=("7-1-2",))
    assert len({plain, one, two}) == 3

    import praetoria.fastjson as fastjson_mod

    real = fastjson_mod.dumps
    calls = {"n": 0}

    def counting(value, **kwargs):
        calls["n"] += 1
        return real(value, **kwargs)

    monkeypatch.setattr("ceres.routes_board.fastjson.dumps", counting)
    full = get_board(_request(config))
    a1 = get_board(_request(config), plots="65-9-83")
    a2 = get_board(_request(config), plots="065-09-083")   # same filter, other spelling: a hit
    b = get_board(_request(config), plots="7-1-2")
    assert calls["n"] == 3, "full + two distinct filters serialize once each; the repeat is served from the cache"
    assert a1.body == a2.body and a1.body != full.body and b.body != a1.body
    etags = {r.headers["ETag"] for r in (full, a1, b)}
    assert len(etags) == 3 and a1.headers["ETag"] == '"tok-1;plots=' + a1.headers["ETag"].split("plots=")[1]
    # If-None-Match on the filtered ETag answers 304; the full board's ETag does not match the filtered read
    assert get_board(_request(config, {"if-none-match": a1.headers["ETag"]}), plots="65-9-83").status_code == 304
    assert get_board(_request(config, {"if-none-match": full.headers["ETag"]}), plots="65-9-83").status_code == 200
    # a new publication token misses the filtered entry too ()
    config = _fallback(monkeypatch, report, tmp_path, token="tok-2")
    get_board(_request(config), plots="65-9-83")
    assert calls["n"] == 4  # tok-2's filtered body is a miss and is serialized afresh


def test_filtered_read_on_the_artifact_path_parses_the_published_bytes(monkeypatch, tmp_path):
    """The happy path serves artifact bytes; the filter reads the SAME bytes, never another snapshot."""
    from ceres import board_artifact
    import praetoria.fastjson as fastjson_mod

    _clear_board_caches()
    config = CeresConfig(root=tmp_path / "ceres", player="player0005")
    raw = fastjson_mod.dumps(BOARD)
    identity = SimpleNamespace(token="art-1", write_counter=1, snapshot_mtime_ns=1, snapshot_size=1,
                               details_mtime_ns=5, details_size=1, interim=False)
    artifact = SimpleNamespace(token="art-1", raw=raw, gzip=b"", meta={"generated_at": "t", "coverage": {}})
    monkeypatch.setattr(board_artifact, "cached_identity", lambda *a: identity)
    monkeypatch.setattr(board_artifact, "cached_artifact", lambda *a: artifact)
    monkeypatch.setattr(capture_mod, "has_plot_patches", lambda *a: False)
    monkeypatch.setattr(capture_mod, "freshness_from_board", lambda *a: capture_mod.CaptureStatus(state="idle", age_seconds=1, stale=False))
    monkeypatch.setattr("ceres.routes_board.project_board", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no projection")))
    response = get_board(_request(config), plots="7-1-2")
    assert response.status_code == 200
    body = _body(response)
    assert [row["deed_uid"] for row in body["plots"]] == ["I-1-c"] and body["totals"] == BOARD["totals"]
    assert response.headers["ETag"].startswith('"art-1;plots=')


# page: static contract

def test_board_js_reads_the_plot_first_and_holds_board_wide_values():
    js = (STATIC / "board.js").read_text(encoding="utf-8")
    assert "/api/board?plots=${encodeURIComponent(hooks.label)}" in js
    # the full board is requested at the same moment, not after the plot
    assert js.index("const fullRequest = fetch(`/api/board${playerQuery()}`") < js.index("first = await drawPlotFirst(")
    # plot-first never reconciles lock plans or restores stage plans from the one row
    draw = js[js.index("async function drawPlotFirst("):js.index("The resets every fresh")]
    assert "reconcilePlotLockPlans" not in draw and "restoreStagePlans" not in draw and "revealBoardTable" not in draw
    assert "controls-row" not in draw and "mobile-action-bar" not in draw  # the filter bar stays hidden
    assert "html.ceres-plot-first .count-cell, html.ceres-plot-first #table-head-sticky .count-total { visibility: hidden; }" in js
    # the kept row and the anchor run in the same task as the full render
    assert "if (keepDetail) {" in js and "PLOT_FIRST_HOOKS?.anchor?.(keepUid, anchorTop)" in js
    assert "if (EXPANDED_DATA_LOADED.has(deedUid)) continue" in js


def test_stage_navigation_opens_on_the_plot_first_draw():
    js = (STATIC / "stage-navigation.js").read_text(encoding="utf-8")
    assert "setPlotFirstHooks({ label: wanted, drawn: open, anchor: anchorPlot });" in js
    assert "window.addEventListener('ceres:board-revealed'" in js  # the fallback when the plot-first draw did not happen
    assert "setReady({ plot: label });" in js and "setReady({ plotMissing: true });" in js


def test_ready_module_states():
    js = (STATIC / "ceres-ready.js").read_text(encoding="utf-8")
    for token in ("window.__ceresReady = snapshot", "new CustomEvent('ceres:ready'", "board: 'none'", "capture: 'running'",
                  "market: null", "boardPlot", "boardFull"):
        assert token in js, token
    assert re.search(r"state\.board === 'full' && state\.workers === true && state\.capture !== 'running'", js)


# page: offline browser (recorded board-basic set)

TRACE = """
(() => {
 window.__ir9252 = { events: [], phasePlot: null, phaseFull: null };
 const sample = () => {
 const rows = [...document.querySelectorAll('#plot-tbody > tr.plot-row')];
 const open = document.querySelector('#plot-tbody > tr.expand-row .editor-band');
 const openRow = open ? open.closest('tr.expand-row').previousElementSibling : null;
 const vis = (el) => !!el && getComputedStyle(el).visibility !== 'hidden' && !el.classList.contains('board-content-hidden');
 return {
 rows: rows.length,
 controls: vis(document.querySelector('.controls-row')),
 countTotal: vis(document.querySelector('#table-head-sticky .count-total')),
 countCell: openRow ? vis(openRow.querySelector('.count-cell')) : null,
 edgeTabs: !!document.querySelector('#edge-tabs.edge-tabs-revealed'),
 top: openRow ? Math.round(openRow.getBoundingClientRect().top) : null,
 };
 };
 window.addEventListener('ceres:ready', (event) => {
 const s = event.detail;
 window.__ir9252.events.push({ board: s.board, plot: s.plot, workers: s.workers, capture: s.capture, ready: s.ready, dom: sample() });
 });
 window.__ir9252.sample = sample;
})();
"""


def _label_of(base_url: str, deed_uid: str) -> str | None:
    with urllib.request.urlopen(f"{base_url}/api/board", timeout=15) as response:
        for row in json.load(response)["plots"]:
            if row["deed_uid"] == deed_uid:
                return row["plot"]
    return None


def test_plot_first_page_in_the_browser():
    from tests.ceres.conftest import LIVE_PLAYER, spawn_live_ceres

    with spawn_live_ceres() as (browser, base_url):
        plot = _label_of(base_url, recording.COMBINE.uid)
        assert plot, "fixture plot missing"
        label = "-".join(part.zfill(width) for part, width in zip(plot.split("-"), (3, 2, 3)))
        context = browser.new_context(viewport={"width": 1366, "height": 900}, reduced_motion="reduce")
        try:
            page = context.new_page()
            page.add_init_script(TRACE)
            requests = []
            page.on("request", lambda r: requests.append(r.url) if "/api/board" in r.url else None)
            page.goto(f"{base_url}/?player={LIVE_PLAYER}&plot={label}")
            page.wait_for_function("() => window.__ceresReady && window.__ceresReady.ready === true", timeout=60_000)
            ready = page.evaluate("() => window.__ceresReady")
            events = page.evaluate("() => window.__ir9252.events")
            final = page.evaluate("() => window.__ir9252.sample()")
        finally:
            context.close()
    assert any("plots=" in url for url in requests), requests
    assert ready["board"] == "full" and ready["plot"] == label and ready["workers"] is True
    assert ready["capture"] == "settled" and ready["plotWanted"] == label
    assert ready["at"]["boardPlot"] <= ready["at"]["boardFull"] and "plot" in ready["at"]
    phase = [e for e in events if e["board"] == "plot"]
    assert phase, events
    for event in phase:  # board-wide values are never shown from the one row
        dom = event["dom"]
        assert dom["rows"] == 1 and not dom["controls"] and not dom["countTotal"] and not dom["edgeTabs"], dom
        assert dom["countCell"] in (None, False), dom
    assert final["rows"] > 1 and final["controls"] and final["countTotal"]
    # the opened plot keeps its place: its row top once the plot is open equals the final one
    opened = [e["dom"]["top"] for e in events if e["plot"] == label and e["dom"]["top"] is not None]
    assert opened and all(abs(top - final["top"]) <= 1 for top in opened), (opened, final)
    assert not page_errors_seen(events)


def page_errors_seen(events) -> bool:
    return any(e.get("board") == "failed" for e in events)


def test_unknown_plot_label_shows_the_full_board_and_says_so():
    from tests.ceres.conftest import LIVE_PLAYER, spawn_live_ceres

    with spawn_live_ceres() as (browser, base_url):
        context = browser.new_context(viewport={"width": 1366, "height": 900}, reduced_motion="reduce")
        try:
            page = context.new_page()
            page.goto(f"{base_url}/?player={LIVE_PLAYER}&plot=999-99-999")
            page.wait_for_function("() => window.__ceresReady && window.__ceresReady.ready === true", timeout=60_000)
            ready = page.evaluate("() => window.__ceresReady")
            rows = page.evaluate("() => document.querySelectorAll('#plot-tbody > tr.plot-row').length")
        finally:
            context.close()
    assert ready["board"] == "full" and ready["plot"] is None and ready["plotMissing"] is True
    assert rows > 1


# the interim board never claims an old plot detail

def _artifact_setup(monkeypatch, tmp_path, *, interim: bool, patches: bool):
    from ceres import board_artifact
    import praetoria.fastjson as fastjson_mod

    _clear_board_caches()
    config = CeresConfig(root=tmp_path / "ceres", player="player0005")
    lean = json.loads(json.dumps(BOARD))
    for row in lean["plots"]:
        row["assets_loaded"] = False  # this read has not loaded any plot's detail yet
    identity = SimpleNamespace(token="live-1", write_counter=2, snapshot_mtime_ns=None, snapshot_size=None,
                               details_mtime_ns=5, details_size=1, interim=interim)
    artifact = SimpleNamespace(token="live-1", raw=fastjson_mod.dumps(lean), gzip=b"", meta={"generated_at": "t", "coverage": {}})
    monkeypatch.setattr(board_artifact, "cached_identity", lambda *a: identity)
    monkeypatch.setattr(board_artifact, "cached_artifact", lambda *a: artifact)
    monkeypatch.setattr(capture_mod, "has_plot_patches", lambda *a: patches)
    monkeypatch.setattr(capture_mod, "freshness_from_board", lambda *a: capture_mod.CaptureStatus(state="running", age_seconds=1, stale=False))
    # the PREVIOUS read's snapshot with an old /expand patch laid over it: plot a claims a loaded detail
    previous = json.loads(json.dumps(BOARD))
    previous["plots"][0]["assets_loaded"] = True
    ref = capture_mod.SnapshotRef(path=str(tmp_path / "snap.json"), generated_at="old", player="player0005", token=None)
    monkeypatch.setattr(capture_mod, "latest_snapshot", lambda *a: (previous, ref))
    monkeypatch.setattr(capture_mod, "freshness", lambda *a: capture_mod.CaptureStatus(state="running", age_seconds=1, stale=False))
    monkeypatch.setattr("ceres.routes_board.load_details_by_id", lambda: {})
    monkeypatch.setattr("ceres.routes_board.details_cache_mtime", lambda: 5)
    monkeypatch.setattr("ceres.routes_board.project_board", lambda report, *a, **k: json.loads(json.dumps(report)))
    return config


@pytest.mark.parametrize("plots", [None, "65-9-83"])
def test_interim_board_with_leftover_patches_serves_this_reads_artifact(monkeypatch, tmp_path, plots):
    """during the interim publication a leftover /expand patch of the
 previous read made /api/board fall back to the previous snapshot -- the linked plot's row claimed
 `assets_loaded: true`, the page paid a GET + a live /expand (~4.8 s), and the board was not this load's read."""
    config = _artifact_setup(monkeypatch, tmp_path, interim=True, patches=True)
    response = get_board(_request(config), plots=plots) if plots else get_board(_request(config))
    assert response.status_code == 200
    row = next(r for r in _body(response)["plots"] if r["deed_uid"] == "I-1-a")
    assert row["assets_loaded"] is False
    assert response.headers["ETag"].startswith('"live-1')


def test_final_board_with_patches_still_merges_them(monkeypatch, tmp_path):
    """Outside the interim the rule is unchanged: patches on disk mean the snapshot (+ patches) is projected."""
    config = _artifact_setup(monkeypatch, tmp_path, interim=False, patches=True)
    row = next(r for r in _body(get_board(_request(config)))["plots"] if r["deed_uid"] == "I-1-a")
    assert row["assets_loaded"] is True
