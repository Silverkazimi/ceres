""": without the Keychain extension the account menu still offers HiveAuth; Keychain is greyed out with a reason."""
from pathlib import Path

MENU = (Path(__file__).parents[2] / "ceres" / "static" / "account-menu.js").read_text(encoding="utf-8")


def test_choice_shows_when_only_one_wallet_is_available():
    assert "!signedIn && (choices.keychain || choices.hiveauth)" in MENU
    assert "!signedIn && choices.keychain && choices.hiveauth" not in MENU
    assert "choiceBtn('keychain', 'Keychain', choices.keychain ? '' :" in MENU
    assert "choiceBtn('hiveauth', 'HiveAuth', choices.hiveauth ? '' :" in MENU
    assert "${missing ? ` disabled" in MENU
