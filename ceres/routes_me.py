""": the signed-in player's own space (`/api/me/...`). The account always comes from the login session, never from a name in the request."""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse

from . import client_limits, player_space, retention, routes_auth
from .routes_auth import _state, prove, session_account
from .routes_board import _config, _require_same_origin

router = APIRouter()
NO_STORE = {"Cache-Control": "no-store"}


def _document_response(document: dict, status: int = 200) -> JSONResponse:
    return JSONResponse(document, status_code=status, headers={**NO_STORE, "ETag": player_space.etag(document)})


def _refusal(error: player_space.SpaceError) -> JSONResponse:
    body: dict = {"detail": str(error)}
    if error.current is not None:
        body["current"] = error.current
    return JSONResponse(body, status_code=error.status, headers=NO_STORE)


@router.post("/api/me/delete")
async def delete_my_data(request: Request):
    """ (W3b): delete the account's saved space or cached data. Needs a FRESH Posting signature made for exactly this deletion (a login session is not enough:
    whoever holds an open page must not be able to erase a player's setup). Works with or without a session; sessions of the account end with it."""
    client_limits.limit(request, "auth")
    body = await routes_auth._json(request)
    scope = body.get("scope")
    if scope not in routes_auth.DELETE_SCOPES:
        raise HTTPException(status_code=422, detail="invalid scope")
    item = await prove(request, body, "delete-all")
    if item.scope != routes_auth.DELETE_SCOPES[scope]:
        raise HTTPException(status_code=401, detail="this signature was made for something else")
    root = _config(request).root
    if scope == "space":
        result = await asyncio.to_thread(retention.delete_space, root, item.account)
        _state(request)[1].revoke_account(item.account)
    else:
        result = await asyncio.to_thread(retention.delete_cache, root, item.account)
    return JSONResponse({"account": item.account, "scope": scope, **result}, headers=NO_STORE)


@router.get("/api/me/export")
async def export_everything(request: Request, account: str = Depends(session_account)):
    client_limits.limit(request, "space_read")
    return JSONResponse(await asyncio.to_thread(player_space.export_all, _config(request).root, account), headers=NO_STORE)


@router.get("/api/me/{kind}")
async def read_document(kind: str, request: Request, account: str = Depends(session_account)):
    client_limits.limit(request, "space_read")
    try:
        document = await asyncio.to_thread(player_space.read, _config(request).root, account, kind)
    except player_space.SpaceError as exc:
        return _refusal(exc)
    if document is None:
        return JSONResponse({"exists": False}, headers=NO_STORE)
    return _document_response(document)


@router.put("/api/me/{kind}")
async def write_document(kind: str, request: Request, account: str = Depends(session_account)):
    _require_same_origin(request)
    client_limits.limit(request, "space_write")
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > player_space.MAX_DOCUMENT_BYTES + 4096:
        raise HTTPException(status_code=413, detail="the document is too large")
    try:
        body = await request.json()
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="invalid request") from exc
    if not isinstance(body, dict) or "payload" not in body:
        raise HTTPException(status_code=422, detail="invalid request")
    if_match = request.headers.get("if-match")
    create_only = request.headers.get("if-none-match", "").strip() == "*"
    if if_match is None and not create_only:
        return JSONResponse({"detail": "send If-Match (the version you edited) or If-None-Match: *"}, status_code=428, headers=NO_STORE)
    try:
        document = await asyncio.to_thread(lambda: player_space.write(_config(request).root, account, kind, body["payload"], if_match=if_match, create_only=create_only))
    except player_space.SpaceError as exc:
        return _refusal(exc)
    return _document_response(document)
