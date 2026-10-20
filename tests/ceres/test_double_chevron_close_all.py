from pathlib import Path
import re


ROOT = Path(__file__).parents[2]
HTML = (ROOT / "ceres" / "static" / "index.html").read_text(encoding="utf-8")
ROWS = (ROOT / "ceres" / "static" / "rows.js").read_text(encoding="utf-8")
CSS = (ROOT / "ceres" / "static" / "ceres.css").read_text(encoding="utf-8")


def test_close_all_reuses_red_chevrons_and_moves_left_of_wallet_slider() -> None:
    button = re.search(r'<button[^>]+id="close-all-plots"[^>]*>(.*?)</button>', HTML)
    assert button
    assert 'class="close-all-btn chevron-close"' in button.group(0)
    assert button.group(1).count("<svg ") == 2
    assert "Close all</button>" not in button.group(0)
    assert "target - (id === 'close-all-plots' ? 3.5 * parseFloat" in ROWS
    assert ".close-all-chevron-stack svg + svg" in CSS
