from pathlib import Path

from .conftest import spawn_live_ceres


ROOT = Path(__file__).parents[2]
ROWS = (ROOT / "ceres" / "static" / "rows.js").read_text(encoding="utf-8")


def test_close_all_hidden_state_tracks_sync_close_all_rule() -> None:
    sync_close_all = ROWS.split("export function syncCloseAll()", 1)[1].split(
        "\n", 1
    )[0]
    assert "const show = !showOpen && (EXPANDED_ROWS.size >= 2 || stagedOn);" in sync_close_all

    with spawn_live_ceres() as (browser, base_url):
        page = browser.new_page(viewport={"width": 1400, "height": 900})
        # The offline board recording belongs to sample01.
        page.goto(f"{base_url}/?player=sample01")
        page.wait_for_function(
            "document.querySelectorAll('#plot-tbody > tr.plot-row').length >= 2",
            timeout=15_000,
        )

        close_all = page.locator("#close-all-plots")
        assert page.locator(".editor-band:visible").count() == 0
        assert close_all.is_hidden()

        rows = page.locator("#plot-tbody > tr.plot-row")
        rows.nth(0).click()
        page.locator(".editor-band").nth(0).wait_for(state="visible", timeout=15_000)
        rows.nth(1).click()
        page.locator(".editor-band").nth(1).wait_for(state="visible", timeout=15_000)

        close_all.wait_for(state="visible", timeout=15_000)
        close_all.click()
        close_all.wait_for(state="hidden", timeout=15_000)
        page.close()
