import base64
import copy
import io
import json
import re
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from praetoria.land import analyze_bundle, available_item_ids, classify_card, recommend_land, render_land_analysis, stable_asset_ref
from praetoria import cli
from praetoria.transport import API_ORIGIN, LAND_ORIGIN, FixedGetTransport, ResponseTooLarge, validate_fixed_url
from praetoria.errors import SchemaError, UpstreamError


ROOT = Path(__file__).parent / "fixtures" / "land"
TRANSPORT_ROOT = Path(__file__).parent / "fixtures" / "transport"
AS_OF = datetime(2026, 8, 9, tzinfo=timezone.utc)


def fixture(name):
    return json.loads((ROOT / name).read_text(encoding="utf-8"))


def report_with_assets():
    bundle = fixture("deeds_multi.json")
    assets = {}
    for name in ("assets_active_above_cap.json", "assets_active_single_worker.json", "assets_active_with_runi.json", "assets_idle_empty_worksite.json", "assets_unfinished_construction.json"):
        data = fixture(name)["data"]
        if data["cards"]:
            assets[data["cards"][0]["stake_ref_uid"]] = data
    return analyze_bundle("player0005", bundle, assets, collection_payload=fixture("collection_sample.json"), as_of=AS_OF)


def r107_roster_inputs():
    asset_a = fixture("assets_active_with_runi.json")["data"]
    asset_b = fixture("assets_active_single_worker.json")["data"]
    asset_a_cards = [card for card in asset_a["cards"] if card["slot"] != 2]
    asset_b_cards = asset_b["cards"]
    deed_a = asset_a_cards[0]["stake_ref_uid"]
    deed_b = asset_b_cards[0]["stake_ref_uid"]

    collection_cards = []
    asset_only_fields = {
        "slot", "stake_type_uid", "is_powered", "base_pp_after_cap", "total_harvest_pp",
        "total_construction_pp", "work_per_hour", "terrain_boost", "card_abilities_boost",
        "card_bloodlines_boost",
    }
    for card in asset_a_cards + asset_b_cards:
        collection_card = {key: value for key, value in card.items() if key not in asset_only_fields}
        collection_card["stake_end_date"] = None
        collection_cards.append(collection_card)
    leaving = copy.deepcopy(collection_cards[0])
    leaving["uid"] = "leaving-a"
    leaving["stake_end_date"] = "2026-09-06T00:00:00Z"
    collection_cards.append(leaving)

    bundle = {
        "status": "success",
        "data": {
            "deeds": [{"deed_uid": deed_a}, {"deed_uid": deed_b}],
            "worksite_details": [],
            "staking_details": [],
        },
    }
    collection = {"cards": collection_cards}
    assets = {
        deed_a: {"cards": asset_a_cards, "items": []},
        deed_b: {"cards": asset_b_cards, "items": []},
    }
    return bundle, collection, assets


class FixtureResponse(io.BytesIO):
    def __init__(self, body, headers):
        super().__init__(body)
        self.headers = headers

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()


class FixtureOpener:
    def __init__(self, body, headers=None):
        self.body = body
        self.headers = headers or {}
        self.request = None
        self.timeout = None

    def open(self, request, timeout):
        self.request = request
        self.timeout = timeout
        return FixtureResponse(self.body, self.headers)


class TransportPhase0Tests(unittest.TestCase):
    def _fixture_bytes(self):
        return (TRANSPORT_ROOT / "collection.json").read_bytes()

    def _gzip_fixture_bytes(self):
        encoded = (TRANSPORT_ROOT / "collection.json.gz.b64").read_text(encoding="ascii")
        return base64.b64decode(encoded)

    def test_requests_advertise_gzip_only(self):
        opener = FixtureOpener(self._fixture_bytes())
        FixedGetTransport(opener=opener).get_json(API_ORIGIN + "/cards/collection")
        headers = {key.lower(): value for key, value in opener.request.header_items()}
        self.assertEqual(headers["accept-encoding"], "gzip")
        self.assertNotIn("br", headers["accept-encoding"])

    def test_gzip_fixture_parses_identically_to_uncompressed_twin(self):
        plain_opener = FixtureOpener(self._fixture_bytes())
        gzip_opener = FixtureOpener(self._gzip_fixture_bytes(), {"Content-Encoding": "gzip"})
        plain = FixedGetTransport(opener=plain_opener).get_json(API_ORIGIN + "/cards/collection")
        compressed = FixedGetTransport(opener=gzip_opener).get_json(API_ORIGIN + "/cards/collection")
        self.assertEqual(compressed.data, plain.data)

    def test_uncompressed_fixture_still_parses_when_gzip_is_advertised(self):
        opener = FixtureOpener(self._fixture_bytes())
        result = FixedGetTransport(opener=opener).get_json(API_ORIGIN + "/cards/collection")
        self.assertEqual(result.data, json.loads(self._fixture_bytes().decode("utf-8")))

    def test_gzip_response_limit_applies_after_decompression(self):
        opener = FixtureOpener(self._gzip_fixture_bytes(), {"Content-Encoding": "gzip"})
        with self.assertRaises(ResponseTooLarge):
            FixedGetTransport(max_response_bytes=150, opener=opener).get_json(API_ORIGIN + "/cards/collection")


class LandPhase1Tests(unittest.TestCase):
    def test_collection_joined_roster_equals_each_deed_asset_roster(self):
        """The decisive R-107 guard compares every deed's joined collection UIDs with
        its per-deed asset UIDs, including a slot-2 gap and Runi at slot 0. Injecting the
        defect `raw["stake_ref_uid"]` -> `raw["stake_plot"]` in the collection join, or
        dropping `and not card.get("stake_end_date")`, makes this assertion fail."""
        bundle, collection, assets = r107_roster_inputs()
        report = analyze_bundle("player0005", bundle, assets, collection_payload=collection, as_of=AS_OF, include_asset_ids=True)
        actual = {}
        for worker in report["workers"]:
            actual.setdefault(worker["stake_ref_uid"], set()).add(worker["uid"])
        expected = {
            deed_uid: {card["uid"] for card in response["cards"]}
            for deed_uid, response in assets.items()
        }
        self.assertEqual(actual, expected)
        deed_a = next(deed_uid for deed_uid, response in assets.items() if any(card["card_detail_id"] == 505 for card in response["cards"]))
        workers_a = [worker for worker in report["workers"] if worker["stake_ref_uid"] == deed_a]
        self.assertEqual(next(worker["slot"] for worker in workers_a if worker["card_detail_id"] == 505), 0)
        self.assertNotIn(2, {worker.get("slot") for worker in workers_a})

    def test_collection_join_keeps_real_shape_without_asset_fanout(self):
        """The bulk-only path keeps the complete roster while leaving asset-only fields
        absent. The slot gap and reserved Runi slot must survive the join. Injecting
        `for collection_worker in asset_cards` in place of `for collection_worker in
        collection_workers` makes the roster empty when `assets={}` and turns this guard red."""
        bundle, collection, _assets = r107_roster_inputs()
        report = analyze_bundle("player0005", bundle, {}, collection_payload=collection, as_of=AS_OF, include_asset_ids=True)
        by_uid = {worker["uid"]: worker for worker in report["workers"]}
        active = {card["uid"] for card in collection["cards"] if not card["stake_end_date"]}
        runi_uid = next(card["uid"] for card in collection["cards"] if card["card_detail_id"] == 505 and not card["stake_end_date"])
        self.assertEqual(set(by_uid), active)
        self.assertEqual(by_uid[runi_uid]["stake_type_uid"], "STK-LND-RUNI")
        self.assertNotIn("slot", by_uid[runi_uid])
        self.assertNotIn("leaving-a", by_uid)

    def test_nested_body_error_predicate_and_valid_empty_page(self):
        with self.assertRaises(UpstreamError):
            available_item_ids(fixture("available_body_error_p0001.json"))
        self.assertEqual(available_item_ids(fixture("available_title_empty.json")), [])
        self.assertEqual(len(available_item_ids(fixture("available_totem_candidates.json"))), 5)

    def test_classifier_is_total_and_missing_stake_evidence_is_not_confident(self):
        cards = fixture("collection_sample.json")["cards"]
        results = [classify_card(card, AS_OF, "player0005") for card in cards]
        self.assertEqual(len(results), len(cards))
        self.assertTrue(all(result.bucket and result.confidence in {"confident", "uncertain", "blocked"} for result in results))
        self.assertTrue(all(result.branch in range(1, 12) for result in results))
        missing = classify_card({"uid": "missing", "delegated_to": None, "rental_type": None, "market_id": None, "unlock_date": None, "expiration_date": None, "stake_ref_uid": None, "stake_start_date": None, "stake_end_date": None}, AS_OF)
        self.assertEqual(missing.confidence, "uncertain")

    def test_rented_in_card_is_free_but_rented_out_card_stays_blocked(self):
        """A card rented IN (maintainer player0011, delegated to the player) keeps the rental's market_id and a past listing
        expiration_date; it is the renter's to stake. Shape recorded from player0018's collection on 2026-10-18."""
        rented_in = {
            "uid": "C2-1053-15A6V5Z4HS", "player": "player0011", "delegated_to": "player0018", "rental_type": "season",
            "market_id": "718dd69c05cf5afb6312f62cf3eab2f79440e482-0", "unlock_date": None,
            "expiration_date": "2025-04-24T18:47:09.000Z", "stake_ref_uid": None, "stake_start_date": None, "stake_end_date": None,
        }
        self.assertEqual(classify_card(rented_in, AS_OF, "player0018").bucket, "unstaked_apparent")
        rented_out = dict(rented_in, player="player0018", delegated_to="someone")
        self.assertEqual(classify_card(rented_out, AS_OF, "player0018").reason, "delegated to someone")
        listed_for_rent = dict(rented_in, player="player0018", delegated_to=None)
        self.assertEqual(classify_card(listed_for_rent, AS_OF, "player0018").branch, 2)

    def test_checked_deed_absence_marks_future_stake_as_cooling_at_exact_end(self):
        """: renamed from `stake_expired_or_historical` to `stake_cooling_down` --
        a future `stake_end_date` is a card already off its deed and counting down, not
        one whose stake has already ended."""
        end = "2026-08-12T18:57:51Z"
        card = {
            "uid": "C3-82-6KHURMQ2VK", "delegated_to": None, "rental_type": None,
            "market_id": None, "unlock_date": None, "expiration_date": None,
            "stake_ref_uid": "I-294-5ee4a78c89c096", "stake_start_date": "2026-08-09T18:57:51Z",
            "stake_end_date": end,
        }
        result = classify_card(card, AS_OF, "player0005", assigned=None, assets_checked=True)
        self.assertEqual(result.bucket, "stake_cooling_down")
        self.assertEqual(result.confidence, "confident")
        self.assertIn(end, result.reason)

    def test_future_stake_end_date_is_cooling_down_even_without_asset_cross_check(self):
        """ / R-127: a `stake_end_date` is only ever set once an unstake has begun, so
        a FUTURE one means the card is already off its deed and counting down -- never a
        live, merely-unconfirmed active stake. Before the fix this fell into
        `stake_active_unconfirmed` whenever the deed's assets were not directly
        cross-checked (`assets_checked=False`, the common case), which fed a false
        `collection_join` assignment for a card that no longer belonged on the plot."""
        card = {
            "uid": "active", "delegated_to": None, "rental_type": None, "market_id": None,
            "unlock_date": None, "expiration_date": None, "stake_ref_uid": "I-1-a",
            "stake_start_date": "2026-08-01T00:00:00Z", "stake_end_date": "2026-08-20T00:00:00Z",
        }
        result = classify_card(card, AS_OF, "player0005", assigned=None, assets_checked=False)
        self.assertEqual(result.bucket, "stake_cooling_down")
        self.assertEqual(result.confidence, "confident")
        self.assertIn("2026-08-20T00:00:00Z", result.reason)

    def test_null_stake_ref_with_past_end_date_is_expired(self):
        card = {
            "uid": "past-null-ref", "delegated_to": None, "rental_type": None, "market_id": None,
            "unlock_date": None, "expiration_date": None, "stake_ref_uid": None,
            "stake_start_date": "2026-08-01T00:00:00Z", "stake_end_date": "2026-08-08T00:00:00Z",
        }
        result = classify_card(card, AS_OF, "player0005", assigned=None, assets_checked=True)
        self.assertEqual(result.bucket, "stake_expired_or_historical")
        self.assertEqual(result.confidence, "confident")

    def test_null_stake_ref_with_future_end_date_is_cooling(self):
        end = "2026-08-20T00:00:00Z"
        card = {
            "uid": "future-null-ref", "delegated_to": None, "rental_type": None, "market_id": None,
            "unlock_date": None, "expiration_date": None, "stake_ref_uid": None,
            "stake_start_date": "2026-08-01T00:00:00Z", "stake_end_date": end,
        }
        result = classify_card(card, AS_OF, "player0005", assigned=None, assets_checked=True)
        self.assertEqual(result.bucket, "stake_cooling_down")
        self.assertEqual(result.confidence, "confident")
        self.assertIn(end, result.reason)

    def test_null_end_date_stake_without_asset_cross_check_remains_unconfirmed(self):
        """The bucket `stake_active_unconfirmed` is now reserved for a card with NO
        `stake_end_date` at all -- genuinely staked, just not cross-checked."""
        card = {
            "uid": "active", "delegated_to": None, "rental_type": None, "market_id": None,
            "unlock_date": None, "expiration_date": None, "stake_ref_uid": "I-1-a",
            "stake_start_date": "2026-08-01T00:00:00Z", "stake_end_date": None,
        }
        result = classify_card(card, AS_OF, "player0005", assigned=None, assets_checked=False)
        self.assertEqual(result.bucket, "stake_active_unconfirmed")
        self.assertEqual(result.confidence, "uncertain")

    def test_verified_formula_replay_and_above_cap_tolerance(self):
        above = fixture("assets_active_above_cap.json")["data"]
        stake = next(row for row in fixture("deeds_multi.json")["data"]["staking_details"] if row["deed_uid"] == above["cards"][0]["stake_ref_uid"])
        self.assertGreater(float(stake["total_base_pp_after_cap"]), float(stake["total_base_pp_cap"]))
        self.assertAlmostEqual(float(stake["total_base_pp_after_cap"]) / float(stake["total_base_pp_cap"]), 1.015, places=3)
        self.assertAlmostEqual(sum(float(card["base_pp_after_cap"]) for card in above["cards"]), float(stake["total_base_pp_after_cap"]), places=2)
        self.assertAlmostEqual(sum(float(card["total_harvest_pp"]) for card in above["cards"]), float(stake["total_harvest_pp"]), places=2)
        for card in above["cards"]:
            construction = float(card["base_pp_after_cap"]) * (1 + float(card["terrain_boost"]))
            self.assertAlmostEqual(construction, float(card["total_construction_pp"]), places=2)
            self.assertAlmostEqual(float(card["work_per_hour"]) * 2000, float(card["total_harvest_pp"]), places=2)

    def test_partial_coverage_is_explicit_at_top_of_every_surface(self):
        bundle = fixture("deeds_multi.json")
        deed_uid = bundle["data"]["deeds"][0]["deed_uid"]
        report = analyze_bundle("player0005", bundle, {deed_uid: {"cards": [], "items": []}}, [{"deed_uid": "other", "reason": "fixture failure"}])
        self.assertFalse(report["coverage"]["assets"]["complete"])
        self.assertTrue(report["warnings"][0].startswith("PARTIAL DATA:"))
        self.assertTrue(render_land_analysis(report, "table").splitlines()[0].startswith("PARTIAL DATA:"))
        self.assertIn("1/11 deeds retrieved", render_land_analysis(report, "table"))

    def test_runi_card_is_a_worker(self):
        data = fixture("assets_active_with_runi.json")["data"]
        self.assertEqual(sum(card["stake_type_uid"] == "STK-LND-RUNI" for card in data["cards"]), 1)
        self.assertEqual(len(data["cards"]), 6)
        deed_uid = data["cards"][0]["stake_ref_uid"]
        stake = next(row for row in fixture("deeds_multi.json")["data"]["staking_details"] if row["deed_uid"] == deed_uid)
        self.assertEqual(stake["worker_count"], 6)

    def test_renderer_schema_and_privacy_are_stable(self):
        report = report_with_assets()
        csv_text = render_land_analysis(report, "csv")
        self.assertIn("base_cap_utilization_after_cap_base_pp_over_base_pp_cap", csv_text.splitlines()[0])
        self.assertNotIn("E84897343D", json.dumps(report))
        self.assertEqual(len(stable_asset_ref("same")), 24)
        private = analyze_bundle("player0005", fixture("deeds_multi.json"), {}, include_asset_ids=True)
        self.assertNotEqual(private["schema_version"], "")

    def test_renderer_views_are_distinct_and_show_their_own_rows(self):
        report = report_with_assets()
        outputs = {view: render_land_analysis(report, "table", view=view, limit=None if view == "diagnostics" else 2) for view in ("status", "plots", "workers", "diagnostics", "analyze")}
        self.assertEqual(len(set(outputs.values())), 5)
        self.assertIn("41-2-27", outputs["plots"])
        self.assertIn("Card ref", outputs["workers"])
        self.assertIn("missing_runi_bonus", outputs["diagnostics"])
        self.assertIn("Deeds:", outputs["analyze"])
        self.assertIn("Diagnostics by finding code:", outputs["analyze"])
        self.assertIn("Plots by harvest PP descending:", outputs["analyze"])

    def test_cli_subcommands_select_distinct_renderer_views(self):
        report = report_with_assets()
        outputs = {}
        with patch.object(cli, "load_snapshot", return_value=report), patch.object(cli, "_require_praetoria_storage_path"):
            for view in ("status", "plots", "workers", "diagnostics", "analyze"):
                stream = io.StringIO()
                with redirect_stdout(stream):
                    self.assertEqual(cli.main(["land", view, "--load-snapshot", "fixture.json", "--limit", "2"]), 0)
                outputs[view] = stream.getvalue()
        self.assertEqual(len(set(outputs.values())), 5)
        self.assertIn("41-2-27", outputs["plots"])
        self.assertIn("Card ref", outputs["workers"])
        self.assertIn("Diagnostics by finding code:", outputs["diagnostics"])

    def test_renderer_limit_announces_true_total(self):
        report = report_with_assets()
        plots = render_land_analysis(report, "table", view="plots", limit=2)
        workers = render_land_analysis(report, "table", view="workers", limit=2)
        diagnostics = render_land_analysis(report, "table", view="diagnostics", limit=2)
        self.assertIn("showing 2 of 11 plots", plots)
        self.assertIn("showing 2 of 4 workers", workers)
        self.assertIn("showing 2 of 9 diagnostic codes", diagnostics)

    def test_json_view_trims_collection_fetched_at_like_generated_at(self):
        report = report_with_assets()
        report["generated_at"] = "2026-08-09T00:00:00.123456Z"
        report["coverage"]["collection"]["fetched_at"] = "2026-08-09T00:00:00.654321Z"
        rendered = json.loads(render_land_analysis(report, "json", view="plots", limit=2))
        self.assertEqual(rendered["generated_at"], "2026-08-09T00:00:00Z")
        self.assertEqual(rendered["coverage"]["collection"]["fetched_at"], "2026-08-09T00:00:00Z")
        # display only: the report itself keeps full precision
        self.assertEqual(report["coverage"]["collection"]["fetched_at"], "2026-08-09T00:00:00.654321Z")

    def test_rendered_numeric_measures_have_no_float_artifacts(self):
        report = report_with_assets()
        for fmt in ("table", "json", "csv"):
            output = render_land_analysis(report, fmt, view="plots", limit=2)
            self.assertNotRegex(output, r"\d+\.\d{6,}")
        recommendations = recommend_land(report)
        table = render_land_analysis(recommendations, "table", view="recommend", show_all=True)
        self.assertNotRegex(table, r"\d+\.\d{6,}")
        self.assertIn("Assumption: target plot's observed aggregate terrain and seven-category boost rates are applied as a proxy for the candidate card.", table)
        stdout = io.StringIO()
        stderr = io.StringIO()
        with patch.object(cli, "load_snapshot", return_value=report), patch.object(cli, "_require_praetoria_storage_path"):
            with redirect_stdout(stdout), redirect_stderr(stderr):
                self.assertEqual(cli.main(["land", "recommend", "--load-snapshot", "fixture.json", "--all", "--no-card-abilities"]), 0)
        self.assertNotRegex(stderr.getvalue(), r"\d+\.\d{6,}")
        self.assertNotIn("delta=None", stderr.getvalue())
        # Fixture rows now carry numeric deltas; exercise an unknown delta through the CLI callback.
        def recommend_with_unknown_delta(_report, **kwargs):
            kwargs["on_recommendation"]({
                "action": "add", "proposal": {"target_plot": "1-1-1"},
                "delta": {"value": None, "reason": "target plot roster or base PP cap is not observed"},
            })
            return recommendations
        stderr = io.StringIO()
        with patch.object(cli, "load_snapshot", return_value=report), patch.object(cli, "_require_praetoria_storage_path"), patch.object(cli, "recommend_land", recommend_with_unknown_delta):
            with redirect_stdout(io.StringIO()), redirect_stderr(stderr):
                self.assertEqual(cli.main(["land", "recommend", "--load-snapshot", "fixture.json", "--all", "--no-card-abilities"]), 0)
        self.assertNotIn("delta=None", stderr.getvalue())
        self.assertIn("delta=unknown (above cap)", stderr.getvalue())
        raw = analyze_bundle("player0005", fixture("deeds_multi.json"), {}, include_raw=True)
        raw_output = render_land_analysis(raw, "json", view="status")
        self.assertIn('"raw"', raw_output)

    def test_safety_allows_only_https_fixed_hosts_and_get_transport(self):
        validate_fixed_url(API_ORIGIN + "/cards/get_details")
        validate_fixed_url(LAND_ORIGIN + "/land/deeds?player=player0005")
        for url in ("http://api.splinterlands.com/cards/get_details", "https://evil.example/cards", "https://api.splinterlands.com.evil.example/cards"):
            with self.assertRaises(UpstreamError):
                validate_fixed_url(url)

    def test_snapshot_roundtrip_is_fixture_safe(self):
        report = report_with_assets()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "land.json"
            from praetoria.land import load_snapshot, save_snapshot
            save_snapshot(report, path)
            loaded = load_snapshot(path)
            self.assertEqual(loaded["schema_version"], report["schema_version"])
            self.assertEqual(loaded["snapshot"]["status"], "snapshot_replay")
            # A path not named `*-latest.json` has no separate card sidecar: the inline
            # cards are replayed, never the snapshot re-read as its own card pool.
            self.assertEqual(loaded["cards"], report["cards"])

    def test_widened_facts_projection_carries_terrain_magic_deed_stats_tax_and_time_crystal(self):
        """Covers the `terrain`/`magic_type`/`deed_stats`/`tax_rate`/`time_crystal_value`
        widening to `land_analysis.py`'s plot `facts` projection (docs land-query.md
        follow-up recorded alongside the Ceres Phase 1 landing, 2026-08-20) -- kept
        verbatim from the deed, and a malformed `stats` string degrades to `{}` rather
        than raising."""
        report = analyze_bundle("player0005", fixture("deeds_multi.json"), {}, as_of=AS_OF)
        row = next(p for p in report["plots"] if p["deed_uid"] == "I-296-b0dbc1ebc5ef75")
        facts = row["facts"]
        self.assertEqual(facts["terrain"], "Tundra")
        self.assertIsNone(facts["magic_type"])  # deed's magic_type is "" -> normalized to None
        self.assertEqual(facts["deed_stats"], {"building_in_box": True, "unstable_totem": True})
        self.assertEqual(facts["tax_rate"], "0.10")
        self.assertEqual(facts["time_crystal_value"], 1000)

        bundle = fixture("deeds_multi.json")
        mutated = next(d for d in bundle["data"]["deeds"] if d["deed_uid"] == "I-296-b0dbc1ebc5ef75")
        mutated["stats"] = "not valid json{{"
        malformed_report = analyze_bundle("player0005", bundle, {}, as_of=AS_OF)
        malformed_row = next(p for p in malformed_report["plots"] if p["deed_uid"] == "I-296-b0dbc1ebc5ef75")
        self.assertEqual(malformed_row["facts"]["deed_stats"], {})


if __name__ == "__main__":
    unittest.main()
