"""Response security headers (, v1.0 plan C4).

Two layers, both in the response path -- the page template is never edited for this:

* `security_headers_middleware` adds `X-Content-Type-Options`, `Referrer-Policy` and
  `X-Frame-Options` to every response.
* `content_security_policy(html)` builds the page's CSP from the HTML actually served:
  every inline `<script>` in index.html is allowed by its sha256 hash, so a later edit
  to an inline script re-hashes itself instead of silently breaking the page.

`CERES_CSP` picks how the CSP is sent: `report-only` (the default in this version --
the maintainer signs through the Keychain extension, the Keychain app browser and HiveAuth,
none of which can be exercised headless), `enforce`, or `off`. Report-only ignores
`frame-ancestors`, which is why `X-Frame-Options: DENY` is always sent as well.

Browser-side hosts the page contacts (checked against ceres/static, 2026-09-25) are
listed in library/ceres.md "Security headers"; a new one must be added to
CONNECT_SRC below and to that list in the same change.
"""

from __future__ import annotations

import base64
import hashlib
import os
import re
from pathlib import Path
from typing import Iterable

# fetch()/WebSocket targets outside the Ceres origin: the avatar settings read
# (avatar.js), the signed-transaction queue's Hive RPC (transaction-queue.js), and the
# HiveAuth relay the vendored worker opens (vendor/hiveauth-worker.js).
CONNECT_SRC = (
    "'self'",
    "https://api.splinterlands.com",
    "https://api.hive.blog",
    "wss://hive-auth.arcange.eu",
)

CSP_MODES = ("report-only", "enforce", "off")

STATIC_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "X-Frame-Options": "DENY",
}

_INLINE_SCRIPT = re.compile(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", re.S | re.I)
# Inline event-handler attributes written into HTML strings by the page's own modules
# (combine-dialog.js's image `onerror` fallbacks). Allowed by hash via 'unsafe-hashes'
# a handler that interpolates `${...}` cannot be hashed and is skipped.
_INLINE_HANDLER = re.compile(r'\bon[a-z]+="([^"`]*)"')


def csp_mode() -> str:
    mode = os.getenv("CERES_CSP", "report-only").strip().lower()
    return mode if mode in CSP_MODES else "report-only"


def _sha256(source: str) -> str:
    digest = base64.b64encode(hashlib.sha256(source.encode("utf-8")).digest()).decode("ascii")
    return f"'sha256-{digest}'"


def inline_script_hashes(html: str) -> list[str]:
    return sorted({_sha256(body) for body in _INLINE_SCRIPT.findall(html) if body.strip()})


def inline_handler_hashes(static_dir: Path) -> list[str]:
    found: set[str] = set()
    for path in sorted(static_dir.glob("*.js")):
        for handler in _INLINE_HANDLER.findall(path.read_text(encoding="utf-8")):
            if "${" not in handler:
                found.add(_sha256(handler))
    return sorted(found)


def content_security_policy(html: str, handler_hashes: Iterable[str] = ()) -> str:
    handlers = list(handler_hashes)
    script_src = ["'self'", *inline_script_hashes(html)]
    if handlers:
        script_src += ["'unsafe-hashes'", *handlers]
    directives = [
        "default-src 'self'",
        "script-src " + " ".join(script_src),
        "style-src 'self' 'unsafe-inline'",
        "img-src 'self' data: blob: https:",
        "font-src 'self' data:",
        "connect-src " + " ".join(CONNECT_SRC),
        "worker-src 'self'",
        "object-src 'none'",
        "base-uri 'none'",
        "form-action 'self'",
        "frame-ancestors 'none'",
    ]
    return "; ".join(directives)


def csp_header(html: str, handler_hashes: Iterable[str] = ()) -> dict[str, str]:
    mode = csp_mode()
    if mode == "off":
        return {}
    name = "Content-Security-Policy" if mode == "enforce" else "Content-Security-Policy-Report-Only"
    return {name: content_security_policy(html, handler_hashes)}


async def security_headers_middleware(request, call_next):
    response = await call_next(request)
    for name, value in STATIC_HEADERS.items():
        response.headers.setdefault(name, value)
    return response
