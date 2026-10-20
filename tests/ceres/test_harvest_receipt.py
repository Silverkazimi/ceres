"""receipt and Harvest all share the current ledger builders."""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from .vm_context import read_entry_source, run_in_module_context

ROOT = Path(__file__).parents[2]
PRE_EXTRACTION_BASELINE = "b8b9279aca529ac7b38b29643480983bd6ba5ced"  # pinned pre-extraction baseline; `master` stopped meaning "before" once landed in .
STATIC = ROOT / "ceres" / "static"
MODAL = STATIC / "harvest-modal.js"
RESULTS = STATIC / "harvest-results.js"
LEDGER = STATIC / "harvest-ledger.js"
CSS = STATIC / "ceres.css"


def test_region_receipt_is_net_first_and_preserves_confirmation() -> None:
    run = {
        "txId": "c81db445bd4265ef5b20d5e07debd0ce3a71df5a",
        "members": [{
            "outcome": "success",
            "region_uid": "PR-CEF-41",
            "received_by_token": {"GRAIN": 100, "WOOD": 20},
            "costs_by_token": {"GRAIN": 15, "WOOD": 5},
            "evidence": ["regional_rewards"],
        }],
    }
    run_js = json.dumps(run)
    result = run_in_module_context(
        f"const receipt = harvestSuccessHtml({run_js});\n"
        "if (receipt.includes('Region 041')) throw Error('region name text remains');\n"
        "if (!receipt.includes('class=\"ch-plot-badge hm-batch-plaque territory-central-fire\">041</span>')) throw Error('territory plaque missing');\n"
        "if (!receipt.includes('class=\"hm-batch-grid\"') || receipt.includes('class=\"harvest-receipt\"')) throw Error('desktop receipt is not the Harvest all ledger ()');\n"
        "if (!receipt.includes('class=\"hm-batch-slots\"')) throw Error('shared six-column slot grid missing');\n"
        "if (!receipt.includes('>Show costs</button>') || receipt.includes('>− costs</span>')) throw Error('costs are not collapsed');\n"
        "if (!receipt.includes('>+85.00</span>') || !receipt.includes('>+15.00</span>')) throw Error('net-first amounts missing');\n"
        "if (!receipt.includes('Harvest complete') || !receipt.includes('1 collection confirmed by the game.')) throw Error('game confirmation banner changed');\n"
        "if (!receipt.includes('https://hivehub.dev/tx/c81db445bd4265ef5b20d5e07debd0ce3a71df5a')) throw Error('transaction link missing');\n"
        f"const expanded = harvestSuccessHtml({run_js}, {{costsOpen: new Set(['total', 'PR-CEF-41'])}});\n"
        "for (const amount of ['+100.00', '−15.00', '+85.00', 'received', '− costs', '= net']) if (!expanded.includes(amount)) throw Error(`expanded ledger missing ${amount}`);",
        entry=RESULTS,
    )
    assert result.returncode == 0


def test_harvest_all_is_byte_identical_after_extracting_shared_builders() -> None:
    old_modal = subprocess.run(
        ["git", "show", f"{PRE_EXTRACTION_BASELINE}:ceres/static/harvest-modal.js"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    old_helpers = old_modal[old_modal.index("function batchBadgeHtml("):old_modal.index("function batchStatusPill(")]
    old_renderer = old_modal[old_modal.index("function batchBodyHtml()"):old_modal.index("an empty batch")]
    old_helpers = re.sub(r"\bbatchBadgeHtml(?=\()", "oldBatchBadgeHtml", old_helpers)
    old_helpers = re.sub(r"\bbatchLedgerRows(?=\()", "oldBatchLedgerRows", old_helpers)
    old_renderer = old_renderer.replace("function batchBodyHtml()", "function oldBatchBodyHtml()")
    old_renderer = old_renderer.replace("batchBadgeHtml(", "oldBatchBadgeHtml(").replace("batchLedgerRows(", "oldBatchLedgerRows(")

    source = read_entry_source(MODAL)
    source = source.rsplit("}", 1)[0] + "\n" + old_helpers + "\n" + old_renderer
    source += "\nObject.defineProperty(globalThis, 'oldBatchBodyHtml', {get: () => oldBatchBodyHtml});\nObject.defineProperty(globalThis, 'M', {get: () => M});\n}\n"
    result = run_in_module_context(
        r"""
M.mode = 'batch'; M.scopeKind = 'all'; M.selectedRegions = new Set(); M.costsOpen = new Set();
M.regionLayout = {resources: ['GRAIN', 'WOOD'], rows: [
 {region_uid: 'PR-CEF-41', region_number: '041', plots: 2, resources: ['GRAIN', 'WOOD']}
]};
M.preview = {regions: [{region_uid: 'PR-CEF-41', region_number: '041', status: 'included',
 net_by_token: {GRAIN: 100, WOOD: 20}, cost_now: {GRAIN: 15, WOOD: 5}}], issues: []};
const before = oldBatchBodyHtml();
const after = batchBodyHtml();
if (before !== after) throw Error(`Harvest all markup changed after extraction\nOLD: ${before}\nNEW: ${after}`);
""",
        entry=MODAL,
        source=source,
    )
    assert result.returncode == 0


def test_both_renderers_import_the_same_builders() -> None:
    modal = MODAL.read_text(encoding="utf-8")
    results = RESULTS.read_text(encoding="utf-8")
    ledger = LEDGER.read_text(encoding="utf-8")
    shared = ("batchLedgerRows", "batchSlotsHtml", "batchRegionPlaqueHtml", "batchRegionLedgerHtml", "batchTotalHtml")
    modal_import = re.search(r"import\s*\{([^}]*)\}\s*from\s*['\"]\./harvest-ledger\.js['\"]", modal, re.S)
    results_import = re.search(r"import\s*\{([^}]*)\}\s*from\s*['\"]\./harvest-ledger\.js['\"]", results, re.S)
    assert modal_import and results_import
    modal_names = {name.strip() for name in modal_import.group(1).split(",")}
    results_names = {name.strip() for name in results_import.group(1).split(",")}
    for name in shared:
        assert name in modal_names and name in results_names and f"export function {name}" in ledger
    assert "batchBadgeHtml," in modal and "export function batchBadgeHtml" in ledger
    assert "netAfterCosts" in results
    assert "Region ${String(member.region_uid" not in results


def test_receipt_total_badges_keep_names_on_one_line_and_size_to_content() -> None:
    css = CSS.read_text(encoding="utf-8")

    def without_max_width_media_blocks(css_text: str) -> str:
        media_start = re.compile(r"@media\s*\(\s*max-width\s*:[^)]*\)\s*\{")
        result = css_text
        while match := media_start.search(result):
            depth = 1
            end = match.end()
            while depth and end < len(result):
                if result[end] == "{":
                    depth += 1
                elif result[end] == "}":
                    depth -= 1
                end += 1
            result = result[:match.start()] + result[end:]
        return result

    total_slots = re.search(
        r"#harvest-modal-overlay \.harvest-receipt-summary \.harvest-receipt-ledger \.hm-batch-total \.hm-batch-slots\s*\{([^}]*)\}",
        css,
    )
    total_badge = re.search(
        r"#harvest-modal-overlay \.harvest-receipt-summary \.harvest-receipt-ledger \.hm-batch-total \.hm-batch-badge\s*\{([^}]*)\}",
        css,
    )
    total_name = re.search(
        r"#harvest-modal-overlay \.harvest-receipt-summary \.harvest-receipt-ledger \.hm-batch-total \.hm-batch-name\s*\{([^}]*)\}",
        css,
    )
    assert total_slots and "display:flex" in total_slots.group(1) and "flex-wrap:wrap" in total_slots.group(1)
    assert total_badge and "inline-size:max-content" in total_badge.group(1)
    assert total_name and re.search(r"white-space\s*:\s*nowrap", total_name.group(1))
    desktop_css = without_max_width_media_blocks(css)
    assert not re.search(r"#harvest-modal-overlay \.harvest-receipt-summary \.harvest-receipt-ledger \.hm-batch-total \.hm-batch-slots\s*\{[^}]*repeat\([^)]*1fr", desktop_css)
