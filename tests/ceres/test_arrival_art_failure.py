"""The consumed-card side of the amplified arrival stage (app.js's
combineArrivalConsumedCardHtml, ceres.css) had a two-part mismatch: a failed
art load applies `is-none` to the OUTER `.combine-arrival-consumed-card`, but
the stylesheet only ever styled `.combine-arrival-consumed-tile.is-none` --
a class that selector never actually gets. The result was a blank tile on
failure instead of the dashed placeholder, which is what the maintainer saw and
reported as "blank boxes" (work/combine-modal-redesign/arrival-confirmed-
amplified.png).

This guard renders the REAL app.js + ceres.css together in a live browser
(Playwright), forces the destroyed card's art request to fail via
`page.route`, and reads the rendered tile's OWN computed border style --
never the CSS source text and never a spy on whether a class was added. Per
the internal rules's mutation standard: this test has had the exact regression it
forbids re-injected (restoring the old `.combine-arrival-consumed-tile.is-
none` selector) and confirmed red before being accepted.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from .conftest import spawn_live_ceres, wait_ready

ROOT = Path(__file__).parents[2]
CSS = ROOT / "ceres/static/ceres.css"
SCREEN_DIR = ROOT / "work" / "combine-modal-redesign"
SCREEN_DIR.mkdir(parents=True, exist_ok=True)


def _render_failed_consumed_tile(css_source: str) -> dict:
    """Build a minimal real page -- the app's OWN markup for one destroyed card
    (combineArrivalConsumedCardHtml's actual output shape, hand-reproduced here
    only insofar as static HTML always has to be to host a `<style>` block --
    against a given ceres.css source string, force the `<img>` to fail via a
    route, and return the tile's rendered classList + computed border style
    after the onerror handler has had a chance to run."""
    with spawn_live_ceres() as (browser, base_url):
        page = browser.new_page(viewport={"width": 400, "height": 300})
        # Intercept the real ceres.css REQUEST itself, before navigation, so the
        # page's only copy of the stylesheet is the one under test -- appending
        # an extra <style> alongside the real (always-fixed, on-disk) link would
        # leave the genuine correct rule active no matter what a "mutated"
        # string claimed, since it is a different selector rather than an
        # override of the same one.
        # `**/ceres.css*` -- index.html loads it as `/static/ceres.css?v={{CERES_VERSION}}`
        # (a cache-busting query string, ceres/__init__.py's own version), so a
        # pattern ending bare in "ceres.css" never matches the real request.
        page.route(
            "**/ceres.css*",
            lambda route: route.fulfill(status=200, content_type="text/css", body=css_source),
        )
        # A real page from the app so the shared design tokens (--border etc.
        # loaded by index.html, not defined in ceres.css itself) are present
        # a bare stylesheet-only page would leave --border unresolved and make
        # the guard meaningless regardless of which selector is live.
        page.goto(base_url)
        # at this 400px viewport the board is the phone layout (plot cards, no desktop table rows),
        # so the page's own ready signal is what says it has loaded
        wait_ready(page)
        # A guaranteed-to-fail art URL, on the SAME markup shape
        # combineArrivalConsumedCardHtml() renders.
        page.route("**/cards_v2.2/Nonexistent%20Ghost%20Card.jpg", lambda route: route.fulfill(status=404, body=""))
        page.evaluate(
            """() => {
              document.body.insertAdjacentHTML('beforeend',
                '<div class="combine-arrival-consumed-card">' +
                '<div class="combine-arrival-consumed-tile">' +
                '<img class="combine-arrival-consumed-art" ' +
                'src="https://d36mxiodymuqjm.cloudfront.net/cards_v2.2/Nonexistent%20Ghost%20Card.jpg" ' +
                'onerror="this.closest(\\'.combine-arrival-consumed-card\\')?.classList.add(\\'is-none\\')">' +
                '<span class="combine-arrival-consumed-strike" aria-hidden="true"></span>' +
                '<span class="combine-arrival-consumed-name" aria-hidden="true">Ghost Card</span>' +
                '</div></div>');  // no Card ID: it stays internal
            }"""
        )
        page.wait_for_selector(".combine-arrival-consumed-card.is-none", timeout=10_000)
        border = page.eval_on_selector(
            ".combine-arrival-consumed-tile", "el => getComputedStyle(el).borderStyle"
        )
        name_visible = page.eval_on_selector(
            ".combine-arrival-consumed-name", "el => getComputedStyle(el).display !== 'none'"
        )
        page.locator(".combine-arrival-consumed-card").screenshot(
            path=str(SCREEN_DIR / "consumed-tile-art-failure-placeholder.png")
        )
        page.close()
        return {"border": border, "name_visible": name_visible}


def _assert_dashed_placeholder(result: dict) -> None:
    assert result["border"] == "dashed", (
        f"expected the tile's own computed border-style to be 'dashed' once the outer " f"card carries `is-none`, got {result['border']!r} -- the selector mismatch is back"
    )
    assert result["name_visible"] is True, "the destroyed card's name must be visible once art fails to load"


def test_failed_art_load_gets_the_dashed_placeholder_and_name() -> None:
    _assert_dashed_placeholder(_render_failed_consumed_tile(CSS.read_text(encoding="utf-8")))


def test_selector_mismatch_regression_is_red() -> None:
    """Injected defect: restore the ORIGINAL mismatched selector
    (`.combine-arrival-consumed-tile.is-none` instead of `.combine-arrival-
    consumed-card.is-none .combine-arrival-consumed-tile`) -- the guard above
    must fail because `is-none` never lands on the tile itself, only on its
    parent, so the dashed border never applies. Confirmed red before this test
    was accepted."""
    source = CSS.read_text(encoding="utf-8")
    marker = ".combine-arrival-consumed-card.is-none .combine-arrival-consumed-tile { border: 1px dashed var(--border); }"
    assert marker in source  # sanity: the fix is actually present pre-mutation
    mutated = source.replace(marker, ".combine-arrival-consumed-tile.is-none { border: 1px dashed var(--border); }", 1)
    with pytest.raises(AssertionError):
        _assert_dashed_placeholder(_render_failed_consumed_tile(mutated))
