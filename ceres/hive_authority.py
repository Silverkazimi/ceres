""": one bounded read of an account's public Posting authority (and its delegates' Posting authorities) from a Hive node.

Used only when the player presses the visible "Save across devices" login: one `condenser_api.get_accounts` for the account and, if it delegates its
Posting authority to other accounts, one more for those (at most eight). No history is read."""
from __future__ import annotations

import json
from http.client import HTTPException, HTTPSConnection

from . import fixture_upstream, outbound_meter
from .security import validate_player

MAX_RESPONSE_BYTES = 262_144
MAX_DELEGATES = 8


class AuthorityReadError(RuntimeError):
    pass


def _get_accounts(names: list[str]) -> list[dict]:
    meter_call = outbound_meter.begin_call("api.hive.blog", "condenser_api.get_accounts", names[0])
    connection = fixture_upstream.connection("api.hive.blog") if fixture_upstream.enabled() else HTTPSConnection("api.hive.blog", timeout=15)
    try:
        request = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "condenser_api.get_accounts", "params": [names]})
        connection.request("POST", "/", request, {"Content-Type": "application/json"})
        response = connection.getresponse()
        raw = response.read(MAX_RESPONSE_BYTES + 1)
        if response.status != 200 or len(raw) > MAX_RESPONSE_BYTES:
            raise AuthorityReadError("the account could not be read")
        body = json.loads(raw)
        result = body.get("result") if isinstance(body, dict) else None
        if not isinstance(result, list) or body.get("error"):
            raise AuthorityReadError("the account could not be read")
        return [row for row in result if isinstance(row, dict)]
    except (OSError, ValueError, HTTPException) as exc:
        raise AuthorityReadError("the account could not be read") from exc
    finally:
        outbound_meter.record(meter_call)
        connection.close()


def read_posting_authorities(account: str) -> dict:
    """{"posting": <authority>, "delegates": {name: <authority>}} for `account`; raises AuthorityReadError when the account does not exist or the node fails."""
    account = validate_player(account)
    rows = _get_accounts([account])
    row = next((item for item in rows if item.get("name") == account), None)
    posting = row.get("posting") if row else None
    if not isinstance(posting, dict):
        raise AuthorityReadError("the account was not found")
    delegates: dict[str, dict] = {}
    names = []
    for entry in posting.get("account_auths") or []:
        if isinstance(entry, (list, tuple)) and entry and isinstance(entry[0], str):
            try:
                names.append(validate_player(entry[0]))
            except Exception:  # noqa: BLE001 -- an invalid delegate name is simply not usable
                continue
    names = names[:MAX_DELEGATES]
    if names:
        for item in _get_accounts(names):
            if isinstance(item.get("name"), str) and isinstance(item.get("posting"), dict):
                delegates[item["name"]] = item["posting"]
    return {"posting": posting, "delegates": delegates}
