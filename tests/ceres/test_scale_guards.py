from pathlib import Path
import re


ROOT = Path(__file__).parents[2]
STATIC = ROOT / "ceres" / "static"


def test_no_page_zoom_and_layout_classes_are_prepainted() -> None:
    # the pre-paint block publishes the board width and the class NAMES
    # (the phone switch reads them), and never zooms the page.
    source = (STATIC / "index.html").read_text()
    script = source.split("<!-- (maintainer: browser zoom", 1)[1].split("</script>", 1)[0]
    assert "style.zoom" not in script and "ceres:contentW'))" not in script and "setContent" not in script
    for tier in (1919, 1659, 1439, 1279, 767):
        assert f"ceres-lw-le-{tier}" in script
    for name in ("ceres-lw-ge-1400", "ceres-lw-le-1099", "ceres-lw-le-544", "ceres-lw-le-960"):
        assert name in script
    assert "window.ceresScale={design:w,apply:apply}" in script


def test_table_is_sized_by_css_not_js() -> None:
    table = (STATIC / "table.js").read_text()
    for gone in ("lockColumnWidths", "distributeColumnWidths", "PLOT_COLUMN_CACHE", "syncHScrollProxy", "syncHeadScroll", "setContent", "factor("):
        assert gone not in table, gone
    css = (STATIC / "ceres.css").read_text()
    assert "table.plot-table { table-layout: auto; width: 100%;" in css
    assert "container: ceres-table / inline-size;" in css
    assert "overflow-x: clip;" in css
    assert "hscroll-proxy" not in (STATIC / "index.html").read_text()


def test_fluid_table_and_open_plot_blocks_have_no_width_tiers() -> None:
    css = (STATIC / "ceres.css").read_text()
    for start, end in (("/* FLUID TABLE START", "/* FLUID TABLE END */"),
                       ("/* FLUID OPEN PLOT START", "/* FLUID OPEN PLOT END */")):
        block = css.split(start, 1)[1].split(end, 1)[0]
        stripped = re.sub(r"/\*.*?\*/", "", block, flags=re.S)
        # (v0.9.489, "fix: fill the plot tile row and remove slot pagination")
        # added ONE documented desktop/mobile boundary -- `html:not(.ceres-lw-le-1279)`
        # gates the container-query-sized `.slot-editor`/`.editor-band` rules to
        # desktop, since mobile mounts the same tiles via a separate one-row layout
        # (library/ceres-layout-width.md "v0.9.489 -- inner plot gap correction").
        # This is a single boundary switching sizing STRATEGY, not the old
        # multi-tier fixed-width ladder removed, so it is stripped here rather
        # than treated as a reintroduced width tier.
        stripped = stripped.replace("html:not(.ceres-lw-le-1279)", "")
        assert "ceres-lw-" not in stripped
        # Same /v0.9.489 correction: the OPEN PLOT block moved from clamp()
        # sizing to container-query calc() sizing (--eos-slot-w divides 100cqi minus
        # insets by the visible slot count) so tiles fill the strip exactly instead of
        # being capped -- it no longer uses clamp() at all. The TABLE block is
        # untouched by that ticket and still must use clamp().
        if "FLUID TABLE" in start:
            assert "clamp(" in block
        else:
            assert "calc(" in block
    assert " PHASES A-B LOW-RES TABLE START" not in css


def test_compact_headers_keep_sort_identity_and_full_labels() -> None:
    source = (STATIC / "index.html").read_text()
    expected = {
        "construction_pp": ("Boostable", "Boost."),
        "work_per_hour": ("Harvest/hr", "Harv/hr"),
        "worker_count": ("Workers", "Wkr"),
        "dec_needed": ("DEC stake", "DEC"),
    }
    for key, (full, short) in expected.items():
        header = re.search(rf'<th[^>]*data-key="{key}"[^>]*>(.*?)</th>', source)
        assert header, key
        assert f'>{full}</span>' in header.group(1)
        assert f'data-label="{short}"' in header.group(1)


def test_changed_geometry_reads_are_annotated() -> None:
    files = ["app.js", "table.js", "eos.js", "tooltip.js", "terrainAffinity.js"]
    tokens = ("getBoundingClientRect(", "innerWidth", "innerHeight", "clientX", "scrollY", "scrollX")
    for name in files:
        for line in (STATIC / name).read_text().splitlines():
            if any(token in line for token in tokens) and not line.lstrip().startswith("//"):
                assert "// V" in line, (name, line)


def test_scale_facade_has_no_zoom_conversions() -> None:
    source = (STATIC / "scale.js").read_text()
    assert "export function availableTableWidth" in source
    for gone in ("export function factor", "export function toLayout", "export function rectToLayout", "export function layoutViewport"):
        assert gone not in source
