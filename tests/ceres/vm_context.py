"""Shared `vm.runInNewContext` sandbox for whole-module Ceres frontend loaders.

 (campaign plan `ceres-modularisation-campaign.md` revision 3,
§15 ruling 6b; census `work/modularisation/TEST-CENSUS.md` §5, "candidate
precedent for the hard cases"). Every test that needs the *real* frontend
module fully evaluated -- not string-sliced, not hand-stubbed per test --
builds its sandbox here instead of writing its own `context` object. Before
this helper existed, `test_f0da_frontend_base_badge_truth.py`'s
`test_g6_unknown_base_badge_is_reason_bearing` built its own context missing
`window`, and was RED on exactly that gap (`ReferenceError: window is not
defined` at `ceres/static/app.js:2891`,
``const OPERATION_POLL_SCHEDULE_MS = window.__ceresOperationPollSchedule ||
...``, a module-scope statement that runs the instant the file is evaluated,
before any function is called). Fixed here so it is fixed once.

`ENTRY` is the one line every caller must update as the split lands:
`ceres/static/app.js` today; the campaign plan (§15 ruling 6a) says it flips
to `ceres/static/staging.js` at M3 (once `staging.js` exists and owns the
functions this precedent covers) and to `ceres/static/operations.js` at M7 --
never both at once, and never guessed ahead of the move it depends on.

** (Step C) supersedes ruling 6a.** The cut landed: `app.js`
is now a 255-line entry that `import`s 19 sibling modules, and a whole-module
test needs functions that live in those siblings (e.g. `initTooltips`, now in
`tooltip.js`) -- flipping `ENTRY` to any single module can never serve a test
that reaches across the graph, because the other 18 modules' declarations
would simply not exist in the sandbox. So `read_entry_source()` no longer
reads one file: it walks the *whole* import graph reachable from `ENTRY`
(still the graph's root -- callers do not change `ENTRY` or the function
signature) in native ES-module evaluation order -- post-order DFS over each
module's `import ... from './x.js'` edges, entry last, exactly the order
`work/modularisation/tooling/verify_split.py::esm_evaluation_order` prints
for this same graph -- scopes every module separately after stripping its
generated `import` lines and `export ` prefixes (see `_scope_module_source`
below), and returns that as one script. Exported and uniquely named private
bindings are published as live VM-global properties so guard bodies can use
them without flattening unrelated module-local declarations into one scope.

## What the sandbox stubs, and why

- **`window`** -- `addEventListener`/`removeEventListener`/`dispatchEvent`
  (recording listeners, not dispatching them -- nothing here drives a real
  event loop), `localStorage`/`sessionStorage` (see below, aliased onto
  `window` the way the module reads them), `location`, `innerWidth`/
  `innerHeight` (full-HD, matching the project's viewport convention),
  `matchMedia` (always `matches: false`), `requestAnimationFrame`/
  `cancelAnimationFrame` (via `setTimeout`/`clearTimeout`), `fetch` (rejects
  loudly -- a whole-module load must never make a real network call; a test
  that needs `fetch` behaviour belongs in the live-browser harness, not here),
  `getComputedStyle` (returns an empty `getPropertyValue`), and the
  `__ceresOperationPollSchedule` override point the module itself reads.
- **`document`** -- `readyState: 'loading'` (so the module's own `ready()`
  gate never fires synchronously during load -- callers that need it to fire
  drive `listeners['doc:DOMContentLoaded']` themselves), `addEventListener`/
  `removeEventListener`, `createElement` (a bare style/classList/attribute
  stand-in), `querySelector`/`querySelectorAll` (empty), `getElementById`
  (`null` -- added for  queue F4, `test_frontend_removal_impact.py`:
  real functions like `stageToast`/`renderCheckoutSurface`/`updateMountedRow`
  guard on `document.getElementById(...)` being falsy and return early; without
  this stub they threw `TypeError: document.getElementById is not a function`
  the instant a whole-module test called any real function on that path), `body`,
  `documentElement`.
- **`localStorage` / `sessionStorage`** -- an in-memory `Map`-backed stub
  (`getItem`/`setItem`/`removeItem`/`clear`), exposed both as top-level
  context globals (some module code reads the bare name) and as
  `window.localStorage`/`window.sessionStorage` (other code reads it off
  `window`) -- the same object either way, so a set through one name is
  visible through the other.
- **`fetch`** -- top-level alias of `window.fetch`, same reject-loudly stub.
- **`URLSearchParams`** -- the real Node global, passed through unstubbed.
- **`location`** -- top-level alias of `window.location`.
- **`CURRENT_PLAYER` / `BOARD`** -- initial `null` browser-global state used
  while cyclic imports evaluate. `board.js` replaces both with live exported
  properties once its module body runs; the placeholders let earlier modules
  observe the same no-player/no-board startup state instead of failing on an
  undeclared identifier.
- **Timers** -- `setTimeout`/`clearTimeout`/`setInterval`/`clearInterval`
  and `queueMicrotask`, Node's real ones, so module-scope `setTimeout(...)`
  calls and `updateMountedRow`'s `queueMicrotask(syncCloseAll)` do not throw.
- **`console`** -- Node's real console, so `console.error`/`console.warn`
  calls in the module are visible in the test's own output instead of
  throwing.
- Plus the ordinary language globals a browser also provides:
  `navigator` (a `clipboard.writeText` stub and a `userAgent` string),
  `Intl`, `Date`, `Math`, `Number`, `String`, `Object`, `Array`, `Set`,
  `Map`, `RegExp`, `JSON`, `Promise`, `Error`, `TypeError`,
  `encodeURIComponent`, `decodeURIComponent`.

## API

- `ENTRY: Path` -- the post-split entry module and the root of the import
  graph `read_entry_source()` walks, see above.
- `read_entry_source(entry: Path = ENTRY) -> str` -- walks the import graph
  reachable from `entry` (post-order DFS, entry last -- native ES-module
  evaluation order) and returns every module's separately scoped source with
  its generated `import` lines and `export ` prefixes stripped (native
  ES-module syntax `vm` cannot parse either). Live harness bindings expose
  exports and uniquely named private declarations. One module can still be
  loaded alone by passing a leaf `entry` with no imports of its own.
- `CONTEXT_SETUP_JS: str` -- the JS source text that declares `const context
  = {...}; context.globalThis = context;` -- inline this before the module
  source and your own assertions in a hand-built script if you need to
  compose additional context entries; most callers do not.
- `run_in_module_context(body_js: str, *, entry: Path = ENTRY, extra_context_js:
  str = "", check: bool = True) -> subprocess.CompletedProcess` -- the one
  call most tests need: builds the full script (context stubs, then `entry`'s
  source evaluated inside that context via `vm.runInNewContext`, then
  the host Node `process` exposed only for `body_js`, which is appended so it
  can call the module's published real top-level functions/consts directly), and shells to
  `node -e`. `body_js` should `throw` or `process.exit(<nonzero>)` to signal
  failure; `subprocess.CalledProcessError` propagates when `check=True`.
  `extra_context_js` lets a caller splice extra context entries (e.g. a
  module-scope override) before `context.globalThis = context;` runs.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

STATIC = Path(__file__).parents[2] / "ceres" / "static"

# Flips to STATIC / "staging.js" at M3, STATIC / "operations.js" at M7
# (campaign plan §15 ruling 6a) -- never guessed ahead of the move.
ENTRY = STATIC / "app.js"

# Matches a generated `import { a, b } from './x.js';` line -- split.py's own
# output syntax, mirrors verify_split.py's GENERATED_IMPORT.
_GENERATED_IMPORT = re.compile(
    r"^import \{[^}]*\} from '\./([A-Za-z0-9_-]+)\.js';$",
    re.MULTILINE,
)
# Matches the `export ` prefix split.py adds ahead of a declaration keyword
# mirrors verify_split.py's EXPORT_PREFIX. Bare named exports and local named
# re-exports are handled by the drop-whole-line branches below.
_EXPORT_PREFIX = re.compile(r"^export (?=(?:async function|function|const|let|var|class)\b)")
_TOP_LEVEL_DECLARATION = re.compile(
    r"^(?P<export>export )?(?P<kind>async function|function|const|let|var|class) "
    r"(?P<name>[A-Za-z_$][A-Za-z0-9_$]*)\b",
    re.MULTILINE,
)
_EXPORT_REEXPORT_LINE = re.compile(r"^export \{[^}]*\};$")
_EXPORT_REEXPORT_FROM_LINE = re.compile(
    r"^export \{ [A-Za-z_][A-Za-z0-9_]*(?:, [A-Za-z_][A-Za-z0-9_]*)* \} "
    r"from '\./[A-Za-z0-9_-]+\.js';$"
)


def _strip_module_syntax(text: str) -> str:
    """Remove exactly the syntax split.py added to a module: generated
    `import { … } from './x.js';` lines and `export ` prefixes ahead of a
    declaration -- same two forms `verify_split.py::strip_module` strips,
    checked with the same regexes so this stays in lockstep with what the
    splitter actually emits. `vm.runInNewContext` cannot parse `import`/
    `export` syntax at all, so both must go before concatenation."""
    # generated named imports may span lines (harvest-modal.js is the first case).
    text = _GENERATED_IMPORT.sub("", text)
    out = []
    for line in text.split("\n"):
        if (
            _EXPORT_REEXPORT_LINE.match(line)
            or _EXPORT_REEXPORT_FROM_LINE.match(line)
        ):
            continue
        out.append(_EXPORT_PREFIX.sub("", line))
    return "\n".join(out)


def _top_level_bindings(text: str) -> dict[str, tuple[str, bool]]:
    """Return declaration kind and export status for module-level bindings."""
    return {
        match.group("name"): (match.group("kind"), bool(match.group("export")))
        for match in _TOP_LEVEL_DECLARATION.finditer(text)
    }


def _scope_module_source(text: str, exposed: set[str]) -> str:
    """Keep one module's lexical declarations isolated and publish harness bindings."""
    bindings = _top_level_bindings(text)
    descriptors = []
    for name in sorted(exposed):
        kind, _ = bindings[name]
        setter = (
            f", set: (value) => {{ {name} = value; }}"
            if kind in {"let", "var", "function", "async function"}
            else ""
        )
        descriptors.append(
            f"  {json.dumps(name)}: {{ configurable: true, get: () => {name}{setter} }}"
        )
    publication = ""
    if descriptors:
        publication = "\nObject.defineProperties(globalThis, {\n" + ",\n".join(descriptors) + "\n});"
    # native ES modules isolate private `let TIMER`/`tick` and other repeated names.
    return "{\n" + _strip_module_syntax(text) + publication + "\n}"


def _esm_evaluation_order(entry: Path) -> list[Path]:
    """Post-order DFS over each module's `import { … } from './x.js';` edges,
    starting at `entry`, entry last -- native ES-module evaluation order, the
    same order `work/modularisation/tooling/verify_split.py::
    esm_evaluation_order` prints for this graph (re-implemented locally here,
    not imported from work/ -- tests must not depend on campaign tooling)."""
    seen: set[Path] = set()
    out: list[Path] = []

    def visit(path: Path) -> None:
        if path in seen:
            return
        seen.add(path)
        text = path.read_text(encoding="utf-8")
        deps = sorted({match.group(1) for match in _GENERATED_IMPORT.finditer(text)})
        for dep in deps:
            visit(path.parent / f"{dep}.js")
        out.append(path)

    visit(entry)
    return out

CONTEXT_SETUP_JS = r"""
function __ceresStubStorage() {
  const store = new Map();
  return {
    getItem: (k) => (store.has(k) ? store.get(k) : null),
    setItem: (k, v) => store.set(k, String(v)),
    removeItem: (k) => store.delete(k),
    clear: () => store.clear(),
  };
}
const __ceresListeners = {};
const __ceresLocalStorage = __ceresStubStorage();
const __ceresSessionStorage = __ceresStubStorage();
function __ceresFetchStub() {
  return Promise.reject(new Error('vm_context fetch stub: whole-module loaders never make a real network call'));
}
const window = {
  addEventListener(type, fn) { (__ceresListeners[type] ||= []).push(fn); },
  removeEventListener() {},
  dispatchEvent() { return true; },
  localStorage: __ceresLocalStorage,
  sessionStorage: __ceresSessionStorage,
  location: { search: '', href: 'http://127.0.0.1/', pathname: '/' },
  innerWidth: 1920,
  innerHeight: 1080,
  matchMedia() {
    return { matches: false, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {} };
  },
  requestAnimationFrame(fn) { return setTimeout(fn, 0); },
  cancelAnimationFrame(id) { clearTimeout(id); },
  fetch: __ceresFetchStub,
  getComputedStyle() { return { getPropertyValue() { return ''; } }; },
  __ceresOperationPollSchedule: undefined,
};
const document = {
  readyState: 'loading',
  addEventListener(type, fn) { (__ceresListeners['doc:' + type] ||= []).push(fn); },
  removeEventListener() {},
  createElement() {
    return { style: {}, setAttribute() {}, appendChild() {}, classList: { add() {}, remove() {}, toggle() {} } };
  },
  querySelector() { return null; },
  querySelectorAll() { return []; },
  getElementById() { return null; },
  // classList: board.js's render()/mobile-sheet toggles call
  // document.body.classList.{add,remove,toggle} unconditionally (no optional chaining).
  body: { appendChild() {}, removeChild() {}, classList: { add() {}, remove() {}, toggle() {} } },
  documentElement: { style: {}, setAttribute() {}, getAttribute() { return null; } },
  // fonts.ready stub for the document.fonts.ready-gated mobile tile text-fit.
  fonts: { ready: Promise.resolve() },
};
const context = {
  window,
  document,
  getComputedStyle: window.getComputedStyle,
  localStorage: __ceresLocalStorage,
  sessionStorage: __ceresSessionStorage,
  fetch: __ceresFetchStub,
  URLSearchParams,
  // URL: wallet.js's walletLogo() builds a `new URL(...)` to resolve vendor asset paths; the sandbox
  // previously exposed URLSearchParams only.
  URL,
  crypto: require("node:crypto").webcrypto,   // the OPTIONAL Posting-key login and the player's own server space (root routing setup, later staged changes).: the optional login hashes and draws random bytes in the page
  TextEncoder,
  AbortController,
  AbortSignal,
  DOMException,
  // CustomEvent: board.js's switchPlayer() dispatches
  // window.dispatchEvent(new CustomEvent('ceres:player-changed', ...)); the vm context
  // is an isolated object, so it does not inherit Node's own global CustomEvent.
  CustomEvent,
  location: window.location,
  // Cyclic import evaluation can reach wallet.js before board.js publishes its live
  // state properties; null matches the browser's no-player/no-board startup state.
  CURRENT_PLAYER: null,
  BOARD: null,
  performance: { now() { return Date.now(); } },
  CSS: { escape(value) { return String(value).replace(/[^a-zA-Z0-9_-]/g, '\\$&'); } },
  navigator: { locks: { request: async (_name, callback) => callback() }, clipboard: { writeText() { return Promise.resolve(); } }, userAgent: 'node' },
  console,
  setTimeout,
  queueMicrotask,
  clearTimeout,
  requestAnimationFrame: window.requestAnimationFrame,
  cancelAnimationFrame: window.cancelAnimationFrame,
  setInterval,
  clearInterval,
  Intl,
  Date,
  Math,
  Number,
  String,
  Object,
  Array,
  Set,
  Map,
  RegExp,
  JSON,
  Promise,
  Error,
  TypeError,
  encodeURIComponent,
  decodeURIComponent,
};
context.globalThis = context;
""".strip("\n")


def read_entry_source(entry: Path = ENTRY) -> str:
    """Return the whole-graph source for `entry`: every module reachable from
    it via `import { … } from './x.js';` edges, scoped separately in native
    ES-module evaluation order (post-order DFS, entry last -- see
    `_esm_evaluation_order`), with generated `import` lines and `export `
    prefixes stripped since `vm.runInNewContext` cannot parse either form.

    A leaf module (no `import` lines of its own -- e.g. `state.js`) still
    works as `entry`: the graph walk visits only itself, so this degrades to
    "read one file, scope it and strip its syntax" for a caller that only needs one
    module."""
    modules = _esm_evaluation_order(entry)
    sources = {path: path.read_text(encoding="utf-8").replace("import.meta.url", json.dumps(path.resolve().as_uri())) for path in modules}
    bindings = {path: _top_level_bindings(text) for path, text in sources.items()}
    occurrences: dict[str, int] = {}
    for module_bindings in bindings.values():
        for name in module_bindings:
            occurrences[name] = occurrences.get(name, 0) + 1

    scoped = []
    for path in modules:
        exposed = {
            name
            for name, (_, exported) in bindings[path].items()
            if exported or occurrences[name] == 1
        }
        scoped.append(_scope_module_source(sources[path], exposed))
    return "\n".join(scoped)


def run_in_module_context(
    body_js: str,
    *,
    entry: Path = ENTRY,
    source: str | None = None,
    extra_context_js: str = "",
    check: bool = True,
) -> subprocess.CompletedProcess:
    """Evaluate `entry` (default `ENTRY`) inside the shared sandbox via
    `vm.runInNewContext`, then run `body_js` against the published live
    harness bindings -- `body_js` can call the module's real top-level
    functions/consts directly and receives the host Node `process` only after
    module evaluation. `body_js` should
    `throw` or `process.exit(<nonzero>)` on failure. `extra_context_js` is
    spliced in just before `context.globalThis = context;`, for a caller
    that needs one more context entry (e.g. a module-level override) without
    hand-rolling the rest of the sandbox.

    `source`, if given, replaces `read_entry_source(entry)` -- for a caller
    that already has a (possibly mutated, for an injection test) copy of the
    module text in hand and wants to run it without a round trip through
    disk. `entry` is still used as the reported filename either way.

    The module source is piped over stdin rather than embedded in the `node
    -e` argument -- `app.js` (~8,700 lines) blows past the OS
    `ARGV`/`ARG_MAX` limit (`OSError: [Errno 7] Argument list too long`) when
    spliced directly into the script text; `body_js` stays embedded since
    callers' bodies are always small assertions."""
    if source is None:
        source = read_entry_source(entry)
    setup = CONTEXT_SETUP_JS
    if extra_context_js:
        setup = setup.replace(
            "context.globalThis = context;",
            f"{extra_context_js}\ncontext.globalThis = context;",
        )
    script = f"""
const vm = require('node:vm');
{setup}
const __ceresModuleSource = require('node:fs').readFileSync(0, 'utf8');
// __ceresModuleSource is now the WHOLE graph's scoped, syntax-stripped source
// (read_entry_source), not just `entry`'s own file; `filename` here
// is only ever used to label a stack trace, so leaving it as `entry`'s path
// is harmless -- it does not claim the concatenated text literally lives at
// that path on disk.
vm.runInNewContext(__ceresModuleSource, context, {{filename: {_js_string_literal(str(entry))}}});
context.process = process;
vm.runInNewContext({_js_string_literal(body_js)}, context, {{filename: 'vm_context body_js'}});
"""
    return subprocess.run(
        ["node", "-e", script], input=source, check=check, capture_output=True, text=True
    )


def _js_string_literal(value: str) -> str:
    """Render `value` as a JS string literal safe to splice into a `node -e`
    script (used for the entry filename and the caller's body_js, both of
    which can contain arbitrary quotes/backticks/newlines)."""
    return json.dumps(value)
