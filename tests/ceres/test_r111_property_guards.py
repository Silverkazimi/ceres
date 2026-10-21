"""Property guards for R-111's simplified reload and board surfaces."""

from __future__ import annotations

import ast
import copy
import re
import subprocess
from html.parser import HTMLParser
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from ceres import routes_op_preview
from ceres.board import project_board
from ceres.config import CeresConfig
from tests.ceres.conftest import build_details_by_id, build_report
from tests.ceres.vm_context import ENTRY, read_entry_source, run_in_module_context


ROOT = Path(__file__).parents[2]
STATIC = ROOT / "ceres" / "static"
RETIRED_CONTROL_WORDS = re.compile(
    r"\b(?:refresh|capture|stale|snapshot|freshness|reload|ago|sweep|read now|up to date)\b",
    re.IGNORECASE,
)
_ANY_VALUE = object()  # matches any string for a given (origin, word) pair
_RETIRED_CONTROL_EXEMPTIONS = {
    "relative_time_formatter_ago": {
        "origin": "format.js",
        "value": "${past ? '' : 'in '}${parts.join(' ')}${past ? ' ago' : ''}",
        "word": "ago",
        "reason": "format.js relative-time formatter renders real cooldown and harvest ages",
    },
    "pill_preview_when_samples": {
        "origin": "pill-preview.html",
        "value": frozenset({"30s ago", "1m ago", "2m ago"}),
        "word": "ago",
        "reason": "pill-preview.html uses these values as catalogue sample data, not board copy",
    },
    # (2026-09-19, commit d42e2b22): the maintainer later approved a discreet
    # avatar-click "read now" affordance -- a different, deliberately named control from
    # the old #refresh-btn/#capture-btn pair R-111 retired, not a regression of that ruling.
    "avatar_refresh_index": {"origin": "index.html", "value": _ANY_VALUE, "word": "refresh",
                              "reason": "/ avatar refresh control"},
    "avatar_refresh_board_js": {"origin": "board.js", "value": _ANY_VALUE, "word": "refresh",
                                 "reason": "/ avatar refresh control"},
    "avatar_refresh_board_js_reload": {"origin": "board.js", "value": _ANY_VALUE, "word": "reload",
                                        "reason": "/ avatar refresh error copy"},
    # These origins are each their own subsystem that happens to use everyday English
    # words (refresh/reload/stale/ago) the retired-control regex also matches; none of
    # them names the deleted board Refresh/Full-capture buttons or freshness indicator.
    "harvest_modal_refresh": {"origin": "harvest-modal.js", "value": _ANY_VALUE, "word": "refresh",
                               "reason": "Harvest review's own refresh control, unrelated to board capture"},
    "harvest_routing_refresh": {"origin": "harvest-routing.js", "value": _ANY_VALUE, "word": "refresh",
                                 "reason": "Harvest routing's own retry/refresh copy, unrelated to board capture"},
    "regional_dec_refresh": {"origin": "regional-dec.js", "value": _ANY_VALUE, "word": "refresh",
                              "reason": "Regional DEC panel's own refresh button, unrelated to board capture"},
    "transaction_queue_stale": {"origin": "transaction-queue.js", "value": _ANY_VALUE, "word": "stale",
                                 "reason": "/ live Hive head freshness for signing, not board capture"},
    "transaction_queue_refresh": {"origin": "transaction-queue.js", "value": _ANY_VALUE, "word": "refresh",
                                   "reason": "/ live Hive head freshness for signing, not board capture"},
    "wallet_resources_refresh": {"origin": "wallet-resources.js", "value": _ANY_VALUE, "word": "refresh",
                                  "reason": "Wallet balances' own refresh copy, unrelated to board capture"},
    # () deed-lock signing copy, the same live-check-before-signing pattern as
    # checkout_reload and transaction_queue_refresh above; it never named the retired board controls.
    "plot_lock_refresh": {"origin": "plot-lock.js", "value": "The live deed lock state changed. Refresh before signing.",
                           "word": "refresh", "reason": " live deed-lock re-check before signing, not board capture"},
    "picker_actions_lock_reload": {"origin": "picker-actions.js",
                                    "value": "This plot is absent from the live board. Reload before changing its lock.",
                                    "word": "reload", "reason": " lock action on a plot missing from the live board"},
    "broadcast_gate_refresh": {"origin": "broadcast-gate.js", "value": _ANY_VALUE, "word": "refresh",
                                "reason": "Per-block Hive limit guidance for harvest signing, unrelated to board capture"},
    "checkout_reload": {"origin": "checkout.js", "value": _ANY_VALUE, "word": "reload",
                         "reason": " kept-plan live-check failure copy, not the retired board control"},
    "combine_dialog_reload": {"origin": "combine-dialog.js", "value": _ANY_VALUE, "word": "reload",
                               "reason": "Combine flow guidance, not the retired board control"},
    "editor_reload": {"origin": "editor.js", "value": _ANY_VALUE, "word": "reload",
                       "reason": "Combine cooldown guidance, not the retired board control"},
    # Reached once the dev-only pill-preview.html is absent (the public tree): three strings that tell the
    # player to reload the browser PAGE after an error, or name the live new-version banner -- none names the
    # retired board Refresh/Full-capture controls or a freshness indicator. Exact values where the string is
    # plain copy; the style guide's banner specimen is matched by file and word.
    "card_browser_read_failed_reload": {"origin": "card-browser.js",
                                         "value": "The cards could not be read. Check the connection and reload the page.",
                                         "word": "reload", "reason": "/cards read-failure error state: reload the browser page"},
    "combine_board_refresh_failed_reload": {"origin": "combine.js",
                                             "value": "The combine is confirmed, but the board could not be refreshed. "
                                                      "Reload the page to see the combined card.",
                                             "word": None, "reason": "Combine success with a failed board re-read: reload the browser page"},
    "styleguide_version_banner": {"origin": "styleguide.js", "value": _ANY_VALUE, "word": "reload",
                                   "reason": "living style guide specimen of the live .reload-banner (new-version banner)"},
    # notices.py/routes_*.py: R-111's own ruling carved this out explicitly -- "when the
    # game cannot be reached, the surface must still not show a figure that is untrue...
    # that is an error state, not a freshness state." These are that error state's copy
    # not the deleted UI controls.
    "notices_reload": {"origin": "ceres/notices.py message", "value": _ANY_VALUE, "word": "reload",
                        "reason": "R-111: local-storage/error-state copy, not the retired board control"},
    "routes_error_copy": {"origin": "ceres/routes_*.py HTTPException detail", "value": _ANY_VALUE,
                           "word": None,
                           "reason": "R-111: board-freshness error-state copy, not the retired board control"},
}
REMOVED_CONTROL_LISTENER = re.compile(
    r"(?:getElementById\(\s*['\"](?:refresh-btn|capture-btn|full-capture-btn|capture-age|capture-progress)['\"]\s*\)"
    r"|querySelector\(\s*['\"]#(?:refresh-btn|capture-btn|full-capture-btn|capture-age|capture-progress)['\"]\s*\))"
    r"\s*\.addEventListener\s*\(",
)


class _DomSurface(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.ids: set[str] = set()
        self.classes: set[str] = set()
        self.visible: list[str] = []
        self.button_text: dict[str, list[str]] = {}
        self._button_stack: list[str] = []
        self._non_visible_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style"}:
            self._non_visible_depth += 1
            return
        if self._non_visible_depth:
            return
        values = dict(attrs)
        if values.get("id"):
            self.ids.add(str(values["id"]))
        self.classes.update(str(values.get("class") or "").split())
        for key in ("aria-label", "data-tip", "data-tip-body", "title", "placeholder", "alt"):
            if values.get(key):
                self.visible.append(str(values[key]))
        if tag == "button" and values.get("id"):
            self._button_stack.append(str(values["id"]))

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"}:
            self._non_visible_depth = max(0, self._non_visible_depth - 1)
            return
        if self._non_visible_depth:
            return
        if tag == "button" and self._button_stack:
            self._button_stack.pop()

    def handle_data(self, data: str) -> None:
        if self._non_visible_depth:
            return
        if self._button_stack and data.strip():
            self.button_text.setdefault(self._button_stack[-1], []).append(data)
        if data.strip():
            self.visible.append(data)


def _scan_markup(source: str) -> tuple[set[str], list[str]]:
    parser = _DomSurface()
    parser.feed(source)
    return parser.ids, parser.visible


def _iter_js_literals(source: str):
    index = 0
    while index < len(source):
        if source.startswith("//", index):
            newline = source.find("\n", index + 2)
            index = len(source) if newline < 0 else newline + 1
            continue
        if source.startswith("/*", index):
            end = source.find("*/", index + 2)
            index = len(source) if end < 0 else end + 2
            continue
        quote = source[index]
        if quote not in "'\"`":
            index += 1
            continue
        start = index
        index += 1
        body: list[str] = []
        while index < len(source):
            character = source[index]
            if character == "\\" and index + 1 < len(source):
                body.extend((character, source[index + 1]))
                index += 2
                continue
            if character == quote:
                yield start, quote, "".join(body), source[max(0, start - 180):start]
                index += 1
                break
            body.append(character)
            index += 1


def _js_literal_is_visible(body: str, before: str) -> bool:
    trimmed = body.strip()
    if not trimmed or trimmed.startswith(("/", "#", ".")):
        return False
    if re.fullmatch(r"[A-Za-z0-9_:/.-]+", trimmed) and not re.search(
        r"(?:textContent|innerHTML|aria-label|data-tip|data-tip-body|title|message|label|placeholder)\s*[:=]",
        before,
        re.IGNORECASE,
    ):
        return False
    if re.search(
        r"(?:getElementById|querySelector(?:All)?|className|classList|dataset|fetch|endpoint|selector|\b(?:id|code|state|action|mode|type|key)\b)\s*[^\n]{0,80}$",
        before,
        re.IGNORECASE,
    ):
        return False
    if re.search(
        r"(?:textContent|innerHTML|aria-label|data-tip(?:-body)?|title|message|label|placeholder)\s*[:=]"
        r"|(?:stageToast|noticeMessage|toast|alert|confirm)\s*\(",
        before,
        re.IGNORECASE,
    ):
        return True
    return " " in trimmed


def _visible_client_strings(source: str) -> list[str]:
    visible: list[str] = []
    for _start, quote, body, before in _iter_js_literals(source):
        if not _js_literal_is_visible(body, before):
            continue
        if quote == "`" and "<" in body:
            _ids, strings = _scan_markup(body)
            # a `${name}` interpolation is code, not copy -- 's `${refresh}` slot was read as the word.
            visible.extend(text for text in (re.sub(r"\$\{[^}]*\}", "", s).strip() for s in strings) if text)
        else:
            visible.append(body)
    return visible


def _string_constants(node: ast.AST) -> list[str]:
    return [child.value for child in ast.walk(node) if isinstance(child, ast.Constant) and isinstance(child.value, str)]


def _notice_messages_from_source() -> list[str]:
    tree = ast.parse((ROOT / "ceres" / "notices.py").read_text(encoding="utf-8"))
    messages: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        for key, value in zip(node.keys, node.values):
            if isinstance(key, ast.Constant) and key.value == "message":
                messages.extend(_string_constants(value))
    return messages


def _route_details_from_source() -> list[str]:
    details: list[str] = []
    for path in sorted(ROOT.glob("ceres/routes_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            function = node.func
            is_http_exception = isinstance(function, ast.Name) and function.id == "HTTPException"
            is_http_exception = is_http_exception or (
                isinstance(function, ast.Attribute) and function.attr == "HTTPException"
            )
            if not is_http_exception:
                continue
            detail = next((keyword.value for keyword in node.keywords if keyword.arg == "detail"), None)
            if detail is not None:
                details.extend(_string_constants(detail))
    return details


def _retired_control_findings() -> list[str]:
    html_source = (STATIC / "index.html").read_text(encoding="utf-8")
    dom_ids, html_visible = _scan_markup(html_source)
    findings: list[str] = []

    def exemption_reason(value: str, origin: str, word: str) -> str | None:
        for exemption in _RETIRED_CONTROL_EXEMPTIONS.values():
            expected_value = exemption["value"]
            if expected_value is _ANY_VALUE:
                value_matches = True
            elif isinstance(expected_value, str):
                value_matches = value == expected_value
            else:
                value_matches = value in expected_value
            expected_word = exemption["word"]
            word_matches = expected_word is None or word == expected_word
            if value_matches and origin.endswith(exemption["origin"]) and word_matches:
                return exemption["reason"]
        return None

    def collect(values: list[str], origin: str) -> None:
        for value in values:
            for match in RETIRED_CONTROL_WORDS.finditer(value):
                word = match.group(0).lower()
                if word == "reload" and "reload-btn" in dom_ids:
                    continue
                if exemption_reason(value, origin, word):
                    continue
                findings.append(f"{origin}: {value}")

    collect(html_visible, "index.html")
    for path in sorted(STATIC.glob("*.js")):
        collect(_visible_client_strings(path.read_text(encoding="utf-8")), str(path))
    # pill-preview.html is a dev-only catalogue page (a source exclusion, see test_ui_standard.py's
    # DEV_ONLY_PAGES); the public tree does not ship it, so it is scanned only where it exists.
    pill_preview_path = STATIC / "pill-preview.html"
    if pill_preview_path.is_file():
        pill_preview = pill_preview_path.read_text(encoding="utf-8")
        _pill_ids, pill_visible = _scan_markup(pill_preview)
        collect(pill_visible, "pill-preview.html")
        for script in re.findall(r"<script\b[^>]*>(.*?)</script>", pill_preview, re.DOTALL | re.IGNORECASE):
            collect(_visible_client_strings(script), "pill-preview.html")
    collect(_notice_messages_from_source(), "ceres/notices.py message")
    collect(_route_details_from_source(), "ceres/routes_*.py HTTPException detail")
    return findings


def _scheduled_board_rereads(source: str) -> list[str]:
    callback_reads = re.compile(
        r"\bset(?:Interval|Timeout)\s*\(\s*(?:"
        r"(?:async\s*)?(?:\([^)]*\)|[A-Za-z_$][\w$]*)\s*=>\s*(?:\{[^}]*?|[^,)]*?)"
        r"(?:loadBoard|readBoard|/api/(?:board|capture/refresh)\b)"
        r"|function\s*(?:[A-Za-z_$][\w$]*)?\s*\([^)]*\)\s*\{[^}]*?"
        r"(?:loadBoard|readBoard|/api/(?:board|capture/refresh)\b)"
        r"|(?:loadBoard|readBoard)\s*,"
        r")",
        re.DOTALL,
    )
    findings = [match.group(0) for match in callback_reads.finditer(source)]
    named_callbacks = {
        match.group(1)
        for match in re.finditer(
            r"\b(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*"
            r"(?:async\s*)?(?:\([^)]*\)|[A-Za-z_$][\w$]*)\s*=>\s*[^;]*?"
            r"(?:loadBoard|readBoard|/api/(?:board|capture/refresh)\b)",
            source,
            re.DOTALL,
        )
    }
    findings.extend(
        match.group(0)
        for match in re.finditer(
            r"\bset(?:Interval|Timeout)\s*\(\s*([A-Za-z_$][\w$]*)\s*,",
            source,
        )
        if match.group(1) in named_callbacks
    )
    return findings


def _removed_control_listener_findings() -> list[str]:
    findings: list[str] = []
    for path in sorted(STATIC.glob("*.js")):
        source = path.read_text(encoding="utf-8")
        findings.extend(f"{path}: {match.group(0)}" for match in REMOVED_CONTROL_LISTENER.finditer(source))
    return findings


def test_header_reload_removed_without_leaving_startup_listener() -> None:
    source = (STATIC / "index.html").read_text(encoding="utf-8")
    parser = _DomSurface()
    parser.feed(source)
    assert "reload-btn" not in parser.ids
    app = (STATIC / "app.js").read_text(encoding="utf-8")
    assert "onReload" not in app
    assert "getElementById('reload-btn')" not in app
    assert "loadBoard" in app
    assert "player-go-btn" in parser.ids


def test_user_visible_copy_does_not_name_an_absent_control() -> None:
    """Injection: reintroduce `Refresh board` in a rendered client string.

    Silent failure: a player is directed to a removed refresh/capture/staleness control.
    """
    assert not _retired_control_findings()
    source = (STATIC / "index.html").read_text(encoding="utf-8")
    broken = source.replace(
        'aria-label="Reload board. Reads every plot from the game again."',
        'aria-label="Refresh board"',
        1,
    )
    ids, visible = _scan_markup(broken)
    injected_findings = [
        value for value in visible
        if any(
            match.group(0).lower() != "reload" or "reload-btn" not in ids
            for match in RETIRED_CONTROL_WORDS.finditer(value)
        )
    ]
    with pytest.raises(AssertionError):
        assert not injected_findings


def test_removed_control_ids_have_no_bound_event_listeners() -> None:
    """Injection: bind a click listener directly to a retired control id.

    Silent failure: a reverted listener still targets an element that R-111 removed,
    so the handler can never be reached by the live page.
    """
    assert not _removed_control_listener_findings()
    source = read_entry_source()
    broken = source + "\ndocument.getElementById('refresh-btn').addEventListener('click', () => {});\n"
    injected_findings = list(_removed_control_listener_findings())
    injected_findings.extend(
        f"entry: {match.group(0)}"
        for match in REMOVED_CONTROL_LISTENER.finditer(broken)
    )
    with pytest.raises(AssertionError):
        assert not injected_findings


def test_board_client_has_no_automatic_board_reread_scheduler() -> None:
    """Injection: schedule `loadBoard()` or a board/capture endpoint from a timer.

    Silent failure: the board silently re-reads itself on a timer and violates the explicit-refresh model.
    This scope deliberately permits spinner timers, capture-status polling, tooltip timers, and header/UI ticks;
    only a timer that schedules a board read or capture refresh is a forbidden scheduler.
    """
    source = (STATIC / "board.js").read_text(encoding="utf-8")
    assert not _scheduled_board_rereads(source)
    assert not _scheduled_board_rereads("setTimeout(() => showSpinner(), 2000);\nloadBoard();")
    for timer in (
        "setInterval(() => loadBoard(), 60000);",
        "setInterval(() => { const x = 1; loadBoard(); }, 60000);",
        "setInterval(function () { loadBoard(); }, 60000);",
        "const tick = () => loadBoard();\nsetInterval(tick, 60000);",
    ):
        broken = source + f"\n{timer}\n"
        with pytest.raises(AssertionError):
            assert not _scheduled_board_rereads(broken)


def test_age_and_staleness_never_reach_the_screen_even_when_supplied() -> None:
    """Injection: render `BOARD.age_seconds` or `BOARD.stale` into the age element.

    Silent failure: an age, STALE marker, or age-driven class appears despite the simplified board contract.

    Uses `entry=STATIC / "table.js"` rather than the default `ENTRY` (app.js): commit
    42404f32 (v0.9.552) added regional-dec.js's own local, non-exported `function
    render(force = false)` (a Regional DEC drawer), and vm_context.py's flattener hoists
    same-named function declarations from every module into one shared scope (Annex B),
    so app.js's whole graph silently resolves the bare global `render` to that DIFFERENT
    function -- this guard's `render(false)` call was calling the wrong function entirely
    and could never catch anything. Scoping to table.js's own (smaller) import graph keeps
    the one `render` this guard needs -- table.js's board render pipeline -- reachable
    without the name collision.
    """
    body = r"""
document.documentElement.classList = { contains: () => false };
const rendered = [];
const tracked = () => {
  let text = '', html = '', className = '';
  return {
    get textContent() { return text; }, set textContent(value) { text = String(value); rendered.push(text); },
    get innerHTML() { return html; }, set innerHTML(value) { html = String(value); rendered.push(html); },
    get className() { return className; }, set className(value) { className = String(value); rendered.push(className); },
    hidden: false, setAttribute() {}, removeAttribute() {}, getAttribute() { return null; },
  };
};
const age = tracked();
const makeElement = () => {
  const element = tracked();
  return Object.assign(element, {value: '', checked: false, hidden: false, scrollLeft: 0,
    style: {}, options: [], firstChild: null,
    classList: {add() {}, remove() {}, toggle() {}, contains() { return false; }},
    appendChild() {}, insertBefore() {}, removeChild() {}, replaceChildren() {}, remove() {},
    querySelector() { return null; }, querySelectorAll() { return []; },
    getBoundingClientRect() { return {top: 0, bottom: 0, height: 0}; }});
};
document.getElementById = (id) => id === 'capture-age' ? age : makeElement();
fetch = async () => ({ok: true, json: async () => ({cards: []})});
BOARD = {
  age_seconds: 7200,
  stale: true,
  coverage: {retrieved: 5, expected: 5},
  plots: [],
  notices: [],
  totals: {base_pp: 10, harvest_pp: 20, dec_needed: 1, plots_idle: 0, plots: 1},
};
render(false);
if (rendered.some((value) => /\bage\b|capture:|\bstale\b|STALE/i.test(value))) throw new Error('age/staleness reached a rendered element');
"""
    table_entry = STATIC / "table.js"
    run_in_module_context(body, entry=table_entry)
    source = read_entry_source(table_entry)
    anchor = "  CURRENT_ROWS = sortRows(BOARD.plots.filter(matchesFilters));\n"
    assert source.count(anchor) == 1
    injected = source.replace(
        anchor,
        anchor + "  document.getElementById('capture-age').textContent = BOARD.stale ? 'STALE' : '';\n",
        1,
    )
    with pytest.raises(subprocess.CalledProcessError):
        run_in_module_context(body, entry=table_entry, source=injected)


def _synthetic_report(
    worker_count: int,
    powered_worker_count: int,
    *,
    runi: bool = False,
    include_worker_rows: bool = True,
    deed_uid: str = "R-111",
    power_core: bool = True,
) -> dict:
    report = build_report()
    plot = copy.deepcopy(report["plots"][0])
    observed_at = plot.get("observed_at")
    plot["deed_uid"] = deed_uid
    plot["staking_facts"].update({
        "worker_count": worker_count,
        "powered_worker_count": powered_worker_count,
        "max_workers_allowed": 5,
        "is_powered": power_core or runi,
        "is_power_core_staked": power_core,
        "is_energized": False,
        "is_runi_staked": runi,
    })
    plot["is_powered"] = True
    workers = []
    for index in range(worker_count):
        is_runi = runi and index == worker_count - 1
        workers.append({
            "asset_ref": f"r111-{index}",
            "stake_ref_uid": deed_uid,
            "stake_type_uid": "STK-LND-RUNI" if is_runi else "STK-LND-WKR",
            "asset_kind": "worker_card" if not is_runi else "runi",
            "slot": index + 1,
            "name": "Runi" if is_runi else f"Worker {index + 1}",
            "is_powered": is_runi or index < (powered_worker_count - 1 if runi else powered_worker_count),
            "total_harvest_pp": 100,
            "observed_at": observed_at,
            "land_abilities": [],
        })
    report["plots"] = [plot]
    report["workers"] = workers if include_worker_rows else []
    report["bonus_assets"] = []
    return report


@pytest.mark.parametrize(
    ("worker_count", "powered_worker_count", "runi", "expected"),
    [(5, 0, False, True), (5, 5, False, False), (4, 4, False, False), (1, 0, False, True), (6, 6, True, False)],
)
def test_unpowered_worker_badge_follows_bulk_power_pair(worker_count: int, powered_worker_count: int, runi: bool, expected: bool) -> None:
    """Injection: subtract the Runi or invert the bulk-pair comparison.

    Silent failure: a filled worker deficit is hidden, or a fully powered plot is falsely marked unpowered.
    """
    row = project_board(
        _synthetic_report(worker_count, powered_worker_count, runi=runi),
        build_details_by_id(),
    )["plots"][0]
    assert ("unpowered_worker" in row["badges"]) is expected


def test_unpowered_worker_badge_counts_a_staked_runi_in_both_bulk_halves() -> None:
    """/ (maintainer ruling 2026-09-15,  gate): a worksite's powered-slot
    count is no longer read off the bulk `powered_worker_count` pair at all -- it is
    derived structurally from `is_power_core_staked`/`is_energized`/`is_runi_staked`
    (a power core staked always powers all 5 regular slots; per his words "a Runi
    without a Power Core deactivates the 5th slot"). A power-core-staked 5/4 pair is
    therefore genuinely fully powered now (this guard's old 5/4-with-power-core
    fixture no longer represents a deficit), so the guard moved to the scenario 
    actually describes: a Runi WITHOUT a Power Core only powers 4 of 5 regular slots.

    Injection: make `powered_worker_slots()` treat a Runi-only plot as fully powered.

    Silent failure: a Runi-only-powered worksite with all 5 regular slots staked is
    misread as fully powered and loses its 5th-slot warning.
    """
    report = _synthetic_report(6, 4, runi=True, power_core=False, include_worker_rows=False)
    row = project_board(report, build_details_by_id())["plots"][0]
    assert row["capacity"]["worker_count"] == 6
    assert row["capacity"]["runi_staked"] is True
    assert row["capacity"]["powered_slots"] == 4
    assert report["workers"] == []
    assert "unpowered_worker" in row["badges"]


def test_i296_all_powered_workers_do_not_become_unpowered_from_zero_capacity() -> None:
    """Injection: compare worker capacity or plot `is_powered` instead of the bulk worker pair.

    Silent failure: the I-296 counter-example gets a false warning even though all five workers are powered and harvesting.
    """
    report = _synthetic_report(5, 5, deed_uid="I-296-b0dbc1ebc5ef75")
    plot = report["plots"][0]
    plot["staking_facts"].update({"max_workers_allowed": 0, "is_powered": False})
    plot["is_powered"] = False
    row = project_board(report, build_details_by_id())["plots"][0]
    assert all(worker["is_powered"] is True and worker["total_harvest_pp"] > 0 for worker in report["workers"])
    assert "unpowered_worker" not in row["badges"]


def _combine_report(generated_at: str) -> dict:
    return {
        "generated_at": generated_at,
        "plots": [{
            "deed_uid": "deed-1",
            "facts": {"plot": "deed-1", "rarity": "common", "status": "natural", "resource": "GRAIN", "territory": "T", "worksite_type": "Grain Farm"},
            "worksite_facts": {"project_type": "worksite_grain"},
            "staking_facts": {"max_workers_allowed": 5, "worker_count": 1, "is_power_core_staked": True, "is_energized": False, "is_runi_staked": False},
        }],
        "workers": [{
            "asset_ref": "old-ref", "uid": "CARD-OLD", "stake_type_uid": "STK-LND-WKR",
            "stake_ref_uid": "deed-1", "slot": 1, "asset_kind": "worker_card", "name": "Old",
            "delegated_to": None, "rental_type": None,
        }],
        "cards": [{"asset_ref": "new-ref", "uid": "CARD-NEW", "facts": {"uid": "CARD-NEW", "player": "player0005", "market_id": None, "delegated_to": None, "rental_type": None, "unlock_date": None}}],
        "bonus_assets": [],
        "unassigned_items": {},
    }


def _request(config: CeresConfig) -> SimpleNamespace:
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(ceres_config=config)))


def test_pre_sign_combine_staleness_gate_still_refuses_old_board(tmp_path, monkeypatch) -> None:
    """Injection: remove the `combine_verified_at` freshness gate from `_preview`.

    Silent failure: a survivor can be staged from a board read older than its combine, creating a stale transaction preview.
    """
    config = CeresConfig(root=tmp_path / ".ceres", player="player0005")
    stale = _combine_report("2026-08-29T00:59:59Z")
    monkeypatch.setattr(routes_op_preview.capture_mod, "latest_snapshot", lambda _config, _player: (stale, object()))
    body = {
        "moves": [{"group_id": "worker", "slot_index": 0, "action": "replace", "asset_ref": "new-ref", "evict_asset_ref": "old-ref"}],
        "capture_generated_at": stale["generated_at"],
        "combine_verified_at": "2026-08-29T01:00:00Z",
    }
    with pytest.raises(HTTPException) as raised:
        routes_op_preview.plot_op_preview("deed-1", body, _request(config))
    assert raised.value.status_code == 409
    assert "reload the board" in str(raised.value.detail).lower()


def test_combine_verification_performs_the_post_combine_collection_read(tmp_path, monkeypatch) -> None:
    """Injection: remove the live collection fetch supplied to `CardVerificationSpec`.

    Silent failure: a successful combine cannot verify against post-combine collection state and restaking remains blocked until manual reload.
    """
    config = CeresConfig(root=tmp_path / ".ceres", player="player0005")
    reads: list[str] = []

    class FakeClient:
        def collection(self, account: str):
            reads.append(account)
            return {"cards": [{"uid": "CARD-SURVIVOR"}]}

    class FakeCardVerificationSpec:
        def __init__(self, fetch_collection, *, expected_records):
            self.fetch_collection = fetch_collection
            self.expected_records = expected_records

        def check_domain(self, _operation_name, _operations):
            self.fetch_collection()
            return {"status": "domain_verified", "evidence": {}}

    monkeypatch.setattr(routes_op_preview, "SplinterlandsClient", FakeClient)
    monkeypatch.setattr(routes_op_preview, "CardVerificationSpec", FakeCardVerificationSpec)
    monkeypatch.setattr(routes_op_preview, "verify_chain", lambda _expected, _tx_id: {"status": "chain_verified", "evidence": {}})
    monkeypatch.setattr(routes_op_preview.capture_mod, "latest_snapshot", lambda _config, _player: (_combine_report("2026-08-29T01:00:00Z"), object()))
    monkeypatch.setattr(routes_op_preview.capture_mod, "is_running", lambda _account: False)
    monkeypatch.setattr(routes_op_preview.capture_mod, "start_refresh", lambda _config, _account: True)
    monkeypatch.setattr(routes_op_preview.combine_evidence, "load", lambda _config, _nonce, account: None)

    response = routes_op_preview.op_verify(
        {
            "tx_id": "not-a-game-lookup-id",
            "operations": [["custom_json", {
                "id": "sm_combine_cards",
                "required_auths": [],
                "required_posting_auths": ["player0005"],
                "json": '{"cards":["CARD-A","CARD-B"],"app":"splinterlands","n":"nonce"}',
            }]],
        },
        _request(config),
    )
    assert response.status_code == 200
    assert reads == ["player0005"]


def test_no_age_renderer_or_stale_marker_is_left_in_visible_client_copy() -> None:
    """Injection: restore an age/STALE rendering literal in any client module.

    Silent failure: source review misses a user-visible age or staleness marker because only a checklist was updated.

    Scans each real `ceres/static/*.js` file's own source rather than
    `read_entry_source()`'s flattened whole-graph text: that flattener concatenates every
    module's scoped source plus its own generated `Object.defineProperties(globalThis,
    {...})` glue (see vm_context.py), and `_iter_js_literals`'s single-pass quote-matching
    loses sync across a module boundary in that generated text -- it was captured
    mid-descriptor (`"stagedWorkersForPlot"`'s own closing quote misread as an opening
    one) and swallowed hundreds of unrelated lines, including table.js's own `// ... a
    stale\n// hardcoded constant ...` comment, as one giant fake "visible string". That
    comment was never client copy to begin with. Per-file scanning is both correct (no
    generated glue in the input) and simpler, and still catches a genuine reintroduced
    literal in any one module (`transaction-queue.js`'s own unrelated "stale" use, per
    /, is the one real match and is exempted the same way the sibling
    retired-control guard exempts it).
    """
    findings = []
    for path in sorted(STATIC.glob("*.js")):
        for value in _visible_client_strings(path.read_text(encoding="utf-8")):
            if path.name == "transaction-queue.js" and "stale" in value.lower():
                continue  # live Hive head freshness for signing, not board capture
            if re.search(r"\bage\b|\bSTALE\b|capture:\s*\$|capture:\s*\d", value, re.IGNORECASE):
                findings.append(f"{path.name}: {value}")
    assert not findings
