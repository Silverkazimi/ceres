""" every page load must end with every plot fetched. Never-fetched
plots are queued uncapped, and the same-capture self-heal requests ALL of them."""

from ceres import capture


def _plot(uid, status=None, observed="2026-09-01T00:00:00Z"):
    plot = {"deed_uid": uid, "observed_at": observed}
    if status is not None:
        plot["asset_fetch"] = {"status": status}
    return plot


def test_never_fetched_plots_are_all_queued_after_failures() -> None:
    plots = [_plot("ok", "retrieved"), _plot("bad", "failed"), _plot("never-a"), _plot("never-b", "not_requested")]
    plots += [_plot(f"n{i}") for i in range(30)]
    report = {"plots": plots, "coverage": {"assets": {}}}
    capture._recompute_asset_coverage(report, stale_after_seconds=3600)
    queue = report["coverage"]["assets"]["needs_refetch"]
    assert queue[0] == "bad"
    assert "ok" not in queue
    assert {"never-a", "never-b", *(f"n{i}" for i in range(30))} <= set(queue)  # no cap
    assert report["coverage"]["assets"]["not_requested"] == 32


def test_self_heal_requests_every_never_fetched_plot_in_one_capture(monkeypatch) -> None:
    plots = [_plot("ok", "retrieved")] + [_plot(f"f{i}", "failed") for i in range(12)] + [_plot(f"n{i}") for i in range(22)]
    merged = {"plots": plots, "coverage": {"assets": {}}}
    capture._recompute_asset_coverage(merged, stale_after_seconds=3600)
    seen = {}

    class FakeCapture:
        def repair_assets(self, queued, player):
            seen["queued"] = list(queued)
            return {}

    monkeypatch.setattr(capture, "_publish_asset_repair", lambda config, player, merged, repaired, queued: merged)
    capture._self_heal_refetches(None, "player0005", merged, capture=FakeCapture())
    queued = seen["queued"]
    # (2026-09-14, his ruling "on demand for every account"): never-fetched plots are no longer batched
    # after the board is published; they load when the plot is opened. Only the capped retries are queued.
    assert not {f"n{i}" for i in range(22)} & set(queued)
    assert sum(uid.startswith("f") for uid in queued) == capture._MAX_SELF_HEAL_DEEDS  # retries stay capped
    assert "ok" not in queued


def test_self_heal_never_batches_never_fetched_plots_for_huge_accounts(monkeypatch) -> None:
    # a whale (player0016: 6,300 never-fetched plots) must not be fetched in one batch.
    # now no never-fetched batch at all, for any account -- repair_assets is not called.
    plots = [_plot(f"n{i}") for i in range(250)]
    merged = {"plots": plots, "coverage": {"assets": {}}}
    capture._recompute_asset_coverage(merged, stale_after_seconds=3600)
    seen = {}

    class FakeCapture:
        def repair_assets(self, queued, player):
            seen["queued"] = list(queued)
            return {}

    monkeypatch.setattr(capture, "_publish_asset_repair", lambda config, player, merged, repaired, queued: merged)
    capture._self_heal_refetches(None, "player0016", merged, capture=FakeCapture())
    assert "queued" not in seen
