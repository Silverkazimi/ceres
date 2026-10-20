"""Ceres runtime configuration.

Everything Ceres writes to disk lives under `config.root` (default `~/.ceres/`),
never in the repository and never in `~/.praetoria/` (Praetoria's own cache/snapshot
store) -- see docs/ceres.md and the plan's §3.3.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path


@dataclass
class CeresConfig:
    root: Path = Path.home() / ".ceres"
    # no built-in account. None means every account-scoped request must name its
    # player; `--player` sets a default for an operator who wants one.
    player: str | None = None
    host: str = "127.0.0.1"
    port: int = 27390
    # 60 seconds is achievable rather than aspirational: deeds measure 1.9–4.4s
    # collection is 4.2s with the compression header in v0.8.44, and the
    # `stake_ref_uid` roster join makes a complete board read 2 requests and about 4.4s
    # roughly thirteen times the headroom against a 60-second ceiling. A threshold
    # loosened to fit a slow system stops being a promise and becomes decoration.
    stale_after_seconds: int = 60
    capture_timeout: int = 180
    card_art_cache_enabled: bool | None = None
    # Full-capture per-deed fan-out concurrency (2026-08-21) -- see
    # praetoria.land_analysis.DEFAULT_CONCURRENCY for the measured rationale (8
    # concurrent requests, 6.5x wall-clock reduction, no observed throttling).
    # Configurable per the same instruction that asked for this feature; a lower
    # value here does NOT change the quick (default) board load, which never
    # fans out per-deed at all.
    capture_concurrency: int = 8
    # plan §13 1C/1D: harvest broadcasting stays OFF until the maintainer signs the first
    # on-chain test (plan §10). (maintainer, 2026-09-11, after the test succeeded): ON by default for single plots
    # Regions and Harvest all; `CERES_HARVEST_SIGNING=0` turns it off. The first region harvest is the maintainer-watched test.
    harvest_signing_enabled: bool | None = None
    harvest_routing_signing_enabled: bool | None = None
    wallet_lp_signing_enabled: bool | None = None
    routing_kinds: frozenset[str] = frozenset()
    routing_offer_kinds: frozenset[str] = frozenset()   # allowed, but the screen offers them only where needed
    # with `CERES_HARVEST_SIGNING=0`, signing stays enabled for exactly these
    # (account, deed_uid) single-plot harvests. : empty by default (the test
    # pair was the maintainer's own plot); consulted only when harvest signing is switched off.
    harvest_signing_allowlist: frozenset = frozenset()
    # a fixture set directory answers every upstream request (loopback test/rig runs only
    # `ceres.fixture_upstream.check_startup` refuses it anywhere else). Defaults to CERES_FIXTURE_UPSTREAM.
    fixture_upstream: Path | None = None

    def __post_init__(self) -> None:
        self.routing_kinds = frozenset(self.routing_kinds)
        if os.getenv("CERES_ROUTING_SALE_DEC", "0").strip().lower() in {"1", "true", "yes", "on"}:
            self.routing_kinds = self.routing_kinds | {"sale_dec"}
        if os.getenv("CERES_ROUTING_LP_WALLET", "0").strip().lower() in {"1", "true", "yes", "on"}:
            self.routing_kinds = self.routing_kinds | {"lp_wallet"}
        if os.getenv("CERES_ROUTING_LP_SELL_HALF", "0").strip().lower() in {"1", "true", "yes", "on"}:
            self.routing_kinds = self.routing_kinds | {"lp_sell_half"}
        # with wallet-DEC liquidity on, sell-half is listed on every LP route. ( hid it until the exact preview showed a
        # wallet-DEC shortfall, so it was only ever offered on the one route that had just been reviewed.)
        if "lp_wallet" in self.routing_kinds and "lp_sell_half" not in self.routing_kinds:
            self.routing_kinds = self.routing_kinds | {"lp_sell_half"}
        if self.harvest_routing_signing_enabled is None:
            self.harvest_routing_signing_enabled = os.getenv("CERES_HARVEST_ROUTING_SIGNING", "0").strip().lower() in {"1", "true", "yes", "on"}
        if self.harvest_signing_enabled is None:
            self.harvest_signing_enabled = os.getenv("CERES_HARVEST_SIGNING", "1").strip().lower() in {"1", "true", "yes", "on"}
        if self.wallet_lp_signing_enabled is None:
            self.wallet_lp_signing_enabled = os.getenv("CERES_WALLET_LP_SIGNING", "1").strip().lower() in {"1", "true", "yes", "on"}
        if isinstance(self.root, str):
            self.root = Path(self.root)
        if self.fixture_upstream is None and os.getenv("CERES_FIXTURE_UPSTREAM", "").strip():
            self.fixture_upstream = Path(os.environ["CERES_FIXTURE_UPSTREAM"].strip())
        if self.fixture_upstream is not None:
            self.fixture_upstream = Path(self.fixture_upstream)
            self.card_art_cache_enabled = False  # the art cache would fetch from the CDN
        if self.card_art_cache_enabled is None:
            self.card_art_cache_enabled = os.getenv("CERES_CARD_ART_CACHE", "1").strip().lower() in {
                "1", "true", "yes", "on",
            }

    @property
    def snapshots_dir(self) -> Path:
        return self.root / "snapshots"

    @property
    def log_path(self) -> Path:
        return self.root / "ceres.log"

    def ensure_dirs(self) -> None:
        self.snapshots_dir.mkdir(parents=True, exist_ok=True)
