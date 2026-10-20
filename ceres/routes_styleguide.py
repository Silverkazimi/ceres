""": GET /styleguide -- the living Ceres style guide.

A read-only page that renders every Ceres UI component from the real stylesheets (ceres.css, buttons.css,
badge-colours.css, card-browser.css, card-checkout.css, mobile-plot.css) with static sample data embedded in
ceres/static/styleguide.js, so drift between the written standard (library/ceres-ui-standards.md) and the screens is
visible at a glance. No data reads, no account, no API call: the page is the same for everyone.

Served like /cards (ceres/app.py): the version tokens are filled by routes_board.stamp_version_tokens, the document
is revalidated on every load (no-cache + ETag) and carries the page's own CSP (security_headers.csp_header).
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from fastapi import APIRouter, Request, Response
from fastapi.responses import HTMLResponse

from .security_headers import csp_header

router = APIRouter()

STYLEGUIDE_HTML = Path(__file__).resolve().parent / "static" / "styleguide.html"


@router.get("/styleguide", include_in_schema=False)
def styleguide_page(request: Request) -> Response:
    from .routes_board import stamp_version_tokens  # served version and local marker, as on /cards

    html = stamp_version_tokens(STYLEGUIDE_HTML.read_text(encoding="utf-8"))
    etag = f'"{hashlib.sha256(html.encode("utf-8")).hexdigest()[:32]}"'
    headers = {"Cache-Control": "no-cache, must-revalidate", "ETag": etag, **csp_header(html)}
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    return HTMLResponse(html, headers=headers)
