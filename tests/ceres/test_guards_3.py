""" permanent guards (brief-requirements.md "Permanent guards"; history.md: 46 per-flow fixes regrew because
each flow kept its own waiting logic, and manual recheck controls were removed on order four times).

G1 (static): every signing call site is found by the IN-0709 enumeration method itself (signer primitives and the gate
facade, traced over every ceres/static module), so a NEW call site is caught without editing this file:
  a. raw wallet signer/broadcaster primitives appear only in the signer layer (wallet.js, broadcast-gate.js,
     transaction-queue.js);
  b. every module that calls the gate facade registers its action with the shared confirmer;
  c. the gate registers both branches (direct/HiveAuth and the Keychain sign-only queue);
  d. no module but shared-confirmer.js owns a confirmation schedule, give-up limit or poller: every timer in a
     signing/confirming module is one of the reviewed non-confirmation timers listed below (UI, quote debounce,
     submission pacing, wallet transport, admission budget), so a new timer there fails until reviewed;
  e. every call site is listed in library/ceres-signing-options.md as `<file>#<n>`.
G2 (static DOM source): no manual recheck / rescan / check again / check now / look further back control, and no
paste-a-transaction-id input, exists in any template or JS module.

KNOWN_GAPS are the paths this build did not migrate (build-report.md says why). They are strict xfails: the moment a gap
is migrated its case passes, the xfail turns red, and the entry must be deleted -- the list can only shrink.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
STATIC = ROOT / "ceres" / "static"
PAGE = ROOT / "library" / "ceres-signing-options.md"

# IN-0709 enumeration method (signing-paths.md): signer primitives and the gate facade.
SIGNER = re.compile(r"\b(requestWalletBroadcast|requestWalletSignature|requestSignTx|requestCustomJson|requestTransfer|"
                    r"requestBroadcast|HAS\.broadcast|broadcast_transaction|BroadcastGate\.broadcast)\b")
FACADE = re.compile(r"\b(requestBroadcastThroughGate|requestSignatureThroughGate)\b")
SIGNER_LAYER = {"wallet.js", "broadcast-gate.js", "transaction-queue.js"}
CONFIRMER = "shared-confirmer.js"
REGISTERS = re.compile(r"import \{[^}]*\b(confirmAction|waitThrough|waitForStep)\b[^}]*\} from '\./shared-confirmer\.js'")

KNOWN_GAPS = {
    # Routing v2 (routing-run.js, routing-window.js) is migrated (continuation 4). Routing v1 is NOT: it is switched off in
    # production twice (harvest-modal.js HARVEST_ROUTING_ENABLED = false; the server answers 404 for its routes unless
    # CERES_LEGACY_ROUTING=1), so its manual controls cannot be reached; delete-or-migrate is an open manager question.
    "harvest-routing.js": "routing v1 (off in production) keeps its own sync timer and manual controls; delete or migrate is open (build-report.md)",
}

# Old per-flow schedule and give-up names: none may exist outside the confirmer.
FORBIDDEN_NAMES = re.compile(r"\b(VERIFY_SCHEDULE_MS|VERIFY_SCHEDULE_S|OPERATION_POLL_SCHEDULE_MS\s*=\s*\[|CHECK_WAITS_MS|RECHECK_MS|"
                             r"CONFIRM_TTL_MS|SETTLE_TTL_MS|SETTLE_SCHEDULE_MS|DEC_NOT_FOUND_AFTER_MS|LOCK_VERIFY_SCHEDULE_MS|"
                             r"LOCK_NOT_FOUND_AFTER_MS|VERIFY_MAX_WAIT_MS|VERIFY_REQUEST_MS|MAX_ROUNDS|COMBINE_RETRY_MS|"
                             r"COMBINE_BUILD_TTL_MS|WORKSITE_VERIFY_MAX_ATTEMPTS|nextVerifyDelayMs|scaledVerifyWaitMs|"
                             r"waitForQueuedSubmission[^\n]*deadline)\b")
TIMER = re.compile(r"\b(setTimeout|setInterval|AbortSignal\.timeout)\s*\(")
# Manager ruling Q3 (, 2026-11-03): the broadcast gate's admission-budget timers (at most 5 custom ops per Hive
# block) are RATE LIMITING -- they decide when a new send may go out, never whether a sent action is confirmed -- so they
# stay as reviewed exceptions below. They must never grow into a confirmation wait or a give-up.
# Reviewed non-confirmation timers in signing/confirming modules: (file, unique source fragment, why it is not a
# confirmation wait). Anything else in these modules is a new timer and fails G1d until reviewed here.
ALLOWED_TIMERS = {
    ("broadcast-gate.js", "timer = setTimeout(() => resolve(null), 5000)"): "admission: one bounded inclusion probe of an EARLIER request before a new send (5 custom ops per block)",
    ("broadcast-gate.js", "autoResolveTimer = setInterval("): "admission: frees the 5-ops-per-block budget held by earlier requests; decides when a new send may go, not an outcome",
    ("broadcast-gate.js", "setTimeout(resolve, BUDGET_WAIT_STEP_MS)"): "admission: next chunk waits for the per-block custom-op budget",
    ("transaction-queue.js", "const queueSleep = (ms) => new Promise(resolve => setTimeout(resolve, ms));"): "submission pacing: a rate-limited row waits for the next Hive block",
    ("transaction-queue.js", "const timer = setTimeout(() => controller.abort(), 10000);"): "transport: one Hive RPC request bound",
    ("transaction-queue.js", "const timer = setTimeout(() => { finished = true; reject(new Error('The signing request timed out."): "wallet transport: Keychain sign-only request; nothing broadcast",
    ("transaction-queue.js", "setTimeout(() => resumeSignedQueue(account), 1000);"): "submission pacing: a sendable/rate-limited row is submitted on the next pass",
    ("wallet.js", "const timer = setTimeout(() => {"): "wallet transport (HiveAuth session/connect)",
    ("wallet.js", "expiryTimer = setTimeout(expireSession,"): "wallet session expiry",
    ("wallet.js", "const timer = setTimeout(() => finishPending({ type: action === 'connect'"): "wallet transport watchdog: an unanswered request ends as uncertain and is handed to the confirmer",
    ("harvest-modal.js", "if (plotTaxesError(p.deed_uid)) setTimeout(() => {"): "UI: retry a failed taxes read in the review",
    ("harvest-modal.js", "M.cooldownTimer = setTimeout(() => {"): "UI: cooldown countdown redraw",
    ("wallet-lp.js", "st.timer = setTimeout(() => runQuote(ctx, symbol), QUOTE_IDLE_MS);"): "UI: quote debounce",
    ("wallet-lp.js", "x.timer = setTimeout(() => runTransferQuote(ctx, symbol, x), QUOTE_IDLE_MS);"): "UI: quote debounce",
    ("operations.js", "setTimeout(resolve, [1000, 2000, 4000][liveAsks] ?? 8000)"): "pre-sign preview: the game's live read for the review is not ready",
    ("operations.js", "setTimeout(resolve, 5000); });"): "admission: an earlier wallet request blocks a new send ()",
    ("card-checkout.js", "closeTimer = setTimeout(() => {"): "UI: auto-close after an all-green outcome",
    ("card-checkout.js", "tickTimer = setInterval(() => {"): "UI: elapsed-time clock in place",
    ("combine-dialog.js", "setTimeout(resolve, 250); });"): "pre-sign: idle cards cache for the combine review",
    ("regional-dec.js", "window.setTimeout(finish, WALLET_SLIDE_DURATION + 80);"): "UI: drawer slide animation",
    ("regional-dec.js", "if (previewPacingTimer == null) previewPacingTimer = setTimeout(() => {"): "pre-sign: stake preview request pacing",
    ("regional-dec.js", "previewTimer = setTimeout(() => {"): "pre-sign: stake preview debounce",
    ("chain-lag.js","const timer = setTimeout(() => controller.abort(), 10000);"): "transport: one public last-block read bound (lag display)",
}
CONFIRMING_EXTRA = {"broadcast-gate.js", "transaction-queue.js", "wallet.js", "chain-lag.js", "combine-dialog.js",
                    "combine-chain-sign.js", "checkout.js"}


def strip_comments(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", lambda m: "\n" * m.group(0).count("\n"), text, flags=re.S)
    return re.sub(r"(^|[\s;{}(),])//[^\n]*", r"\1", text)


def modules() -> dict[str, str]:
    return {path.name: strip_comments(path.read_text(encoding="utf-8")) for path in sorted(STATIC.glob("*.js"))
            if ".min." not in path.name}


def facade_sites() -> dict[str, int]:
    """file -> number of gate-facade call sites (calls and injected defaults; import lines excluded)."""
    sites = {}
    for name, text in modules().items():
        if name in SIGNER_LAYER or name == CONFIRMER:
            continue
        code = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("import "))
        count = len(FACADE.findall(code))
        if count:
            sites[name] = count
    return sites


def _gap(name: str):
    return pytest.mark.xfail(strict=True, reason=KNOWN_GAPS[name]) if name in KNOWN_GAPS else ()


def test_g1_enumeration_finds_the_fifteen_known_entry_points():
    sites = facade_sites()
    # The IN-0709 count (signing-paths.md). A new call site changes this number: add it to the library page and
    # register it with the shared confirmer, then update the expected count here.
    assert sum(sites.values()) == 15, sites
    assert set(sites) == {"harvest-modal.js", "wallet-lp.js", "wallet-claim.js", "regional-dec.js", "worksite.js", "plot-lock.js",
                          "operations.js", "card-checkout.js", "harvest-routing.js", "routing-run.js"}, sites


def test_g1a_raw_signer_primitives_stay_in_the_signer_layer():
    offenders = {name: sorted(set(SIGNER.findall(text))) for name, text in modules().items()
                 if name not in SIGNER_LAYER and SIGNER.search(text)}
    assert offenders == {}, f"a wallet signer/broadcaster is called outside the gate facade: {offenders}"


@pytest.mark.parametrize("name", sorted(set(facade_sites()) | set(KNOWN_GAPS) - {"routing-window.js"}),
                         ids=lambda n: n)
def test_g1b_every_signing_module_registers_with_the_shared_confirmer(request, name):
    if name in KNOWN_GAPS:
        request.applymarker(_gap(name))
    text = (STATIC / name).read_text(encoding="utf-8")
    assert REGISTERS.search(text), f"{name} signs through the gate but never hands its action to shared-confirmer.js"


def test_g1c_gate_registers_both_branches_with_the_confirmer():
    gate = strip_comments((STATIC / "broadcast-gate.js").read_text(encoding="utf-8"))
    direct = gate[gate.index("function requestBroadcastPromise("):gate.index("export function reviewedMarketPurchase")]
    queued = gate[gate.index("export async function requestSignatureThroughGate("):]
    # Fix round 2 (P1-3): the id is attached to the attempt that asked for it.
    assert "beginAttempt(account, operations" in direct and "attachTransaction(account, operations, txId, { attempt: attemptRow.attempt })" in direct
    # Round 4 (aletheia r4 6): the begin starts early (startBegin) and is awaited before the wallet is asked.
    assert "startBegin(account, operations)" in direct and "await begun" in direct, "the server must hear the attempt's begin (P0-1)"
    assert direct.index("await begun") < direct.index("requestWalletBroadcast("), "the wallet opens only after the begin answer (N1)"
    assert "markSilent(" in direct and "closed: walletClosed(response)" in direct, "silence is not an answer (P0-1)"
    assert "findSignedTransaction(account, operations)" in direct, "a lost ack must go to the own-history finder"
    assert "subscribeConfirmation(account, operations" in direct, "the hand-off must follow the confirmer's proof (HIGH-1, F2)"
    assert "beginAttempt(account, operations" in queued and "attachTransaction(account, operations, queued.txId)" in queued
    assert "afterWalletSilence(" in gate and "KEYCHAIN_RESPONSE_TIMEOUT_MS)" not in gate


@pytest.mark.parametrize("name", sorted((set(facade_sites()) | CONFIRMING_EXTRA | set(KNOWN_GAPS)) - {"routing-window.js"}),
                         ids=lambda n: n)
def test_g1d_no_module_but_the_confirmer_owns_a_confirmation_wait(request, name):
    if name in KNOWN_GAPS:
        request.applymarker(_gap(name))
    raw = (STATIC / name).read_text(encoding="utf-8")
    text = strip_comments(raw)
    names = sorted(set(m.group(1) for m in FORBIDDEN_NAMES.finditer(text)))
    assert not names, f"{name} owns a confirmation schedule or give-up limit: {names}"
    unreviewed = []
    for line in text.splitlines():
        if TIMER.search(line) and not any(file == name and fragment in line for file, fragment in ALLOWED_TIMERS):
            unreviewed.append(line.strip()[:140])
    assert not unreviewed, f"{name} has timers that are not reviewed non-confirmation timers: {unreviewed}"


def test_g1d_allowlisted_timers_still_exist():
    # A stale allowlist entry would hide nothing today but could cover a future poller with the same text.
    stale = [(file, fragment) for file, fragment in ALLOWED_TIMERS if fragment not in (STATIC / file).read_text(encoding="utf-8")]
    assert stale == []


def test_g1d_confirmer_is_the_only_owner_of_the_shared_schedule():
    for name, text in modules().items():
        if name == CONFIRMER:
            continue
        assert "nextCheckOffset(" not in text and "CONFIRM_FAST_MS = [" not in text, name


def test_g1e_every_call_site_is_on_the_signing_options_page():
    page = PAGE.read_text(encoding="utf-8")
    missing = [f"{name}#{index}" for name, count in facade_sites().items() for index in range(1, count + 1)
               if f"`{name}#{index}`" not in page]
    assert missing == [], f"library/ceres-signing-options.md lacks these signing paths: {missing}"


MANUAL_LABEL = re.compile(r"check again|check now|re-?check(?:ing)? (?:the|it|now)|\brescan\b|look further back|resume checking|"
                          r"stop checking|find my transaction|find (?:the )?signed|recover harvest|change transaction id|"
                          r"check transaction|i approved it|i checked my wallet|check pending (?:wallet )?requests|"
                          r"check the outstanding outcome|"
                          # fix round 1 (Aletheia MEDIUM-5: a "Verify now" button passed G2)
                          r"verify (?:now|again|it|the transaction)|\bre-?verify\b|retry (?:the )?(?:confirmation|verification|check)|"
                          r"refresh (?:the )?(?:status|confirmation|result|receipt)|scan again|search again|look again|try checking", re.I)
MANUAL_HOOK = re.compile(r"data-(?:hm|dec|co-action|combine-action|upgrade-action|checkout-action|notice-action|worksite-action)"
                         r"=\\?\"(?:check-now|recover-harvest|verify|lp:verify|lp:stop|lp:settle|lp:settle-stop|lp:settle-older|"
                         r"lp:funding-not-sent|claim:verify|check|combine-check|combine-retry|check-combine|lock-recheck|"
                         r"retry-verification|verify-again|change-tx|recover|discard)\\?\""
                         # routing v2 window (data-r3) and v1 (data-hr): the former check/find buttons and recoveries
                         r"|data-(?:r3|hr)=\\?\"(?:check|find|verify|verify-older|funding-verify|funding-recover|funding-adopt|check-harvest|recover|not-sent)\\?\""
                         # the regional DEC "Dismiss" exit of an unproven change (manager, continuation 4)
                         r"|data-dec=\\?\"dismiss\\?\"")
PASTE_INPUT = re.compile(r"<input[^>]*\bid=\\?\"(?:[a-z-]*(?:transaction|recovery-tx|tx-id|txid)[a-z-]*)\\?\"", re.I)


def surfaces():
    for path in sorted(STATIC.glob("*.js")) + sorted(STATIC.glob("*.html")) + sorted((ROOT / "ceres").glob("templates/**/*.html")):
        if ".min." in path.name or path.name in {"pill-preview.html", "styleguide.html"}:
            continue
        yield path


@pytest.mark.parametrize("path", list(surfaces()), ids=lambda p: p.name)
def test_g2_no_manual_recheck_control_or_paste_input(request, path):
    if path.name in KNOWN_GAPS:
        request.applymarker(_gap(path.name))
    text = strip_comments(path.read_text(encoding="utf-8")) if path.suffix == ".js" else path.read_text(encoding="utf-8")
    found = sorted({m.group(0) for m in MANUAL_LABEL.finditer(text)} | {m.group(0) for m in MANUAL_HOOK.finditer(text)}
                   | {m.group(0)[:60] for m in PASTE_INPUT.finditer(text)})
    assert not found, f"{path.name} shows a manual confirm control or paste field: {found}"


def test_g2_server_notices_attach_no_manual_action():
    from ceres import notices
    assert notices.RETRY_VERIFICATION_ACTION is None
    assert not MANUAL_LABEL.search(strip_comments((ROOT / "ceres" / "notices.py").read_text(encoding="utf-8")).split("RETRY_VERIFICATION_ACTION = None", 1)[1])


# G1f (continuation 4): routing v2's server keeps no give-up rule
def test_g1f_routing_v2_server_has_no_poll_cap_probe_cap_budget_stop_or_manual_bypass():
    """ Q2: the server once ended routing checks after 5 game polls, 2 chain probes or the run's call budget, and a
    `manual` flag bypassed them ("use Check again"). Only a rate limit may remain, answered as 429 "read later"."""
    server = (ROOT / "ceres" / "routing_runs_v2.py").read_text(encoding="utf-8")
    routes = (ROOT / "ceres" / "routes_routing_v2.py").read_text(encoding="utf-8")
    code = re.sub(r'"""[\s\S]*?"""|#[^\n]*', "", server)
    for pattern in (r'\["polls"\]\s*>=', r'chain_probes",\s*0\)\s*>=', r"\bmanual\s*=", r"checking_stopped",
                    r'budget\["stopped"\]\s*=\s*True', r"use Check again", r"def find_signed"):
        assert not re.search(pattern, code), f"routing_runs_v2.py still has a give-up/manual rule: {pattern}"
    assert "RoutingRateLimited" in code and "429" in routes and '"/api/harvest/routing/v2/find"' not in routes


# G1g (fix round 1, Aletheia MEDIUM-5): no module but the confirmer POLLS a confirmation read
# G1d's timer allowlist covers the signing modules only; the reviewers' mutations put a poller in a UI module
# (setInterval in combine-chain-ui.js), a recursive poller in board.js and a timer-less loop in wallet-lp.js. G1g scans
# EVERY module: a "confirmation read" is a call to a confirmation endpoint (verify, op-verify, op-inclusion, settle
# api/confirm/*, find, reconcile, transactions/lookup) or to a function of that module that makes one (transitively).
# Forbidden outside shared-confirmer.js: such a read inside a timer callback, inside a loop that awaits it, or in a
# function that calls itself again (recursion), except the reviewed cases below (bounded per-row/per-chunk reads
# admission rate limiting, and self-references handed to the shared schedule).
CONF_ENDPOINT = re.compile(r"""['"`][^'"`\n]*(?:/verify|op-verify|op-inclusion|/settle|/api/confirm/|lp/find|/reconcile|transactions/lookup|/find\b)[^'"`\n]*['"`]"""
                           r"""|\b(?:post|call|api|postJson)\([^()'"]{0,40}?['"](?:verify|settle|find|reconcile|combine-verify)['"]""")
POLL_TIMER = re.compile(r"\b(setTimeout|setInterval|requestAnimationFrame)\s*\(")
POLL_LOOP = re.compile(r"\b(while\s*\(|for\s*\(|do\s*\{)")
POLL_FUNC = re.compile(r"(?:async\s+)?function\s+([A-Za-z_$][\w$]*)\s*\([^)]*\)\s*\{|(?:const|let)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?(?:\([^)]*\)|[A-Za-z_$][\w$]*)\s*=>\s*\{")
SHARED_SCHEDULE_CALL = re.compile(r"\b(?:scheduleOperationPoll|waitThrough|waitForStep|confirmAction)\s*\(")
ALLOWED_POLLS = {
    ("broadcast-gate.js", "setInterval(() => {       if (!activeAdmissions(name).length)"): "admission rate limit (): frees the per-block budget, never a confirmation",
    ("broadcast-gate.js", "for (const row of rows) {     const inclusion = await probeAdmission"): "admission: one probe per earlier request before a new send",
    ("broadcast-gate.js", "for (let attempt = 0; attempt < BUDGET_WAIT_ATTEMPTS;"): "admission: the next chunk waits for the per-block budget (reviewed G1d timer)",
    ("card-checkout.js", "for (let i = 0; i < co.run.txs.length; i += 1) {"): "signing loop: one purchase after another; each is confirmed by confirmAction",
    ("card-checkout.js", "for (const item of list) {     const probe = { nonce: item.nonce"): "legacy watched intents: one nonce lookup each on open ()",
    ("harvest-modal.js", "for (let index = 0; index < pv.chunks.length; index += 1) {       if (M.run.stop)"): "signing loop: chunk after chunk; each is confirmed by confirmAction",
    ("operations.js", "for (let index = 0; index < chunks.length; index += 1) {       const chunk = chunks[index]"): "signing loop: chunk after chunk; each is confirmed through scheduleOperationPoll",
    ("routing-run.js", "for (let index = 0; index < ids.length; index += 1) {       if (FINISHED.has("): "readBundle: ONE read of each open operation, called only as the shared confirmer's check",
    ("routing-run.js", "for (let guard = 0, limit = Object.keys(S.record?.bundles || {}).length + 8;"): "signAll: signs bundle after bundle; each is confirmed by confirmAction",
    ("wallet-lp.js", "for (const item of list) {     let found = null;     try { found = await post('find'"): "legacy watched intents: one nonce lookup each on open ()",
}


def _block(text: str, start: int, open_ch: str = "{", close_ch: str = "}") -> str:
    depth = 0
    for index in range(start, len(text)):
        if text[index] == open_ch:
            depth += 1
        elif text[index] == close_ch:
            depth -= 1
            if depth == 0:
                return text[start:index + 1]
    return text[start:]


def confirmation_polls(text: str) -> list[tuple[str, str]]:
    """(kind, where) for every timer/loop/recursion around a confirmation read in one comment-stripped module."""
    funcs: dict[str, str] = {}
    for match in POLL_FUNC.finditer(text):
        funcs.setdefault(match.group(1) or match.group(2), _block(text, match.end() - 1))
    conf = {name for name, body in funcs.items() if CONF_ENDPOINT.search(body)}
    changed = True
    while changed:
        changed = False
        for name, body in funcs.items():
            if name not in conf and any(re.search(rf"\b{re.escape(c)}\s*\(", body) for c in conf):
                conf.add(name)
                changed = True
    calls = re.compile(r"\b(" + "|".join(map(re.escape, sorted(conf))) + r")\b") if conf else None
    reads = lambda body: bool(CONF_ENDPOINT.search(body) or (calls and calls.search(body)))  # noqa: E731
    found = []
    for match in POLL_TIMER.finditer(text):
        if reads(_block(text, match.end() - 1, "(", ")")):
            found.append(("timer", re.sub(r"\s+", " ", text[match.start():match.start() + 90])))
    for match in POLL_LOOP.finditer(text):
        if match.group(1).startswith("do"):
            body = _block(text, match.end() - 1)
        else:
            head = _block(text, match.end() - 1, "(", ")")
            rest = text[match.end() - 1 + len(head):]
            body = _block(rest, rest.index("{")) if rest.lstrip().startswith("{") else rest.split(";", 1)[0]
        if "await" in body and reads(body):
            found.append(("loop", re.sub(r"\s+", " ", text[match.start():match.start() + 90])))
    for name in sorted(conf):
        body = funcs[name][1:]
        # A self-reference handed to the shared schedule (scheduleOperationPoll / waitThrough / ...) is the confirmer's.
        for call in list(SHARED_SCHEDULE_CALL.finditer(body))[::-1]:
            span = _block(body, call.end() - 1, "(", ")")
            body = body[:call.start()] + body[call.end() - 1 + len(span):]
        body = re.sub(r"\.pollVerify\s*=\s*[^;]*;", ";", body)
        if re.search(rf"(?<![\w.]){re.escape(name)}\s*\(", body):
            found.append(("recursion", name))
    return found


def unreviewed_polls(name: str, text: str) -> list[tuple[str, str]]:
    return [(kind, where) for kind, where in confirmation_polls(text)
            if not any(file == name and where.startswith(re.sub(r"\s+", " ", fragment)[:60]) for file, fragment in ALLOWED_POLLS)]


@pytest.mark.parametrize("name", sorted(n for n in modules() if n != CONFIRMER), ids=lambda n: n)
def test_g1g_no_module_but_the_confirmer_polls_a_confirmation_read(request, name):
    if name in KNOWN_GAPS:
        request.applymarker(_gap(name))
    assert unreviewed_polls(name, modules()[name]) == [], f"{name} polls a confirmation read outside shared-confirmer.js"


def test_g1g_reviewed_polls_still_exist():
    present = {(name, where) for name, text in modules().items() for _, where in confirmation_polls(text)}
    stale = [key for key in ALLOWED_POLLS if not any(file == key[0] and where.startswith(re.sub(r"\s+", " ", key[1])[:60]) for file, where in present)]
    assert stale == []


# The reviewers' mutations (aletheia-verdict.md MEDIUM-5), each applied to a copy of the real module text: every one must
# be caught. These fixtures keep the guard from silently weakening.
MUTATIONS = {
    "setInterval poller in combine-chain-ui.js": ("combine-chain-ui.js", "\nconst __mutation = setInterval(() => fetch('/api/op-verify', { method: 'POST' }), 5000);\n"),
    "recursive setTimeout poller in board.js": ("board.js", "\nfunction __mutationPoll() { return fetch('/api/op-verify').then(() => setTimeout(__mutationPoll, 2000)); }\n"),
    "timer-less loop in wallet-lp.js": ("wallet-lp.js", "\nasync function __mutationSpin(ctx) { while (true) { const r = await post('verify', ctx.player, {}); if (r.status === 'confirmed') return r; } }\n"),
    "setTimeout poller in wallet-claim.js": ("wallet-claim.js", "\nfunction __mutationClaim() { setTimeout(() => api(D, 'verify', {}), 3000); }\n"),
    "recursive async re-read in plot-lock.js": ("plot-lock.js", "\nasync function __mutationLock(uid) { const r = await fetch('/api/plot-lock/verify'); if (!r.ok) return __mutationLock(uid); return r; }\n"),
}


@pytest.mark.parametrize("label", sorted(MUTATIONS), ids=lambda n: n)
def test_g1g_catches_each_reviewer_mutation(label):
    name, injected = MUTATIONS[label]
    text = strip_comments((STATIC / name).read_text(encoding="utf-8") + injected)
    assert unreviewed_polls(name, text), f"G1g missed: {label}"


@pytest.mark.parametrize("label", ["Verify now", "Verify again", "Retry confirmation", "Refresh status", "Search again"])
def test_g2_catches_each_manual_label_mutation(label):
    text = strip_comments((STATIC / "picker-actions.js").read_text(encoding="utf-8")) + f'\nconst __m = `<button type="button" class="ceres-btn">{label}</button>`;\n'
    assert MANUAL_LABEL.search(text), f"G2 missed the label {label!r}"


# G2 rendered (fix round 1, Aletheia MEDIUM-5: "G2 should check rendered markup from the JS templates")
# The real templates of the confirming screens are rendered in every waiting/final state they can show, and the markup
# itself is scanned: no button may carry a manual recheck label and no element a manual recheck hook.
RENDERERS = {
    "harvest-modal.js": r"""
const out = [];
Object.assign(M, { open: true, player: 'alice', mode: 'batch', scopeKind: 'regions', v2Kinds: [], regionLayout: null, costsOpen: new Set() });
for (const phase of ['keychain', 'queued', 'confirming', 'pending', 'never_sent', 'failed']) {
  M.run = { phase, busy: phase === 'keychain', chunks: 2, txId: phase === 'keychain' ? null : 'ab'.repeat(20), txIds: [], message: 'm',
    members: [{ region_uid: 'PR-LOP-1', deed_uids: [], outcome: phase === 'failed' ? 'failed' : null, error: phase === 'failed' ? 'refused' : '' }] };
  out.push(runBodyHtml(), resultFooterHtml());
}
console.log('RENDERED ' + JSON.stringify(out));
""",
    "wallet-claim.js": r"""
const out = [];
const rewards = { total: '5.000', rows: [{ label: 'Land', source: 'land', balance: '5.000' }] };
const base = { player: 'alice', busy: false, signing: false, rewards, claim: { busy: false, review: null, pending: null, checking: false } };
const row = { nonce: 'a'.repeat(16), tx_id: 'b'.repeat(40), total: '5.000', status: 'pending' };
for (const phase of ['asked', 'sent', 'checking', 'retried']) out.push(claimHtml({ ...base, claim: { ...base.claim, pending: { ...row, phase } } }));
out.push(claimHtml({ ...base, claim: { ...base.claim, pending: { ...row, status: 'failed', phase: 'failed', message: 'The claim was not sent.' } } }));
out.push(claimHtml({ ...base, claim: { ...base.claim, pending: { ...row, status: 'confirmed', phase: 'done', claimed: [] } } }));
console.log('RENDERED ' + JSON.stringify(out));
""",
    "wallet-lp.js": r"""
const out = [];
const wallet = { account: { DEC: '100', SPS: '10' }, swap: { symbols: ['SPS'], directions: ['swap_sell', 'swap_buy'], signing_enabled: true, rates: {}, fees: {} }, regions: [] };
const receipt = { nonce: 'ab'.repeat(8), tx_id: 'ab'.repeat(20), kind: 'swap_sell', symbol: 'SPS', pay: 'SPS', receive: 'DEC', input: 5, output: null, signed_at: new Date().toISOString() };
for (const [stage, extra] of [['confirming', {}], ['confirming', { tx_id: null, unknownOutcome: true }], ['settling', { fundingUnknown: true }], ['settling', {}],
    ['funding', {}], ['funding-signing', {}], ['done', { output: 2 }], ['refused', {}], ['refused', { kind: 'add' }]]) {
  const st = swapState('alice', 'SPS');
  Object.assign(st, { stage, receipt: { ...receipt, ...extra }, busy: false, checking: stage === 'confirming', settleNote: 'n', historyUnreadable: true, fundError: '' });
  out.push(swapHtml('SPS', wallet, 'alice'));
}
console.log('RENDERED ' + JSON.stringify(out));
""",
}


# Fix round 2 (astra-r2 P2 "strengthen the G2 rendered guard"): the restored-result region, every tone.
RENDERERS["confirm-notices.js"] = r"""
const out = [];
for (const state of ['confirmed', 'refused', 'never-sent']) {
  const row = { orphan: true, state, error: 'reason', operations: [['custom_json', { id: 'sm_land_operation' }], ['custom_json', { id: 'sm_land_operation' }]],
    perOp: [{ index: 0, state: 'confirmed' }, { index: 1, state: state === 'confirmed' ? 'confirmed' : 'refused', error: 'reason' }] };
  out.push(confirmNoticesHtml([{ key: state, text: confirmNoticeText(row), tone: state === 'confirmed' ? 'ok' : state === 'refused' ? 'danger' : 'warn' }]));
}
out.push(confirmNoticesHtml([]), confirmNoticesHtml([{ key: 'a', text: 'x', tone: 'ok' }, { key: 'b', text: 'y', tone: 'warn' }, { key: 'c', text: 'z', tone: 'danger' }]));
out.push(confirmNoticesHtml([{ key: 'd', text: 'w', tone: 'ok' }]));
console.log('RENDERED ' + JSON.stringify(out));
"""


def rendered_markup(name: str, source: str | None = None) -> list[str]:
    import json as _json
    from tests.ceres.vm_context import read_entry_source, run_in_module_context
    entry = STATIC / name
    text = source if source is not None else read_entry_source(entry)
    if name == "harvest-modal.js":
        text = text.rsplit("}", 1)[0] + "\nObject.defineProperty(globalThis, 'M', { configurable: true, get: () => M });\n}\n"
    result = run_in_module_context(RENDERERS[name], entry=entry, source=text, check=False)
    line = next((l for l in result.stdout.splitlines() if l.startswith("RENDERED ")), None)
    assert line, (result.stdout[-800:], result.stderr[-1500:])
    return _json.loads(line.split(" ", 1)[1])


def manual_controls_in(markup: list[str]) -> list[str]:
    found = []
    for html in markup:
        for label in re.findall(r"<button\b[^>]*>(.*?)</button>", html, flags=re.S):
            text = re.sub(r"<[^>]+>", " ", label)
            if MANUAL_LABEL.search(text):
                found.append(text.strip()[:60])
        found += [m.group(0) for m in MANUAL_HOOK.finditer(html)]   # the optional backslash also matches plain markup
    return found


@pytest.mark.parametrize("name", sorted(RENDERERS), ids=lambda n: n)
def test_g2_rendered_confirming_screens_show_no_manual_control(name):
    markup = rendered_markup(name)
    assert len(markup) >= 6 and all(isinstance(html, str) and html for html in markup[:1]), markup[:1]
    assert manual_controls_in(markup) == [], f"{name} renders a manual confirm control"


def test_g2_rendered_scan_catches_a_verify_now_button_in_a_template():
    from tests.ceres.vm_context import read_entry_source
    source = read_entry_source(STATIC / "wallet-claim.js").replace(
        "function claimHtml(D) {", "function claimHtml(D) { return __claimHtml(D) + '<button type=\"button\" class=\"ceres-btn\">Verify now</button>'; }\nfunction __claimHtml(D) {", 1)
    assert manual_controls_in(rendered_markup("wallet-claim.js", source)), "the rendered G2 scan missed an injected Verify now button"


# Fix round 2 (astra-r2 P2): beyond the hand-written confirming screens, EVERY exported `...Html` renderer of every module
# is called with a spread of argument shapes (nothing, an empty object, a pending/confirmed/refused row) and whatever it
# renders is scanned. A renderer that throws on an argument shape contributes nothing (the hand-written RENDERERS above
# cover the confirming screens in every state); this sweep is the wide net for screens nobody listed.
HTML_EXPORT = re.compile(r"^export function ([A-Za-z0-9_]*Html)\b", re.M)
SWEEP_MODULES = sorted(path.name for path in STATIC.glob("*.js") if HTML_EXPORT.search(path.read_text(encoding="utf-8")))


# Round 4 (astra-review-r4 / N8): exported renderers that render nothing for every generic argument shape (they need
# real arguments). Recorded so the sweep can no longer be silently vacuous: a renderer that newly renders nothing fails.
SWEEP_SILENT = {
    'badges.js': ['badgeHtml', 'bloodlineCardBadgeHtml', 'bloodlineCompanionBadgeHtml', 'bloodlineTileBadgesHtml', 'costLineHtml', 'foilBadgeHtml'],
    'candidates.js': ['bloodlinePairBadgeHtml', 'bloodlineRelationHtml', 'pickerRegionHtml'],
    'card-cart.js': ['cartBundleButtonsHtml', 'cartFootHtml', 'cartMaxButtonHtml', 'cartMenuHtml'],
    'card-checkout.js': ['groupsHtml', 'outcomeHtml', 'quoteRowsHtml'],
    'card-combinables.js': ['cxEntryHtml'],
    'card-market.js': ['cmFoldTileHtml', 'cmSkeletonHtml', 'cmTileHtml'],
    'card-purchases.js': ['cpMoreStatsHtml'],
    'checkout.js': ['mobileKeychainGuidanceHtml', 'planPlacementTileHtml'],
    'combine-chain-ui.js': ['chainCardHtml', 'planReviewHtml', 'plotLossWarningHtml', 'upgradeActionHtml', 'upgradePlansHtml', 'upgradeWindowHtml'],
    'combine.js': ['combineTileActionHtml'],
    'confirm-notices.js': ['confirmNoticesHtml'],
    'editor.js': ['editorCardImageHtml', 'stageWaitBadgeHtml'],
    'harvest-ledger.js': ['batchSlotsHtml'],
    'harvest-state.js': ['harvestBarHtml'],
    'hazard.js': ['combineWarningHtml'],
    'icons.js': ['cardSplinterTipHtml', 'rarityOptionIconHtml', 'terrainCellHtml', 'terrainEmblemHtml', 'terrainOptionIconHtml', 'territoryCellHtml', 'typeIconOnlyHtml'],
    'operations.js': ['serverNoticeHtml'],
    'planner.js': ['boardReadHtml', 'plannerDroppedHtml'],
    'plot-warnings.js': ['plotWarningListHtml', 'plotWarningPillsHtml'],
    'rows.js': ['plotRowCellsHtml'],
    'wallet-claim.js': ['claimHtml'],
    'wallet-lp.js': ['positionHtml', 'swapHtml'],
    'worksite.js': ['worksiteChangeButtonHtml'],
}
# Modules whose WHOLE sweep is empty. Those in RENDERERS are covered by hand-written states; the others are residual N8
# (no confirming-screen states rendered by this guard yet; build-report Round 4).
SWEEP_EMPTY = ['card-combinables.js', 'card-market.js', 'confirm-notices.js', 'editor.js', 'harvest-state.js', 'hazard.js', 'planner.js', 'plot-warnings.js', 'wallet-claim.js', 'wallet-lp.js', 'worksite.js']


def sweep_result(name: str) -> dict:
    """Round 4 (astra-review-r4  / N8): the sweep reports, per exported renderer, how many argument shapes rendered
    markup and the first error of each that threw, so an empty or broken sweep can no longer pass silently."""
    import json as _json
    from tests.ceres.vm_context import run_in_module_context
    names = HTML_EXPORT.findall((STATIC / name).read_text(encoding="utf-8"))
    body = "const out = []; const per = {}; const errors = {};\nconst shapes = [[], [{}], [{ state: 'pending', stage: 'confirming', phase: 'confirming', status: 'pending' }], " \
        "[{ state: 'confirmed', stage: 'done', phase: 'success', status: 'confirmed' }], [{ state: 'refused', stage: 'refused', phase: 'failed', status: 'failed', error: 'x' }], ['SPS', {}, 'alice']];\n" + \
        "".join(f"per[{fn!r}] = 0; for (const args of shapes) {{ try {{ const html = {fn}(...args); if (typeof html === 'string' && html) {{ out.push(html); per[{fn!r}] += 1; }} }} catch (e) {{ errors[{fn!r}] ||= String(e && e.message || e).slice(0, 80); }} }}\n" for fn in names) + \
        "console.log('RENDERED ' + JSON.stringify({ out, per, errors }));\n"
    result = run_in_module_context(body, entry=STATIC / name, check=False)
    line = next((l for l in result.stdout.splitlines() if l.startswith("RENDERED ")), None)
    assert line, f"{name}: the renderer sweep printed no result ({result.stderr[-600:]})"
    out = _json.loads(line.split(" ", 1)[1])
    out["names"] = names
    return out


def sweep_markup(name: str) -> list[str]:
    return sweep_result(name)["out"]


@pytest.mark.parametrize("name", SWEEP_MODULES, ids=lambda n: n)
def test_g2_sweep_every_exported_renderer_shows_no_manual_control(name):
    if name in KNOWN_GAPS:
        pytest.xfail(KNOWN_GAPS[name])
    result = sweep_result(name)
    markup = result["out"]
    # Round 4 (/N8): the module was really rendered -- non-empty markup, and every renderer that produced nothing for
    # every shape is a recorded one (it needs real arguments; its screen is covered by RENDERERS or is not a confirming
    # screen). A renderer that newly renders nothing, or a module whose sweep is empty, fails here.
    assert (name in SWEEP_EMPTY) == (not markup), f"{name}: the sweep rendered {'nothing' if not markup else 'markup'} (SWEEP_EMPTY is out of date)"
    assert all(isinstance(html, str) and html for html in markup), name
    silent = sorted(fn for fn in result["names"] if not result["per"].get(fn))
    assert silent == sorted(SWEEP_SILENT.get(name, [])), (name, silent, {fn: result["errors"].get(fn) for fn in silent})
    assert manual_controls_in(markup) == [], f"{name} renders a manual confirm control"


def test_g2_sweep_fails_when_a_renderer_breaks():
    """Control for : an exported renderer that throws on every shape is reported, not swallowed."""
    from tests.ceres.vm_context import run_in_module_context  # noqa: F401 (same harness as the sweep)
    name = SWEEP_MODULES[0]
    result = sweep_result(name)
    assert result["names"] and sum(result["per"].values()) > 0, result["per"]
