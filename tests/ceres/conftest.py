"""Shared fixtures for the Ceres test package.

The `report`/`details_by_id` fixtures below are all offline -- reuses the same real
Land fixtures Praetoria's own tests use (`tests/fixtures/land/`), built the same way
`tests/test_land_phase2.py` does.

`spawn_live_ceres` runs the real Ceres CLI and Chromium against recorded upstream
responses, with private storage and no external network. The historical name and
shared `(Browser, base_url)` API are retained. Callers select the fixture account
explicitly with `?player=sample01` and choose a viewport for the surface they test.
The recorded set is synthetic and generated: tests/ceres/board_basic_recording.py.
The server and browser are always torn down, including on bootstrap failure.
"""

from __future__ import annotations

import atexit
import contextlib
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from datetime import datetime, timezone
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _capture_in_process(monkeypatch):
    """ (2026-09-14): the board read runs in a worker child by default, where a
    monkeypatched `LandCapture` cannot reach. Every test keeps the in-process thread path
    (`CERES_CAPTURE_IN_PROCESS=1`); `test_capture_worker.py` opts back out."""
    monkeypatch.setenv("CERES_CAPTURE_IN_PROCESS", "1")
    # Q5: a Ceres a test spawns never removes files from the real praetoria cache.
    monkeypatch.setenv("CERES_NO_STORED_DATA_CLEANUP", "1")


@pytest.fixture(autouse=True)
def _fresh_routing_run_memory(monkeypatch):
    """: routing runs live in server memory only; every test starts with none."""
    from ceres import routing_runs_v2
    monkeypatch.setenv("CERES_LEGACY_ROUTING", "1")   # the old v1 routing routes are off in production; their own tests turn them on (the gate test turns them off)
    routing_runs_v2._RUNS.clear()
    yield
    routing_runs_v2._RUNS.clear()


@pytest.fixture(autouse=True)
def _no_live_sign_read(monkeypatch):
    """ (2026-09-28): an operation preview reads every staged plot live from the game
    (`capture.read_plots_live`). Tests never reach the game, so by default that read passes the
    test's own fixture board through unchanged; `test_sign_live_read.py` tests the real reader."""
    import ceres.capture as capture_mod
    monkeypatch.setattr(capture_mod, "read_plots_live", lambda config, player, deed_uids, base_report: {
        "report": base_report, "failed": [], "read": sorted(str(uid) for uid in deed_uids)})

    def _no_game(player, deed_uid, plot_id):
        raise RuntimeError("tests never reach the game")

    # the picker's on-demand live slot read (routes_candidates.with_live_slots) -- unanswered in tests
    # so a fixture board is priced exactly as it is; test_live_slots.py tests the read itself.
    monkeypatch.setattr(capture_mod, "read_plot_live", _no_game)


@pytest.fixture(autouse=True)
def _clear_projection_caches():
    """ (2026-09-07): `ceres.candidates` and `ceres.board` memoise per-snapshot work
    (eligible picker rows, per-card scores, board rows). The keys are object identities
    pinned by the cached values, so two live snapshots never collide -- but a fixture that
    is mutated in place between two calls IS the same object, and a test doing that would
    read its own earlier answer. Every test starts with empty caches; production never
    calls this."""
    from ceres import board as board_mod
    from ceres import candidates as candidates_mod
    candidates_mod.clear_candidate_caches()
    with board_mod._plot_row_memo_lock:
        board_mod._plot_row_memo_entries.clear()
    yield
    candidates_mod.clear_candidate_caches()
    with board_mod._plot_row_memo_lock:
        board_mod._plot_row_memo_entries.clear()

import pytest
from playwright.sync_api import sync_playwright

from praetoria.land import analyze_bundle

LAND_ROOT = Path(__file__).parent.parent / "fixtures" / "land"
AS_OF = datetime(2026, 8, 9, tzinfo=timezone.utc)
PROJECT_ROOT = Path(__file__).resolve().parents[2]

_IGNORED_ASSET_DIRS = (
    Path("ceres/static/assets/land/terrain-backgrounds"),
    Path("ceres/static/assets/land/worksites"),
    Path("ceres/static/assets/land/deeds-surveyed"),
    Path("ceres/static/assets/land/constructions"),
    Path("ceres/static/assets/cards"),
    Path("ceres/static/assets/ui"),
    Path("ceres/static/assets/thumbs"),
)


def _main_checkout_root(project_root: Path = PROJECT_ROOT) -> Path:
    output = subprocess.check_output(
        ["git", "rev-parse", "--git-common-dir"],
        cwd=project_root,
        text=True,
    ).strip()
    common_dir = Path(output)
    if not common_dir.is_absolute():
        common_dir = project_root / common_dir
    common_dir = common_dir.resolve()
    if common_dir.name != ".git":
        raise RuntimeError(f"Git common directory is not a checkout .git directory: {common_dir}")
    return common_dir.parent


def _ensure_worktree_asset_links(project_root: Path = PROJECT_ROOT) -> tuple[Path, ...]:
    project_root = project_root.resolve()
    main_checkout = _main_checkout_root(project_root)
    if main_checkout == project_root:
        return ()

    linked: list[Path] = []
    for relative in _IGNORED_ASSET_DIRS:
        destination = project_root / relative
        if destination.exists() or destination.is_symlink():
            continue
        source = main_checkout / relative
        if not source.is_dir():
            continue
        ignored = subprocess.run(
            ["git", "check-ignore", "--quiet", "--no-index", "--", str(relative)],
            cwd=project_root,
            check=False,
        )
        if ignored.returncode != 0:
            raise RuntimeError(f"Refusing to link a non-ignored asset path: {relative}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            destination.symlink_to(source, target_is_directory=True)
        except FileExistsError:
            continue  # a parallel (xdist) worker linked it between the check above and here
        linked.append(destination)
    return tuple(linked)


@pytest.fixture(scope="session", autouse=True)
def _worktree_asset_links() -> None:
    _ensure_worktree_asset_links()

_TESTCLIENT_SOCKET_EXCLUSION = "CERES_TESTCLIENT_SOCKET_EXCLUSION"
_TESTCLIENT_SOCKET_REASON = (
    f"{_TESTCLIENT_SOCKET_EXCLUSION}: excluded because this environment cannot create "
    "the socket or complete the FastAPI TestClient/AnyIO portal probe; rerun on a "
    "socket-capable machine"
)
_TESTCLIENT_PORTAL_PROBE_TIMEOUT = 30
_TESTCLIENT_PORTAL_PROBE_ATTEMPTS = 2
_TESTCLIENT_PROBE_ELAPSED = 0.0
_TESTCLIENT_INIT = None
_TESTCLIENT_EXCLUDED_NODEIDS: list[str] = []
_TESTCLIENT_SOCKET_CAPABLE = True


_TESTCLIENT_PORTAL_PROBE = """
from fastapi import FastAPI
from fastapi.testclient import TestClient

app = FastAPI()

@app.get('/')
def health():
    return {'status': 'ok'}

with TestClient(app) as client:
    assert client.get('/').json() == {'status': 'ok'}
"""


def _run_testclient_portal_probe() -> None:
    subprocess.run(
        [sys.executable, "-c", _TESTCLIENT_PORTAL_PROBE],
        stdin=subprocess.DEVNULL,
        check=True,
        timeout=_TESTCLIENT_PORTAL_PROBE_TIMEOUT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _probe_testclient_socket(socket_factory=socket.socket) -> bool:
    try:
        sock = socket_factory()
    except OSError:
        return False
    sock.close()
    return True


def _probe_testclient_capability(socket_factory=socket.socket, portal_probe=None) -> bool:
    global _TESTCLIENT_PROBE_ELAPSED
    started = time.monotonic()
    try:
        if not _probe_testclient_socket(socket_factory):
            return False
        if portal_probe is None:
            portal_probe = _run_testclient_portal_probe
        for _ in range(_TESTCLIENT_PORTAL_PROBE_ATTEMPTS):
            try:
                portal_probe()
            except (OSError, TimeoutError, subprocess.SubprocessError):
                continue
            return True
        return False
    finally:
        _TESTCLIENT_PROBE_ELAPSED = time.monotonic() - started


def _testclient_socket_reason() -> str:
    budget = _TESTCLIENT_PORTAL_PROBE_TIMEOUT * _TESTCLIENT_PORTAL_PROBE_ATTEMPTS
    return (
        f"{_TESTCLIENT_SOCKET_REASON}; probe took {_TESTCLIENT_PROBE_ELAPSED:.1f} s "
        f"of {budget} s ({_TESTCLIENT_PORTAL_PROBE_TIMEOUT} s per attempt)"
    )


def require_testclient_socket() -> None:
    if not _TESTCLIENT_SOCKET_CAPABLE:
        pytest.skip(_testclient_socket_reason())


def _testclient_init_for_capability(original_init, *, capable: bool):
    if capable:
        return original_init
    def guarded_init(self, *args, **kwargs):
        pytest.skip(_testclient_socket_reason())

    return guarded_init


def _install_testclient_capability_guard(testclient_type, *, capable: bool) -> None:
    global _TESTCLIENT_INIT
    if capable or _TESTCLIENT_INIT is not None:
        return
    _TESTCLIENT_INIT = testclient_type.__init__
    testclient_type.__init__ = _testclient_init_for_capability(_TESTCLIENT_INIT, capable=capable)


def pytest_configure(config):
    global _TESTCLIENT_SOCKET_CAPABLE
    from fastapi.testclient import TestClient

    capable = _probe_testclient_capability()
    _TESTCLIENT_SOCKET_CAPABLE = capable
    config._ceres_testclient_socket_capable = capable
    _install_testclient_capability_guard(TestClient, capable=capable)


def pytest_unconfigure(config):
    global _TESTCLIENT_INIT, _TESTCLIENT_SOCKET_CAPABLE
    if _TESTCLIENT_INIT is not None:
        from fastapi.testclient import TestClient

        TestClient.__init__ = _TESTCLIENT_INIT
    _TESTCLIENT_INIT = None
    _TESTCLIENT_SOCKET_CAPABLE = True


def pytest_runtest_logreport(report):
    if report.outcome != "skipped" or _TESTCLIENT_SOCKET_EXCLUSION not in str(report.longrepr):
        return
    if report.nodeid not in _TESTCLIENT_EXCLUDED_NODEIDS:
        _TESTCLIENT_EXCLUDED_NODEIDS.append(report.nodeid)


def pytest_terminal_summary(terminalreporter):
    if not _TESTCLIENT_EXCLUDED_NODEIDS:
        return
    terminalreporter.write_sep("-", "Ceres capability exclusions")
    terminalreporter.write_line(
        f"{len(_TESTCLIENT_EXCLUDED_NODEIDS)} tests excluded: "
        "FastAPI TestClient socket/AnyIO portal capability is unavailable in this environment."
    )
    for nodeid in _TESTCLIENT_EXCLUDED_NODEIDS:
        terminalreporter.write_line(f"excluded: {nodeid}")


def land_fixture(name: str):
    return json.loads((LAND_ROOT / name).read_text(encoding="utf-8"))


def build_report(assets: dict | None = None, *, include_card_names: bool = True) -> dict:
    if assets is None:
        assets = {}
        for name in ("assets_active_above_cap.json", "assets_active_single_worker.json", "assets_active_with_runi.json"):
            data = land_fixture(name)["data"]
            if data["cards"]:
                assets[data["cards"][0]["stake_ref_uid"]] = data
        bundle = land_fixture("deeds_multi.json")
        for deed in bundle["data"]["deeds"]:
            uid = str(deed.get("deed_uid"))
            # Full asset coverage (2026-08-21 bulk-first redesign): only 3 of the bundle's 11
            # deeds have a dedicated worker-carrying fixture file above; the rest get an empty
            # (but RETRIEVED) `{cards: [], items: []}` response so `coverage.assets.complete`
            # is genuinely True here -- this fixture represents a completed full capture (the
            # scenario idle-card classification and the ability-holder tests below assume)
            # not a partial/quick one. `ceres.board.project_board` now gates idle_cards and the
            # unassigned-bonus runi/land_card families on that coverage flag, per the owner's
            # correction that the default board load is bulk-only and must not present
            # unmeasured per-worker data as a confident answer.
            assets.setdefault(uid, {"cards": [], "items": []})
    else:
        bundle = land_fixture("deeds_multi.json")
    details = land_fixture("card_details_sample.json")
    kwargs = {
        "collection_payload": land_fixture("collection_sample.json"),
        "as_of": AS_OF,
    }
    if include_card_names:
        kwargs["card_names"] = {item["id"]: item["name"] for item in details if item.get("id") is not None and item.get("name")}
    return analyze_bundle(
        "player0005",
        bundle,
        assets,
        **kwargs,
    )


def build_report_quick() -> dict:
    healthy = build_report()
    quick = build_report(assets={}, include_card_names=False)
    quick["plots"] = healthy["plots"]
    quick["coverage"] = healthy["coverage"]
    return quick


def build_details_by_id() -> dict[int, dict]:
    details = land_fixture("card_details_sample.json")
    return {item["id"]: item for item in details if "id" in item}


def wait_for_board(page) -> None:
    try:
        page.wait_for_function(
            """() => { const s = window.__ceresReady;
              return !!s && s.page === 'board' && s.board === 'full' && s.ready === true
                && document.querySelector('#plot-tbody > tr.plot-row') !== null;
            }""",
            timeout=30_000,
        )
    except Exception as exc:
        state = page.evaluate("""() => ({
          url: location.href, ready: window.__ceresReady,
          rows: document.querySelectorAll('#plot-tbody > tr.plot-row').length,
          body: document.body.innerText.slice(0, 1000),
          resources: performance.getEntriesByType('resource').map(r => r.name).filter(x => x.includes('/api/'))
        })""")
        raise AssertionError(f"Board did not become ready: {json.dumps(state, default=str)}; "
                             f"browser: {getattr(page, '_ceres_diagnostics', [])}") from exc


@pytest.fixture
def report() -> dict:
    return build_report()


@pytest.fixture
def report_quick() -> dict:
    return build_report_quick()


@pytest.fixture
def details_by_id() -> dict[int, dict]:
    return build_details_by_id()


LIVE_HOST = "127.0.0.1"
LIVE_PLAYER = "sample01"
LIVE_FIXTURES = PROJECT_ROOT / "tests/fixtures/upstream/board-basic"


def _free_port() -> int:
    require_testclient_socket()
    with socket.socket() as sock:
        sock.bind((LIVE_HOST, 0))
        return int(sock.getsockname()[1])


# 28 of 111 test files each spawned their OWN Ceres server and Chromium via a
# module-scoped fixture. Measured cost: 978 seconds across 79 files, with single files
# reaching 97s. A suite nobody can run to completion stops being run -- roughly 20 files
# were failing on master unnoticed for exactly that reason.
# The spawn is now memoised for the process: the first caller starts a server and a
# browser, every later caller receives the same pair, and teardown happens once at exit.
# The contextmanager API is unchanged so no call site needed editing.
# Set CERES_TEST_ISOLATED_SERVER=1 to opt a run out and get the old per-caller behaviour
# for a test that genuinely needs a server with no other test's state on it.
_SHARED_LIVE: dict = {}


def _shared_live_teardown() -> None:
    entry = _SHARED_LIVE.pop("value", None)
    if entry is None:
        return
    for close in reversed(entry["closers"]):
        try:
            close()
        except Exception:  # teardown must not mask a test failure
            pass


atexit.register(_shared_live_teardown)


def _prime_fixture_plots(base_url: str, plots=None) -> None:
    """Give each caller slot-aware recorded workers, even after a prior page reload.

    The server is shared, but a page's quick capture invalidates earlier detail
    patches. Inspecting only the initial warmup would make later tests depend on
    which plot the previous test happened to expand.
    """
    from tests.ceres import board_basic_recording as recording

    with urllib.request.urlopen(f"{base_url}/api/board", timeout=15) as response:
        loaded = {p["deed_uid"] for p in json.load(response)["plots"] if p.get("assets_loaded")}
    for plot in recording.PRIMED if plots is None else plots:
        deed_uid = plot.uid
        if deed_uid in loaded:
            continue
        request = urllib.request.Request(
            f"{base_url}/api/plots/{deed_uid}/expand", data=b"", method="POST",
        )
        with urllib.request.urlopen(request, timeout=15) as response:
            row = json.load(response)
            if plot.workers and not any(isinstance(w.get("slot"), int) and w["slot"] >= 1 for w in row.get("workers", [])):
                raise RuntimeError(f"Fixture plot has no slot-aware workers: {deed_uid}")


def prime_all_fixture_plots(base_url: str) -> None:
    """Read every recorded plot's assets, so the board's asset coverage is complete and the idle-card
    pool loads (the app lists idle cards only when every plot was read). About one second per plot:
    call it only from a test that needs the idle pool, after its last page load."""
    from tests.ceres import board_basic_recording as recording

    _prime_fixture_plots(base_url, recording.PLOTS)


@contextlib.contextmanager
def spawn_live_ceres(*, host: str = LIVE_HOST):
    """Yield a shared `(Browser, base_url)` for the whole test session.

    See the  note above for why this is memoised rather than per-caller."""
    if os.environ.get("CERES_TEST_ISOLATED_SERVER") == "1":
        with _spawn_live_ceres_isolated(host=host) as value:
            yield value
        return
    cached = _SHARED_LIVE.get("value")
    if cached is None:
        stack = contextlib.ExitStack()
        value = stack.enter_context(_spawn_live_ceres_isolated(host=host))
        _SHARED_LIVE["value"] = {"value": value, "closers": [stack.close]}
        cached = _SHARED_LIVE["value"]
    else:
        _prime_fixture_plots(cached["value"][1])
    yield cached["value"]


@contextlib.contextmanager
def standalone_browser():
    """Yield a Chromium `Browser` for tests that only render `set_content` markup.

    : while the session-shared live browser above is running, its sync Playwright owns this thread's
    event loop, and a second `sync_playwright()` raises "using Playwright Sync API inside the asyncio loop" --
    so a set_content test failed whenever any live-board module happened to run before it. Reuse that browser
    when it exists; otherwise start (and stop) a private one."""
    cached = _SHARED_LIVE.get("value")
    if cached is not None:
        yield cached["value"][0]
        return
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            yield browser
        finally:
            browser.close()


@contextlib.contextmanager
def _spawn_live_ceres_isolated(*, host: str = LIVE_HOST, log_directory: Path | None = None):
    """Run the Ceres CLI through the offline socket guard on a free port and yield
    `(Browser, base_url)`. The child process is terminated in a `finally` that wraps
    everything from the `Popen` call onward -- including the startup-wait loop and the
    Playwright session -- so it is reaped whether the caller's block succeeds, raises,
    or never runs at all (e.g. a startup timeout)."""
    port = _free_port()
    with contextlib.ExitStack() as stack:
        home = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix="ceres-test-home-")))
        if log_directory is None:
            log_directory = home / "logs"
        output_file = stack.enter_context((home / "server.stdout").open("w+", encoding="utf-8"))
        child_env = os.environ.copy()
        child_env.update({
            "HOME": str(home),
            "PYTHONPATH": os.pathsep.join(p for p in sys.path if p),
            "TMPDIR": str(home),
            "CERES_TEST_LOG_DIR": str(log_directory),
            "CERES_FIXTURE_UPSTREAM": str(LIVE_FIXTURES),
            "CERES_FIXTURE_OVERLAYS": "browser-combine",
            "PRAETORIA_CACHE_DIR": str(home / "ceres-fixture-cache-private"),
            "CERES_NO_STORED_DATA_CLEANUP": "1",
            "CERES_CAPTURE_IN_PROCESS": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
        })
        child_env.pop("CERES_DEV_WATCH", None)
        # LOCAL INVARIANT: The child's stdout must be drained or the server blocks once its output exceeds the pipe buffer.
        process = subprocess.Popen(
            [sys.executable, "-m", "tests.ceres.offline_server", "--host", host, "--port", str(port),
             "--no-reload-watch", "--player", LIVE_PLAYER],
            cwd=PROJECT_ROOT,
            stdout=output_file,
            stderr=subprocess.STDOUT,
            text=True,
            env=child_env,
        )
        try:
            base_url = f"http://{host}:{port}"
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline and process.poll() is None:  # an exited server never answers
                try:
                    with urllib.request.urlopen(f"{base_url}/api/health", timeout=2) as response:
                        if response.status == 200:
                            break
                except OSError:
                    time.sleep(0.25)
            else:
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                output_file.seek(0)
                output = output_file.read()
                raise RuntimeError(f"Ceres did not start:\n{output}")

            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(ignore_default_args=['--hide-scrollbars'])
                from tests.ceres.fixture_live import install_routes

                # Install on every new context/page before navigation. Page-level mocks
                # in individual tests still take precedence over these offline routes.
                new_context, new_page = browser.new_context, browser.new_page

                def guard_context(context):
                    install_routes(context, LIVE_FIXTURES, overlays=["browser-combine"])
                    # No connect_to_server(): every WebSocket is local and inert.
                    # Closing from this synchronous callback deadlocks Playwright.
                    context.route_web_socket("**/*", lambda ws: None)

                def offline_context(*args, **kwargs):
                    kwargs["service_workers"] = "block"
                    context = new_context(*args, **kwargs)
                    guard_context(context)
                    return context

                def offline_page(*args, **kwargs):
                    kwargs["service_workers"] = "block"
                    page = new_page(*args, **kwargs)
                    guard_context(page.context)
                    diagnostics = []
                    page._ceres_diagnostics = diagnostics
                    page.on("console", lambda message: diagnostics.append(f"console {message.type}: {message.text}") if message.type == "error" else None)
                    page.on("pageerror", lambda error: diagnostics.append(f"pageerror: {error}"))
                    page.on("requestfailed", lambda request: diagnostics.append(f"requestfailed: {request.url}: {request.failure}"))
                    page.on("response", lambda response: diagnostics.append(f"HTTP {response.status}: {response.url}") if response.status >= 400 else None)

                    # The fixture server's --player is an API default; the app reads the
                    # account from the URL. Bare-origin board tests need that query too.
                    original_goto = page.goto

                    def fixture_board_goto(url, *goto_args, **goto_kwargs):
                        parts = urlsplit(url)
                        if parts.netloc == urlsplit(base_url).netloc and not parts.path:
                            query = parse_qsl(parts.query, keep_blank_values=True)
                            if not any(key == "player" for key, _ in query):
                                query.append(("player", LIVE_PLAYER))
                                url = urlunsplit((parts.scheme, parts.netloc, "/", urlencode(query), parts.fragment))
                        return original_goto(url, *goto_args, **goto_kwargs)

                    page.goto = fixture_board_goto
                    return page

                browser.new_context = offline_context
                browser.new_page = offline_page
                try:
                    # API-based tests need a captured board before creating their own
                    # page. Exercise the real load pipeline against recorded upstream.
                    page = browser.new_page(viewport={"width": 1600, "height": 1000})
                    errors = []
                    page.on("pageerror", lambda error: errors.append(str(error)))
                    page.on("console", lambda message: errors.append(message.text) if message.type == "error" else None)
                    try:
                        page.goto(f"{base_url}/?player={LIVE_PLAYER}", wait_until="domcontentloaded")
                        page.wait_for_selector("#plot-tbody > tr.plot-row", timeout=30_000)
                    except Exception as exc:
                        output_file.seek(0)
                        raise RuntimeError(f"Fixture board did not load: {errors}\n{page.locator('body').inner_text()[:3000]}\n{output_file.read()}") from exc
                    finally:
                        page.close()
                    _prime_fixture_plots(base_url)
                    yield browser, base_url
                finally:
                    browser.close()
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
