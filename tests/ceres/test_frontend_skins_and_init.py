from __future__ import annotations

import re
from pathlib import Path
from ceres.board import SORTABLE

from .vm_context import STATIC, read_entry_source, run_in_module_context


APP = Path(__file__).parents[2] / "ceres" / "static" / "app.js"
INIT = Path(__file__).parents[2] / "ceres" / "__init__.py"
CHANGELOG = Path(__file__).parents[2] / "CHANGELOG.md"


_SKIN_ART_BODY = r"""
function assert(ok, message) { if (!ok) throw new Error(message); }
const card = {card_name: 'Rage', name: 'Rage', edition: 13, editions: '13', card_set: 'rebellion'};
const plain = editorCardImageCandidates(card);
const skinned = editorCardImageCandidates({...card, skin: 'Golden Age'});
assert(plain.length > 0 && plain.every((url) => url.startsWith(CARD_CDN_BASE + '/') && !url.includes('/Golden%20Age/')),
  'unskinned art must be the base path: ' + plain);
// A skinned card shows its skin or the named placeholder, never the base art (the
// candidates and the rendered <img> both stay inside the skin folder).
assert(skinned.length > 0 && skinned.every((url) => /\/Golden%20Age\/Rage\.(jpg|png)$/.test(url)),
  'skinned art must stay inside the skin folder: ' + skinned);
const html = editorCardImageHtml({...card, skin: 'Golden Age'}, {candidates: [...plain, ...skinned]});
assert(html.includes('data-card-skin="Golden Age"') && !html.includes(plain[0]),
  'a skinned <img> must never start on, or fall back to, base art: ' + html);
const occupant = editorOccupant({...card, skin: 'Golden Age', uid: 'synthetic-card-1'}, 'worker');
assert(occupant.image === skinned[0] && occupant.base_image && !occupant.base_image.includes('/Golden%20Age/'),
  'worker occupant must carry skinned art first and a base_image without the skin');
"""


def test_skin_art_url_and_base_fallback_contract() -> None:
    source = read_entry_source()
    assert source.count("function editorCardImage(asset)") == 1
    # The skin folder sits between the set folder and the file name.
    assert "candidates.push(`${CARD_CDN_BASE}/${set}${skin ? `/${cardImagePart(skin)}` : ''}/${filename}.${extension}`);" in source
    assert "const baseImage = asset.skin && (groupId === 'worker' || groupId === 'runi')" in source
    # When no candidate loads, the art is replaced by a named placeholder (not removed).
    assert "renderCardImageFallback(image);" in source
    assert "'Unknown card — no image'" in source
    run_in_module_context(_SKIN_ART_BODY)


def test_plot_sort_option_is_client_side_and_precedes_server_options() -> None:
    # Sorting is header-click only (the separate sort dropdown is gone): 'plot' is the
    # first sortable header, sorted in the browser and accepted outside the server's
    # `sortable` allowlist; every other header must be in that allowlist.
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    head = html.split('id="table-head-sticky"', 1)[1].split("</thead>", 1)[0]
    keys = re.findall(r'<(?:th|button)\b[^>]*\bdata-key="([^"]+)"', head)
    assert keys[0] == "plot"
    assert "plot" not in SORTABLE
    source = read_entry_source()
    assert "if (!BOARD?.sortable?.includes(key) && key !== 'plot') return;" in source
    assert "if (S.SORT_KEY === 'plot') return sortByPlot(rows);" in source


def test_ready_is_invoked_once_at_module_end() -> None:
    app = APP.read_text(encoding="utf-8")
    # init() is called exactly once, from the module-end ready() block.
    assert len(re.findall(r"(?<![\w.])init\(\);", app)) == 1
    init_call = app.index("const initDone = init();")
    block_start = app.rindex("ready(() => {", 0, init_call)
    assert block_start > app.index("async function init()")
    # Every ready() invocation sits at the end of the module: no module-level let/const
    # is declared after the first one, so no callback can read uninitialised state.
    first_ready = re.search(r"^ready\(", app, re.MULTILINE).start()
    assert not re.search(r"^(?:let|const|var) ", app[first_ready:], re.MULTILINE)
    assert app.rstrip().endswith("});")
    source = read_entry_source()
    assert source.index("const initDone = init();") > source.index("async function prefetchIdleCardsCache")
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
