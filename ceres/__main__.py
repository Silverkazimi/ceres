"""``python3 -m ceres`` -- start the Ceres land board, bound to loopback by default."""

from __future__ import annotations

import argparse
import os

import uvicorn

from .app import create_app
from .config import CeresConfig
from .fixture_upstream import FixtureRefusal, check_startup
from .logging_setup import configure_logging
from .reload_watch import start_reload_watch
from .routes_board import dev_watch_enabled


def main() -> None:
    parser = argparse.ArgumentParser(prog="ceres", description="Ceres — read-only Splinterlands Land board")
    parser.add_argument("--host", default="127.0.0.1", help="bind host (default 127.0.0.1, loopback only)")
    parser.add_argument("--port", type=int, default=27390, help="bind port (default 27390)")
    parser.add_argument("--player", default=None, help="optional default player for API calls that name none (default: none -- the page opens on its start page)")
    parser.add_argument("--stale-after", type=int, default=60, help="seconds before the board header marks the snapshot stale")
    parser.add_argument("--capture-concurrency", type=int, default=8, help="full-capture per-deed fan-out thread-pool size (default 8, measured 2026-08-21; does not affect the default quick board load)")
    parser.add_argument("--log-level", default="info")
    parser.add_argument("--no-reload-watch", action="store_true", help="disable the Layer-3 auto-restart-on-change watcher (for tests/one-offs)")
    args = parser.parse_args()

    try:
        check_startup(args.host)  # fixture mode only on loopback, never production
    except FixtureRefusal as exc:
        parser.exit(2, f"ceres: {exc}\n")
    config = CeresConfig(
        host=args.host, port=args.port, player=args.player, stale_after_seconds=args.stale_after,
        capture_concurrency=args.capture_concurrency,
    )
    configure_logging(config.root, args.log_level)  # one timestamped, rotating log file
    app = create_app(config)
    # Q5: remove stored account data this version no longer uses (background, logged).
    from . import __version__, stored_data_cleanup
    stored_data_cleanup.start_in_background(config.root, __version__)
    from . import retention
    retention.start_in_background(config.root)
    # `--no-reload-watch` and `CERES_DEV_WATCH=0` are one switch -- the flag
    # also exports the env so the `/api/dev/watch` endpoint and the browser poll stop.
    if args.no_reload_watch:
        os.environ["CERES_DEV_WATCH"] = "0"
    if dev_watch_enabled():
        start_reload_watch()
    # C3: behind a reverse proxy on the same host (Caddy on the public box) every visitor connects from 127.0.0.1
    # trusting exactly that peer makes request.client.host the visitor's address (uvicorn takes the rightmost
    # untrusted X-Forwarded-For entry), which the per-client limits key on. Explicit, so a stray
    # FORWARDED_ALLOW_IPS=* in the environment can never let a client choose its own key.
    uvicorn.run(app, host=config.host, port=config.port, log_level=args.log_level, log_config=None, access_log=False,
                proxy_headers=True, forwarded_allow_ips="127.0.0.1")


if __name__ == "__main__":
    main()
