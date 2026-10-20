""" -- Ceres logs: complete, timestamped, rotated, and readable.

One log file, ``<root>/logs/ceres.log`` (10 MB x 5 rotations), every line ``<local ISO time> <LEVEL> <logger>: <message>``:

- ``ceres.access`` -- one line per HTTP request: client IP, method, path+query, status, duration, response size.
  The dev watcher (``/api/dev/watch``) and successful static files are not logged; anything >= 400 always is.
- ``ceres.action`` -- one line per POST (a request that asks Ceres to do something: capture refresh, harvest
  attempt/verify, op-verify, combine build, fill proposal, stage checks) with the player and the outcome status.
- ``ceres.*`` -- the modules' own INFO/WARNING lines (capture start/finish, refusals, failures).
- uvicorn's own loggers (startup, errors) go to the same file; its access log is off (``ceres.access`` replaces it).

stderr (the service log / journald) gets WARNING and up only, so it stops growing by the megabyte. Read the file
with ``python3 -m ceres.logs`` (tail / since / grep / summary), the same on a server.
"""

from __future__ import annotations

import hashlib
import logging
import logging.handlers
import os
import re
import secrets
import time
from pathlib import Path
from typing import Callable

from starlette.requests import Request
from starlette.responses import Response

FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
DATE_FORMAT = "%Y-%m-%dT%H:%M:%S%z"
MAX_BYTES = 10 * 1024 * 1024
BACKUP_COUNT = 5

access_logger = logging.getLogger("ceres.access")
action_logger = logging.getLogger("ceres.action")

_SKIP_PATHS = ("/api/dev/watch",)


# (W1): a hosted Ceres keeps no account name or address in its logs. With CERES_REDACT_LOGS=1 every account name (`player=`, `account=`, `Account:`) and every
# IP address becomes a short tag that is stable inside one process run (so one failure can still be followed through its lines) and means nothing after a restart
# the file rotates by day and keeps seven days.
_REDACT_SALT = secrets.token_hex(8)
_REDACTIONS = (
    (re.compile(r'(?i)(["\'](?:player|account|username|user_name|owner|owner_name|account_name|display_name)["\']\s*:\s*["\'])([^"\']*)(["\'])'), lambda m: f"{m.group(1)}{_tag(m.group(2))}{m.group(3)}"),
    (re.compile(r"(?i)\b(player|account|user|owner)=([^\s&,;\"')\]]+)"), lambda m: f"{m.group(1)}={_tag(m.group(2))}"),
    (re.compile(r"(?i)\b(player|account|username|user_name|owner|account_name|display_name)\s*[:=]\s*([a-z0-9_.-]+)"), lambda m: f"{m.group(1)}={_tag(m.group(2))}"),
    (re.compile(r"(?i)\b(?:for|about|of)\s+(?:player|account|user|owner)\s+([a-z0-9_.-]+)"), lambda m: f"for account={_tag(m.group(1))}"),
    (re.compile(r"(?i)\bAccount: ([a-z0-9.-]+)"), lambda m: f"Account: {_tag(m.group(1))}"),
    (re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b"), lambda m: _tag(m.group(0), "ip")),
    (re.compile(r"(?<![\w:])(?:(?:[0-9a-fA-F]{1,4}:){7}[0-9a-fA-F]{1,4}|(?:[0-9a-fA-F]{1,4}:){1,7}:(?:[0-9a-fA-F]{1,4}(?::[0-9a-fA-F]{1,4}){0,6})?|::(?:[0-9a-fA-F]{1,4}(?::[0-9a-fA-F]{1,4}){0,6})?)(?![\w:])"), lambda m: _tag(m.group(0), "ip")),
)


def _tag(value: str, prefix: str = "p") -> str:
    return f"{prefix}-{hashlib.sha256((_REDACT_SALT + value.lower()).encode()).hexdigest()[:6]}"


def redact(text: str) -> str:
    for pattern, replace in _REDACTIONS:
        text = pattern.sub(replace, text)
    return text


def redact_account_text(text: str, account: str) -> str:
    """Redact a known request account even when an upstream error mentions it without a label."""
    if not redacting():
        return text
    if account:
        text = re.sub(re.escape(account), _tag(account), text, flags=re.IGNORECASE)
    return redact(text)


class RedactingFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record))


def redacting() -> bool:
    return os.environ.get("CERES_REDACT_LOGS", "").strip() == "1"


def log_dir(root: Path) -> Path:
    override = os.environ.get("CERES_TEST_LOG_DIR")
    if override:
        return Path(override)
    return Path(root) / "logs"


def log_file(root: Path) -> Path:
    return log_dir(root) / "ceres.log"


def configure_logging(root: Path, level: str = "info") -> Path:
    """Install the file + stderr handlers on the root logger. Idempotent (a second call changes nothing)."""
    root_logger = logging.getLogger()
    if getattr(root_logger, "_ceres_configured", False):
        return log_file(root)
    directory = log_dir(root)
    directory.mkdir(parents=True, exist_ok=True)
    private = redacting()
    formatter = (RedactingFormatter if private else logging.Formatter)(FORMAT, DATE_FORMAT)
    file_handler = (logging.handlers.TimedRotatingFileHandler(log_file(root), when="midnight", backupCount=7, utc=True, encoding="utf-8") if private
                    else logging.handlers.RotatingFileHandler(log_file(root), maxBytes=MAX_BYTES, backupCount=BACKUP_COUNT, encoding="utf-8"))
    file_handler.setFormatter(formatter)
    file_handler.setLevel(logging.DEBUG)
    stderr_handler = logging.StreamHandler()
    stderr_handler.setFormatter(formatter)
    stderr_handler.setLevel(logging.WARNING)
    for handler in list(root_logger.handlers):
        root_logger.removeHandler(handler)
    root_logger.addHandler(file_handler)
    root_logger.addHandler(stderr_handler)
    root_logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    # uvicorn is started with log_config=None, so its loggers propagate here. Its access logger is silenced
    # ceres.access carries the request lines (with the client IP, duration and size uvicorn's line lacks).
    for name in ("uvicorn", "uvicorn.error"):
        logger = logging.getLogger(name)
        logger.handlers.clear()
        logger.propagate = True
    logging.getLogger("uvicorn.access").disabled = True
    root_logger._ceres_configured = True  # type: ignore[attr-defined]
    logging.getLogger("ceres").info("logging to %s (%s)", log_file(root), "names and addresses redacted, 7 daily files" if private else f"rotating {BACKUP_COUNT} x {MAX_BYTES // (1024 * 1024)} MB")
    return log_file(root)


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "-"


def log_safe(value: object, limit: int = 160) -> str:
    """ S1 / L1-07: a client-controlled value never forges a log line: control characters are escaped, the length is bounded."""
    text = str(value)
    cleaned = "".join(ch if ch.isprintable() and ch not in "\u2028\u2029" else f"\\x{ord(ch):02x}" if ord(ch) < 256 else f"\\u{ord(ch):04x}" for ch in text[:limit])
    return cleaned + ("..." if len(text) > limit else "")


async def access_log_middleware(request: Request, call_next: Callable) -> Response:
    """One line per request (ceres.access) and one per POST (ceres.action). Never fails a request on its own."""
    started = time.perf_counter()
    path = request.url.path
    try:
        response = await call_next(request)
    except Exception:
        elapsed = (time.perf_counter() - started) * 1000
        access_logger.error("%s %s %s 500 %.0fms unhandled", client_ip(request), request.method, _target(request), elapsed)
        raise
    elapsed = (time.perf_counter() - started) * 1000
    status = response.status_code
    quiet = path.startswith(_SKIP_PATHS) or (path.startswith("/static/") and status < 400)
    if not quiet:
        size = response.headers.get("content-length", "-")
        access_logger.log(logging.WARNING if status >= 500 else logging.INFO, "%s %s %s %s %.0fms %sB",
                          client_ip(request), request.method, _target(request), status, elapsed, size)
        if request.method == "POST":
            player = log_safe(request.query_params.get("player") or "-", 64)
            action_logger.log(logging.WARNING if status >= 400 else logging.INFO, "%s player=%s -> %s in %.0fms from %s",
                              log_safe(path), player, status, elapsed, client_ip(request))
    return response


def _target(request: Request) -> str:
    if redacting():
        # Query strings may contain arbitrary account aliases and client data.
        return log_safe(request.url.path, 400)
    query = request.url.query
    return log_safe(f"{request.url.path}?{query}" if query else request.url.path, 400)
