""" a reproduction, not a fix.

The MCP session measured that the deeds endpoint returns FOUR different top-level
`data` contracts keyed on which call was made rather than on success or failure, and
that a by-plot MISS returns HTTP 200 with `data` explicitly null (their MCP-).

Our normalizers already refuse that correctly and fail closed:
`praetoria.land.normalize_plots:79-80` and `land_analysis.normalize_bundle:531-533`
both raise `SchemaError` when `data` is not a dict.

The gap is one level up. `LandCapture.capture`/`capture_quick` guard their bundle fetch
with `except ResponseTooLarge` and nothing else (`land_analysis.py:1124-1126`), and
`except SchemaError` appears zero times in `ceres/` and `praetoria/`. So a SchemaError
escapes the same fetch, before the `failed` list exists, and reproduces the exact total
failure was landed to prevent: the board freezes on its last snapshot and 503s.

These tests are xfail on purpose. They document a defect that is real in current source
rather than asserting a fix that does not exist yet -- a red test in the suite would be
noise, and a deleted test would be a lost finding. When the gap is closed they turn
XPASS, which is the signal to make them strict.

NOT REPRODUCED AGAINST THE LIVE API. This forces the exception through the same code
path the live shape would take; it does not prove the live endpoint returns it. The
live half belongs to a network queue, and MCP- already carries that measurement.
"""
import pytest

from praetoria.errors import SchemaError
from praetoria.land_analysis import LandCapture


class _NullDataLandClient:
    """Stands in for the by-plot-miss shape: HTTP 200, `data` explicitly null.

 The normalizer converts that into SchemaError before any caller sees it, so raising
 SchemaError here is the same event the live response produces, one layer earlier.
    """

    def bundle(self, _player):
        raise SchemaError("Land deeds response must contain deeds and staking_details arrays")


def _capture() -> LandCapture:
    return LandCapture(
        land_client=_NullDataLandClient(),
        cache=object(),
        min_delay=0,
        sleeper=lambda _delay: None,
    )


@pytest.mark.xfail(
    reason="capture_quick guards ResponseTooLarge only; a SchemaError still escapes",
    strict=False,
)
def test_capture_quick_survives_a_null_data_bundle():
    report = _capture().capture_quick("player0016")
    # The contract established for the oversized case: a structured outcome the
    # caller can report, never an escaping exception that strands the stored snapshot.
    assert report["capture"]["status"] != "ok"
    assert "status" in report


@pytest.mark.xfail(
    reason="capture guards ResponseTooLarge only; a SchemaError still escapes",
    strict=False,
)
def test_full_capture_survives_a_null_data_bundle():
    report = _capture().capture("player0016")
    assert report["capture"]["status"] != "ok"
    assert "status" in report


def test_the_gap_is_real_and_this_is_what_it_looks_like():
    """The positive half: assert the CURRENT behaviour explicitly.

 If someone closes , this test fails and points them at the two xfails above
 to make strict. That is deliberate -- it means the fix cannot land silently.
    """
    with pytest.raises(SchemaError):
        _capture().capture_quick("player0016")
