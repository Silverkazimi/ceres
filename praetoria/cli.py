"""Command-line interface for Praetoria."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from .cache import JsonCache
from .errors import PraetoriaError
from .models import QuerySpec
from .query import run_query
from .render import to_csv, to_json, to_table
from .api import SplinterlandsClient
from .land import DEFAULT_PLAYER, LandCapture, load_snapshot, recommend_land, recommendation_measure_text, render_land_analysis, save_snapshot
from .land_reallocate import ReallocationCriteria, plan_reallocation, plan_to_dict, render_plan_text


def _range(value: str) -> tuple[int, int]:
    try:
        start, end = value.split(":", 1)
        start, end = int(start), int(end)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("range must be START:END") from exc
    if start < 0 or end < start:
        raise argparse.ArgumentTypeError("range must be a non-negative ascending range")
    return start, end


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="praetoria", description="Read-only Splinterlands queries")
    sub = root.add_subparsers(dest="command", required=True)
    collection = sub.add_parser("collection", help="aggregate a player's card collection")
    collection.add_argument("--player", required=True)
    scope = collection.add_mutually_exclusive_group()
    scope.add_argument("--set", dest="set_name")
    scope.add_argument("--api-card-set")
    scope.add_argument("--id-range", type=_range)
    collection.add_argument("--edition", type=int)
    collection.add_argument("--include-zero", action="store_true")
    collection.add_argument("--foil", choices=["any", "regular", "gold"], default="any")
    collection.add_argument("--stake-status", choices=["any", "active", "historical", "unstaked", "unknown"], default="any")
    collection.add_argument("--delegation-status", choices=["any", "none", "present"], default="any")
    collection.add_argument("--as-of")
    collection.add_argument("--sort", choices=["id", "name", "bcx", "instances", "power"], default="id")
    collection.add_argument("--format", choices=["json", "csv", "table"], required=True)
    collection.add_argument("--output")
    collection.add_argument("--snapshot-dir")
    collection.add_argument("--cache-dir")
    collection.add_argument("--refresh", action="store_true")
    collection.add_argument("--offline", action="store_true")
    collection.add_argument("--timeout", type=float, default=20.0)
    collection.add_argument("--retries", type=int, default=3)
    land = sub.add_parser("land", help="analyze an observed Land portfolio")
    land.add_argument("land_action", choices=["status", "plots", "workers", "diagnostics", "analyze", "recommend", "reallocate"], nargs="?", default="status")
    _land_options(land)
    return root


def _land_options(parser: argparse.ArgumentParser, suppress_defaults: bool = False) -> None:
    default = argparse.SUPPRESS if suppress_defaults else None
    parser.add_argument("--player", default=default or DEFAULT_PLAYER)
    parser.add_argument("--format", choices=["table", "json", "csv"], default=default or "table")
    parser.add_argument("--json", action="store_true", default=default or False)
    parser.add_argument("--csv", action="store_true", default=default or False)
    parser.add_argument("--csv-view", choices=["plots", "workers", "cards"], default=default or "plots")
    parser.add_argument("--offline", action="store_true", default=default or False)
    parser.add_argument("--include-asset-ids", action="store_true", default=default or False)
    parser.add_argument("--include-raw", action="store_true", default=default or False)
    parser.add_argument("--save-snapshot", action="store_true", default=default or False)
    parser.add_argument("--snapshot-path", type=Path, default=default)
    parser.add_argument("--snapshot-dir", type=Path, default=default)
    parser.add_argument("--load-snapshot", type=Path, default=default)
    parser.add_argument("--snapshot", type=Path, default=default)
    parser.add_argument("--cache-dir", type=Path, default=default)
    parser.add_argument("--min-delay", type=float, default=default or 0.300)
    parser.add_argument("--max-requests", type=int, default=default or 200)
    parser.add_argument("--timeout", type=float, default=default or 20.0)
    parser.add_argument("--retries", type=int, default=default or 3)
    parser.add_argument("--as-of", default=default)
    parser.add_argument("--limit", type=int, default=default or 25)
    parser.add_argument("--all", action="store_true", default=default or False)
    parser.add_argument("--goal", choices=["raw-pp", "raw_pp", "pp"], default=default or "raw-pp")
    parser.add_argument("--top-k-per-plot", type=int, default=default or 5)
    parser.add_argument("--beam-width", type=int, default=default or 20)
    parser.add_argument("--candidates-per-plot", type=int, default=default or 24)
    parser.add_argument("--max-candidates-evaluated", type=int, default=default or 12000)
    parser.add_argument("--candidate-pool-limit", type=int, default=default or 256)
    parser.add_argument("--target-rarity", default=default)
    parser.add_argument("--target-status", default=default)
    parser.add_argument("--target-plots", default=default)
    parser.add_argument("--min-mult", type=float, default=default)
    parser.add_argument("--source-rarities", default=default or "common,rare")
    parser.add_argument("--min-card-pp", type=float, default=default)
    parser.add_argument("--allow-negative-terrain", action="store_true", default=default or False)
    parser.add_argument("--no-eviction", action="store_true", default=default or False)
    parser.add_argument("--no-idle", action="store_true", default=default or False)
    parser.add_argument("--assume-capacity", type=int, default=default)
    parser.add_argument("--no-card-abilities", action="store_true", default=default or False)


def _as_of(raw: str | None) -> datetime:
    if not raw:
        return datetime.now(timezone.utc)
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError as exc:
        raise ValueError("--as-of must be ISO-8601") from exc


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "land":
            return _land_main(args)
        if args.command != "collection":
            raise ValueError("unsupported command")
        if args.refresh and args.offline:
            raise ValueError("--refresh and --offline are mutually exclusive")
        query = QuerySpec(player=args.player, set_name=args.set_name, api_card_set=args.api_card_set, id_range=args.id_range, edition=args.edition, include_zero=args.include_zero, foil=args.foil, stake_status=args.stake_status, delegation_status=args.delegation_status, as_of=_as_of(args.as_of), sort=args.sort)
        result = run_query(query, client=SplinterlandsClient(timeout=args.timeout, retries=args.retries), cache=JsonCache(Path(args.cache_dir) if args.cache_dir else None), refresh=args.refresh, offline=args.offline)
        output = {"json": to_json, "csv": to_csv, "table": to_table}[args.format](result)
        if args.output:
            Path(args.output).write_text(output, encoding="utf-8", newline="")
        else:
            sys.stdout.write(output)
        if args.snapshot_dir:
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            path = Path(args.snapshot_dir) / f"{args.player}-{stamp}.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists():
                raise OSError(f"snapshot already exists: {path}")
            path.write_text(to_json(result), encoding="utf-8", newline="")
        return 0
    except PraetoriaError as exc:
        print(f"{exc.code}: {exc.message}", file=sys.stderr)
        return exc.exit_code
    except (ValueError, OSError) as exc:
        print(f"PRAETORIA_E_OUTPUT: {exc}", file=sys.stderr)
        return 7


def _land_main(args: argparse.Namespace) -> int:
    fmt = "json" if args.json else "csv" if args.csv else args.format
    if args.json and args.csv:
        raise ValueError("--json and --csv are mutually exclusive")
    load_path = args.load_snapshot or args.snapshot
    # no built-in account -- a live capture, or a snapshot saved under the account's
    # name, needs --player.
    if not args.player and (not load_path or (args.save_snapshot and args.snapshot_path is None)):
        raise ValueError("--player is required (Praetoria has no default account)")
    if load_path:
        _require_praetoria_storage_path(load_path)
        report = load_snapshot(load_path)
    else:
        if args.offline and args.save_snapshot:
            raise ValueError("--save-snapshot cannot be used with --offline")
        if args.cache_dir:
            _require_praetoria_storage_path(args.cache_dir)
        cache = JsonCache(args.cache_dir) if args.cache_dir else None
        capture = LandCapture(cache=cache, min_delay=args.min_delay, request_budget=args.max_requests)
        report = capture.capture(args.player, offline=args.offline, include_asset_ids=args.include_asset_ids, include_raw=args.include_raw, as_of=_as_of(args.as_of))
    if args.save_snapshot:
        target = args.snapshot_path
        if target is None and args.snapshot_dir is not None:
            target = args.snapshot_dir / f"{args.player}-latest.json"
        if target is None:
            target = Path.home() / ".praetoria" / "snapshots" / f"{args.player}-latest.json"
        _require_praetoria_storage_path(target)
        save_snapshot(report, target)
    if args.land_action == "reallocate":
        return _land_reallocate(args, report, fmt)
    if args.land_action == "workers":
        args.csv_view = "workers"
    if args.land_action == "diagnostics":
        report = dict(report)
        report["plots"] = [row for row in report.get("plots", []) if row.get("diagnostics")]
    if args.land_action == "recommend":
        preamble = render_land_analysis(report, "table", view="status", limit=None)
        if fmt == "table":
            sys.stdout.write(preamble)
        else:
            sys.stderr.write(preamble)
        sys.stdout.flush()
        sys.stderr.flush()
        # plot-wide land-ability/bloodline side-effect pricing is priced BY DEFAULT
        # card_details.json is routinely fetched/cached, so availability is the normal case.
        # `--no-card-abilities` is the escape hatch, not the default; if details genuinely
        # cannot be loaded (offline with no cache, upstream failure, or an
        # unexpected shape), fall back to the unevaluated path -- recommend_land itself marks
        # `side_effects_evaluated: false` and states the assumption explicitly in that case, so a
        # degraded result never silently implies zero side effects.
        details_by_id = None
        if not args.no_card_abilities:
            try:
                cache = JsonCache(args.cache_dir) if args.cache_dir else JsonCache()
                client = SplinterlandsClient(timeout=args.timeout, retries=args.retries)
                details, _info = cache.get_or_fetch("card_details", 86400, args.offline, False, client.details)
                if isinstance(details, list):
                    details_by_id = {item["id"]: item for item in details if isinstance(item, dict) and "id" in item}
            except PraetoriaError:
                details_by_id = None
        report = recommend_land(
            report,
            goal=args.goal,
            top_k=args.top_k_per_plot,
            beam_width=args.beam_width,
            candidates_per_plot=args.candidates_per_plot,
            max_candidates_evaluated=args.max_candidates_evaluated,
            candidate_pool_limit=args.candidate_pool_limit,
            details_by_id=details_by_id,
            progress=lambda message: print(message, file=sys.stderr, flush=True),
            on_recommendation=lambda row: print(
                f"Recommendation found (provisional): {row.get('action')} {row.get('proposal', {}).get('target_plot')} delta={recommendation_measure_text(row.get('delta', {}).get('value'), row.get('delta', {}).get('reason'))}",
                file=sys.stderr,
                flush=True,
            ),
        )
    output = render_land_analysis(report, fmt, args.csv_view, view=args.land_action, limit=args.limit, show_all=args.all, include_prefix=args.land_action != "recommend")
    sys.stdout.write(output)
    return 0


def _split_csv(value: str | None) -> frozenset[str] | None:
    if not value:
        return None
    return frozenset(item.strip() for item in value.split(",") if item.strip())


def _land_reallocate(args: argparse.Namespace, report: dict, fmt: str) -> int:
    if fmt not in ("table", "json"):
        raise ValueError("land reallocate only supports --format table or --format json (csv is not applicable)")
    cache = JsonCache(args.cache_dir) if args.cache_dir else JsonCache()
    client = SplinterlandsClient(timeout=args.timeout, retries=args.retries)
    details, _info = cache.get_or_fetch("card_details", 86400, args.offline, False, client.details)
    if not isinstance(details, list):
        raise ValueError("card_details cache/fetch returned an unexpected shape")
    details_by_id = {item["id"]: item for item in details if isinstance(item, dict) and "id" in item}
    source_rarities = _split_csv(args.source_rarities) or frozenset({"common", "rare"})
    criteria = ReallocationCriteria(
        target_rarities=_split_csv(args.target_rarity),
        target_statuses=_split_csv(args.target_status),
        target_plots=_split_csv(args.target_plots),
        min_mult=args.min_mult,
        source_rarities=source_rarities,
        include_idle=not args.no_idle,
        min_card_pp=args.min_card_pp,
        require_nonnegative_terrain=not args.allow_negative_terrain,
        allow_eviction=not args.no_eviction,
        assume_capacity_for_unpowered=args.assume_capacity,
    )
    now = _as_of(args.as_of).isoformat().replace("+00:00", "Z")
    plan, plots = plan_reallocation(report, details_by_id, criteria, now=now)
    if fmt == "json":
        sys.stdout.write(json.dumps(plan_to_dict(plan), indent=2, default=str))
        sys.stdout.write("\n")
    else:
        sys.stdout.write(render_plan_text(plan, plots))
    return 0


def _require_praetoria_storage_path(path: Path) -> None:
    root = (Path.home() / ".praetoria").resolve()
    target = path.expanduser().resolve()
    if target != root and root not in target.parents:
        raise ValueError("Land cache and snapshots must be under ~/.praetoria")
