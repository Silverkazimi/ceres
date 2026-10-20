"""the login routes -- a signed text proves the account, a session token is the only door to private resources; nothing else is trusted."""
import hashlib
from types import SimpleNamespace

import pytest

pytest.importorskip("ecdsa")
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from ceres import auth, client_limits, routes_auth
from tests.ceres.test_auth_core import authority, make_key, sign

@pytest.fixture(autouse=True)
def _limits_off(monkeypatch):
    monkeypatch.setenv(client_limits.ENV_SWITCH, "off")   # the flows below make many calls; the limit itself is tested apart


VERIFIER = "browser-verifier-" + "x" * 20
BINDING = hashlib.sha256(VERIFIER.encode()).hexdigest()


def build(authorities):
    app = FastAPI()
    app.state.ceres_config = SimpleNamespace(host="127.0.0.1", port=27390, player=None)
    app.state.authority_reader = lambda account: authorities[account]
    app.include_router(routes_auth.router)

    @app.get("/api/me/probe")
    def probe(account: str = Depends(routes_auth.session_account)):
        return {"account": account}

    return app, TestClient(app, base_url="http://127.0.0.1:27390", raise_server_exceptions=False, headers={"Origin": "http://127.0.0.1:27390"})


def login(client, account, signer, verifier=VERIFIER, purpose="login", scope=None):
    challenge = client.post("/api/auth/challenge", json={"account": account, "binding": BINDING, "purpose": purpose, **({"scope": scope} if scope else {})})
    assert challenge.status_code == 200, challenge.text
    data = challenge.json()
    return client.post("/api/auth/verify", json={"challenge_id": data["challenge_id"], "verifier": verifier,
                                                   "signatures": [sign(signer, data["message"].encode())]}), data


def test_a_valid_posting_signature_opens_a_session_that_names_only_its_own_account():
    alice, alice_pub = make_key(101)
    app, client = build({"alice": {"posting": authority(1, [(alice_pub, 1)]), "delegates": {}}})
    reply, _ = login(client, "alice", alice)
    assert reply.status_code == 200 and reply.json()["account"] == "alice"
    token = reply.json()["token"]
    assert client.get("/api/me/probe").status_code == 401                                         # no token: no door
    assert client.get("/api/me/probe", headers={"Authorization": "Bearer " + token}).json() == {"account": "alice"}
    assert client.get("/api/me/probe?account=bob", headers={"Authorization": "Bearer " + token}).json() == {"account": "alice"}   # a name in the request changes nothing
    client.post("/api/auth/logout", headers={"Authorization": "Bearer " + token})
    assert client.get("/api/me/probe", headers={"Authorization": "Bearer " + token}).status_code == 401


def test_another_players_key_a_wrong_browser_a_reused_challenge_and_a_missing_account_are_all_refused():
    alice, alice_pub = make_key(111)
    player0009, _ = make_key(112)
    app, client = build({"alice": {"posting": authority(1, [(alice_pub, 1)]), "delegates": {}}})
    assert login(client, "alice", player0009)[0].status_code == 401                                   # not alice's key
    assert login(client, "alice", alice, verifier="w" * 30)[0].status_code == 401                  # another browser
    reply, data = login(client, "alice", alice)
    assert reply.status_code == 200
    again = client.post("/api/auth/verify", json={"challenge_id": data["challenge_id"], "verifier": VERIFIER, "signatures": [sign(alice, data["message"].encode())]})
    assert again.status_code == 401                                                                  # a challenge yields one session
    def missing(account):
        raise routes_auth.hive_authority.AuthorityReadError("not found")
    app.state.authority_reader = missing
    assert login(client, "alice", alice)[0].status_code == 503
    assert client.post("/api/auth/challenge", json={"account": "Not A Name", "binding": BINDING}).status_code == 422


def test_a_signature_made_for_one_account_or_one_audience_does_not_open_another():
    alice, alice_pub = make_key(121)
    bob, bob_pub = make_key(122)
    app, client = build({"alice": {"posting": authority(1, [(alice_pub, 1)]), "delegates": {}}, "bob": {"posting": authority(1, [(bob_pub, 1)]), "delegates": {}}})
    first = client.post("/api/auth/challenge", json={"account": "alice", "binding": BINDING}).json()
    # alice's signature offered on bob's challenge: refused
    second = client.post("/api/auth/challenge", json={"account": "bob", "binding": BINDING}).json()
    reply = client.post("/api/auth/verify", json={"challenge_id": second["challenge_id"], "verifier": VERIFIER, "signatures": [sign(alice, second["message"].encode())]})
    assert reply.status_code == 401
    assert "Site: http" in first["message"]


def test_multi_key_and_delegated_accounts_log_in_with_enough_weight_only():
    a, pa = make_key(131)
    b, pb = make_key(132)
    d, pd = make_key(133)
    app, client = build({"player0003": {"posting": authority(2, [(pa, 1), (pb, 1)]), "delegates": {}},
                         "boss": {"posting": authority(1, [], [("clerk", 1)]), "delegates": {"clerk": authority(1, [(pd, 1)])}}})
    one = client.post("/api/auth/challenge", json={"account": "player0003", "binding": BINDING}).json()
    assert client.post("/api/auth/verify", json={"challenge_id": one["challenge_id"], "verifier": VERIFIER, "signatures": [sign(a, one["message"].encode())]}).status_code == 401
    two = client.post("/api/auth/challenge", json={"account": "player0003", "binding": BINDING}).json()
    both = [sign(a, two["message"].encode()), sign(b, two["message"].encode())]
    assert client.post("/api/auth/verify", json={"challenge_id": two["challenge_id"], "verifier": VERIFIER, "signatures": both}).status_code == 200
    assert login(client, "boss", d)[0].status_code == 200


def test_challenges_are_rate_limited_before_any_state_is_kept_and_a_delete_proof_is_not_a_session(monkeypatch):
    alice, alice_pub = make_key(141)
    app, client = build({"alice": {"posting": authority(1, [(alice_pub, 1)]), "delegates": {}}})
    reply, _ = login(client, "alice", alice, purpose="delete-all", scope="space")
    assert reply.status_code == 401 and "token" not in reply.text   # a delete proof never opens a session
    monkeypatch.setenv(client_limits.ENV_SWITCH, "on")
    app2, limited = build({"alice": {"posting": authority(1, [(alice_pub, 1)]), "delegates": {}}})
    codes = [limited.post("/api/auth/challenge", json={"account": "alice", "binding": BINDING}).status_code for _ in range(8)]
    assert 429 in codes and codes[0] == 200
