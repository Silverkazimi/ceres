"""Static regression guards for the shared Ceres wide-input treatment."""

from __future__ import annotations

import re

from .vm_context import STATIC


CSS = (STATIC / "ceres.css").read_text(encoding="utf-8")
BUTTONS = (STATIC / "buttons.css").read_text(encoding="utf-8")
BOARD = (STATIC / "board.js").read_text(encoding="utf-8")
CANDIDATES = (STATIC / "candidates.js").read_text(encoding="utf-8")
HTML = (STATIC / "index.html").read_text(encoding="utf-8")


def test_shared_input_class_is_defined_once_and_used_on_all_three_surfaces() -> None:
    assert len(re.findall(r"(?m)^\.ceres-wide-input\s*\{", CSS)) == 1
    assert re.search(r'<input[^>]+id="text-filter"[^>]+class="ceres-wide-input"', HTML)
    assert 'class="ceres-wide-input start-input"' in BOARD
    assert CANDIDATES.count('class="ceres-wide-input"') == 8
    assert 'const itemFields = `${nameField}' in CANDIDATES
    picker_text_inputs = re.findall(
        r'<input([^>]+data-picker-filter="(?:q|min_pp|max_pp|edition)"[^>]*)>',
        CANDIDATES,
    )
    assert len(picker_text_inputs) == 8
    assert all(
        'type="text"' in attrs and 'class="ceres-wide-input"' in attrs
        for attrs in picker_text_inputs
    )
    assert "data-picker-filter=\"min_pp\"" in CANDIDATES
    assert "data-picker-filter=\"max_pp\"" in CANDIDATES
    for state in ("::placeholder", ":not(:placeholder-shown)", ":focus-visible"):
        assert f".ceres-wide-input{state}" in CSS
    shared_rule = CSS.split(".ceres-wide-input {", 1)[1].split("}", 1)[0]
    assert "font-size: var(--ceres-control-font-size)" in shared_rule
    assert "font-weight: 600" in shared_rule
    assert "color: var(--heading)" in shared_rule
    assert "letter-spacing: normal" in shared_rule
    placeholder_rule = CSS.split(".ceres-wide-input::placeholder {", 1)[1].split("}", 1)[0]
    assert "font-size: inherit" in placeholder_rule
    assert "font-weight: inherit" in placeholder_rule
    assert "letter-spacing: inherit" in placeholder_rule
    assert "color: revert" in placeholder_rule
    assert "opacity: revert" in placeholder_rule


def test_text_styling_and_states_are_owned_only_by_the_shared_class() -> None:
    assert len(re.findall(r"(?m)^\.ceres-wide-input(?:::placeholder|:not\(:placeholder-shown\)|:focus-visible)?\s*\{", CSS)) == 4
    start_rule = CSS.split(".start-input {", 1)[1].split("}", 1)[0]
    assert not re.search(r"(?:font(?:-size|-weight)?|letter-spacing|color)", start_rule)
    assert not re.search(r"\.controls-row input\[type=text\]#text-filter\s*\{[^}]*\b(?:font|color|letter-spacing)", CSS)
    assert ".start-input:focus-visible" not in CSS
    populated_rules = re.findall(
        r"(?m)^([^{}]+:not\(:placeholder-shown\))\s*\{([^{}]*)\}", CSS
    )
    assert {selector.strip() for selector, _ in populated_rules} == {
        ".ceres-wide-input:not(:placeholder-shown)",
        ".controls-row input[type=text]#text-filter:not(:placeholder-shown)",
        ".picker-filters input:is([type=text], [type=number], [type=search], :not([type])):not(:placeholder-shown)",
    }
    assert all(
        declaration.strip() == "border-color: var(--primary);"
        for _, declaration in populated_rules
    )
    assert ".controls-row input[type=text]#text-filter:focus-visible" not in CSS
    assert ".picker-filters input:focus-visible, .picker-filters select:focus-visible { outline: 2px solid var(--accent); outline-offset: 1px; }" in CSS
    assert ".picker-filters input:focus-visible { border-color: var(--primary); outline: 2px solid var(--primary); outline-offset: 1px; }" not in CSS


def test_start_input_uses_open_board_comfortable_height_token() -> None:
    assert 'data-control-density="comfortable"' in BOARD
    assert "min-block-size: var(--ceres-control-min-block)" in CSS.split(
        ".start-input {", 1
    )[1].split("}", 1)[0]
    comfortable_rule = re.search(
        r'is\(\.ceres-btn-group, \.ceres-btn, \.ceres-wide-input\)\'\n r\'\[data-control-density="comfortable"\]\s*\{([^}]+)\}',
        BUTTONS,
    )
    assert comfortable_rule
    assert "ceres-control-min-block: 2.75rem" in comfortable_rule.group(1)


def test_phone_start_controls_share_the_stacked_width() -> None:
    phone_rules = CSS.split("@media (max-width: 600px)", 1)[1].split("}", 2)
    shared_width_rule = phone_rules[1]
    assert ".start-form .start-input, .start-form .start-go" in shared_width_rule
    assert "inline-size: 100%" in shared_width_rule
