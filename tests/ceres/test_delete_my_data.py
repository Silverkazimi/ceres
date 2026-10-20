""" (W3b): deleting saved data needs a fresh signature made for exactly that deletion; a login session or a signature for something else is not enough."""
import hashlib
import os
from types import SimpleNamespace

import pytest

pytest.importorskip("ecdsa")
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ceres import client_limits, player_space, retention, routes_auth, routes_me, routing_safety
from tests.ceres.test_auth_core import authority, make_key, sign

VERIFIER = "browser-verifier-" + "x" * 20
BINDING = hashlib.sha256(VERIFIER.encode()).hexdigest()


@pytest.fixture(autouse=True)
def _limits_off(monkeypatch):
    monkeypatch.setenv(client_limits.ENV_SWITCH, "off")


def build(tmp_path, authorities):
    app = FastAPI()
    app.state.ceres_config = SimpleNamespace(host="127.0.0.1", port=27390, player=None, root=tmp_path)
    app.state.authority_reader = lambda account: authorities[account]
    app.include_router(routes_auth.router)
    app.include_router(routes_me.router)
    return TestClient(app, base_url="http://127.0.0.1:27390", raise_server_exceptions=False, headers={"Origin": "http://127.0.0.1:27390"})


def proof(client, account, signer, purpose, scope=None):
    challenge = client.post("/api/auth/challenge", json={"account": account, "binding": BINDING, "purpose": purpose, **({"scope": scope} if scope else {})}).json()
    return {"challenge_id": challenge["challenge_id"], "verifier": VERIFIER, "signatures": [sign(signer, challenge["message"].encode())]}, challenge["message"]


def login(client, account, signer):
    body, _ = proof(client, account, signer, "login")
    return client.post("/api/auth/verify", json=body).json()["token"]


def test_a_fresh_signature_for_this_deletion_erases_the_saved_space_and_its_versions(tmp_path):
    alice, alice_pub = make_key(201)
    client = build(tmp_path, {"alice": {"posting": authority(1, [(alice_pub, 1)]), "delegates": {}}})
    token = login(client, "alice", alice)
    h = {"Authorization": "Bearer " + token}
    reply = client.put("/api/me/routing", json={"payload": {"rules": [{"id": "a"}]}}, headers={**h, "If-None-Match": "*"})
    for n in range(3):
        reply = client.put("/api/me/routing", json={"payload": {"rules": [{"id": "a"}, {"id": str(n)}]}}, headers={**h, "If-Match": reply.headers["etag"]})
    assert client.get("/api/me/routing", headers=h).json()["revision"] == 4
    body, message = proof(client, "alice", alice, "delete-all", "space")
    assert "delete all my saved data" in message and "routing setup, staged changes" in message   # the wallet shows what is deleted
    done = client.post("/api/me/delete", json={**body, "scope": "space"})
    assert done.status_code == 200 and done.json()["documents"] >= 4 and done.json()["scope"] == "space"
    assert client.get("/api/me/routing", headers=h).status_code == 401                              # its sessions ended with it
    assert list((tmp_path / "player-space" / "alice").rglob("*.json")) == []
    assert client.post("/api/me/delete", json={**body, "scope": "space"}).status_code == 401       # a proof is good once


def test_a_login_signature_another_scope_or_another_account_cannot_delete(tmp_path):
    alice, alice_pub = make_key(211)
    player0009, _ = make_key(212)
    client = build(tmp_path, {"alice": {"posting": authority(1, [(alice_pub, 1)]), "delegates": {}}})
    token = login(client, "alice", alice)
    h = {"Authorization": "Bearer " + token}
    client.put("/api/me/routing", json={"payload": {"rules": []}}, headers={**h, "If-None-Match": "*"})
    login_body, _ = proof(client, "alice", alice, "login")
    assert client.post("/api/me/delete", json={**login_body, "scope": "space"}).status_code == 401           # a login text is not a deletion
    cache_body, _ = proof(client, "alice", alice, "delete-all", "cache")
    assert client.post("/api/me/delete", json={**cache_body, "scope": "space"}).status_code == 401           # signed for the cache, asked for the space
    forged, _ = proof(client, "alice", player0009, "delete-all", "space")
    assert client.post("/api/me/delete", json={**forged, "scope": "space"}).status_code == 401               # not alice's key
    assert client.post("/api/me/delete", json={"scope": "everything"}).status_code == 422
    assert client.post("/api/me/delete", headers=h, json={"scope": "space"}).status_code in (401, 422)       # a session alone is not enough
    assert client.get("/api/me/routing", headers=h).status_code == 200                                         # nothing was deleted


def test_cache_deletion_leaves_the_saved_setup_and_stale_caches_expire_after_seven_days(tmp_path, monkeypatch):
    monkeypatch.setenv("PRAETORIA_CACHE_DIR", str(tmp_path / "cache"))
    (tmp_path / "cache").mkdir()
    snapshots = tmp_path / "snapshots"
    snapshots.mkdir()
    for player in ("alice", "bob"):
        (snapshots / f"board-{player}-counter.json").write_text("{}")
        (snapshots / f"board-{player}-abc-1.json").write_text("{}")
        (snapshots / f"land-{player}-latest.json").write_text("{}")
        (tmp_path / "cache" / f"land_deeds_{player}.json").write_text("{}")
        player_space.write(tmp_path, player, "routing", {"rules": []}, if_match=None, create_only=True)
    old = 1_000_000.0
    for path in list(snapshots.glob("*bob*")) + [tmp_path / "cache" / "land_deeds_bob.json"]:
        os.utime(path, (old, old))
    assert retention.purge_stale_caches(tmp_path, now=old + 8 * 86400) == {"players": 1}
    assert not list(snapshots.glob("*bob*")) and list(snapshots.glob("*alice*")) and not (tmp_path / "cache" / "land_deeds_bob.json").exists()
    assert player_space.read(tmp_path, "bob", "routing") is not None                                         # a saved setup never expires
    removed = retention.delete_cache(tmp_path, "alice")
    assert removed["files"] == 4 and player_space.read(tmp_path, "alice", "routing") is not None
