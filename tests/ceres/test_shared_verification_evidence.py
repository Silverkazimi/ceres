from __future__ import annotations

import copy
import inspect
import json
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from ceres import capture as capture_mod
from ceres.config import CeresConfig
import ceres.routes_op_preview as routes_op_preview_mod
from ceres.routes_op_preview import op_verify
from praetoria import land_analysis
from praetoria.errors import SchemaError, TransportError
from praetoria.cache import JsonCache
from praetoria.land_analysis import LandCapture, project_worksite_facts
from praetoria.transport import ResponseData


PLAYER = "player0005"
DEED = "deed-1"
DEEDS = ("deed-1", "deed-2", "deed-3")
RETRIEVED_AT = "2026-09-05T12:34:56Z"
BUNDLE_TIME = "2037-01-01T00:00:01Z"
COLLECTION_TIME = "2037-01-01T00:00:05Z"
UNTOUCHED = "deed-unrelated"


@pytest.fixture(autouse=True)
def isolated_game_verdict(monkeypatch):
    monkeypatch.setattr(routes_op_preview_mod, "game_verdict", lambda *args, **kwargs: {
        "status": "game_unknown", "evidence": {"lookup_attempted": False},
    })


def _bundle(*, include_worksite: bool = True, duplicate: bool = False, completion: bool = False) -> dict:
    worksite_details = [
        {"deed_uid": deed_uid, "project_type": "worksite_aura", "project_id": 7, "is_construction": False}
        for deed_uid in DEEDS
    ] if include_worksite else []
    if not completion:
        for row in worksite_details:
            row.pop("project_id")
            row.pop("is_construction")
            row["project_type"] = "worksite_aura_construction"
    if duplicate:
        worksite_details.append({"deed_uid": DEED, "project_type": "worksite_last"})
    return {
        "status": "success",
        "data": {
            "deeds": [{"deed_uid": deed_uid, "region_uid": "PR-BRL-41", "is_construction": not completion} for deed_uid in DEEDS],
            "worksite_details": worksite_details,
            "staking_details": [],
        },
    }


class _LandClient:
    def __init__(self, payload: dict, *, error: Exception | None = None):
        self.payload = payload
        self.error = error
        self.bundle_calls = 0
        self.asset_calls = 0
        self.collection_calls = 0
        self.history_calls = 0

    def bundle(self, player: str) -> ResponseData:
        self.bundle_calls += 1
        if self.error is not None:
            raise self.error
        return ResponseData(self.payload, RETRIEVED_AT, 1)

    def assets(self, deed_uid: str):
        self.asset_calls += 1
        raise AssertionError("worksite verification must not fetch deed assets")

    def project_history(self, deed_uid: str):
        self.history_calls += 1
        raise AssertionError("worksite verification must not fetch project history")


class _CollectionClient:
    def __init__(self):
        self.collection_calls = 0

    def collection(self, player: str):
        self.collection_calls += 1
        raise AssertionError("worksite verification must not fetch collection")


class _NoCache(JsonCache):
    def __init__(self):
        self.reads = 0
        self.writes = 0

    def read(self, *args, **kwargs):
        self.reads += 1
        raise AssertionError("worksite verification must not read JsonCache")

    def write(self, *args, **kwargs):
        self.writes += 1
        raise AssertionError("worksite verification must not write JsonCache")


def _request(config):
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(ceres_config=config)))


def _worksite_op(deed_uid: str = DEED) -> list:
    payload = {
        "op": "worksite_aura_construction", "region_uid": "PR-BRL-41",
        "deed_uids": [deed_uid], "time_crystals": 0, "app": "x", "n": "abcdefghij",
    }
    return ["custom_json", {"id": "sm_land_operation", "json": json.dumps(payload), "required_auths": [], "required_posting_auths": [PLAYER]}]


def _completion_op(deed_uid: str = DEED) -> list:
    payload = {
        "op": "update_worksite", "region_uid": "PR-BRL-41", "deed_uids": [deed_uid],
        "project_id": 7, "time_crystals": 0, "auto_buy_grain": False, "app": "x", "n": "abcdefghij",
    }
    return ["custom_json", {"id": "sm_land_operation", "json": json.dumps(payload), "required_auths": [], "required_posting_auths": [PLAYER]}]


def _stake_op(deed_uid: str = DEED) -> list:
    payload = {
        "deed_uid": deed_uid, "stake": {"cards": [], "items": []}, "unstake": {"cards": [], "items": []},
        "app": "x", "n": "abcdefghij",
    }
    return ["custom_json", {"id": "sm_stake_change", "json": json.dumps(payload), "required_auths": [], "required_posting_auths": [PLAYER]}]


def _chain_verified(*_args, **_kwargs):
    return {"status": "chain_verified", "evidence": {"tx_id": "772d51e445ae98a9e4194be2a4dad10c84277d5f", "block_num": 100, "observed": []}}


def _install_real_fetch(monkeypatch, payload=None, error=None):
    instances = []

    def make_capture(*, land_client=None, collection_client=None):
        instance = LandCapture(
            land_client=_LandClient(payload or _bundle(), error=error),
            collection_client=_CollectionClient(), cache=_NoCache(), min_delay=0, sleeper=lambda _: None,
        )
        instances.append(instance)
        return instance

    monkeypatch.setattr(capture_mod, "LandCapture", make_capture)
    return instances


def test_project_worksite_facts_requires_a_matching_deed_and_preserves_timestamp():
    """Red if projection invents an absent deed, worksite, or uses first duplicate."""
    assert project_worksite_facts(_bundle(), DEED, RETRIEVED_AT)["project_type"] == "worksite_aura_construction"
    assert project_worksite_facts(_bundle(duplicate=True), DEED, RETRIEVED_AT)["project_type"] == "worksite_last"
    assert project_worksite_facts(_bundle(include_worksite=False), DEED, RETRIEVED_AT) is None
    assert project_worksite_facts(_bundle(), "absent-deed", RETRIEVED_AT) is None
    orphan = _bundle()
    orphan["data"]["deeds"] = [{"deed_uid": uid} for uid in DEEDS[1:]]
    assert project_worksite_facts(orphan, DEED, RETRIEVED_AT) is None


def test_fetch_worksite_facts_passes_response_timestamp_to_real_normalizer(monkeypatch, tmp_path):
    """Red if projection stamps with datetime.now or discards ResponseData.retrieved_at."""
    seen = []
    original = land_analysis.normalize_bundle

    def recording_normalize(payload, observed_at=None):
        seen.append(observed_at)
        return original(payload, observed_at=observed_at)

    monkeypatch.setattr(land_analysis, "normalize_bundle", recording_normalize)
    _install_real_fetch(monkeypatch)
    capture_mod.fetch_worksite_facts(CeresConfig(root=tmp_path), PLAYER, DEED)
    assert seen == [RETRIEVED_AT]


def test_fresh_read_returns_response_data_identity_and_counts_dispatch():
    """Red if fresh_read unwraps ResponseData or bypasses _request bookkeeping."""
    response = ResponseData(_bundle(), RETRIEVED_AT, 2)
    capture = LandCapture(land_client=_LandClient(_bundle()), cache=_NoCache(), min_delay=0, sleeper=lambda _: None)
    assert capture.fresh_read(lambda: response) is response
    assert capture._requests == 1


def test_fetch_worksite_facts_uses_one_real_fresh_dispatch_and_no_assets_or_collection(monkeypatch, tmp_path):
    """Red if fetch_worksite_facts restores expand_deed or bypasses fresh_read/_request."""
    instances: list[LandCapture] = []

    def make_capture(*, land_client=None, collection_client=None):
        client = _LandClient(_bundle())
        collection = _CollectionClient()
        instance = LandCapture(land_client=client, collection_client=collection, cache=_NoCache(), min_delay=0, sleeper=lambda _: None)
        instance._test_collection = collection
        instances.append(instance)
        return instance

    monkeypatch.setattr(capture_mod, "LandCapture", make_capture)
    result = capture_mod.fetch_worksite_facts(CeresConfig(root=tmp_path), PLAYER, DEED)

    assert result["project_type"] == "worksite_aura_construction"
    assert len(instances) == 1
    assert instances[0]._requests == 1
    assert instances[0].land_client.bundle_calls == 1
    assert instances[0].land_client.asset_calls == 0
    assert instances[0].land_client.history_calls == 0
    assert instances[0]._test_collection.collection_calls == 0
    assert instances[0].cache.reads == 0
    assert instances[0].cache.writes == 0


def test_d3_worksite_facts_uses_three_captured_dispatches(monkeypatch, tmp_path):
    """Red if assets are restored per deed, or if direct bundle I/O undercounts _requests."""
    instances: list[LandCapture] = []

    def make_capture(*, land_client=None, collection_client=None):
        instance = LandCapture(land_client=_LandClient(_bundle()), collection_client=_CollectionClient(), cache=_NoCache(), min_delay=0, sleeper=lambda _: None)
        instance._test_collection = instance.collection_client
        instances.append(instance)
        return instance

    monkeypatch.setattr(capture_mod, "LandCapture", make_capture)
    for deed_uid in ("deed-1", "deed-2", "deed-3"):
        assert capture_mod.fetch_worksite_facts(CeresConfig(root=tmp_path), PLAYER, deed_uid) is not None

    assert len(instances) == 3
    assert sum(instance._requests for instance in instances) == 3
    assert sum(instance.land_client.bundle_calls for instance in instances) == 3
    assert all(instance.land_client.asset_calls == 0 for instance in instances)
    assert all(instance.land_client.history_calls == 0 for instance in instances)
    assert all(instance._test_collection.collection_calls == 0 for instance in instances)
    assert all(instance.cache.reads == 0 and instance.cache.writes == 0 for instance in instances)


def test_fetch_worksite_facts_propagates_normalization_failure_inside_callback(monkeypatch, tmp_path):
    """Red if fetch_worksite_facts swallows or replaces a normalization failure."""
    def make_capture(*, land_client=None, collection_client=None):
        return LandCapture(land_client=_LandClient({"status": "success", "data": {"deeds": "bad"}}), cache=_NoCache(), min_delay=0, sleeper=lambda _: None)

    monkeypatch.setattr(capture_mod, "LandCapture", make_capture)
    with pytest.raises(SchemaError):
        capture_mod.fetch_worksite_facts(CeresConfig(root=tmp_path), PLAYER, DEED)


@pytest.mark.parametrize(
    ("operation", "payload"),
    [(_worksite_op(), _bundle()), (_completion_op(), _bundle(completion=True))],
)
def test_real_worksite_facts_preserve_construction_and_completion_verdicts(monkeypatch, tmp_path, operation, payload):
    """Red if route facts bypass real normalization or either worksite callback changes verdicts."""
    config = CeresConfig(root=tmp_path / ".ceres", player=PLAYER)
    monkeypatch.setattr(routes_op_preview_mod, "verify_chain", _chain_verified)
    monkeypatch.setattr(routes_op_preview_mod, "game_verdict", lambda *args, **kwargs: {"status": "game_accepted", "evidence": {}})
    instances = _install_real_fetch(monkeypatch, payload=payload)
    facts = capture_mod.fetch_worksite_facts(config, PLAYER, DEED)
    assert facts == payload["data"]["worksite_details"][0]
    assert len(instances) == 1 and instances[0]._requests == 1 and instances[0].land_client.bundle_calls == 1
    monkeypatch.setattr(capture_mod, "expand_plots", lambda *args, **kwargs: {"capture": {"refreshed_deeds": [DEED], "refresh_errors": []}})
    result = json.loads(op_verify({"tx_id": "772d51e445ae98a9e4194be2a4dad10c84277d5f", "operations": [operation]}, _request(config), player=None).body)
    assert result["domain"]["status"] == "domain_verified"
    assert result["state"] == "domain-confirmed"
    op_payload = json.loads(operation[1]["json"])
    deed_evidence = result["domain"]["evidence"]["deeds"][DEED]
    if op_payload["op"].endswith("_construction"):
        assert deed_evidence["project_type"] == op_payload["op"]
    else:
        assert deed_evidence["project_type"] == facts["project_type"]
    if op_payload["op"] == "update_worksite":
        assert deed_evidence["project_id"] == op_payload["project_id"]
    assert result["refreshed_deeds"] == [DEED]
    assert result["refresh_errors"] == []
    assert result["notices"][0]["code"] == "verification_confirmed"


@pytest.mark.parametrize(
    ("operation", "error_kind"),
    [(_worksite_op(), "fetch"), (_worksite_op(), "normalize"), (_completion_op(), "fetch"), (_completion_op(), "normalize")],
)
def test_real_worksite_callback_errors_keep_per_deed_could_not_verify_contract(monkeypatch, tmp_path, operation, error_kind):
    """Red if fetch or normalization moves outside LandVerificationSpec's per-deed try block."""
    config = CeresConfig(root=tmp_path / ".ceres", player=PLAYER)
    monkeypatch.setattr(routes_op_preview_mod, "verify_chain", _chain_verified)
    if error_kind == "fetch":
        instances = _install_real_fetch(monkeypatch, error=OSError("connection reset"))
    else:
        instances = _install_real_fetch(monkeypatch, payload={"status": "success", "data": {"deeds": "malformed"}})
    result = json.loads(op_verify({"tx_id": "772d51e445ae98a9e4194be2a4dad10c84277d5f", "operations": [operation]}, _request(config), player=None).body)
    assert result["state"] == "domain-could-not-verify"
    reason = "connection reset" if error_kind == "fetch" else "Land deed is missing deed_uid"
    assert _without_provenance(result["domain"]) == {
        "status": "domain_could_not_verify",
        "evidence": {"unverifiable": [f"deed {DEED}: failed to re-read worksite_facts ({reason})"], "deeds": {}},
    }
    assert [notice["code"] for notice in result["notices"]] == ["verification_could_not_verify"]
    assert result["refreshed_deeds"] == []
    assert result["refresh_errors"] == []
    assert len(instances) == 1 and instances[0]._requests == 1 and instances[0].land_client.bundle_calls == 1


def test_stake_fetch_exception_and_malformed_assets_retain_existing_classes(monkeypatch, tmp_path):
    """Red if Phase 1 widens the stake catch or changes malformed assets classification."""
    harness = _install_batch(monkeypatch, tmp_path)
    harness.upstream.asset_errors[DEED] = OSError("connection reset")
    result = _verify(harness, [_stake_op()])
    assert result["state"] == "domain-could-not-verify"
    assert result["domain"] == {"status": "domain_check_failed", "evidence": {"reason": "domain verification failed; retry later"}}
    assert [notice["code"] for notice in result["notices"]] == ["verification_could_not_verify", "verification_domain_check_failed"]
    assert result["refreshed_deeds"] == []
    harness.upstream.asset_errors.clear()
    harness.upstream.asset_payloads[DEED] = {"status": "success", "data": {"cards": []}}
    result = _verify(harness, [_stake_op()])
    assert result["state"] == "domain-could-not-verify"
    assert result["domain"]["status"] == "domain_could_not_verify"
    assert result["domain"]["evidence"]["deeds"] == {}
    assert len(result["domain"]["evidence"]["unverifiable"]) == 1
    assert result["domain"]["evidence"]["unverifiable"] == [
        "deed deed-1: land assets response 'data' is not the expected {cards: [...], items: [...]} shape (possible upstream P0001 error): {'status': 'success', 'data': {'cards': []}}",
    ]
    assert [notice["code"] for notice in result["notices"]] == ["verification_could_not_verify"]
    assert result["refreshed_deeds"] == []


class _ReferenceOnlyCache(JsonCache):
    def __init__(self, *, cold=False):
        self.cold = cold
        self.reads = []
        self.writes = []
        self.evidence_accesses = []

    def _check(self, key):
        if key != "card_details" and not key.startswith("land_project_history_"):
            self.evidence_accesses.append(key)
            raise AssertionError(f"verification evidence must never access JsonCache: {key}")

    def read(self, key, **kwargs):
        self._check(key)
        self.reads.append(key)
        if self.cold:
            return None, None
        return ([{"id": 1, "name": "Reference worker"}] if key == "card_details" else _history()), None

    def write(self, key, payload, retrieved_at):
        self._check(key)
        self.writes.append((key, retrieved_at))


def _history():
    return {"status": "success", "data": [{
        "project_number": 1, "project_type": "worksite_grain",
        "start_date": "2036-01-01T00:00:00Z", "completed_date": "2036-02-01T00:00:00Z",
    }]}


def _batch_bundle(deeds=DEEDS, *, construction=False):
    payload = _bundle(completion=True)
    payload["data"]["deeds"] = [{"deed_uid": uid, "region_uid": "PR-BRL-41", "is_construction": construction} for uid in (*deeds, UNTOUCHED)]
    payload["data"]["worksite_details"] = [{
        "deed_uid": uid, "project_type": "worksite_aura_construction" if construction else "worksite_aura",
        "project_id": 7, "project_number": 2, "is_construction": construction,
    } for uid in (*deeds, UNTOUCHED)]
    return payload


def _asset_time(uid):
    return f"2037-01-01T01:{sum(map(ord, uid)) % 60:02d}:00Z"


def _asset_payload(uid):
    return {"status": "success", "data": {"cards": [{
        "uid": f"CARD-{uid}", "stake_ref_uid": uid, "card_detail_id": 1,
        "stake_type_uid": "STK-LND-WKR", "slot": "1",
    }], "items": []}}


class _BatchUpstream:
    def __init__(self, bundle=None):
        self.payload = _batch_bundle() if bundle is None else bundle
        self.bundle_time = BUNDLE_TIME
        self.bundle_error = None
        self.collection_error = None
        self.asset_errors = {}
        self.asset_payloads = {}
        self.calls = []
        self.before_call = lambda kind, key: None
        self.after_call = lambda kind, key: None

    def _observe(self, kind, key, payload, timestamp, error=None):
        self.before_call(kind, key)
        self.calls.append((kind, key))
        if error is not None:
            raise error
        self.after_call(kind, key)
        return ResponseData(copy.deepcopy(payload), timestamp, 2)

    def bundle(self, player):
        return self._observe("bundle", player, self.payload, self.bundle_time, self.bundle_error)

    def assets(self, uid):
        return self._observe("assets", uid, self.asset_payloads.get(uid, _asset_payload(uid)), _asset_time(uid), self.asset_errors.get(uid))

    def collection(self, player):
        cards = [_asset_payload(uid)["data"]["cards"][0] for uid in DEEDS]
        return self._observe("collection", player, {"player": player, "cards": cards}, COLLECTION_TIME, self.collection_error)

    def project_history(self, uid):
        return self._observe("history", uid, _history(), "2037-01-01T00:00:06Z")

    def details(self):
        return self._observe("details", None, [{"id": 1, "name": "Reference worker"}], "2037-01-01T00:00:07Z")


def _stake_with_card(uid, card=None):
    operation = _stake_op(uid)
    payload = json.loads(operation[1]["json"])
    payload["stake"]["cards"] = [{"card_uid": card or f"CARD-{uid}", "stake_type_uid": "STK-LND-WKR", "slot": "1"}]
    operation[1]["json"] = json.dumps(payload)
    return operation


def _batch_worksite(*, deeds=DEEDS, construction=False):
    operation = _worksite_op() if construction else _completion_op()
    payload = json.loads(operation[1]["json"])
    payload["deed_uids"] = list(deeds)
    operation[1]["json"] = json.dumps(payload)
    return operation


def _seed_snapshot(config, bundle=None):
    config.ensure_dirs()
    bundle = _batch_bundle() if bundle is None else bundle
    report = land_analysis.analyze_bundle(
        PLAYER, bundle, {UNTOUCHED: _asset_payload(UNTOUCHED)["data"]},
        include_asset_ids=True, observed_at="2036-12-31T00:00:00Z",
    )
    for plot in report["plots"]:
        if plot["deed_uid"] == UNTOUCHED:
            plot["retained_detail"] = "base detail"
    capture_mod._save_snapshot_and_refresh_cache(report, capture_mod._latest_path(config, PLAYER))
    return report


def _install_batch(monkeypatch, tmp_path, *, upstream=None, budget=200, cold=False, min_delay=0, sleeper=lambda _: None):
    config = CeresConfig(root=tmp_path / ".ceres", player=PLAYER)
    upstream = upstream or _BatchUpstream()
    _seed_snapshot(config)
    instances = []

    def make_capture(*, land_client=None, collection_client=None):
        capture = LandCapture(
            land_client=upstream, collection_client=upstream, cache=_ReferenceOnlyCache(cold=cold),
            min_delay=min_delay, request_budget=budget, sleeper=sleeper,
        )
        instances.append(capture)
        return capture

    def forbidden(*args, **kwargs):
        raise AssertionError("verification must not invoke a legacy fetch/expand or background recapture")

    monkeypatch.setattr(capture_mod, "LandCapture", make_capture)
    monkeypatch.setattr(capture_mod, "fetch_land_assets", forbidden)
    monkeypatch.setattr(capture_mod, "fetch_worksite_facts", forbidden)
    monkeypatch.setattr(capture_mod, "expand_plot", forbidden)
    monkeypatch.setattr(capture_mod, "_self_heal_refetches", forbidden)
    monkeypatch.setattr(capture_mod, "start_refresh", forbidden)
    monkeypatch.setattr(routes_op_preview_mod, "verify_chain", _chain_verified)
    monkeypatch.setattr(capture_mod, "_shared_capture", make_capture)
    monkeypatch.setattr(routes_op_preview_mod, "game_verdict", lambda *args, **kwargs: {"status": "game_accepted", "evidence": {}})
    return SimpleNamespace(config=config, upstream=upstream, instances=instances)


def _verify(harness, operations, **body):
    return json.loads(op_verify(
        {"tx_id": "772d51e445ae98a9e4194be2a4dad10c84277d5f", "operations": operations, **body}, _request(harness.config), player=None,
    ).body)


def _saved(harness):
    path = capture_mod._latest_path(harness.config, PLAYER)
    disk = json.loads(path.read_text())
    cached, _ = capture_mod.latest_snapshot(harness.config, PLAYER)
    return disk, cached


def _without_provenance(domain):
    domain = copy.deepcopy(domain)
    provenance = domain["evidence"].pop("provenance")
    assert provenance["account"] == PLAYER
    return domain


@pytest.mark.parametrize("family, expected_count", [("stake", 4), ("worksite", 5)])
def test_d3_terminal_uses_one_capture_and_exact_actual_request_counter(monkeypatch, tmp_path, family, expected_count):
    """G2/G3 red: per-deed bundle/collection, asset re-fetch, new captures, or UNDERCOUNT via direct client calls."""
    harness = _install_batch(monkeypatch, tmp_path)
    operations = [_stake_with_card(uid) for uid in DEEDS] if family == "stake" else [_batch_worksite()]
    result = _verify(harness, operations)
    assert result["state"] == "domain-confirmed"
    assert result["refreshed_deeds"] == list(DEEDS)
    assert result["refresh_errors"] == []
    assert len(harness.instances) == 1
    capture = harness.instances[0]
    assert capture._requests == expected_count
    assert capture._requests == len(harness.upstream.calls)
    assert sorted(harness.upstream.calls) == sorted(
        [("assets", uid) for uid in DEEDS] + [("bundle", PLAYER)]
        + ([("collection", PLAYER)] if family == "worksite" else [])
    )
    assert capture.cache.evidence_accesses == []
    for saved in _saved(harness):
        assert saved["capture"]["requests"] == expected_count
        assert saved["capture"]["core_requests"] == expected_count
        assert saved["capture"]["history_requests"] == saved["capture"]["card_metadata_requests"] == 0
        plots = {plot["deed_uid"]: plot for plot in saved["plots"]}
        assert set(plots) == {*DEEDS, UNTOUCHED}
        assert all(plots[uid]["asset_fetch"]["status"] == "retrieved" for uid in DEEDS)
        assert plots[UNTOUCHED]["retained_detail"] == "base detail"
        assert {worker["uid"] for worker in saved["workers"]} == {f"CARD-{uid}" for uid in (*DEEDS, UNTOUCHED)}


@pytest.mark.parametrize("first_failure", [False, True])
def test_next_handler_refetches_with_same_transaction_and_attempt(monkeypatch, tmp_path, first_failure):
    """G4 red: context or failed-slot reuse keyed by account, transaction, nonce, or attempt."""
    harness = _install_batch(monkeypatch, tmp_path)
    harness.upstream.bundle_time = "2037-01-01T00:00:00Z"
    if first_failure:
        harness.upstream.bundle_error = OSError("first read failed")
    else:
        harness.upstream.payload = _batch_bundle(construction=True)
    first = _verify(harness, [_batch_worksite()], verification_attempt=1)
    assert first["state"] == ("domain-could-not-verify" if first_failure else "domain-pending")
    assert first["refreshed_deeds"] == []
    assert len(harness.upstream.calls) == 1
    assert harness.instances[0]._requests == 1
    harness.upstream.bundle_error = None
    harness.upstream.bundle_time = BUNDLE_TIME
    harness.upstream.payload = _batch_bundle()
    second = _verify(harness, [_batch_worksite()], verification_attempt=1)
    assert second["state"] == "domain-confirmed"
    assert second["refresh_errors"] == []
    assert len(harness.instances) == 2
    assert harness.instances[0] is not harness.instances[1]
    assert [capture._requests for capture in harness.instances] == [1, 5]
    assert harness.upstream.calls.count(("bundle", PLAYER)) == 2
    assert BUNDLE_TIME in json.dumps(second["domain"]["evidence"]["provenance"])


@pytest.mark.parametrize("error_kind", ["fetch", "normalize"])
def test_shared_worksite_failure_is_lazy_and_replayed_per_deed(monkeypatch, tmp_path, error_kind):
    """G5 red: eager dispatch/normalization, empty fallback, or failed bundle re-dispatch per deed."""
    harness = _install_batch(monkeypatch, tmp_path)
    if error_kind == "fetch":
        harness.upstream.bundle_error = OSError("shared connection reset")
    else:
        harness.upstream.payload = {"status": "success", "data": {"deeds": "bad"}}
    normalization_calls = []
    original_normalize = land_analysis.normalize_bundle

    def normalize(*args, **kwargs):
        normalization_calls.append(True)
        return original_normalize(*args, **kwargs)

    monkeypatch.setattr(land_analysis, "normalize_bundle", normalize)
    monkeypatch.setattr(capture_mod, "normalize_bundle", normalize)
    result = _verify(harness, [_batch_worksite()])
    domain = _without_provenance(result["domain"])
    reason = "shared connection reset" if error_kind == "fetch" else "Land deed is missing deed_uid"
    assert result["state"] == "domain-could-not-verify"
    assert domain == {
        "status": "domain_could_not_verify", "evidence": {
            "deeds": {}, "unverifiable": [f"deed {uid}: failed to re-read worksite_facts ({reason})" for uid in DEEDS],
        },
    }
    assert [notice["code"] for notice in result["notices"]] == ["verification_could_not_verify"]
    assert result["refreshed_deeds"] == result["refresh_errors"] == []
    assert len(harness.instances) == 1
    assert harness.instances[0]._requests == len(harness.upstream.calls) == 1
    assert len(normalization_calls) == (0 if error_kind == "fetch" else 1)
    assert result["domain"]["evidence"]["provenance"]["resources"] == (
        {} if error_kind == "fetch" else {"bundle": {"retrieved_at": BUNDLE_TIME, "attempts": 2}}
    )


@pytest.mark.parametrize("terminal", ["confirmed", "not-applied"])
def test_terminal_batch_merges_and_saves_once_before_response(monkeypatch, tmp_path, terminal):
    """G7 red: only-last-deed report, per-deed save, early response, or cache-bypassing snapshot write."""
    harness = _install_batch(monkeypatch, tmp_path)
    if terminal == "not-applied":
        monkeypatch.setattr(routes_op_preview_mod, "game_verdict", lambda *args, **kwargs: {"status": "game_unknown", "evidence": {"lookup_attempted": False}})
        for uid in DEEDS:
            harness.upstream.asset_payloads[uid] = {"status": "success", "data": {"cards": [], "items": []}}
        monkeypatch.setattr(routes_op_preview_mod.fides_verify, "_http_rpc_call", lambda *args: {"head_block_number": 120})
    original_merge = capture_mod.merge_reports
    original_save = capture_mod._save_snapshot_and_refresh_cache_locked
    merged_reports = []
    completed_saves = []

    def merge(base, incoming, **kwargs):
        retrieved = {plot["deed_uid"] for plot in incoming["plots"] if plot["asset_fetch"]["status"] == "retrieved"}
        assert retrieved == set(DEEDS)
        merged_reports.append(incoming)
        return original_merge(base, incoming, **kwargs)

    def save(report, path, **kwargs):
        assert len(merged_reports) == 1
        original_save(report, path, **kwargs)
        completed_saves.append(report)

    monkeypatch.setattr(capture_mod, "merge_reports", merge)
    monkeypatch.setattr(capture_mod, "_save_snapshot_and_refresh_cache_locked", save)
    result = _verify(harness, [_stake_with_card(uid) for uid in DEEDS], verification_attempt=2)
    assert result["state"] == f"domain-{terminal}"
    assert result["refreshed_deeds"] == list(DEEDS)
    assert result["refresh_errors"] == []
    assert len(merged_reports) == len(completed_saves) == 1
    for report in _saved(harness):
        plots = {plot["deed_uid"]: plot for plot in report["plots"]}
        assert all(plots[uid]["asset_fetch"]["status"] == "retrieved" for uid in DEEDS)
        assert plots[UNTOUCHED]["retained_detail"] == "base detail"


def test_batch_rereads_concurrent_writer_under_lock_and_fetches_outside(monkeypatch, tmp_path):
    """G8 red: precheck used as base, unlocked latest read/merge, or network within write lock."""
    harness = _install_batch(monkeypatch, tmp_path)
    locked = False
    lock_reads = []
    original_lock = capture_mod._snapshot_write_lock
    original_latest = capture_mod.latest_snapshot
    original_save = capture_mod._save_snapshot_and_refresh_cache_locked
    original_merge = capture_mod.merge_reports

    @contextmanager
    def lock(player):
        nonlocal locked
        with original_lock(player):
            assert not locked
            locked = True
            try:
                yield
            finally:
                locked = False

    def latest(*args):
        lock_reads.append(locked)
        return original_latest(*args)

    def merge(*args, **kwargs):
        assert locked
        return original_merge(*args, **kwargs)

    def save(*args, **kwargs):
        assert locked
        return original_save(*args, **kwargs)

    def before(kind, key):
        assert not locked, (kind, key)

    def other_writer(kind, key):
        if kind != "collection":
            return
        with original_lock(PLAYER):
            report, _ = original_latest(harness.config, PLAYER)
            report = copy.deepcopy(report)
            for plot in report["plots"]:
                if plot["deed_uid"] == UNTOUCHED:
                    plot["retained_detail"] = "concurrent writer"
            original_save(report, capture_mod._latest_path(harness.config, PLAYER), pool_may_have_changed=False)

    harness.upstream.before_call = before
    harness.upstream.after_call = other_writer
    monkeypatch.setattr(capture_mod, "_snapshot_write_lock", lock)
    monkeypatch.setattr(capture_mod, "latest_snapshot", latest)
    monkeypatch.setattr(capture_mod, "merge_reports", merge)
    monkeypatch.setattr(capture_mod, "_save_snapshot_and_refresh_cache_locked", save)
    result = _verify(harness, [_batch_worksite()])
    assert result["refresh_errors"] == []
    assert True in lock_reads
    for report in _saved(harness):
        plots = {plot["deed_uid"]: plot for plot in report["plots"]}
        assert plots[UNTOUCHED]["retained_detail"] == "concurrent writer"
        assert all(plots[uid]["asset_fetch"]["status"] == "retrieved" for uid in DEEDS)


@pytest.mark.parametrize("failure", ["asset", "malformed_asset", "missing", "bundle", "assembly", "save"])
def test_degraded_terminal_refresh_names_every_unsaved_deed(monkeypatch, tmp_path, failure):
    """G9 red: premature refreshed IDs, failed/missing plots called retrieved, swallowed shared failure."""
    harness = _install_batch(monkeypatch, tmp_path)
    operations = [_batch_worksite()]
    failed_uids = set(DEEDS)
    if failure in {"asset", "malformed_asset"}:
        if failure == "asset":
            harness.upstream.asset_errors[DEEDS[1]] = OSError("asset unavailable")
        else:
            harness.upstream.asset_payloads[DEEDS[1]] = {"status": "success", "data": {"cards": []}}
        failed_uids = {DEEDS[1]}
    elif failure == "missing":
        operations = [_stake_with_card(uid) for uid in DEEDS]
        harness.upstream.payload["data"]["deeds"] = [row for row in harness.upstream.payload["data"]["deeds"] if row["deed_uid"] != DEEDS[1]]
        failed_uids = {DEEDS[1]}
    elif failure == "bundle":
        operations = [_stake_with_card(uid) for uid in DEEDS]
        harness.upstream.bundle_error = OSError("bundle unavailable")
    else:
        def fail(*args, **kwargs):
            raise OSError(f"{failure} unavailable")
        monkeypatch.setattr(land_analysis if failure == "assembly" else capture_mod,
                            "analyze_bundle" if failure == "assembly" else "_save_snapshot_and_refresh_cache_locked", fail)
    result = _verify(harness, operations)
    assert result["state"] == "domain-confirmed"
    assert result["domain"]["status"] == "domain_verified"
    assert {error["deed_uid"] for error in result["refresh_errors"]} == failed_uids
    assert set(result["refreshed_deeds"]) == set(DEEDS) - failed_uids
    assert [notice["code"] for notice in result["notices"]] == ["verification_confirmed"] + ["verification_refresh_failed"] * len(failed_uids)
    if failure in {"asset", "malformed_asset"}:
        for report in _saved(harness):
            plots = {plot["deed_uid"]: plot for plot in report["plots"]}
            assert plots[DEEDS[1]]["asset_fetch"]["status"] == "failed"
            assert all(plots[uid]["asset_fetch"]["status"] == "retrieved" for uid in set(DEEDS) - failed_uids)


@pytest.mark.parametrize("consumer", ["coordinator", "capture"])
def test_explicit_context_cannot_cross_account_before_consumption(monkeypatch, tmp_path, consumer):
    """G4 red: account validation deleted at either explicit-context consumer boundary."""
    harness = _install_batch(monkeypatch, tmp_path)
    evidence = capture_mod.MnemosyneEvidence(PLAYER)
    response = evidence.assets(DEED)
    assert response.data == _asset_payload(DEED)
    before = list(harness.upstream.calls)
    with pytest.raises(ValueError, match="account"):
        if consumer == "coordinator":
            capture_mod.expand_plots(harness.config, "anotherplayer", DEEDS, evidence=evidence)
        else:
            evidence.capture.expand_deeds(DEEDS, "anotherplayer", evidence=evidence)
    assert harness.upstream.calls == before


def test_context_construction_and_provenance_do_not_acquire_evidence(monkeypatch, tmp_path):
    """G12/G5 red: constructor/provenance constructs capture/cache or fetches before the spec callback."""
    calls = []

    def failing_factory(*, land_client=None, collection_client=None):
        calls.append(True)
        raise OSError("capture construction unavailable")

    monkeypatch.setattr(capture_mod, "LandCapture", failing_factory)
    evidence = capture_mod.MnemosyneEvidence(PLAYER, attempt=2)
    assert evidence.provenance()["resources"] == {}
    assert calls == []
    monkeypatch.setattr(routes_op_preview_mod, "verify_chain", _chain_verified)
    config = CeresConfig(root=tmp_path / ".ceres", player=PLAYER)
    result = json.loads(op_verify({"tx_id": "772d51e445ae98a9e4194be2a4dad10c84277d5f", "operations": [_batch_worksite()]}, _request(config), player=None).body)
    assert result["domain"]["status"] == "domain_could_not_verify"
    assert len(result["domain"]["evidence"]["unverifiable"]) == 3
    assert [notice["code"] for notice in result["notices"]] == ["verification_could_not_verify"]
    assert calls == [True]


@pytest.mark.parametrize("family", ["stake", "worksite"])
def test_resource_times_and_provenance_are_transport_observations(monkeypatch, tmp_path, family):
    """G10 red: local clocks, shared timestamp, discarded ResponseData, cache writes, or refresh bundle attributed to stake verdict."""
    harness = _install_batch(monkeypatch, tmp_path)
    observed_times = []
    original_normalize = land_analysis.normalize_bundle

    def normalize(payload, observed_at=None):
        observed_times.append(observed_at)
        return original_normalize(payload, observed_at=observed_at)

    monkeypatch.setattr(land_analysis, "normalize_bundle", normalize)
    monkeypatch.setattr(capture_mod, "normalize_bundle", normalize)
    operations = [_stake_with_card(uid) for uid in DEEDS] if family == "stake" else [_batch_worksite()]
    result = _verify(harness, operations, verification_attempt=3)
    assert observed_times
    assert set(observed_times) == {BUNDLE_TIME}
    provenance = result["domain"]["evidence"]["provenance"]
    assert provenance["account"] == PLAYER
    assert provenance["attempt"] == 3
    expected_resources = {"assets": {uid: {"retrieved_at": _asset_time(uid), "attempts": 2} for uid in DEEDS}} if family == "stake" else {
        "bundle": {"retrieved_at": BUNDLE_TIME, "attempts": 2},
    }
    assert provenance["resources"] == expected_resources
    for report in _saved(harness):
        plots = {plot["deed_uid"]: plot for plot in report["plots"]}
        assert all(plots[uid]["observed_at"] == _asset_time(uid) for uid in DEEDS)
        for worker in report["workers"]:
            if worker["stake_ref_uid"] in DEEDS:
                assert worker["observed_at"] == _asset_time(worker["stake_ref_uid"])
        assert report["provenance"]["resources"]["bundle"]["retrieved_at"] == BUNDLE_TIME
        if family == "worksite":
            assert report["coverage"]["collection"]["fetched_at"] == COLLECTION_TIME
            assert report["provenance"]["resources"]["collection"] == {"retrieved_at": COLLECTION_TIME, "attempts": 2}
        assert "raw" not in report
    assert all(capture.cache.evidence_accesses == [] for capture in harness.instances)


def test_collection_failure_saves_assets_and_carries_old_pool_honestly(monkeypatch, tmp_path):
    """G9 red: failed collection advertised freshly complete, erased base pool, or hidden refresh error."""
    harness = _install_batch(monkeypatch, tmp_path)
    old_time = "2036-12-30T00:00:00Z"
    base, _ = capture_mod.latest_snapshot(harness.config, PLAYER)
    base = copy.deepcopy(base)
    base["cards"] = [{"uid": "OLD-POOL", "facts": {"uid": "OLD-POOL"}}]
    base["coverage"]["collection"] = {"complete": True, "retrieved": 1, "expected": None, "fetched_at": old_time}
    capture_mod._save_snapshot_and_refresh_cache(base, capture_mod._latest_path(harness.config, PLAYER))
    harness.upstream.collection_error = OSError("collection unavailable")
    original_merge = capture_mod.merge_reports
    incoming = []

    def merge(base, report, **kwargs):
        incoming.append(copy.deepcopy(report))
        return original_merge(base, report, **kwargs)

    monkeypatch.setattr(capture_mod, "merge_reports", merge)
    result = _verify(harness, [_batch_worksite()])
    assert result["state"] == "domain-confirmed"
    assert result["refreshed_deeds"] == list(DEEDS)
    assert {error["deed_uid"] for error in result["refresh_errors"]} == set(DEEDS)
    assert len(incoming) == 1
    assert incoming[0]["coverage"]["collection"]["complete"] is False
    assert incoming[0]["coverage"]["collection"]["fetched_at"] is None
    assert "collection" not in incoming[0]["provenance"]["resources"]
    for report in _saved(harness):
        assert report["coverage"]["collection"]["fetched_at"] == old_time
    cached, _ = capture_mod.latest_snapshot(harness.config, PLAYER)
    assert [card["uid"] for card in capture_mod.load_card_pool(cached)["cards"]] == ["OLD-POOL"]


def test_asset_and_collection_failures_are_unioned_without_losing_siblings(monkeypatch, tmp_path):
    """G9 red: per-deed errors suppress a simultaneous shared collection failure."""
    harness = _install_batch(monkeypatch, tmp_path)
    harness.upstream.asset_errors[DEEDS[1]] = OSError("asset unavailable")
    harness.upstream.collection_error = OSError("collection unavailable")
    result = _verify(harness, [_batch_worksite()])
    assert result["state"] == "domain-confirmed"
    assert set(result["refreshed_deeds"]) == {DEEDS[0], DEEDS[2]}
    assert {error["deed_uid"] for error in result["refresh_errors"]} == set(DEEDS)
    assert len(result["refresh_errors"]) == 3
    for report in _saved(harness):
        plots = {plot["deed_uid"]: plot for plot in report["plots"]}
        assert plots[DEEDS[1]]["asset_fetch"]["status"] == "failed"
        assert plots[DEEDS[0]]["asset_fetch"]["status"] == plots[DEEDS[2]]["asset_fetch"]["status"] == "retrieved"


@pytest.mark.parametrize(
    "domain_kind, chain_status, game_status, attempt, head, block, expected_state, notice",
    [
        ("verified", "chain_verified", "game_rejected", 1, 120, 100, "game-refused", "verification_game_refused"),
        ("verified", "chain_absent", "game_unknown", 1, 120, 100, "domain-confirmed", "verification_confirmed"),
        ("pending", "chain_verified", "game_unknown", 1, 120, 100, "domain-pending", "verification_pending"),
        ("pending", "chain_verified", "game_unknown", 2, 119, 100, "domain-pending", "verification_pending"),
        ("pending", "chain_verified", "game_unknown", 2, 120, 100, "domain-not-applied", "verification_not_applied"),
        ("pending", "chain_verified", "game_accepted", 2, 120, 100, "domain-pending", "verification_pending"),
        ("pending", "chain_absent", "game_unknown", 2, 120, 100, "domain-pending", "verification_pending"),
        ("worksite_pending", "chain_verified", "game_unknown", 2, 120, 100, "domain-pending", "verification_pending"),
        ("pending", "chain_verified", "game_unknown", 2, 120, None, "domain-pending", "verification_pending"),
        ("pending", "chain_verified", "game_unknown", 2, None, 100, "domain-pending", "verification_pending"),
        ("unverifiable", "chain_verified", "game_unknown", 2, 120, 100, "domain-could-not-verify", "verification_could_not_verify"),
        ("failed", "chain_verified", "game_unknown", 2, 120, 100, "domain-could-not-verify", "verification_could_not_verify"),
        ("mismatch", "chain_absent", "game_unknown", 2, 120, 100, "domain-mismatch", "verification_mismatch"),
        ("fallback", "chain_absent", "game_unknown", 2, 120, 100, "chain_absent", "verification_not_found"),
    ],
)
def test_route_eight_branch_precedence_and_not_applied_conjuncts(monkeypatch, tmp_path, domain_kind, chain_status, game_status, attempt, head, block, expected_state, notice):
    """G6 red: reordered branches, removed settlement conjunct, strict > thresholds, or collapsed unverifiable classes."""
    harness = _install_batch(monkeypatch, tmp_path)
    chain_evidence = {"tx_id": "a" * 40}
    if block is not None:
        chain_evidence["block_num"] = block
    monkeypatch.setattr(routes_op_preview_mod, "verify_chain", lambda *args, **kwargs: {"status": chain_status, "evidence": chain_evidence})
    monkeypatch.setattr(routes_op_preview_mod, "game_verdict", lambda *args, **kwargs: {"status": game_status, "evidence": {"reason": "fixture game verdict", "lookup_attempted": False}})

    def rpc(*args):
        if head is None:
            raise OSError("head unavailable")
        return {"head_block_number": head}

    monkeypatch.setattr(routes_op_preview_mod.fides_verify, "_http_rpc_call", rpc)
    operations = [_stake_with_card(DEED)]
    if domain_kind == "pending":
        harness.upstream.asset_payloads[DEED] = {"status": "success", "data": {"cards": [], "items": []}}
    elif domain_kind == "worksite_pending":
        operations = [_batch_worksite()]
        harness.upstream.payload = _batch_bundle(construction=True)
    elif domain_kind == "unverifiable":
        harness.upstream.asset_payloads[DEED] = {"status": "success", "data": {"cards": []}}
    elif domain_kind == "failed":
        harness.upstream.asset_errors[DEED] = OSError("generic stake failure")
    elif domain_kind == "mismatch":
        operations = [["custom_json", {"id": "sm_stake_change", "json": "{}"}]]
    elif domain_kind == "fallback":
        monkeypatch.setattr(routes_op_preview_mod.LandVerificationSpec, "check_domain", lambda *args, **kwargs: {"status": "not_checked", "evidence": {}})
    result = _verify(harness, operations, tx_id="a" * 40, verification_attempt=attempt)
    assert result["state"] == expected_state
    domain_status = {"verified": "domain_verified", "pending": "domain_pending", "worksite_pending": "domain_pending", "unverifiable": "domain_could_not_verify", "failed": "domain_check_failed", "mismatch": "domain_mismatch", "fallback": "not_checked"}[domain_kind]
    assert result["domain"]["status"] == domain_status
    assert [item["code"] for item in result["notices"]] == [notice] + (["verification_domain_check_failed"] if domain_kind == "failed" else [])
    evidence = result["domain"]["evidence"]
    if domain_status == "domain_pending":
        assert evidence["pending_metadata"] == {"attempt": attempt, "escalate_after_attempts": 5, "requires_escalation": False}
    if expected_state == "domain-not-applied":
        assert {key: evidence[key] for key in ("inclusion_block", "head_block", "age_blocks", "age_seconds", "attempt")} == {
            "inclusion_block": 100, "head_block": 120, "age_blocks": 20, "age_seconds": 60, "attempt": 2,
        }
    else:
        assert "age_blocks" not in evidence


@pytest.mark.parametrize("cold, expected_requests", [(False, 5), (True, 6)])
def test_construction_skips_retired_history_reads_and_counts_card_metadata(monkeypatch, tmp_path, cold, expected_requests):
    """Retired project-history reads stay absent; card metadata is retained and counted."""
    upstream = _BatchUpstream(_batch_bundle(construction=True))
    harness = _install_batch(monkeypatch, tmp_path, upstream=upstream, cold=cold)
    result = _verify(harness, [_batch_worksite(construction=True)])
    assert result["state"] == "domain-confirmed"
    assert result["refresh_errors"] == []
    assert len(harness.instances) == 1
    assert harness.instances[0]._requests == len(upstream.calls) == expected_requests
    assert sum(kind == "history" for kind, _ in upstream.calls) == 0
    assert sum(kind == "details" for kind, _ in upstream.calls) == (1 if cold else 0)
    assert sum(key.startswith("land_project_history_") for key in harness.instances[0].cache.reads) == 0
    for report in _saved(harness):
        plots = {plot["deed_uid"]: plot for plot in report["plots"]}
        assert all(plots[uid]["facts"]["prior_resource"] is None for uid in DEEDS)
        assert all(worker["name"] == "Reference worker" for worker in report["workers"] if worker["stake_ref_uid"] in DEEDS)
        assert report["capture"]["requests"] == expected_requests
        assert report["capture"]["core_requests"] == 5
        assert report["capture"]["history_requests"] == 0
        assert report["capture"]["card_metadata_requests"] == (1 if cold else 0)


def test_shared_capture_paces_dispatches_and_charges_failed_attempts(monkeypatch, tmp_path):
    """G11 red: reset counter/last dispatch per deed, disable pacing, count successes only, or ignore budget."""
    now = [100.0]
    sleeps = []
    starts = []

    def sleeper(delay):
        sleeps.append(delay)
        now[0] += delay

    monkeypatch.setattr(land_analysis.time, "monotonic", lambda: now[0])
    harness = _install_batch(monkeypatch, tmp_path, budget=3, min_delay=0.3, sleeper=sleeper)
    harness.upstream.before_call = lambda kind, key: starts.append(now[0])
    harness.upstream.asset_errors[DEEDS[1]] = OSError("asset read failed")
    result = _verify(harness, [_batch_worksite()])
    assert result["state"] == "domain-confirmed"
    assert len(harness.instances) == 1
    assert harness.instances[0]._requests == len(harness.upstream.calls) == 3
    assert len(starts) == 3
    assert all(right - left >= 0.3 - 1e-10 for left, right in zip(starts, starts[1:]))
    assert len(sleeps) == 2
    assert {error["deed_uid"] for error in result["refresh_errors"]} >= {DEEDS[1], DEEDS[2]}
    assert ("assets", DEEDS[2]) not in harness.upstream.calls
    assert ("collection", PLAYER) not in harness.upstream.calls


@pytest.mark.parametrize("deed_count", [198, 199])
def test_attempt_wide_worksite_budget_boundary(monkeypatch, tmp_path, deed_count):
    """G11 red: per-deed budgets, silent cap increase/reset, 201st dispatch, or hidden collection exhaustion."""
    deeds = tuple(f"budget-deed-{index:03d}" for index in range(deed_count))
    upstream = _BatchUpstream(_batch_bundle(deeds))
    harness = _install_batch(monkeypatch, tmp_path, upstream=upstream)
    result = _verify(harness, [_batch_worksite(deeds=deeds)])
    assert result["state"] == "domain-confirmed"
    assert len(harness.instances) == 1
    assert harness.instances[0].request_budget == 200
    assert harness.instances[0]._requests == len(upstream.calls) == 200
    assert sum(kind == "assets" for kind, _ in upstream.calls) == deed_count
    assert sum(kind == "collection" for kind, _ in upstream.calls) == (1 if deed_count == 198 else 0)
    assert result["refreshed_deeds"] == list(deeds)
    if deed_count == 198:
        assert result["refresh_errors"] == []
    else:
        assert {error["deed_uid"] for error in result["refresh_errors"]} == set(deeds)


@pytest.mark.parametrize("kind", ["history", "metadata"])
def test_optional_reference_budget_exhaustion_is_reported(monkeypatch, tmp_path, kind):
    """G11 red: H/C bypasses shared budget or exhaustion appears only as a swallowed display warning."""
    upstream = _BatchUpstream(_batch_bundle(construction=kind == "history"))
    harness = _install_batch(monkeypatch, tmp_path, upstream=upstream, budget=5, cold=True)
    result = _verify(harness, [_batch_worksite(construction=kind == "history")])
    assert result["state"] == "domain-confirmed"
    assert harness.instances[0]._requests == len(upstream.calls) == 5
    assert result["refreshed_deeds"] == list(DEEDS)
    assert result["refresh_errors"]
    assert not any(resource in {"history", "details"} for resource, _ in upstream.calls)


def test_duplicate_deed_operations_share_reads_but_each_comparison_survives(monkeypatch, tmp_path):
    """G12 red: repeated deed comparison skipped or duplicate references acquire extra responses."""
    harness = _install_batch(monkeypatch, tmp_path)
    result = _verify(harness, [_stake_with_card(DEED), _stake_with_card(DEED, "MISSING-CARD")])
    assert result["state"] == "domain-pending"
    assert result["domain"]["evidence"]["pending"] == [
        f"expected MISSING-CARD staked at {DEED}, not observed present (may still be propagating)",
    ]
    assert harness.instances[0]._requests == len(harness.upstream.calls) == 1
    result = _verify(harness, [_batch_worksite(deeds=(*DEEDS, DEED)), _completion_op(DEED)])
    assert result["state"] == "domain-confirmed"
    assert result["refreshed_deeds"] == list(DEEDS)
    assert len(harness.instances) == 2
    assert harness.instances[1]._requests == 5
    assert harness.upstream.calls.count(("bundle", PLAYER)) == 1


@pytest.mark.parametrize("include_malformed", [False, True])
def test_spec_mixed_outcomes_preserve_failure_precedence(monkeypatch, tmp_path, include_malformed):
    """G6 red: pending outranks unavailable, unavailable outranks malformed, or operation order skips comparisons."""
    harness = _install_batch(monkeypatch, tmp_path)
    harness.upstream.asset_payloads[DEEDS[1]] = {"status": "success", "data": {"cards": []}}
    operations = [_stake_with_card(DEEDS[0], "MISSING"), _stake_with_card(DEEDS[1])]
    if include_malformed:
        operations.append(["custom_json", {"id": "sm_stake_change", "json": "{}"}])
    result = _verify(harness, operations)
    assert result["domain"]["status"] == ("domain_mismatch" if include_malformed else "domain_could_not_verify")
    evidence = result["domain"]["evidence"]
    assert len(evidence["unverifiable"]) == 1
    assert set(evidence["deeds"]) == {DEEDS[0]}
    if include_malformed:
        assert len(evidence["mismatches"]) == len(evidence["pending"]) == 1
    assert harness.instances[0]._requests == 2


def test_legacy_expansion_signatures_and_defaults_remain_unchanged():
    """G12 red: reshape old single-deed APIs or change offline/TTL/collection defaults."""
    capture_parameters = inspect.signature(LandCapture.expand_deed).parameters
    # one additive trailing keyword; old order and defaults unchanged.
    assert list(capture_parameters) == ["self", "deed_uid", "player", "offline", "bundle_ttl", "include_collection", "narrow", "plot_id"]
    assert {name: capture_parameters[name].default for name in ("player", "offline", "bundle_ttl", "include_collection")} == {
        "player": land_analysis.DEFAULT_PLAYER, "offline": False, "bundle_ttl": 0, "include_collection": False,  # was 3600
    }
    assert capture_parameters["narrow"].default is False
    assert capture_parameters["plot_id"].default is None
    route_parameters = inspect.signature(capture_mod.expand_plot).parameters
    assert list(route_parameters) == ["config", "player", "deed_uid", "include_collection", "bundle_ttl"]
    assert route_parameters["include_collection"].kind is inspect.Parameter.KEYWORD_ONLY
    assert route_parameters["include_collection"].default is False
    assert route_parameters["bundle_ttl"].kind is inspect.Parameter.KEYWORD_ONLY
    assert route_parameters["bundle_ttl"].default is None


def test_attempt_slots_retain_original_response_and_exception_identity(monkeypatch, tmp_path):
    """G4/G5/G11 red: payload-only storage, failure re-dispatch, recreated exception, or budget reset."""
    harness = _install_batch(monkeypatch, tmp_path, budget=2)
    evidence = capture_mod.MnemosyneEvidence(PLAYER)
    response = evidence.assets(DEED)
    assert evidence.assets(DEED) is response
    original_error = OSError("original bundle error")
    harness.upstream.bundle_error = original_error
    for _ in range(2):
        with pytest.raises(OSError) as failure:
            evidence.bundle()
        assert failure.value is original_error
    with pytest.raises(TransportError, match="budget exhausted"):
        evidence.assets(DEEDS[1])
    assert evidence.capture._requests == len(harness.upstream.calls) == 2
    assert evidence.provenance()["resources"] == {"assets": {DEED: {"retrieved_at": _asset_time(DEED), "attempts": 2}}}


@pytest.mark.parametrize("resource", ["assets", "collection"])
def test_asset_and_collection_failure_slots_replay_original_exception(monkeypatch, tmp_path, resource):
    """G5 red: failed resource retried within the attempt or original exception class/reason replaced."""
    harness = _install_batch(monkeypatch, tmp_path)
    original_error = OSError(f"{resource} connection reset")
    if resource == "assets":
        harness.upstream.asset_errors[DEED] = original_error
    else:
        harness.upstream.collection_error = original_error
    evidence = capture_mod.MnemosyneEvidence(PLAYER)
    for _ in range(2):
        with pytest.raises(OSError) as failure:
            evidence.assets(DEED) if resource == "assets" else evidence.collection()
        assert failure.value is original_error
    assert evidence.capture._requests == len(harness.upstream.calls) == 1
    assert evidence.provenance()["resources"] == {}


def test_legacy_assembler_keeps_single_stamp_defaults():
    """G12 red: additive timestamp inputs remove legacy collection/worker/plot observation fallbacks."""
    report = land_analysis.analyze_bundle(
        PLAYER, _batch_bundle(), {DEED: _asset_payload(DEED)["data"]},
        collection_payload={"cards": [_asset_payload(DEED)["data"]["cards"][0]]},
        observed_at=RETRIEVED_AT,
    )
    assert all(plot["observed_at"] == RETRIEVED_AT for plot in report["plots"])
    assert all(worker["observed_at"] == RETRIEVED_AT for worker in report["workers"])
    assert report["coverage"]["collection"]["fetched_at"] == RETRIEVED_AT


def test_combine_keeps_real_card_verifier_and_separate_refresh(monkeypatch, tmp_path):
    """G12 red: combine constructs land evidence, acquires deeds, or switches its existing quick refresh."""
    config = CeresConfig(root=tmp_path / ".ceres", player=PLAYER)
    cards = [
        {"uid": "G18-100-000001", "card_detail_id": "879", "bcx": 10, "level": 1},
        {"uid": "G18-100-000002", "card_detail_id": "879", "bcx": 5, "level": 1},
    ]
    operations = [["custom_json", {
        "id": "sm_combine_cards", "json": json.dumps({"cards": [card["uid"] for card in cards], "app": "x", "n": "abcdefghij"}),
        "required_auths": [PLAYER], "required_posting_auths": [],
    }]]
    refreshed = []
    collections = []

    def forbidden(*args, **kwargs):
        raise AssertionError("combine must not enter shared Land evidence")

    class Client:
        def collection(self, player):
            collections.append(player)
            return ResponseData({"cards": [{**cards[0], "bcx": 15}]}, COLLECTION_TIME, 1)

    monkeypatch.setattr(capture_mod, "MnemosyneEvidence", forbidden)
    monkeypatch.setattr(capture_mod, "expand_plots", forbidden)
    monkeypatch.setattr(capture_mod, "LandCapture", forbidden)
    monkeypatch.setattr(routes_op_preview_mod, "verify_chain", _chain_verified)
    monkeypatch.setattr(routes_op_preview_mod, "SplinterlandsClient", Client)
    monkeypatch.setattr(routes_op_preview_mod.combine_evidence, "load", lambda *args, **kwargs: cards)
    monkeypatch.setattr(routes_op_preview_mod.combine_evidence, "discard", lambda *args: None)
    monkeypatch.setattr(capture_mod, "is_running", lambda account: False)
    monkeypatch.setattr(capture_mod, "start_refresh", lambda cfg, account: refreshed.append(account) or True)
    result = json.loads(op_verify({"tx_id": "772d51e445ae98a9e4194be2a4dad10c84277d5f", "operations": operations}, _request(config), player=None).body)
    assert result["state"] == "domain-confirmed"
    assert result["domain"]["status"] == "domain_verified"
    assert result["domain"]["evidence"]["survivor_uid"] == cards[0]["uid"]
    assert "provenance" not in result["domain"]["evidence"]
    assert result["refreshed_deeds"] == []
    assert result["combine_refresh"] == {"triggered": True, "mode": "quick", "already_running": False}
    assert refreshed == collections == [PLAYER]


@pytest.mark.parametrize("absence", ["deed", "worksite"])
def test_shared_projection_keeps_absent_and_orphan_deeds_unverifiable(monkeypatch, tmp_path, absence):
    """G1/G5 red: shared projection invents a deed from an orphan worksite or fabricates missing facts."""
    harness = _install_batch(monkeypatch, tmp_path)
    key = "deeds" if absence == "deed" else "worksite_details"
    harness.upstream.payload["data"][key] = [row for row in harness.upstream.payload["data"][key] if row["deed_uid"] != DEEDS[1]]
    result = _verify(harness, [_batch_worksite()])
    assert result["state"] == "domain-could-not-verify"
    assert result["domain"]["status"] == "domain_could_not_verify"
    assert result["domain"]["evidence"]["unverifiable"] == [f"deed {DEEDS[1]}: worksite_facts not available in the fresh bundle"]
    assert set(result["domain"]["evidence"]["deeds"]) == {DEEDS[0], DEEDS[2]}
    assert result["refreshed_deeds"] == result["refresh_errors"] == []
    assert harness.instances[0]._requests == len(harness.upstream.calls) == 1
