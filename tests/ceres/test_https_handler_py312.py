""": urllib.request.HTTPSHandler lost its _check_hostname attribute in Python 3.12, so reading it made every HTTPS call
through the observed opener fail on the production server (3.12) while working on 3.10. The handler must not depend on it."""
import urllib.request

from mnemosyne import accounting


def test_observed_https_handler_works_without_check_hostname(monkeypatch):
    handler = accounting._ObservedHTTPSHandler()
    if hasattr(handler, "_check_hostname"):
        del handler._check_hostname  # what Python 3.12 does
    seen = {}

    def fake_open(handler_, connection_class, req, **kwargs):
        seen.update(kwargs)
        return "opened"

    monkeypatch.setattr(accounting, "_observed_open", fake_open)
    request = urllib.request.Request("https://example.invalid/")
    assert handler.https_open(request) == "opened"
    assert "context" in seen and "check_hostname" not in seen


def test_observed_https_handler_still_passes_check_hostname_when_present(monkeypatch):
    handler = accounting._ObservedHTTPSHandler()
    handler._check_hostname = True  # Python < 3.12
    seen = {}
    monkeypatch.setattr(accounting, "_observed_open", lambda h, c, r, **kw: seen.update(kw) or "opened")
    handler.https_open(urllib.request.Request("https://example.invalid/"))
    assert seen["check_hostname"] is True
