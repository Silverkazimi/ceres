""" a terrain-penalised worker shows its reduced value, in red.

His report: "Tireless Farmhand on plot 065-09-083 shows 2000 BasePP, although it has -50%
terrain penalty. This is a double bug: The basePP must show the correct 1000 PP value and
second it must be colored red because it is reduced forcefully and potentially a
misplacment on that plot (in that case it is delberate and the penalty accepted but
nevertheless it must be marked loudly)."

Measured before the fix: base_pp 2,000, terrain_boost -0.5, construction_pp 1,000. The game
applies the penalty server-side into construction_pp and BOTH fields already reached the
browser -- the tile simply rendered base_pp and put the -50% in a hover tooltip. So the
number was never wrong; it was silent. He was shown that keeping 2,000 preserves one meaning
of "Base PP" across the tile and the picker, and ruled for the productive value instead.

RED MUTATIONS are named per test. The source-shape tests are the weakest here and are
labelled as such; the live-DOM test is the one that would catch a real regression.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EDITOR = (ROOT / "ceres" / "static" / "editor.js").read_text(encoding="utf-8")


def _base_pp_column() -> str:
    start = EDITOR.index("function editorBasePpColumn(")
    return EDITOR[start:EDITOR.index("\nfunction ", start + 10)]


def _without_js_comments(source: str) -> str:
    cleaned = []
    state = "code"
    index = 0
    delimiters = {"single": "'", "double": '"', "template": "`"}
    while index < len(source):
        char = source[index]
        following = source[index + 1] if index + 1 < len(source) else ""
        if state == "line_comment":
            if char == "\n":
                cleaned.append(char)
                state = "code"
            else:
                cleaned.append(" ")
        elif state == "block_comment":
            if char == "*" and following == "/":
                cleaned.extend((" ", " "))
                state = "code"
                index += 1
            else:
                cleaned.append("\n" if char == "\n" else " ")
        elif state in delimiters:
            cleaned.append(char)
            if char == "\\" and following:
                cleaned.append(following)
                index += 1
            elif char == delimiters[state]:
                state = "code"
        elif char == "/" and following == "/":
            cleaned.extend((" ", " "))
            state = "line_comment"
            index += 1
        elif char == "/" and following == "*":
            cleaned.extend((" ", " "))
            state = "block_comment"
            index += 1
        else:
            cleaned.append(char)
            if char == "'":
                state = "single"
            elif char == '"':
                state = "double"
            elif char == "`":
                state = "template"
        index += 1
    return "".join(cleaned)


def test_the_penalised_figure_replaces_the_unpenalised_one():
    """ (v0.9.299) retitled the badge Base PP -> Boostable PP and folded the terrain
 factor directly into the figure formula instead of switching between two precomputed
 values, so the guard now targets that formula.

 RED MUTATION: change `displayed * (1 + (terrain || 0))` back to `displayed`. This must
 turn red."""
    body = _base_pp_column()
    assert re.search(r"displayed\s*\*\s*\(1\s*\+\s*\(terrain\s*\|\|\s*0\)\)", body), (
        "the cell no longer applies the terrain factor to the after-cap value"
    )
    assert "Number.isFinite(construction) ? construction" in body, (
        "the cell no longer prefers the server-authoritative post-penalty value when present"
    )


def test_the_penalty_flag_is_gated_on_a_negative_boost_not_on_falsiness():
    """R-50: a null `terrain_boost` means the plot modifier is missing and must NEVER render
 as 0. `terrainValue()` returns null for that case, and `!terrain` would swallow it.

 RED MUTATION: insert a decoy comment containing the correct assignment, then replace the
 executable guard with `const penalised = !terrain;`. Comments are removed before matching,
 so the executable falsiness expression is captured and this must turn red."""
    body = _without_js_comments(_base_pp_column())
    match = re.search(r"\bconst\s+penalised\s*=\s*(?P<rhs>[^;]+);", body)
    assert match, "the executable `const penalised = ...;` expression must remain present"
    rhs = match.group("rhs")
    assert re.search(r"\bterrain\s*!==\s*null\s*&&\s*terrain\s*<\s*0\b", rhs), (
        "the terrain-penalty flag must test `terrain < 0` explicitly; `!terrain` would treat a "
        "missing plot modifier as a penalty and contradict R-50"
    )
    assert "!terrain" not in rhs


def test_a_penalty_never_marks_a_worker_whose_value_did_not_drop():
    """the old `construction < displayed` conjunct is gone -- the fallback formula's
 `(terrain || 0)` multiplier is now what guarantees a non-negative/absent terrain leaves the
 figure unscaled, so a worker without an actual penalty is never reduced by this path.

 RED MUTATION: change the multiplier to unconditional `terrain` (dropping the `|| 0`
 fallback), which would corrupt the figure whenever terrain is null instead of leaving it
 unscaled. This must turn red."""
    body = _base_pp_column()
    assert "(terrain || 0)" in body


def test_the_screen_reader_is_told_why_the_number_shrank():
    """A colour alone is not a signal for everyone. The aria label must name the penalty and
 the value it replaced.

 RED MUTATION: revert `ariaLabel` to the unconditional `Base PP ${figure}`."""
    body = _base_pp_column()
    assert "terrain penalty on this plot" in body
    assert "reduced from" in body


def test_there_is_no_dismissal_path_that_could_clear_the_mark():
    """His requirement: "in that case it is delberate and the penalty accepted but nevertheless
 it must be marked loudly." The flag is derived per render from the occupant's own
 terrain_boost, so nothing can acknowledge it away.

 RED MUTATION: introduce any acknowledged/dismissed state into this path. This test is a
 guard against a FUTURE change, not against the current code -- it is the weakest test in
 this file and is included because the requirement is about persistence, which no snapshot
 of correct code can demonstrate on its own."""
    body = _base_pp_column()
    for token in ("acknowledged", "dismissed", "suppress", "confirmedPlacement"):
        assert token not in body, f"a dismissal path ({token}) would defeat the 'nevertheless' in his ruling"
