""" (critical): a board read that dies because the server restarted or the connection dropped is retried once, automatically."""
from pathlib import Path

BOARD = (Path(__file__).parents[2] / "ceres" / "static" / "board.js").read_text(encoding="utf-8")


def test_a_failed_board_read_is_retried_once_before_the_player_is_asked_to_reload():
    start = BOARD.index("const readOnce = () => readBoard(")
    block = BOARD[start:BOARD.index("if (generation !== BOARD_GENERATION) return false;", start)]
    assert "let read = opts.fromCompletedCapture === true ? { ok: true } : await readOnce();" in block
    assert "!read.ok && opts.fromCompletedCapture !== true && read.error?.name !== 'AbortError' && !signal.aborted && generation === BOARD_GENERATION" in block   # never after an abort or a newer load
    assert "await abortableDelay(3000, signal)" in block and "read = await readOnce();" in block
    assert block.count("await readOnce()") == 2                                                         # exactly one retry, never a loop
    assert "The board read did not finish. Reload to try again." in BOARD                              # the second failure is still shown
