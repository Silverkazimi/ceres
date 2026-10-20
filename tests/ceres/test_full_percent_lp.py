"""a 100% LP route takes what is spendable and says what stayed; it never fails on the region's cost reserve."""
from ceres import harvest_routing as routing

REGION = "PR-LOP-46"
POOL = routing.Pool(1, "GRAIN", routing.amount("1000"), routing.amount("1000"), routing.amount("1000"), False)


def _plan(mode="percent", quantity="100", reserve="5"):
    rule = {"id": "lp", "kind": "liquidity", "source_region": REGION, "symbol": "GRAIN",
            "quantity_mode": mode, "quantity": quantity, "dec_source": "wallet"}
    ledger = routing.Ledger({(REGION, "GRAIN"): routing.amount("20"), (routing.INVENTORY, "DEC"): routing.amount("100")},
                            {(REGION, "GRAIN"): routing.amount(reserve)}, {(REGION, "GRAIN"): routing.amount("20")})
    return routing.plan_after_harvest(ledger, [rule], {"GRAIN": POOL}, "2.5")


def test_full_percent_moves_the_spendable_amount_and_explains_the_rest():
    step = _plan()[0]
    assert step["status"] == "ready" and step["quantity"] == "15.000"
    assert step["held_back"].startswith("Region 46 keeps 5 GRAIN back")


def test_ninety_percent_is_untouched():
    step = _plan(quantity="50")[0]
    assert step["quantity"] == "10.000" and "held_back" not in step


def test_no_reserve_means_the_whole_harvest_moves():
    step = _plan(reserve="0")[0]
    assert step["quantity"] == "20.000" and "held_back" not in step


def test_exact_amount_over_the_limit_still_errors_in_plain_words():
    try:
        _plan(mode="amount", quantity="18")
    except routing.RoutingError as exc:
        assert "Region 46 keeps 5 GRAIN back" in str(exc) and "at most 15 GRAIN" in str(exc)
    else:
        raise AssertionError("expected a RoutingError")
