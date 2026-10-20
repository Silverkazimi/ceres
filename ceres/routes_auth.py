""": the optional Posting-key login. Nothing here is required to browse, to route or to stage locally; it only gives a player the choice of a
server space reachable from any device. The login signs a text, never a transaction."""
from __future__ import annotations

import asyncio
import os

from fastapi import APIRouter, HTTPException, Request

from . import auth, client_limits, hive_authority
from .routes_board import _require_same_origin
from .security import InvalidPlayer, validate_player

router = APIRouter()
DELETE_SCOPES = {
    "space": "all saved data: routing setup, staged changes and their older versions on this server",
    "cache": "cached game data of this account on this server",
}
LOOPBACK = {"127.0.0.1", "localhost", "::1"}


def _state(request: Request):
    state = request.app.state
    if not hasattr(state, "auth_challenges"):
        state.auth_challenges, state.auth_sessions = auth.Challenges(), auth.Sessions()
    return state.auth_challenges, state.auth_sessions


def audience(request: Request) -> str:
    """The origin a signature is bound to. Hosted: the configured public origin (never a request header); local: the exact loopback origin."""
    configured = os.environ.get("CERES_PUBLIC_ORIGIN", "").strip().rstrip("/")
    if configured:
        return configured
    config = request.app.state.ceres_config
    if config.host in LOOPBACK or config.host == "":
        return f"http://{'127.0.0.1' if config.host in ('', '127.0.0.1') else config.host}:{config.port}"
    raise HTTPException(status_code=503, detail="login is not configured on this server")


async def _json(request: Request, limit: int = 8192) -> dict:
    _require_same_origin(request)
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > limit:
        raise HTTPException(status_code=413, detail="request too large")
    try:
        body = await request.json()
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="invalid request") from exc
    if not isinstance(body, dict):
        raise HTTPException(status_code=422, detail="invalid request")
    return body


def bearer(request: Request) -> str | None:
    header = request.headers.get("authorization", "")
    return header[7:].strip() if header[:7].lower() == "bearer " else None


def session_account(request: Request) -> str:
    """The account of a valid bearer session, else 401. Every private `/api/me/...` resource derives its account from this -- never from a name in the request."""
    _challenges, sessions = _state(request)
    account = sessions.account_for(bearer(request))
    if account is None:
        raise HTTPException(status_code=401, detail="sign in required")
    return account


@router.post("/api/auth/challenge")
async def challenge(request: Request):
    client_limits.limit(request, "auth")
    body = await _json(request)
    try:
        account = validate_player(body.get("account"))
    except InvalidPlayer as exc:
        raise HTTPException(status_code=422, detail="invalid account") from exc
    purpose = body.get("purpose", "login")
    if purpose not in auth.PURPOSES:
        raise HTTPException(status_code=422, detail="invalid purpose")
    challenges, _sessions = _state(request)
    scope = ""
    if purpose == "delete-all":
        scope = DELETE_SCOPES.get(body.get("scope"), "")   # the text the wallet shows is the server's own, never a client's wording
        if not scope:
            raise HTTPException(status_code=422, detail="invalid scope")
    try:
        item = challenges.issue(account=account, purpose=purpose, audience=audience(request), binding=str(body.get("binding") or ""), scope=scope)
    except auth.AuthError as exc:
        raise HTTPException(status_code=429 if "too many" in str(exc) else 422, detail=str(exc)) from exc
    return {"challenge_id": item.challenge_id, "message": item.message, "expires_in": auth.CHALLENGE_TTL_SECONDS}


async def prove(request: Request, body: dict, purpose: str):
    """One fresh, purpose-bound proof that the signer controls the account (shared by the login and the deletion). Returns the consumed challenge."""
    challenges, _sessions = _state(request)
    try:
        item = challenges.take(body.get("challenge_id"), body.get("verifier"))
    except auth.AuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    if item.purpose != purpose:
        raise HTTPException(status_code=401, detail="this signature was made for something else")
    reader = getattr(request.app.state, "authority_reader", hive_authority.read_posting_authorities)
    try:
        authorities = await asyncio.to_thread(reader, item.account)
    except hive_authority.AuthorityReadError as exc:
        raise HTTPException(status_code=503, detail="the account could not be checked right now; try again") from exc
    signatures = body.get("signatures")
    if not auth.authority_satisfied(item.message.encode("utf-8"), signatures, authorities.get("posting"), authorities.get("delegates")):
        raise HTTPException(status_code=401, detail="the signature does not match this account's Posting authority")
    if not challenges.consume(item.challenge_id):  # a parallel request already used it: a challenge is good for exactly one use
        raise HTTPException(status_code=401, detail="this login was already used")
    return item


@router.post("/api/auth/verify")
async def verify(request: Request):
    client_limits.limit(request, "auth")
    body = await _json(request)
    _challenges, sessions = _state(request)
    item = await prove(request, body, "login")
    try:
        token = sessions.create(item.account)
    except auth.AuthError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    return {"account": item.account, "token": token, "idle_seconds": auth.SESSION_IDLE_SECONDS, "max_seconds": auth.SESSION_MAX_SECONDS}


@router.get("/api/auth/session")
async def session(request: Request):
    return {"account": session_account(request)}


@router.post("/api/auth/logout")
async def logout(request: Request):
    _require_same_origin(request)
    _challenges, sessions = _state(request)
    sessions.revoke(bearer(request))
    return {"ok": True}
