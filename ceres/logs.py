"""``python3 -m ceres.logs`` -- read the Ceres log (): tail, filter, summarise.

    python3 -m ceres.logs                       # last 100 lines
    python3 -m ceres.logs --tail 500 --since 2h # last 500 lines of the past two hours
    python3 -m ceres.logs --kind action         # only what people asked Ceres to do (POSTs)
    python3 -m ceres.logs --grep harvest --level WARNING
    python3 -m ceres.logs --summary --since 24h # requests, statuses, visitors, actions, warnings

Reads the rotated files too (ceres.log.5 ... ceres.log.1, ceres.log), oldest first. ``--file`` points elsewhere
(on a server: the log under that server's data root, ``logging_setup.log_file(<root>)``).
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .config import CeresConfig
from .logging_setup import BACKUP_COUNT, log_file

LINE = re.compile(r"^(?P<ts>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{4}) (?P<level>[A-Z]+)\s+(?P<name>[\w.\-]+): (?P<msg>.*)$")
ACCESS = re.compile(r"^(?P<ip>\S+) (?P<method>[A-Z]+) (?P<target>\S+) (?P<status>\d{3}) (?P<ms>\d+)ms (?P<size>\S+)B$")
KINDS = {"access": "ceres.access", "action": "ceres.action", "transaction": "ceres.transaction", "routing": "ceres.routing"}
LEVELS = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


def parse_since(value: str | None, now: datetime) -> datetime | None:
    if not value:
        return None
    match = re.fullmatch(r"(\d+)([smhd])", value.strip())
    if match:
        amount, unit = int(match.group(1)), match.group(2)
        delta = {"s": timedelta(seconds=amount), "m": timedelta(minutes=amount), "h": timedelta(hours=amount), "d": timedelta(days=amount)}[unit]
        return now - delta
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=now.tzinfo)


def log_files(path: Path) -> list[Path]:
    rotated = [path.with_name(f"{path.name}.{i}") for i in range(BACKUP_COUNT, 0, -1)]
    return [p for p in rotated if p.exists()] + ([path] if path.exists() else [])


def read_entries(path: Path, since: datetime | None):
    """Yield (timestamp, level, name, message, raw) for every parseable line; continuation lines join the previous."""
    current = None
    for file in log_files(path):
        with file.open("r", encoding="utf-8", errors="replace") as handle:
            for raw in handle:
                raw = raw.rstrip("\n")
                match = LINE.match(raw)
                if not match:
                    if current is not None:
                        current[3] += "\n" + raw
                        current[4] += "\n" + raw
                    continue
                if current is not None:
                    yield tuple(current)
                ts = datetime.strptime(match.group("ts"), "%Y-%m-%dT%H:%M:%S%z")
                current = [ts, match.group("level"), match.group("name"), match.group("msg"), raw]
                if since is not None and ts < since:
                    current = None
    if current is not None:
        yield tuple(current)


def summarise(entries) -> str:
    requests = Counter()
    statuses = Counter()
    paths = Counter()
    ips = Counter()
    players = Counter()
    actions = Counter()
    slow: list[tuple[int, str]] = []
    problems: list[str] = []
    first = last = None
    total = 0
    for ts, level, name, msg, raw in entries:
        total += 1
        first = first or ts
        last = ts
        if name == "ceres.access":
            match = ACCESS.match(msg)
            if match:
                requests["all"] += 1
                statuses[match.group("status")] += 1
                target = match.group("target")
                path = target.split("?", 1)[0]
                paths[path] += 1
                ips[match.group("ip")] += 1
                player = re.search(r"[?&]player=([^&]+)", target)
                if player:
                    players[player.group(1)] += 1
                ms = int(match.group("ms"))
                if ms >= 2000:
                    slow.append((ms, f"{match.group('method')} {target}"))
        elif name == "ceres.action":
            actions[msg.split(" ", 1)[0]] += 1
        if level in ("WARNING", "ERROR", "CRITICAL") and name != "ceres.access":
            problems.append(raw.split("\n", 1)[0])
    if not total:
        return "no log lines in that window"
    span = f"{first:%Y-%m-%d %H:%M} -> {last:%Y-%m-%d %H:%M}"
    out = [f"Ceres log summary  {span}  ({total} lines)", ""]
    out.append(f"Requests: {requests['all']}  statuses: " + ", ".join(f"{s} x{n}" for s, n in sorted(statuses.items())))
    out.append("Top paths:   " + ", ".join(f"{p} x{n}" for p, n in paths.most_common(8)))
    out.append(f"Visitors:    {len(ips)} IPs -- " + ", ".join(f"{ip} x{n}" for ip, n in ips.most_common(8)))
    if players:
        out.append("Accounts:    " + ", ".join(f"{p} x{n}" for p, n in players.most_common(10)))
    if actions:
        out.append("Actions:     " + ", ".join(f"{a} x{n}" for a, n in actions.most_common(12)))
    if slow:
        slow.sort(reverse=True)
        out.append("Slow (>=2s): " + "; ".join(f"{ms}ms {t}" for ms, t in slow[:6]))
    out.append(f"Warnings/errors: {len(problems)}")
    out.extend("  " + line for line in problems[-10:])
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python3 -m ceres.logs", description="Read the Ceres log.")
    parser.add_argument("--file", type=Path, default=None, help="log file (default: <root>/logs/ceres.log)")
    parser.add_argument("--tail", type=int, default=100, help="last N lines (0 = all)")
    parser.add_argument("--since", default=None, help="window: 30m, 2h, 3d, or an ISO time")
    parser.add_argument("--kind", choices=sorted(KINDS), default=None, help="only access lines or only action lines")
    parser.add_argument("--level", choices=LEVELS, default=None, help="at least this level")
    parser.add_argument("--grep", default=None, help="regular expression on the line")
    parser.add_argument("--summary", action="store_true", help="counts instead of lines")
    args = parser.parse_args(argv)
    path = args.file or log_file(CeresConfig().root)
    if not log_files(path):
        print(f"no log file at {path}", file=sys.stderr)
        return 1
    now = datetime.now(timezone.utc).astimezone()
    since = parse_since(args.since, now)
    entries = read_entries(path, since)
    if args.kind:
        entries = (e for e in entries if e[2] == KINDS[args.kind])
    if args.level:
        floor = LEVELS.index(args.level)
        entries = (e for e in entries if e[1] in LEVELS and LEVELS.index(e[1]) >= floor)
    if args.grep:
        pattern = re.compile(args.grep, re.IGNORECASE)
        entries = (e for e in entries if pattern.search(e[4]))
    if args.summary:
        print(summarise(entries))
        return 0
    lines = [e[4] for e in entries]
    if args.tail:
        lines = lines[-args.tail:]
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
