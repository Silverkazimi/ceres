from pathlib import Path


ROOT = Path(__file__).parents[2]
CERES = (ROOT / "ceres" / "static" / "ceres.css").read_text(encoding="utf-8")
BUTTONS = (ROOT / "ceres" / "static" / "buttons.css").read_text(encoding="utf-8")


def test_selected_splinter_ring_uses_gold_dim_token() -> None:
    rule = ".picker-bar .terrain-affinity-badge.is-selected"
    declaration = next(line for line in BUTTONS.splitlines() if line.startswith(rule))
    assert rule not in CERES
    assert (
        "box-shadow: 0 0 0 2px var(--gold-dim), 0 0 0 4px color-mix(in srgb, var(--gold-dim) 25%, transparent)"
        in declaration
    )
    assert "var(--gold-dim)" in declaration
    assert "var(--info)" not in declaration
    assert "var(--ok)" not in declaration
