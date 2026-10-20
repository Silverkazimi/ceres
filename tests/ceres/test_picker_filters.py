"""Regression guards for the  worker picker filters."""

from __future__ import annotations

import re

from .vm_context import STATIC


CANDIDATES = (STATIC / "candidates.js").read_text(encoding="utf-8")
CSS = (STATIC / "ceres.css").read_text(encoding="utf-8")
HTML = (STATIC / "index.html").read_text(encoding="utf-8")

# Historical picker state is recorded here because the public tree omits the base commit.
BASE_MIN_MAX_INLINE_SIZE_REM = 4.5
BASE_FILTER_KEYS = {"${key}", "edition", "keep_open", "max_pp", "min_pp", "q", "rarity", "splinter"}
BASE_IDS = {"${candidateDomId(key)}"}
BASE_DATA_ATTRIBUTES = {
    "data-candidate-index", "data-candidate-key", "data-control-case", "data-control-density",
    "data-control-mode", "data-countdown-until", "data-detail-key", "data-mobile-context", "data-picker",
    "data-picker-action", "data-picker-filter", "data-picker-group", "data-picker-rarity", "data-picker-view",
    "data-sort", "data-tip",
}


def _worker_fields(source: str) -> str:
    return source.split("const workerFields = isWorker", 1)[1].split(
        "const legacyFields =", 1
    )[0]


def _attribute_contract(source: str) -> tuple[set[str], set[str], set[str]]:
    filter_keys = set(re.findall(r'data-picker-filter="([^"]+)"', source))
    ids = set(re.findall(r'\bid="([^"]+)"', source))
    data_attributes = set(re.findall(r'\b(data-[a-z-]+)\s*=', source))
    return filter_keys, ids, data_attributes


def test_worker_filter_groups_follow_splinter_inputs_dropdowns_toggles() -> None:
    fields = _worker_fields(CANDIDATES)
    filters_html = CANDIDATES.split("const filtersHtml =", 1)[1].split(
        "const sortHtml =", 1
    )[0]

    assert "${affinity}${isWorker ? '<span class=\"controls-sep\"" in filters_html
    assert filters_html.index("${affinity}") < filters_html.index("${fields}")
    expected_order = (
        "${workerNameField}",
        'data-picker-filter="min_pp"',
        'data-picker-filter="max_pp"',
        "rarityField('Rarity'",
        'data-picker-filter="edition"',
        "toggle('ready_now'",
    )
    positions = [fields.index(marker) for marker in expected_order]
    assert positions == sorted(positions)
    first_separator = fields.index('class="controls-sep"')
    second_separator = fields.index('class="controls-sep"', first_separator + 1)
    assert positions[2] < first_separator < positions[3]
    assert positions[4] < second_separator < positions[5]


def test_worker_text_fields_reuse_the_board_filter_field_class() -> None:
    board_search = re.search(
        r'<label class="([^"]+)"[^>]*>\s*<span class="sr-only">Filter</span>'
        r'\s*<input[^>]*id="text-filter"',
        HTML,
    )
    assert board_search
    board_classes = set(board_search.group(1).split())
    assert "board-filter-field" in board_classes

    name_field = CANDIDATES.split("const workerNameField =", 1)[1].split("\n", 1)[0]
    fields = name_field + _worker_fields(CANDIDATES)
    for key in ("q", "min_pp", "max_pp"):
        label = re.search(
            rf'<label class="([^"]+)"[^>]*>'
            rf'(?:(?!</label>).)*data-picker-filter="{key}"',
            fields,
            re.DOTALL,
        )
        assert label, key
        assert board_classes <= set(label.group(1).split()), key


def test_worker_min_max_widths_exceed_the_base_commit() -> None:
    def width(css: str) -> float:
        rule = re.search(
            r'\.picker-filters input\[data-picker-filter="min_pp"\]'
            r'[^{}]*\{([^}]+)\}',
            css,
        )
        assert rule
        value = re.search(r"inline-size:\s*([\d.]+)rem", rule.group(1))
        assert value
        return float(value.group(1))

    assert width(CSS) > BASE_MIN_MAX_INLINE_SIZE_REM


def test_worker_groups_have_three_board_style_separators() -> None:
    fields = _worker_fields(CANDIDATES)
    filters_html = CANDIDATES.split("const filtersHtml =", 1)[1].split(
        "const sortHtml =", 1
    )[0]
    separators = fields.count('class="controls-sep"') + filters_html.count(
        'class="controls-sep"'
    )

    assert '<span class="controls-sep" aria-hidden="true"></span>' in HTML
    assert separators == 3


def test_picker_filter_keys_ids_and_data_attributes_match_the_base_commit() -> None:
    actual_keys, actual_ids, actual_attrs = _attribute_contract(CANDIDATES)
    base_keys, base_ids, base_attrs = BASE_FILTER_KEYS, BASE_IDS, BASE_DATA_ATTRIBUTES
    # adds exactly Ability; IDs and action attributes stay at this queue's base.
    assert actual_keys == base_keys | {"ability"}
    assert actual_ids == base_ids
    assert actual_attrs == base_attrs
