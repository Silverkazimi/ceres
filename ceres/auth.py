""": the optional Posting-key login (off-chain proof of account ownership).

A player who wants their routing setup and staged changes kept in a server space proves they own the account by signing ONE harmless text with a Posting
key (Hive Keychain `requestSignBuffer` / HiveAuth challenge). It is not a transaction: nothing is broadcast and nothing is paid. The server checks the
signature against the account's CURRENT Posting authority read from a Hive node (one by-account read), then issues a memory-only session token.

Pure functions plus two small in-memory stores; no network and no files here (the Hive read and the HTTP routes live elsewhere).
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from ecdsa import SECP256k1, BadSignatureError, VerifyingKey
from ecdsa.util import sigdecode_string

CHALLENGE_TTL_SECONDS = 300
MAX_PENDING_CHALLENGES = 10_000
MAX_ATTEMPTS = 5
SESSION_IDLE_SECONDS = 30 * 60
SESSION_MAX_SECONDS = 12 * 3600
MAX_SESSIONS = 20_000
MAX_SIGNATURES = 8
MAX_AUTHORITY_ENTRIES = 40
PURPOSES = ("login", "delete-all")

_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_B58_INDEX = {char: index for index, char in enumerate(_B58)}


class AuthError(Exception):
    """A refused proof; the text is safe to show (never a key, signature or nonce)."""


# keys and signatures

def b58decode(text: str) -> bytes:
    number = 0
    for char in text:
        if char not in _B58_INDEX:
            raise AuthError("invalid public key")
        number = number * 58 + _B58_INDEX[char]
    body = number.to_bytes((number.bit_length() + 7) // 8, "big") if number else b""
    pad = len(text) - len(text.lstrip("1"))
    return b"\x00" * pad + body


def parse_public_key(text: Any) -> bytes | None:
    """`STM...` (33-byte compressed key + 4 checksum bytes, base58) -> the 33 key bytes, or None for anything else. The key comes from the chain node
    (the trusted source of the authority), so only its shape and curve membership are checked here."""
    if not isinstance(text, str) or not text.startswith("STM") or len(text) > 60:
        return None
    try:
        payload = b58decode(text[3:])
    except AuthError:
        return None
    if len(payload) != 37 or payload[0] not in (2, 3):
        return None
    key = payload[:33]
    try:
        VerifyingKey.from_string(key, curve=SECP256k1)
    except Exception:  # noqa: BLE001 -- a point that is not on the curve
        return None
    return key


def verify_signature(message: bytes, signature_hex: Any, key: bytes) -> bool:
    """One Hive compact signature (65 bytes: header, r, s) over SHA-256(message), checked against one public key. Never raises."""
    if not isinstance(signature_hex, str) or len(signature_hex) != 130:
        return False
    try:
        raw = bytes.fromhex(signature_hex)
    except ValueError:
        return False
    if not 27 <= raw[0] <= 34:  # compact-signature header: 27 + recovery id (+4 compressed)
        return False
    try:
        verifier = VerifyingKey.from_string(key, curve=SECP256k1)
        return bool(verifier.verify_digest(raw[1:], hashlib.sha256(message).digest(), sigdecode=sigdecode_string))
    except (BadSignatureError, ValueError, AssertionError):
        return False
    except Exception:  # noqa: BLE001 -- malformed numbers must never become a 500 or an accept
        return False


def _authority(value: Any) -> dict | None:
    if not isinstance(value, dict):
        return None
    threshold = value.get("weight_threshold")
    keys, delegates = value.get("key_auths"), value.get("account_auths")
    if isinstance(threshold, bool) or not isinstance(threshold, int) or threshold < 1:
        return None
    if not isinstance(keys, list) or not isinstance(delegates, list) or len(keys) > MAX_AUTHORITY_ENTRIES or len(delegates) > MAX_AUTHORITY_ENTRIES:
        return None
    return {"threshold": threshold, "keys": keys, "delegates": delegates}


def direct_weight(message: bytes, signatures: list[str], authority: dict) -> int:
    """Sum of the weights of the DIRECT keys of an authority that produced a valid signature (each key counted once)."""
    info = _authority(authority)
    if info is None:
        return 0
    seen: set[str] = set()
    total = 0
    for entry in info["keys"]:
        if not (isinstance(entry, (list, tuple)) and len(entry) == 2) or entry[0] in seen:
            continue
        key_text, weight = entry
        if isinstance(weight, bool) or not isinstance(weight, int) or weight < 1:
            continue
        key = parse_public_key(key_text)
        if key is not None and any(verify_signature(message, signature, key) for signature in signatures):
            seen.add(key_text)
            total += weight
    return total


def authority_satisfied(message: bytes, signatures: Any, authority: Any, delegate_authorities: dict[str, Any] | None = None) -> bool:
    """True when the signatures carry at least the Posting authority's weight threshold. Multi-key accounts need the signatures of enough keys;
    an account that delegated its Posting authority (`account_auths`) is satisfied through the delegate's own keys (one level, as read by the caller).
    A key of any other role (Active, Owner, Memo) is never part of the authority that is passed in."""
    if not isinstance(signatures, list) or not signatures or len(signatures) > MAX_SIGNATURES:
        return False
    info = _authority(authority)
    if info is None:
        return False
    weight = direct_weight(message, signatures, authority)
    for entry in info["delegates"]:
        if not (isinstance(entry, (list, tuple)) and len(entry) == 2):
            continue
        name, delegate_weight = entry
        delegate = (delegate_authorities or {}).get(name)
        delegate_info = _authority(delegate)
        if delegate_info is None or isinstance(delegate_weight, bool) or not isinstance(delegate_weight, int) or delegate_weight < 1:
            continue
        if direct_weight(message, signatures, delegate) >= delegate_info["threshold"]:
            weight += delegate_weight
    return weight >= info["threshold"]


# challenges

def _clock(moment: float) -> str:
    """One normalised, human-readable time: `2026-10-02 06:59 UTC`."""
    return time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(moment))


def challenge_message(*, purpose: str, account: str, audience: str, challenge_id: str, expires: float, binding: str, scope: str = "") -> str:
    """The exact text the wallet shows and signs: short, readable, ASCII. The server rebuilds it and never trusts a client copy. The challenge id is the
    one-time random value (144 bits); the first characters of the browser binding tie it to the tab that asked."""
    headline = "Ceres sign-in" if purpose == "login" else "Ceres: delete all my saved data"
    # ONE line, no \n or \r. The Hive Keychain phone app injects the text into a single-quoted JavaScript string; a raw line break is a syntax
    # error there, so the app froze after the maintainer chose a session length and never answered.
    parts = [headline, f"Account: {account}", f"Site: {audience}", f"Valid until: {_clock(expires)}", f"Ref: {challenge_id} / {binding[:8]}"]
    if scope:
        parts.append(f"Scope: {scope}")
    parts.append("Only signs you in to your saved Ceres settings. It sends no transaction and costs nothing." if purpose == "login"
                 else "Confirms deleting your saved Ceres data. It sends no transaction and costs nothing.")
    return " | ".join(parts)


@dataclass
class Challenge:
    challenge_id: str
    account: str
    purpose: str
    audience: str
    binding: str
    scope: str
    message: str
    expires: float
    attempts: int = 0


class Challenges:
    """Pending challenges, in memory only (a restart forgets them). Each one is used at most once and has a small attempt budget."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._items: dict[str, Challenge] = {}

    def issue(self, *, account: str, purpose: str, audience: str, binding: str, scope: str = "", now: float | None = None) -> Challenge:
        if purpose not in PURPOSES:
            raise AuthError("unknown purpose")
        if len(binding) != 64 or any(char not in "0123456789abcdef" for char in binding):
            raise AuthError("invalid browser binding")
        now = time.time() if now is None else now
        with self._lock:
            for key in [k for k, item in self._items.items() if item.expires <= now]:
                del self._items[key]
            if len(self._items) >= MAX_PENDING_CHALLENGES:
                raise AuthError("too many pending logins; try again shortly")
            challenge_id = secrets.token_urlsafe(18)
            expires = now + CHALLENGE_TTL_SECONDS
            message = challenge_message(purpose=purpose, account=account, audience=audience, challenge_id=challenge_id, expires=expires, binding=binding, scope=scope)
            item = Challenge(challenge_id, account, purpose, audience, binding, scope, message, expires)
            self._items[challenge_id] = item
            return item

    def take(self, challenge_id: Any, verifier: Any, now: float | None = None) -> Challenge:
        """Fetch a pending challenge for ONE verification attempt: it must exist, be fresh and match the browser verifier. A challenge is removed when it is
        consumed (`consume`) or when its attempts are used up."""
        now = time.time() if now is None else now
        if not isinstance(challenge_id, str) or not isinstance(verifier, str) or not 16 <= len(verifier) <= 200:
            raise AuthError("invalid login request")
        with self._lock:
            item = self._items.get(challenge_id)
            if item is None or item.expires <= now:
                self._items.pop(challenge_id, None)
                raise AuthError("this login has expired; start again")
            if not hmac.compare_digest(hashlib.sha256(verifier.encode("utf-8")).hexdigest(), item.binding):
                item.attempts += 1
                if item.attempts >= MAX_ATTEMPTS:
                    del self._items[challenge_id]
                raise AuthError("this login belongs to another browser")
            item.attempts += 1
            if item.attempts > MAX_ATTEMPTS:
                del self._items[challenge_id]
                raise AuthError("too many attempts; start again")
            return item

    def consume(self, challenge_id: str) -> bool:
        """Remove a challenge after a successful proof; False if another request already consumed it (a challenge yields at most one session)."""
        with self._lock:
            return self._items.pop(challenge_id, None) is not None


# sessions

@dataclass
class Session:
    account: str
    issued: float
    last_used: float
    purposes: tuple[str, ...] = field(default_factory=lambda: ("login",))


class Sessions:
    """Opaque bearer tokens kept only as hashes, in memory only: a restart or a full page refresh ends every session (the player signs in again)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._items: dict[str, Session] = {}

    @staticmethod
    def _hash(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def create(self, account: str, now: float | None = None) -> str:
        now = time.time() if now is None else now
        with self._lock:
            for key in [k for k, item in self._items.items() if self._expired(item, now)]:
                del self._items[key]
            if len(self._items) >= MAX_SESSIONS:
                raise AuthError("too many open sessions; try again shortly")
            token = secrets.token_urlsafe(32)
            self._items[self._hash(token)] = Session(account, now, now)
            return token

    @staticmethod
    def _expired(item: Session, now: float) -> bool:
        return now - item.last_used > SESSION_IDLE_SECONDS or now - item.issued > SESSION_MAX_SECONDS

    def account_for(self, token: Any, now: float | None = None) -> str | None:
        if not isinstance(token, str) or not 20 <= len(token) <= 100:
            return None
        now = time.time() if now is None else now
        with self._lock:
            key = self._hash(token)
            item = self._items.get(key)
            if item is None:
                return None
            if self._expired(item, now):
                del self._items[key]
                return None
            item.last_used = now
            return item.account

    def revoke(self, token: Any) -> None:
        if isinstance(token, str):
            with self._lock:
                self._items.pop(self._hash(token), None)

    def revoke_account(self, account: str) -> int:
        with self._lock:
            gone = [key for key, item in self._items.items() if item.account == account]
            for key in gone:
                del self._items[key]
            return len(gone)
