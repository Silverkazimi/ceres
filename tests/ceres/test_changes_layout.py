from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[2]
CHECKOUT = (ROOT / "ceres/static/checkout.js").read_text()
CSS = (ROOT / "ceres/static/ceres.css").read_text()


class ChangesLayoutGuard(unittest.TestCase):
    def test_lock_tile_is_inside_the_change_tile_grid(self):
        self.assertIn('class="slot-tile ch-lock-plan ${stageClass}"', CHECKOUT)
        self.assertRegex(CHECKOUT, r'const itemTiles = .*\$\{plotLockPlanHtml\(group\)\}')
        self.assertIn('class="fp-tiles slot-grid ch-tiles">${workerTiles}', CHECKOUT)
        self.assertIn('.changes-panel .ch-plot { box-sizing: border-box; inline-size: 100%; min-inline-size: 0; max-inline-size: 100%; }', CSS)

    def test_signing_notice_uses_even_warning_border(self):
        self.assertIn("const plotNotices = (group) => portfolioNotices.filter((notice) => notice.context?.deed_uid === group.deedUid)", CHECKOUT)
        self.assertIn("serverNoticeHtml(notices)", CHECKOUT)
        # (maintainer): the plot's "N changes · Total" moved to the card's top-right corner, on the badge line.
        self.assertIn('<div class="ch-plot-aside">${serverNoticeHtml(notices)}</div></div>', CHECKOUT)
        self.assertIn('<p class="hm-dim ch-plot-summary">${groupSummary}</p></div>`', CHECKOUT)
        self.assertIn(".changes-panel .ch-plot .ceres-notice.slot-hazard-inline { border: 1px solid var(--warn); }", CSS)

    def test_duplicate_notice_reason_is_suppressed(self):
        self.assertIn("notice.reason === notice.message ? { ...notice, reason: null } : notice", CHECKOUT)

    def test_operation_and_lock_counts_share_one_phrase(self):
        summary_line = next(line for line in CHECKOUT.splitlines() if line.lstrip().startswith("const signingSummary ="))
        self.assertIn(": !review?.payload && !onlyStandalone", summary_line)  # after the upgrade-plan-only branch
        self.assertIn("plural(signatureCount, 'signature')", CHECKOUT)
        summary_at = CHECKOUT.index("${summary}")
        header_start = CHECKOUT.rfind("<header", 0, summary_at)
        header = CHECKOUT[header_start:CHECKOUT.index("</header>", header_start)]
        signing = CHECKOUT.split("const signingSummary =", 1)[1].split("const note =", 1)[0]
        signing_card = CHECKOUT.split("const signingCard", 1)[1].split("return `<header", 1)[0]
        footer = CHECKOUT.split('+ `<footer class="hm-foot"><span class="hm-note ch-signing-note"', 1)[1]
        self.assertNotIn("signingSummary", header)
        self.assertIn("signatureParts.join(', ')", signing)
        self.assertIn("${esc(signingSummary)}", signing_card)
        self.assertIn("const signatureCount = postingSignatures + readyLocks.length", CHECKOUT)
        self.assertIn("readyLocks.length ? `${readyLocks.length} Active (deed", CHECKOUT)
        signature_parts = CHECKOUT.split("const signatureParts =", 1)[1].split("const signingSummary =", 1)[0]
        self.assertIn("readyLocks.length ? `${readyLocks.length} Active (deed", signature_parts)

    def test_zero_count_parts_are_omitted_in_all_signing_summaries(self):
        self.assertIn("postingSignatures ? `${postingSignatures} Posting (", CHECKOUT)
        self.assertIn("readyLocks.length ? `${readyLocks.length} Active (deed", CHECKOUT)
        self.assertIn("].filter(Boolean);", CHECKOUT)

    def test_signing_status_grows_to_its_content(self):
        self.assertIn(".changes-panel .ch-signing-status{box-sizing:border-box;min-inline-size:0;overflow-wrap:anywhere}", CSS)
        self.assertIn(".changes-panel .ch-signing-status>.op-preview-review.is-compact{box-sizing:border-box;min-block-size:0;", CSS)

    def test_plot_strip_contains_only_changed_slots_and_fits_panel(self):
        self.assertIn("const changedWorkerSlots = [...new Set(stagedSlots)];", CHECKOUT)
        self.assertIn("changedWorkerSlots.map((slot) => tile('worker', slot - 1, 'Empty'))", CHECKOUT)
        self.assertIn("const powerTile = powerChanged ?", CHECKOUT)
        self.assertIn("totemAt.item || totemAt.undone ? tile('totem', 0, 'No totem') : ''", CHECKOUT)
        self.assertIn("titleAt.item || titleAt.undone ? tile('title', 0, 'No title') : ''", CHECKOUT)
        self.assertIn(".changes-panel .ch-plot { box-sizing: border-box; inline-size: 100%; min-inline-size: 0; max-inline-size: 100%; }", CSS)


if __name__ == "__main__":
    unittest.main()
