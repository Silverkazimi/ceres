"""a different retained Card ID is valid only with full combine effects."""
from fides_cards.verify import CardVerificationSpec

BEFORE = [{"uid": "C-A", "card_detail_id": 7, "bcx": 5, "level": 2, "owner": "alice"},
          {"uid": "C-B", "card_detail_id": 7, "bcx": 3, "level": 1, "owner": "alice"}]
OPS = [{"id": "sm_combine_cards", "json": {"cards": ["C-A", "C-B"]}}]


def check(after):
    return CardVerificationSpec(lambda: {"cards": after}, expected_records=BEFORE, intent="alice").check_domain("u", OPS)


def test_actual_survivor_with_different_id_verifies():
    result = check([{"uid": "C-B", "card_detail_id": 7, "bcx": 8, "level": 2, "owner": "alice"}])
    assert result["status"] == "domain_verified"
    assert result["evidence"]["survivor_uid"] == "C-B"
    assert result["evidence"]["survivor_differs"] is True


def test_wrong_cc_or_consumed_copy_still_present_does_not_verify():
    assert check([{"uid": "C-B", "bcx": 7, "level": 2}])["status"] != "domain_verified"
    assert check([{"uid": "C-B", "bcx": 8, "level": 2}, {"uid": "C-A", "bcx": 5, "level": 2}])["status"] != "domain_verified"
    assert check([])["status"] != "domain_verified"
    assert check([{"uid": "C-B", "bcx": 8, "level": 2, "owner": "player0009"}])["status"] != "domain_verified"
