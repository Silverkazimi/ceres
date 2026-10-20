""" (W1): the hosted log keeps no account name and no address, but one failure can still be followed through its lines."""
import logging

from ceres import logging_setup


def test_names_and_addresses_become_stable_tags_and_nothing_else_changes():
    line = "2026-10-02T12:30:45+0200 INFO ceres.action: /api/harvest/routing/v2/claim player=player0005 -> 200 in 5ms from 203.0.113.9 acct account=bob v0.15.2 ratio 3:4 ::1 2001:db8::7"
    clean = logging_setup.redact(line)
    for secret in ("player0005", "bob", "203.0.113.9", "2001:db8::7"):
        assert secret not in clean
    assert "2026-10-02T12:30:45+0200" in clean and "v0.15.2" in clean and "ratio 3:4" in clean and "> 200 in 5ms" in clean
    assert logging_setup.redact("player=player0005") == logging_setup.redact("player=player0005") != "player=player0005"      # stable inside one run, case-insensitive
    assert logging_setup.redact("player=player0005") != logging_setup.redact("player=bob")


def test_json_account_fields_and_prose_account_mentions_are_redacted():
    for field in ("player", "account", "username", "owner", "account_name"):
        clean = logging_setup.redact('{"' + field + '":"secret_player","ok":true}')
        assert "secret_player" not in clean
    for line in ("failed for player secret_player", "account: secret_player", "snapshot unavailable player=secret_player"):
        assert "secret_player" not in logging_setup.redact(line)


def test_sensitive_access_query_is_omitted_when_redaction_enabled(monkeypatch):
    from starlette.requests import Request
    monkeypatch.setenv("CERES_REDACT_LOGS", "1")
    scope = {"type": "http", "method": "GET", "path": "/api/cards", "query_string": b"player=secret_player", "headers": [], "server": ("testserver", 80), "client": ("127.0.0.1", 1), "scheme": "http"}
    assert logging_setup._target(Request(scope)) == "/api/cards"


def test_the_redacting_formatter_covers_every_logger_and_the_hosted_file_keeps_seven_days(tmp_path, monkeypatch):
    record = logging.LogRecord("uvicorn.error", logging.WARNING, __file__, 1, "refused player=%s from %s", ("alice", "198.51.100.4"), None)
    out = logging_setup.RedactingFormatter(logging_setup.FORMAT).format(record)
    assert "alice" not in out and "198.51.100.4" not in out and "refused player=" in out
    monkeypatch.setenv("CERES_REDACT_LOGS", "1")
    monkeypatch.delenv("CERES_TEST_LOG_DIR", raising=False)
    root = logging.getLogger()
    saved = (root.handlers[:], getattr(root, "_ceres_configured", False), root.level)
    root._ceres_configured = False
    try:
        logging_setup.configure_logging(tmp_path)
        files = [h for h in root.handlers if isinstance(h, logging.handlers.TimedRotatingFileHandler)]
        assert files and files[0].backupCount == 7 and isinstance(files[0].formatter, logging_setup.RedactingFormatter)
    finally:
        for handler in root.handlers[:]:
            root.removeHandler(handler)
            handler.close()
        for handler in saved[0]:
            root.addHandler(handler)
        root._ceres_configured = saved[1]
        root.setLevel(saved[2])
