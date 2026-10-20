"""§6.6 test_metrics_dec.py"""

from __future__ import annotations

from ceres.board import project_board
from ceres.metrics import dec_for_plot, plot_dec_efficiency


def test_dec_source_positive_uses_total_dec_staked():
    dec = dec_for_plot({"total_dec_staked": "125000"}, worker_dec=[100.0, 200.0])
    assert dec["source"] == "total_dec_staked"
    assert dec["committed"] == 125000.0


def test_dec_source_falls_back_to_worker_sum_when_not_positive():
    dec = dec_for_plot({"total_dec_staked": "0"}, worker_dec=[100.0, 200.0])
    assert dec["source"] == "worker_sum"
    assert dec["committed"] == 300.0

    dec_missing = dec_for_plot({}, worker_dec=[50.0])
    assert dec_missing["source"] == "worker_sum"
    assert dec_missing["committed"] == 50.0


def test_plot_dec_efficiency_null_on_zero_denominator():
    # plot_dec_efficiency is denominated on dec.needed (total_dec_stake_needed), never
    # dec.committed (total_dec_staked) -- that field is not reliably per-plot (only 3
    # distinct values across 130 live plots). Returns (value, reason); reason is set
    # whenever value is None so a caller can render *why*, never a bare dash or a
    # silently-dropped row -- three live plots legitimately have needed == 0.
    assert plot_dec_efficiency(1000.0, 0.0) == (None, "no DEC requirement recorded for this plot")
    assert plot_dec_efficiency(1000.0, None) == (None, "no DEC requirement recorded for this plot")
    assert plot_dec_efficiency(None, 100.0) == (None, "harvest PP unknown")
    assert plot_dec_efficiency(1000.0, 500.0) == (2.0, None)


def test_plot_dec_efficiency_prices_dark_discount_normally_now_settled():
    # Precision fix, 2026-08-21: Dark Discount is now SETTLED (multiplicative, per-card
    # per-level -- library/land-worker-abilities.md §9, verified at individual-worker
    # granularity on every non-Runi discount plot, 0 exceptions). `total_dec_stake_needed`
    # already reflects it exactly (dec_stake_needed = land_dec_stake_needed x (1 +
    # discount)), so a nonzero needed_discount no longer withholds a price on its own.
    assert plot_dec_efficiency(1000.0, 500.0, needed_discount=-0.5) == (2.0, None)


def test_plot_dec_efficiency_nulls_only_runi_staked_plots():
    # The three plots that appeared to falsify Dark Discount pricing were Runi-staked
    # Runi zeroes a plot's ENTIRE DEC requirement independently of Dark Discount
    # (library/land-worker-abilities.md §8, primary-sourced). Runi, not the discount
    # itself, is now the only thing that withholds a price.
    assert plot_dec_efficiency(1000.0, 500.0, needed_discount=-0.5, runi_staked=True) == (
        None, "DEC requirement is zero (Runi-staked plot; Runi zeroes the plot's DEC requirement)",
    )
    assert plot_dec_efficiency(1000.0, 0.0, runi_staked=True) == (
        None, "DEC requirement is zero (Runi-staked plot; Runi zeroes the plot's DEC requirement)",
    )


def test_portfolio_efficiency_equals_trusted_rows_ratio(report, details_by_id):
    # plot_dec_efficiency defect fix, portfolio level: the old assertion divided
    # totals["harvest_pp"] by totals["dec_committed"] (the unreliable field) -- that is
    # exactly the defect being fixed here, so it is no longer the correct expectation.
    # portfolio_dec_efficiency must now equal harvest_pp/dec.needed summed ONLY over
    # plots whose own plot_dec_efficiency_flag is None (excludes Dark-Discount-unsettled
    # and zero-dec.needed plots) -- the same treatment as the per-plot value.
    board = project_board(report, details_by_id)
    totals = board["totals"]
    trusted = [row for row in board["plots"] if row["plot_dec_efficiency_flag"] is None]
    trusted_harvest = sum(row["harvest_pp"] or 0.0 for row in trusted)
    trusted_needed = sum(row["dec"]["needed"] or 0.0 for row in trusted)
    assert totals["portfolio_dec_efficiency_excluded_plots"] == len(board["plots"]) - len(trusted)
    if trusted_needed:
        assert totals["portfolio_dec_efficiency"] == trusted_harvest / trusted_needed
    else:
        assert totals["portfolio_dec_efficiency"] is None
