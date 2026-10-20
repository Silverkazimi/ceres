"""the optional Posting-key login -- signature checks against a Posting authority, single-use challenges, memory-only sessions.
Synthetic secp256k1 keys only; no wallet, no network."""
import hashlib

import pytest

ecdsa = pytest.importorskip("ecdsa")
from ecdsa import SECP256k1, SigningKey
from ecdsa.util import sigencode_string

from ceres import auth

ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def b58encode(raw: bytes) -> str:
    number = int.from_bytes(raw, "big")
    out = ""
    while number:
        number, rest = divmod(number, 58)
        out = ALPHABET[rest] + out
    return "1" * (len(raw) - len(raw.lstrip(b"\0"))) + out


def make_key(seed: int):
    sk = SigningKey.from_secret_exponent(seed, curve=SECP256k1)
    compressed = sk.get_verifying_key().to_string("compressed")
    return sk, "STM" + b58encode(compressed + b"\0\0\0\0")


def sign(sk, message: bytes, header: int = 31) -> str:
    return (bytes([header]) + sk.sign_digest(hashlib.sha256(message).digest(), sigencode=sigencode_string)).hex()


MESSAGE = b"CERES OFF-CHAIN AUTH v1\npurpose=login\naccount=alice"


def authority(threshold, keys, delegates=()):
    return {"weight_threshold": threshold, "key_auths": [[key, weight] for key, weight in keys], "account_auths": [[n, w] for n, w in delegates]}


def test_one_key_account_accepts_its_own_signature_and_refuses_anyone_elses():
    sk, pub = make_key(11)
    other, _ = make_key(12)
    assert auth.authority_satisfied(MESSAGE, [sign(sk, MESSAGE)], authority(1, [(pub, 1)]))
    assert not auth.authority_satisfied(MESSAGE, [sign(other, MESSAGE)], authority(1, [(pub, 1)]))
    assert not auth.authority_satisfied(MESSAGE + b"x", [sign(sk, MESSAGE)], authority(1, [(pub, 1)]))   # another text


def test_threshold_two_needs_enough_distinct_keys_and_a_repeated_signature_counts_once():
    a, pa = make_key(21)
    b, pb = make_key(22)
    two_of_two = authority(2, [(pa, 1), (pb, 1)])
    assert not auth.authority_satisfied(MESSAGE, [sign(a, MESSAGE)], two_of_two)
    assert not auth.authority_satisfied(MESSAGE, [sign(a, MESSAGE), sign(a, MESSAGE)], two_of_two)
    assert auth.authority_satisfied(MESSAGE, [sign(a, MESSAGE), sign(b, MESSAGE)], two_of_two)
    heavy = authority(2, [(pa, 2), (pb, 1)])   # one heavy key is enough
    assert auth.authority_satisfied(MESSAGE, [sign(a, MESSAGE)], heavy)
    assert not auth.authority_satisfied(MESSAGE, [sign(b, MESSAGE)], heavy)


def test_a_delegated_posting_authority_is_satisfied_through_the_delegates_own_keys():
    d, pd = make_key(31)
    delegate = authority(1, [(pd, 1)])
    mine = authority(1, [], [("bob", 1)])
    assert auth.authority_satisfied(MESSAGE, [sign(d, MESSAGE)], mine, {"bob": delegate})
    assert not auth.authority_satisfied(MESSAGE, [sign(d, MESSAGE)], mine, {})                     # delegate unknown: refused
    stranger, _ = make_key(32)
    assert not auth.authority_satisfied(MESSAGE, [sign(stranger, MESSAGE)], mine, {"bob": delegate})


def test_malformed_input_is_a_refusal_never_an_error_or_an_accept():
    sk, pub = make_key(41)
    good = authority(1, [(pub, 1)])
    for bad in (None, [], ["zz"], ["0" * 130], [sign(sk, MESSAGE)[:-2]], [123], [sign(sk, MESSAGE, header=5)], ["1f" + "00" * 64]):
        assert not auth.authority_satisfied(MESSAGE, bad, good)
    for broken in (None, {}, {"weight_threshold": 0, "key_auths": [[pub, 1]], "account_auths": []}, {"weight_threshold": 1, "key_auths": "x", "account_auths": []},
                   {"weight_threshold": True, "key_auths": [[pub, 1]], "account_auths": []}, authority(1, [("STMnotakey", 1)])):
        assert not auth.authority_satisfied(MESSAGE, [sign(sk, MESSAGE)], broken)
    assert auth.parse_public_key("STM" + "1" * 40) is None and auth.parse_public_key("TST" + pub[3:]) is None and auth.parse_public_key(5) is None
    assert not auth.authority_satisfied(MESSAGE, [sign(sk, MESSAGE)] * 9, good)    # more signatures than allowed


def test_a_challenge_is_used_once_is_bound_to_its_browser_and_expires():
    verifier = "v" * 32
    binding = hashlib.sha256(verifier.encode()).hexdigest()
    store = auth.Challenges()
    item = store.issue(account="alice", purpose="login", audience="http://127.0.0.1:27390", binding=binding, now=1000.0)
    assert "\n" not in item.message and "\r" not in item.message and "\\" not in item.message     # one line, the Keychain phone app cannot sign a text with line breaks
    lines = item.message.split(" | ")
    assert lines[0] == "Ceres sign-in" and lines[1] == "Account: alice" and lines[2] == "Site: http"
    assert lines[3] == "Valid until: 1970-01-01 00:21 UTC"                                  # one normalised, readable time (1000 s + 300 s)
    assert lines[4] == f"Ref: {item.challenge_id} / {binding[:8]}" and "It sends no transaction and costs nothing." in lines[-1]
    assert len(item.message) < 330 and item.message.isascii()                            # compact and plain
    gone = store.issue(account="alice", purpose="delete-all", audience="x", binding=binding, scope="routing, staged changes", now=1000.0)
    assert gone.message.startswith("Ceres: delete all my saved data") and "Scope: routing, staged changes" in gone.message
    assert "\n" not in gone.message and "\r" not in gone.message
    with pytest.raises(auth.AuthError):
        store.take(item.challenge_id, "w" * 32, now=1001.0)     # another browser
    assert store.take(item.challenge_id, verifier, now=1001.0).account == "alice"
    assert store.consume(item.challenge_id) is True
    assert store.consume(item.challenge_id) is False             # a second request cannot use it again
    with pytest.raises(auth.AuthError):
        store.take(item.challenge_id, verifier, now=1002.0)
    late = store.issue(account="alice", purpose="login", audience="x", binding=binding, now=2000.0)
    with pytest.raises(auth.AuthError):
        store.take(late.challenge_id, verifier, now=2000.0 + auth.CHALLENGE_TTL_SECONDS + 1)
    again = store.issue(account="alice", purpose="login", audience="x", binding=binding, now=3000.0)
    for _ in range(auth.MAX_ATTEMPTS):
        with pytest.raises(auth.AuthError):
            store.take(again.challenge_id, "w" * 32, now=3001.0)
    with pytest.raises(auth.AuthError):
        store.take(again.challenge_id, verifier, now=3002.0)     # attempts used up: gone


def test_sessions_are_memory_only_idle_and_absolute_limited_and_revocable():
    sessions = auth.Sessions()
    token = sessions.create("alice", now=0.0)
    assert sessions.account_for(token, now=10.0) == "alice"
    assert sessions.account_for(token + "x", now=10.0) is None and sessions.account_for(None, now=10.0) is None
    assert sessions.account_for(token, now=10.0 + auth.SESSION_IDLE_SECONDS + 1) is None            # idle
    kept = sessions.create("bob", now=0.0)
    now = 0.0
    while now < auth.SESSION_MAX_SECONDS:
        now += 600
        assert sessions.account_for(kept, now=now) == "bob" or now > auth.SESSION_MAX_SECONDS
    assert sessions.account_for(kept, now=auth.SESSION_MAX_SECONDS + 1) is None                      # absolute
    third = sessions.create("carol", now=0.0)
    sessions.revoke(third)
    assert sessions.account_for(third, now=1.0) is None
    assert not any(token in repr(v) for v in vars(sessions).values())                                  # only hashes are held
