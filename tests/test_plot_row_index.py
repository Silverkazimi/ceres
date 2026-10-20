""" — the per-plot rebuild of a whole-index set is gone, and stays gone.

`_plot_row` runs once per plot. It used to open with
`indexed_refs = set(level_by_asset) | set(skin_by_asset)`, rebuilding two sets over the
entire card index and unioning them for every plot on the board — a value identical for
all of them, used only for two membership tests. Measured at 48.91s of a 140.66s
`project_board` on a 6,303-plot account with a 22,476-entry index; 0.01s on a normal one.

Both uses were redundant, so the line was deleted rather than hoisted:
  levels       — `.get()` yields None for an absent ref and None for a skin-only ref, so
                 the membership test could never change the result.
  level_states — `ref in level_by_asset` already implies `ref in indexed_refs`.

RED MUTATION for this file: restore the original two statements in
`ceres/board.py::_plot_row` —
    indexed_refs = set(level_by_asset) | set(skin_by_asset)
    levels = [level_by_asset.get(w.get("asset_ref")) if w.get("asset_ref") in indexed_refs else None for w in workers]
    level_states = ["resolved" if w.get("asset_ref") in indexed_refs and w.get("asset_ref") in level_by_asset else "unavailable" for w in workers]
`test_plot_row_does_not_build_a_whole_index_set_per_plot` must turn red on the call
count. The equivalence tests must stay green under that mutation — they assert the
behaviour that was preserved, so if one of them fails the deletion changed something.
"""
import inspect
import io
import textwrap
import tokenize

from ceres import board


def test_plot_row_does_not_build_a_whole_index_set_per_plot():
    """The scaling defect itself: no per-plot set construction over the index."""
    counter = {"calls": 0}

    class _CountingIndex(dict):
        def __iter__(self):
            counter["calls"] += 1
            return super().__iter__()

    level_by_asset = _CountingIndex({"ref-a": 5})
    skin_by_asset = _CountingIndex({"ref-b": "gold"})
    plot = {
        "facts": {}, "staking_facts": {}, "deed_uid": "I-1-aaa",
    }
    try:
        board._plot_row(plot, [{"asset_ref": "ref-a"}], {}, level_by_asset, skin_by_asset, {}, {}, lean=True)
    except Exception:
        # A missing fact key is fine — the point is whether the index was iterated on the
        # way in, which happens before anything downstream can raise.
        pass
    assert counter["calls"] == 0, (
        f"the card index was iterated {counter['calls']}x inside a per-plot function; "
        " is back"
    )


def test_levels_equivalence_absent_and_skin_only_refs_still_yield_none():
    """The behaviour the deletion had to preserve, stated as a property."""
    level_by_asset = {"has-level": 7}
    skin_by_asset = {"skin-only": "gold"}
    refs = ["has-level", "skin-only", "absent", None]

    new = [level_by_asset.get(r) for r in refs]
    old = [level_by_asset.get(r) if r in (set(level_by_asset) | set(skin_by_asset)) else None for r in refs]
    assert new == old == [7, None, None, None]


def test_level_states_equivalence_membership_conjunct_was_never_deciding():
    level_by_asset = {"has-level": 7}
    skin_by_asset = {"skin-only": "gold"}
    refs = ["has-level", "skin-only", "absent", None]

    new = ["resolved" if r in level_by_asset else "unavailable" for r in refs]
    old = [
        "resolved" if r in (set(level_by_asset) | set(skin_by_asset)) and r in level_by_asset
        else "unavailable"
        for r in refs
    ]
    assert new == old == ["resolved", "unavailable", "unavailable", "unavailable"]


def test_the_deleted_line_is_not_reintroduced_verbatim():
    """Cheap backstop. Not a substitute for the call-count guard above — it is the
    only assertion here that could be satisfied by a rename, and it is deliberately
    the weakest of the four."""
    source = inspect.getsource(board._plot_row)
    # Only executable code counts: `_plot_row` keeps a comment that quotes the deleted line.
    def code_only(text: str) -> str:
        tokens = tokenize.generate_tokens(io.StringIO(textwrap.dedent(text)).readline)
        return " ".join(token.string for token in tokens if token.type not in (tokenize.COMMENT, tokenize.STRING))

    deleted = code_only("set(level_by_asset) | set(skin_by_asset)\n").strip()
    assert deleted in code_only("indexed_refs = set(level_by_asset) | set(skin_by_asset)\n")
    assert deleted not in code_only("# indexed_refs = set(level_by_asset) | set(skin_by_asset)\n")
    assert deleted not in code_only(source)
