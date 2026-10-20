from pathlib import Path


ROOT = Path(__file__).parents[2]
CSS = (ROOT / "ceres" / "static" / "ceres.css").read_text(encoding="utf-8")


def test_populated_board_and_picker_inputs_keep_primary_border() -> None:
    assert ".controls-row input[type=text]#text-filter:not(:placeholder-shown) { border-color: var(--primary); }" in CSS
    assert ".picker-filters input:is([type=text], [type=number], [type=search], :not([type])):not(:placeholder-shown) { border-color: var(--primary); }" in CSS
    assert ".picker-filters input:focus-visible, .picker-filters select:focus-visible { outline: 2px solid var(--accent); outline-offset: 1px; }" in CSS
    assert ".picker-filters input:focus-visible { border-color: var(--primary); outline: 2px solid var(--primary); outline-offset: 1px; }" not in CSS
