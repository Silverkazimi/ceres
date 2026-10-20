from fides_cards.render import allowlist_family



TEST_APP = "ceres/0.0.0"

def test_combine_family_requires_exact_app_even_without_common_rules():
    rule = next(row for row in allowlist_family(app=TEST_APP)["rules"] if row["path"] == "body.json.app")
    assert rule["check"] == "eq"
    assert rule["value"] == TEST_APP
