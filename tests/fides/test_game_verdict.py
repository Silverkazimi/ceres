import json
import unittest
from pathlib import Path

from praetoria.errors import UpstreamError
from praetoria.transport import FixedGetTransport, ResponseData

from fides.game_verdict import game_verdict


FIXTURES = Path(__file__).parents[1] / "fixtures" / "fides"
SURVIVAL_REFUSAL_ERROR = "Error 45: p_stake_cards invalid. One or more cards are dead from survival mode."


class RecordingTransport:
    def __init__(self, responses=None, error=None):
        self.responses = responses or {}
        self.error = error
        self.urls = []

    def get_json(self, url):
        self.urls.append(url)
        if self.error is not None:
            raise self.error
        return ResponseData(self.responses.get(url), "2026-09-03T00:00:00Z", 1)


def response(*, nonce, success=True, player="player0005", deed_uid=None, error=None):
    data = {"n": nonce}
    if deed_uid is not None:
        data["deed_uid"] = deed_uid
    trx_info = {"player": player, "data": json.dumps(data), "success": success}
    if error is not None:
        trx_info["error"] = error
    return {"trx_info": trx_info}


class GameVerdictTests(unittest.TestCase):
    def test_g1_preserves_refusal_error_verbatim(self):
        recorded = json.loads((FIXTURES / "refusal_error_45.json").read_text(encoding="utf-8"))
        transport = RecordingTransport({"https://api.splinterlands.com/transactions/lookup?trx_id=tx-1": recorded})
        result = game_verdict("tx-1", op_count=1, expected_nonces=["ZyXwVuTsRq"], account="player0005", transport=transport)
        self.assertEqual(result["status"], "game_rejected")
        self.assertEqual(result["evidence"]["error"], SURVIVAL_REFUSAL_ERROR)

    def test_g2_walks_and_binds_every_operation(self):
        tx_id = "tx-multi"
        nonces = ["n0", "n1", "n2", "n3"]
        transport = RecordingTransport({
            f"https://api.splinterlands.com/transactions/lookup?trx_id={tx_id}": response(nonce="n2"),
            f"https://api.splinterlands.com/transactions/lookup?trx_id={tx_id}-1": response(nonce="n0"),
            f"https://api.splinterlands.com/transactions/lookup?trx_id={tx_id}-2": response(nonce="n3"),
            f"https://api.splinterlands.com/transactions/lookup?trx_id={tx_id}-3": response(nonce="n1"),
        })
        result = game_verdict(tx_id, op_count=4, expected_nonces=nonces, account="player0005", transport=transport)
        self.assertEqual(result["status"], "game_accepted")
        self.assertEqual([url.rsplit("=", 1)[1] for url in transport.urls], [tx_id, f"{tx_id}-1", f"{tx_id}-2", f"{tx_id}-3"])
        self.assertEqual([op["nonce"] for op in result["evidence"]["operations"]], nonces)

    def test_g3_one_attributed_refusal_rejects_chunk_and_names_deed(self):
        tx_id = "tx-refused"
        transport = RecordingTransport({
            f"https://api.splinterlands.com/transactions/lookup?trx_id={tx_id}": response(nonce="n0"),
            f"https://api.splinterlands.com/transactions/lookup?trx_id={tx_id}-1": response(nonce="n1"),
            f"https://api.splinterlands.com/transactions/lookup?trx_id={tx_id}-2": response(nonce="n2", success=False, deed_uid="I-294-refused", error="game refused"),
            f"https://api.splinterlands.com/transactions/lookup?trx_id={tx_id}-3": response(nonce="n3"),
        })
        result = game_verdict(tx_id, op_count=4, expected_nonces=["n0", "n1", "n2", "n3"], account="player0005", transport=transport)
        self.assertEqual(result["status"], "game_rejected")
        self.assertEqual(result["evidence"]["refused"][0]["deed_uid"], "I-294-refused")

    def test_g4_wrong_player_or_nonce_is_unknown(self):
        for payload in (
            response(nonce="n0", player="someone-else", success=False, error="not ours"),
            response(nonce="not-ours", success=False, error="not ours"),
        ):
            with self.subTest(payload=payload):
                transport = RecordingTransport({"https://api.splinterlands.com/transactions/lookup?trx_id=tx-attr": payload})
                result = game_verdict("tx-attr", op_count=1, expected_nonces=["n0"], account="player0005", transport=transport)
                self.assertEqual(result["status"], "game_unknown")

    def test_g5_not_found_is_unknown_and_only_a_missing_bare_id_stops_the_walk(self):
        # a missing later record no longer hides the records after it; a missing bare id still stops the walk.
        tx_id = "tx-missing"
        for missing_at in (0, 2):
            with self.subTest(missing_at=missing_at):
                responses = {}
                if missing_at > 0:
                    responses[f"https://api.splinterlands.com/transactions/lookup?trx_id={tx_id}"] = response(nonce="n0")
                if missing_at > 1:
                    responses[f"https://api.splinterlands.com/transactions/lookup?trx_id={tx_id}-1"] = response(nonce="n1")
                missing_id = tx_id if missing_at == 0 else f"{tx_id}-{missing_at}"
                responses[f"https://api.splinterlands.com/transactions/lookup?trx_id={missing_id}"] = {
                    "error": f"Transaction {missing_id} not found.",
                    "error_code": 1,
                }
                transport = RecordingTransport(responses)
                result = game_verdict(tx_id, op_count=4, expected_nonces=["n0", "n1", "n2", "n3"], account="player0005", transport=transport)
                self.assertEqual(result["status"], "game_unknown")
                self.assertEqual(len(transport.urls), 1 if missing_at == 0 else 4)

    def test_g6_transport_failure_is_unknown(self):
        result = game_verdict(
            "tx-network",
            op_count=1,
            expected_nonces=["n0"],
            account="player0005",
            transport=RecordingTransport(error=OSError("offline")),
        )
        self.assertEqual(result["status"], "game_unknown")

    def test_g18_recorded_refusal_response(self):
        recorded = json.loads((FIXTURES / "refusal_error_45.json").read_text(encoding="utf-8"))
        FixedGetTransport.check_envelope(recorded)
        transport = RecordingTransport({"https://api.splinterlands.com/transactions/lookup?trx_id=46cff4df769dc7c3e051cdb123ad1a25045472de": recorded})
        result = game_verdict(
            "46cff4df769dc7c3e051cdb123ad1a25045472de",
            op_count=1,
            expected_nonces=["ZyXwVuTsRq"],
            account="player0005",
            transport=transport,
        )
        self.assertEqual(result["status"], "game_rejected")
        self.assertEqual(result["evidence"]["refused"][0]["error"], SURVIVAL_REFUSAL_ERROR)
        self.assertEqual(result["evidence"]["refused"][0]["deed_uid"], "I-294-99ed4b7a4d170e")

    def test_refusal_exception_does_not_allow_other_body_errors_through(self):
        with self.assertRaises(UpstreamError):
            FixedGetTransport.check_envelope({"error": "ordinary API failure", "trx_info": {"success": True}})


if __name__ == "__main__":
    unittest.main()
