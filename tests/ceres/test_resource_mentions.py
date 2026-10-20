"""resource names in running text carry their icon (source guards; the helper is pure JS)."""
from pathlib import Path

STATIC = Path(__file__).parents[2] / "ceres" / "static"


def test_helper_exists_and_is_applied_at_the_three_owner_sites():
    icons = (STATIC / "icons.js").read_text()
    assert "export function decorateResourceMentions(escapedText)" in icons
    assert "Grain|Wood|Stone|Iron|Research|Aura|SPS|DEC|Vouchers?" in icons
    worksite = (STATIC / "worksite.js").read_text()
    assert "decorateResourceMentions(esc(noticeMessage(notice)))" in worksite
    assert "note.innerHTML = decorateResourceMentions(esc(worksiteFooterNote(row, review)))" in worksite
    assert "note.textContent = worksiteFooterNote" not in worksite
    wallet = (STATIC / "wallet.js").read_text()
    assert "decorateResourceMentions(esc(prefix))" in wallet
    assert ".res-inline-icon {" in (STATIC / "ceres.css").read_text()


def test_helper_only_ever_wraps_escaped_input():
    # Every call site passes esc(...) first: the helper adds icon markup and nothing else.
    for name in ("worksite.js", "wallet.js", "board.js"):
        text = (STATIC / name).read_text()
        for call in [m for m in text.split("decorateResourceMentions(")[1:]]:
            assert call.startswith("esc("), name


def test_decorate_resource_mentions_executed(tmp_path):
    """run the real helper — amounts get icons, worksite names do not, input stays escaped."""
    from tests.ceres.vm_context import run_in_module_context
    body = """
      const out = decorateResourceMentions('Grain Farm is ready \u2014 feed workers with 15,000 Grain to complete; Research Hut; Aura Lab; 3 Vouchers; tx 3decDEC0; Ironclad; 2 SPS');
 const icons = (out.match(/<img class=\"res-inline-icon\"/g) || []).length;
 if (icons !== 3) throw new Error('expected 3 icons, got ' + icons + ': ' + out);
 if (!out.startsWith('Grain Farm is ready')) throw new Error('worksite name decorated: ' + out);
 if (out.includes('Research Hut<img') || out.includes('Aura Lab<img') || out.includes('Ironclad<img')) throw new Error(out);
 if (!out.includes('Vouchers<img') || !out.includes('2 SPS<img')) throw new Error(out);
 if (out.includes('3decDEC0<img')) throw new Error('tx id decorated');
    """
    run_in_module_context(body, entry=STATIC / "icons.js")
