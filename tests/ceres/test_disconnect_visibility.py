""": Disconnect HiveAuth must not show while Keychain is the active wallet."""
from __future__ import annotations

import subprocess
import shutil
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
WALLET_JS = (ROOT / "ceres/static/wallet.js").read_text()


def _connection_line() -> str:
    line = WALLET_JS.split("const connection =", 1)[1].split(";", 1)[0]
    return line


def test_disconnect_condition_requires_hiveauth_as_active_wallet() -> None:
    """Keychain connected + stored HiveAuth session -> no Disconnect.

    The `connection` render must gate on `kind === 'hiveauth'` so a stored,
    unexpired HiveAuth session alone is not enough to show the Disconnect
    button when Keychain is the account's selected wallet.
    """
    connection = _connection_line()
    assert "kind === 'hiveauth'" in connection


def test_disconnect_condition_still_requires_live_hiveauth_session() -> None:
    """HiveAuth connected -> Disconnect shown (only with a live session).

    The pre-existing session checks must survive the fix: the account must
    match the stored session and that session must not be expired.
    """
    connection = _connection_line()
    assert "session?.account === account" in connection
    assert "session.expire > Date.now()" in connection


def test_disconnect_button_markup_still_present() -> None:
    """HiveAuth connected -> Disconnect shown.

    The Disconnect button's markup itself (attributes and accessible name)
    must remain untouched by the visibility fix.
    """
    connection = _connection_line()
    assert "data-wallet-disconnect" in connection
    assert 'aria-label="Disconnect HiveAuth"' in connection


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_disconnect_condition_behaviour_via_node() -> None:
    """Evaluate the actual condition expression with stub values via node.

    Keychain connected + stored HiveAuth session -> no Disconnect.
    HiveAuth connected -> Disconnect shown.
    """
    connection = _connection_line()
    expr = connection.split(" ? `", 1)[0].strip()
    assert expr.startswith("kind === 'hiveauth'")

    script = f"""
    function evalCondition(kind, session, account, now) {{
      const Date = {{ now: () => now }};
      return eval({expr!r});
    }}
    const liveSession = {{ account: 'player0005', expire: 2000 }};
    // Keychain connected + stored HiveAuth session -> no Disconnect
    console.log(JSON.stringify(evalCondition('keychain', liveSession, 'player0005', 1000)));
    // HiveAuth connected -> Disconnect shown
    console.log(JSON.stringify(evalCondition('hiveauth', liveSession, 'player0005', 1000)));
    """
    result = subprocess.run(
        ["node", "-e", script],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, result.stderr
    keychain_result, hiveauth_result = result.stdout.strip().splitlines()
    assert keychain_result == "false"
    assert hiveauth_result == "true"
