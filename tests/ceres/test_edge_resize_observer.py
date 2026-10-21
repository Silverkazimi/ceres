""" (maintainer, verbatim): "alignment fix not visible in latest version with
hard refresh". 's own live measurement of Iris's `.theme-picker`/
`.theme-toggle` only re-ran on `resize`/`ceres:table-layout`/the load-time
retry passes -- none of which fire when the picker changes size on its OWN
(a late avatar image finishing layout inside the toggle, an emoji/font glyph
metric settling after its own font-face loads, or any future change to
Iris's own vendored recipe). A `ResizeObserver` on both `.theme-picker` and
`.theme-toggle` catches every one of those cases directly. See
library/ceres-edge-tabs.md.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STATIC = ROOT / "ceres" / "static"
APP_JS = STATIC / "app.js"


def _fn(js: str, name: str) -> str:
    fn_match = re.search(rf"function {name}\(\) \{{.*?\n\}}\n", js, re.S)
    assert fn_match, f"{name} source shape changed -- update this test's slice"
    return fn_match.group(0)


def test_resize_observer_watches_both_the_pill_and_the_toggle():
    js = APP_JS.read_text(encoding="utf-8")
    body = _fn(js, "initEdgeGeometryObserver")
    assert "const picker = document.querySelector('.theme-picker');" in body
    assert "const toggle = document.querySelector('.theme-picker .theme-toggle');" in body
    assert "const observer = new ResizeObserver(onResize);" in body
    assert "observer.observe(picker);" in body
    assert "observer.observe(toggle);" in body


def test_observer_callback_skips_while_the_tray_is_open():
    js = APP_JS.read_text(encoding="utf-8")
    body = _fn(js, "initEdgeGeometryObserver")
    assert "if (picker.classList.contains('is-open')) return;" in body
    assert "scheduleCenterThemePicker();" in body


def test_observer_reuses_the_existing_raf_debounce_not_a_second_one():
    """`scheduleCenterThemePicker()` is already rAF-debounced -- the observer
    must call that, not `centerThemePicker()` directly (which would skip the
    debounce and could run synchronously inside the observer's own callback,
    a context browsers restrict layout work in)."""
    js = APP_JS.read_text(encoding="utf-8")
    body = _fn(js, "initEdgeGeometryObserver")
    assert "centerThemePicker();" not in body
    assert "scheduleCenterThemePicker();" in body


def test_toggle_img_load_listener_is_defensive_future_proofing():
    """theme.js's own vendored toggle carries only emoji text today (no
    <img>) -- this listener attaches to zero elements and does nothing until
    a future Iris release or project-local change mounts an image into it,
    matching the maintainer's own screenshot description."""
    js = APP_JS.read_text(encoding="utf-8")
    body = _fn(js, "initEdgeGeometryObserver")
    assert "toggle.querySelectorAll('img').forEach((img) => {" in body
    assert "img.addEventListener('load', onResize);" in body
    theme_js = (STATIC / "theme.js").read_text(encoding="utf-8")
    assert "<img" not in theme_js
    assert "createElement(\"img\")" not in theme_js


def test_initEdgeGeometryObserver_is_wired_into_init():
    js = APP_JS.read_text(encoding="utf-8")
    assert "mountThemePicker({ storageKey: 'ceres:theme', defaultTheme: 'dark' });" in js
    assert re.search(r"(?m)^  initEdgeGeometryObserver\(\);(?:\s*//[^\n]*)?$", js)
    # Wired right after mountThemePicker() -- .theme-picker/.theme-toggle must
    # already exist in the DOM before the observer tries to select them.
    idx_mount = js.index("mountThemePicker({ storageKey: 'ceres:theme', defaultTheme: 'dark' });")
    # The top-level call in ready() has a unique two-space indent; definition and watcher calls differ.
    observer_call = re.compile(r"(?m)^  initEdgeGeometryObserver\(\);(?:\s*//[^\n]*)?$")
    assert len(observer_call.findall(js)) == 1
    idx_observer = observer_call.search(js).start()
    assert idx_observer > idx_mount
    assert idx_observer - idx_mount < 200  # adjacent lines, not buried elsewhere


def test_observer_disconnects_the_old_resize_observer_on_remount():
    """theme.js's mountThemePicker() calls its own `_unmount()` first, which removes
    the `.theme-picker`/`.theme-toggle` nodes this file's ResizeObserver watches. Since
    theme.js is vendored Iris canonical code (see its own header) and must not be
    edited here, app.js instead disconnects and rebuilds its own observer, and watches
    the picker's host for childList changes to know when to do so."""
    js = APP_JS.read_text(encoding="utf-8")
    body = _fn(js, "initEdgeGeometryObserver")
    assert "if (EDGE_GEOMETRY_OBSERVER) {" in body
    assert "EDGE_GEOMETRY_OBSERVER.disconnect();" in body
    assert "EDGE_GEOMETRY_OBSERVER = observer;" in body


def test_theme_js_is_not_edited_to_dispatch_a_remount_event():
    theme_js = (STATIC / "theme.js").read_text(encoding="utf-8")
    assert "Canonical home: ~/Projects/Iris/theme.js" in theme_js, "theme.js header changed -- re-check it is still vendored, unedited Iris code"
    assert "ceres:theme-picker-mounted" not in theme_js


def test_watch_remount_is_wired_right_after_the_observer():
    js = APP_JS.read_text(encoding="utf-8")
    watch_fn = re.search(r"function watchThemePickerRemount\(\) \{.*?\n\}\n", js, re.S)
    assert watch_fn, "watchThemePickerRemount source shape changed -- update this test's slice"
    body = watch_fn.group(0)
    assert "new MutationObserver(" in body
    assert "initEdgeGeometryObserver();" in body
    assert ".observe(host, { childList: true });" in body
    watch_call = re.compile(r"(?m)^  watchThemePickerRemount\(\);(?:\s*//[^\n]*)?$")
    assert len(watch_call.findall(js)) == 1
    # The top-level call in ready() has a unique two-space indent; definition and watcher calls differ.
    observer_call = re.compile(r"(?m)^  initEdgeGeometryObserver\(\);(?:\s*//[^\n]*)?$")
    assert len(observer_call.findall(js)) == 1
    idx_observer = observer_call.search(js).start()
    idx_watch = watch_call.search(js).start()
    assert idx_watch > idx_observer
    assert idx_watch - idx_observer < 100


def test_node_check_touched_js_file():
    result = subprocess.run(["node", "--check", str(APP_JS)], capture_output=True, text=True)
    assert result.returncode == 0, f"{APP_JS}: {result.stderr}"
