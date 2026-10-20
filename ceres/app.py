"""FastAPI app factory for Ceres."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, Response
from starlette.exceptions import HTTPException as StarletteHTTPException
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from mnemosyne.accounting import JournalWriter, configure_sink, release_sink

from . import __version__
from .card_art_cache import CardArtStore, register_card_art_route
from .logging_setup import access_log_middleware
from .security_headers import csp_header, inline_handler_hashes, security_headers_middleware
from .client_limits import pause_middleware  # C3: operator pause
from .asset_sources import EXECUTOR as _ASSET_EXECUTOR, fetch_asset
from .request_limits import BodyLimitMiddleware, HostGuardMiddleware, allowed_hosts, recursion_handler
from . import fixture_upstream
from .config import CeresConfig
from .routes_harvest_routing import router as harvest_routing_router
from .routes_routing_v2 import router as routing_v2_router
from .routes_auth import router as auth_router
from .routes_me import router as me_router
from .routes_dec import router as dec_router
from .routes_plot_lock import router as plot_lock_router  # by-id verdict for a signed deed lock
from .routes_wallet import router as wallet_router
from .routes_wallet_lp import router as wallet_lp_router
from .routes_board import router as board_router
from .routes_candidates import router as candidates_router
from .routes_combine import router as combine_router
from .routes_combine_chain import router as combine_chain_router
from .routes_op_preview import router as op_preview_router
from .routes_harvest import router as harvest_router
from .routes_stage_plans import router as stage_plans_router  # phase 2
from .routes_fill_plot import router as fill_plot_router  # Fill plot
from .routes_cards import router as cards_router  # card browser
from .routes_market import router as market_router  # card market (read only, on the maintainer's press)
from .routes_wallet_transfer import router as wallet_transfer_router
from .routes_market_purchase import router as market_purchase_router  # checkout (Buy from Ceres)
from .routes_combinables import router as combinables_router  # find combinables (read only)
from .routes_purchase_history import router as purchase_history_router  # on-demand purchases
from .routes_styleguide import router as styleguide_router  # living style guide (read only)

STATIC_DIR = Path(__file__).parent / "static"


class _CachedStaticFiles(StaticFiles):
    """Long `Cache-Control` for `/static/assets/*` only ( whale-account
    performance batch, team-lead: "the icon set is immutable and content-addressed
    in the manifest -- long Cache-Control... would stop them being re-fetched on
    every load"). Scoped to a SEPARATE mount from the main `/static` one
    (`create_app` below registers this one first, at `/static/assets`) rather than
    applied blanket to the whole static tree -- `app.js`/`ceres.css`/`theme.js`/etc.
    MUST stay uncached, since the entire Layer-1/2/3 auto-reload system
    depends on the browser re-fetching those on
    every version bump; a blanket cache header would silently defeat it. Icons are
    genuinely immutable once fetched (manifest.json records their sha256 and
    fetch date; a changed icon gets a new filename, per the existing convention in
    that file), so 30 days + `immutable` is safe."""

    async def get_response(self, path: str, scope) -> Response:  # type: ignore[override]
        try:
            response = await super().get_response(path, scope)
            missing = response.status_code == 404
        except StarletteHTTPException as exc:
            if exc.status_code != 404:
                raise
            response, missing = None, True
        if missing and self.directory is not None:
            # S9 (: the public repository ships no game artwork; a listed image is fetched from its official host on first use.
            assets_dir = Path(self.directory)
            fetched = await asyncio.get_running_loop().run_in_executor(_ASSET_EXECUTOR, fetch_asset, assets_dir, path)
            if fetched is not None:
                response = await super().get_response(path, scope)
        if response is None:
            raise StarletteHTTPException(status_code=404)
        if response.status_code == 200:
            response.headers["Cache-Control"] = "public, max-age=2592000, immutable"
        return response


class _NoCacheStaticFiles(StaticFiles):
    """Explicit `Cache-Control: no-cache` for the main `/static` mount (app.js,
    ceres.css, theme.js, etc -- everything outside `/static/assets`, which is
    matched first by the more specific mount above and never reaches this
    class). The `?v={{CERES_VERSION}}` query token on app.js/ceres.css
    (index.html) already busts the cache on a version bump, but a browser can
    still apply its own heuristic freshness lifetime to an unlabelled 200
    response -- this is belt-and-braces so a stale copy can never survive
    even without the query token changing (e.g. a direct hit on the URL, or a
    browser that ignores query strings in its heuristic). `no-cache` (not
    `no-store`): still cacheable, but the browser MUST revalidate before
    reuse, so a 304 skips re-downloading unchanged bytes."""

    async def get_response(self, path: str, scope) -> Response:  # type: ignore[override]
        response = await super().get_response(path, scope)
        if response.status_code == 200:
            response.headers["Cache-Control"] = "no-cache"
        return response


class _SuppressDevWatchLog(logging.Filter):
    """`/api/dev/watch` is polled every 1.5s by every open browser tab (auto-reload
    Layer 1) -- left unfiltered it would dominate
    uvicorn's access log within minutes."""

    def filter(self, record: logging.LogRecord) -> bool:
        return "/api/dev/watch" not in record.getMessage()


logging.getLogger("uvicorn.access").addFilter(_SuppressDevWatchLog())


def create_app(config: CeresConfig | None = None) -> FastAPI:
    config = config or CeresConfig()
    config.ensure_dirs()

    if config.host not in ("127.0.0.1", "localhost", "::1"):
        logging.getLogger("ceres").warning(
            "ceres is binding to a non-loopback host (%s) -- this leaves the loopback-only "
            "guarantee in docs/ceres.md; make sure that is intended.", config.host,
        )

    # Was hardcoded "" independent of ceres/__init__.py's __version__
    # found stale (still after the version bump to , 2026-08-21
    # polish pass) precisely because it was a second copy of the same fact.
    # Reads __version__ now so this can't drift again.
    try:
        writer = JournalWriter(config.root / "mnemosyne", version=__version__)
    except Exception:
        writer = None

    @asynccontextmanager
    async def lifespan(app):
        if writer is not None:
            writer.start()
        previous_sink = configure_sink(writer)
        try:
            from . import routing_runs_v2   # no one-time routing run survives a server start (what may have been sent is kept as a tiny safety record)
            await asyncio.to_thread(routing_runs_v2.purge_all, config.root)
            # only the disposable run is cleared on restart; tiny unknown-outcome
            # reservations survive so a fresh preview can settle them by transaction ID.
        except Exception:  # noqa: BLE001 -- a purge problem never stops the server
            logging.getLogger("ceres.routing").warning("routing run purge at start failed", exc_info=True)
        try:
            yield
        finally:
            release_sink(writer, previous_sink)
            if writer is not None:
                writer.close()

    app = FastAPI(title="Ceres", version=__version__, lifespan=lifespan)
    app.state.ceres_config = config
    app.state.mnemosyne_writer = writer
    # GZipMiddleware ( whale-account performance batch, team-lead measured
    # live: a 796 KB board response compresses to ~24 KB at level 6, ~33x smaller
    # Ceres was serving every response uncompressed). stdlib-backed (Python's
    # own `gzip`/`zlib`), no new dependency. `minimum_size=1024` skips compressing
    # tiny responses (health checks, dev-watch polls) where the CPU cost isn't
    # worth it; Ceres is loopback-only, so the compression CPU cost itself is
    # trivial next to the transfer/parse time it saves. Composes with (does not
    # replace) the lean list projection above -- lean reduces what there is to
    # compress, gzip then reduces what actually crosses the wire.
    # (2026-09-07): `compresslevel` was the default 9. On a 6,303-plot board (18.8 MB
    # of JSON) level 9 is seconds of CPU on the request thread for a few percent fewer bytes
    # over loopback; level 5 keeps the ~20x reduction the row above describes.
    app.add_middleware(GZipMiddleware, minimum_size=1024, compresslevel=5)
    _hosts = allowed_hosts(config.host)  # S10 / L1-04: DNS-rebinding guard for the loopback deployment
    if _hosts is not None:
        app.add_middleware(HostGuardMiddleware, allowed=_hosts)
    app.add_middleware(BodyLimitMiddleware)  # S1 / L1-04: 4 MiB request-body limit, also for streamed bodies
    app.add_exception_handler(RecursionError, recursion_handler)  # L1-04: absurdly nested JSON -> 422, not 500
    app.middleware("http")(access_log_middleware)  # ceres.access / ceres.action lines
    app.middleware("http")(security_headers_middleware)  # nosniff, Referrer-Policy, X-Frame-Options
    app.middleware("http")(pause_middleware)  # C3: CERES_PAUSED=1 or <root>/PAUSED -> /api/* answers 503
    # fixture upstream -- refused off loopback or on a production-configured process; when on, every
    # upstream answer comes from recorded files and any proxied request is answered 503.
    fixture_dir = fixture_upstream.check_startup(config.host, config.fixture_upstream)
    if fixture_dir is not None:
        fixture_upstream.install(fixture_dir)
        app.middleware("http")(fixture_upstream.forwarded_refusal_middleware)
    handler_hashes = inline_handler_hashes(STATIC_DIR)  # CSP hashes for the modules' inline onerror handlers
    app.include_router(board_router)
    app.include_router(dec_router)
    app.include_router(plot_lock_router)
    app.include_router(wallet_router)  # multi-resource wallet reads
    app.include_router(wallet_lp_router)
    app.include_router(harvest_routing_router)
    app.include_router(routing_v2_router)
    app.include_router(auth_router)  # optional Posting-key login
    app.include_router(me_router)  # the signed-in player's own space
    app.include_router(candidates_router)
    app.include_router(combine_router)
    app.include_router(combine_chain_router)
    app.include_router(op_preview_router)
    app.include_router(harvest_router)
    app.include_router(stage_plans_router)  # phase 2: live check of kept stage plans
    app.include_router(fill_plot_router)  # read-only fill proposal for one plot
    app.include_router(cards_router)  # read-only card browser rows
    app.include_router(market_router)  # read-only market listings, only on the maintainer's press
    app.include_router(wallet_transfer_router)  # player-to-player resource transfer
    app.include_router(market_purchase_router)  # checkout quote, preview, verify, find, test-buy, combine
    app.include_router(combinables_router)  # own combine sets + going-rate upgrade price
    app.include_router(purchase_history_router)
    app.include_router(styleguide_router)  # GET /styleguide
    register_card_art_route(app, store=CardArtStore(config.root / "card-art") if config.card_art_cache_enabled else None, enabled=config.card_art_cache_enabled)
    # Test worktrees link ignored art outside STATIC_DIR; production keeps Starlette's containment default.
    follow_test_asset_links = bool(os.environ.get("CERES_TEST_LOG_DIR"))
    # `/static/assets` mounted BEFORE the general `/static` mount so a more
    # specific path match wins (Starlette matches mounts in registration order)
    # see _CachedStaticFiles above for why only this subtree gets a long
    # Cache-Control and the rest of `/static` (app.js/ceres.css/etc.) must not.
    app.mount(
        "/static/assets",
        _CachedStaticFiles(directory=str(STATIC_DIR / "assets"), follow_symlink=follow_test_asset_links),
        name="static-assets",
    )
    app.mount("/static", _NoCacheStaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.get("/")
    def index(request: Request) -> Response:
        # Version hygiene (board-polish batch 11): the version
        # must be shown IN the page, and it must reflect the version actually served
        # to THIS browser tab, not a fresh fetch painted over stale markup -- that
        # disagreement (stale on-page number vs. live /api/health) is precisely how a
        # cached-JS tab becomes detectable. index.html has no build step (Ceres
        # deliberately has none -- docs/ceres.md), so the substitution happens here
        # at request time, on every GET / -- a tab that loaded this response keeps
        # showing whatever __version__ was live at THAT moment, even after a later
        # bump, until it reloads. See docs/ceres.md "Version hygiene" for the
        # PATCH/MINOR/MAJOR policy this file's __version__ follows.
        html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
        # show and cache-bust on the RELEASED version, the same one
        # api/dev/watch announces. The two must never disagree: a page tag on an
        # unreleased working-tree number against a watch reply on the last real
        # release is what produced a permanent "new version" banner that no reload
        # could clear. released_version() falls back to __version__ when the release
        # record and changelog entry are both present, so a true release is unaffected.
        # the SERVED version -- "<released>+local.<hash>" plus a visible marker when shipped files differ.
        from .routes_board import stamp_version_tokens

        html = stamp_version_tokens(html)
        # (root cause of the maintainer's report, measured: a stale
        # CACHED copy of this very document -- built with an OLD ceres.css
        # reference -- reproduces his 1536px/125% picture exactly; his real
        # browser renders correctly against a live fetch of master. The
        # `?v={{CERES_VERSION}}` query token on app.js/ceres.css (inside this
        # HTML) already busts THEIR cache on a version bump -- but this
        # document's OWN response had no Cache-Control at all, leaving it to
        # the browser's own heuristic freshness lifetime, which is exactly
        # what let a version-old copy of the HTML (and therefore an
        # old-version asset reference) survive past a bump undetected.
        # `no-cache, must-revalidate` (NOT `no-store`): still cacheable, but a
        # tab MUST revalidate with the server before reusing its copy -- an
        # ETag (below) makes that revalidation a cheap 304 instead of a full
        # re-download whenever the document is genuinely unchanged. This
        # mirrors `_NoCacheStaticFiles`'s own `no-cache` reasoning for
        # app.js/ceres.css above -- the SAME belt-and-braces logic, now
        # applied to the document that carries their version-bust query
        # tokens in the first place. `/static/assets/*`'s own long
        # `immutable` caching (`_CachedStaticFiles`, above) is UNCHANGED
        # this only touches the document response itself.
        etag = f'"{hashlib.sha256(html.encode("utf-8")).hexdigest()[:32]}"'
        # the CSP is built from this exact HTML, so every inline script is hashed as served.
        headers = {"Cache-Control": "no-cache, must-revalidate", "ETag": etag, **csp_header(html, handler_hashes)}
        if request.headers.get("if-none-match") == etag:
            return Response(status_code=304, headers=headers)
        return HTMLResponse(html, headers=headers)

    @app.get("/cards")
    def cards_page(request: Request) -> Response:
        # the read-only card browser -- same version stamp, cache and CSP handling as the board page.
        from .routes_board import stamp_version_tokens  # served version and local marker, as on the board

        html = stamp_version_tokens((STATIC_DIR / "cards.html").read_text(encoding="utf-8"))
        etag = f'"{hashlib.sha256(html.encode("utf-8")).hexdigest()[:32]}"'
        headers = {"Cache-Control": "no-cache, must-revalidate", "ETag": etag, **csp_header(html, handler_hashes)}
        if request.headers.get("if-none-match") == etag:
            return Response(status_code=304, headers=headers)
        return HTMLResponse(html, headers=headers)

    return app
