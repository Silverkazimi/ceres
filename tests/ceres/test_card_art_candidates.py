"""Browser guards for card-art candidate fallback."""

from __future__ import annotations

import base64

import pytest
from playwright.sync_api import Browser

from .conftest import spawn_live_ceres


JPEG_BYTES = base64.b64decode(
    "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAP//////////////////////////////////////////////////////////////////////////////////////"
    "2wBDAf//////////////////////////////////////////////////////////////////////////////////////wAARCAABAAEDASIAAhEBAxEB/"
    "8QAFQABAQAAAAAAAAAAAAAAAAAAAAf/xAAUEAEAAAAAAAAAAAAAAAAAAAAA/9oADAMBAAIQAxAAAAH/xAAUEAEAAAAAAAAAAAAAAAAAAAAA/"
    "9oACAEBAAE/AF//xAAUEQEAAAAAAAAAAAAAAAAAAAAA/9oACAECAQE/AF//xAAUEQEAAAAAAAAAAAAAAAAAAAAA/9oACAEDAQE/AF//2Q=="
)
NO_SUCH_KEY = """<?xml version="1.0" encoding="UTF-8"?>
<Error><Code>NoSuchKey</Code><Message>The specified key does not exist.</Message></Error>"""
HARKLAW = {"name": "Harklaw", "editions": "3", "edition": 3, "card_set": "chaos", "foil": 0}


@pytest.fixture(scope="module")
def live_browser() -> tuple[Browser, str]:
    with spawn_live_ceres() as (browser, base_url):
        yield browser, base_url


def _render_card_art(page, base_url: str, record: dict) -> None:
    page.goto(base_url)
    page.wait_for_function("document.querySelector('#plot-tbody > tr.plot-row') !== null", timeout=30_000)
    page.evaluate(
        """async (record) => {
          const { editorCardImageHtml } = await import('/static/editor.js');
          const host = document.createElement('span');
          host.id = 'card-art-guard';
          host.innerHTML = editorCardImageHtml(record, { alt: `${record.name} card art` });
          document.body.append(host);
          // editorCardImageHtml() emits loading="lazy". Appended to the bottom of a
          // 129-row board the host lands ~4000px below a 720px-tall headless viewport
          // and Chromium defers the fetch INDEFINITELY -- so neither load nor error
          // ever fires and the request is never even issued. Measured 2026-09-04: that
          // is indistinguishable from an ORB-blocked response from the outside, and it
          // sent three readers hunting a Chrome behaviour that was never involved.
          // Scrolling the host into view is what makes this guard exercise the fix
          // rather than time out against the browser's own laziness.
          host.scrollIntoView();
        }""",
        record,
    )


def test_chaos_403_advances_to_numeric_edition_art(live_browser: tuple[Browser, str]) -> None:
    browser, base_url = live_browser
    page = browser.new_page()
    page.route(
        "**/cards_chaos/**",
        lambda route: route.fulfill(status=403, content_type="application/xml", body=NO_SUCH_KEY),
    )
    page.route(
        "**/cards_beta/**",
        lambda route: route.fulfill(status=200, content_type="image/jpeg", body=JPEG_BYTES),
    )
    _render_card_art(page, base_url, HARKLAW)
    page.wait_for_function(
        """() => {
          const image = document.querySelector('#card-art-guard img');
          return Boolean(image && image.complete && image.naturalWidth > 0);
        }""",
        timeout=10_000,
    )
    assert page.eval_on_selector("#card-art-guard img", "image => image.complete && image.naturalWidth > 0") is True
    page.close()


def test_named_placeholder_appears_when_no_card_art_candidate_resolves(live_browser: tuple[Browser, str]) -> None:
    browser, base_url = live_browser
    page = browser.new_page()
    for pattern in ("**/cards_chaos/**", "**/cards_beta/**"):
        page.route(
            pattern,
            lambda route: route.fulfill(status=403, content_type="application/xml", body=NO_SUCH_KEY),
        )
    _render_card_art(page, base_url, HARKLAW)
    page.wait_for_function(
        """() => document.querySelector('#card-art-guard .combine-arrival-art-fallback')?.textContent === 'Harklaw — no image'""",
        timeout=10_000,
    )
    placeholder = page.locator("#card-art-guard .combine-arrival-art-fallback")
    assert placeholder.is_visible()
    assert placeholder.inner_text() == "Harklaw — no image"
    assert page.eval_on_selector("#card-art-guard img", "image => image.complete && image.naturalWidth === 0") is True
    page.close()


def test_every_held_record_keeps_its_numeric_edition_candidate(live_browser: tuple[Browser, str], report: dict, details_by_id: dict) -> None:
    records = []
    for card in report.get("cards", []):
        facts = card.get("facts", {}) if isinstance(card, dict) else {}
        detail = details_by_id.get(facts.get("card_detail_id"), {}) if isinstance(facts, dict) else {}
        if detail.get("name") and facts.get("edition") is not None:
            records.append({
                "name": detail["name"],
                "editions": detail.get("editions"),
                "edition": facts["edition"],
                "card_set": facts.get("card_set"),
                "skin": facts.get("skin"),
                "foil": facts.get("foil"),
                "gold": facts.get("gold"),
            })
    assert records
    browser, base_url = live_browser
    page = browser.new_page()
    page.goto(base_url)
    page.wait_for_function("document.querySelector('#plot-tbody > tr.plot-row') !== null", timeout=30_000)
    results = page.evaluate(
        """async (records) => {
          const { editorCardImageCandidates } = await import('/static/editor.js');
          const { CARD_CDN_SET } = await import('/static/icons.js');
          return records.map((record) => {
            const numericFolder = CARD_CDN_SET[Number(record.edition)];
            const candidates = editorCardImageCandidates(record);
            return {
              name: record.name,
              numericFolder,
              hasNumericCandidate: candidates.some((url) => new URL(url).pathname.split('/').filter(Boolean)[0] === numericFolder),
            };
          });
        }""",
        records,
    )
    missing = [result for result in results if result["numericFolder"] and not result["hasNumericCandidate"]]
    assert not missing, f"numeric-edition card-art candidate missing for {missing!r}"
    page.close()
