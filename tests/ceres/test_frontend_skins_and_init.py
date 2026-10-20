from __future__ import annotations

import re
from pathlib import Path
from .vm_context import read_entry_source


APP = Path(__file__).parents[2] / "ceres" / "static" / "app.js"
INIT = Path(__file__).parents[2] / "ceres" / "__init__.py"
CHANGELOG = Path(__file__).parents[2] / "CHANGELOG.md"


def test_skin_art_url_and_base_fallback_contract() -> None:
    source = read_entry_source()
    assert source.count("function editorCardImage(asset)") == 1
    assert "const encodedSkin = asset.skin" in source
    assert "${CARD_CDN_BASE}/${set}${encodedSkin ? `/${encodedSkin}` : ''}/${encodedName}" in source
    assert "const baseImage = asset.skin && (groupId === 'worker' || groupId === 'runi')" in source
    assert "const fallback = occupant.base_image ||" in source
    assert "this.parentElement.remove()" in source


def test_plot_sort_option_is_client_side_and_precedes_server_options() -> None:
    source = read_entry_source()
    start = source.index("function populateSortOptions()")
    end = source.index("\n}\n\nfunction populateFilterOptions", start)
    block = source[start:end]
    assert block.index("plotOpt.value = 'plot'") < block.index("for (const key of BOARD.sortable)")
    assert "plotOpt.textContent = 'plot'" in block


def test_ready_is_invoked_once_at_module_end() -> None:
    source = read_entry_source()
    assert source.count("ready(() =>") == 1
    ready_at = source.index("ready(() =>")
    assert ready_at > source.index("async function prefetchIdleCardsCache")
    assert source.rstrip().endswith("});")
    assert "STRUCTURALLY CLOSED" in source


def test_version_bumped_for_frontend_fixes() -> None:
    # A frozen literal here goes stale on every later bump (observed: it pinned
    # "" and broke as soon as v0.5.14 shipped). The durable intent is that
    # ceres/__init__.py's __version__ always matches CHANGELOG.md's newest
    # "## ceres vX.Y.Z" heading -- so compare against that instead of a literal.
    changelog = CHANGELOG.read_text(encoding="utf-8")
    match = re.search(r"^## (?:ceres v)?(\d+\.\d+\.\d+)\b", changelog, re.MULTILINE)
    assert match is not None, "CHANGELOG.md has no version heading"
    newest_version = match.group(1)
    assert f'__version__ = "{newest_version}"' in INIT.read_text(encoding="utf-8")
