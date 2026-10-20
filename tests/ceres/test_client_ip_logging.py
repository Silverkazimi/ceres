from types import SimpleNamespace

from ceres.logging_setup import client_ip


def test_client_ip_uses_client_host_when_forwarded_header_is_spoofed():
    request = SimpleNamespace(
        headers={"x-forwarded-for": "203.0.113.99, 198.51.100.7"},
        client=SimpleNamespace(host="192.0.2.10"),
    )

    assert client_ip(request) == "192.0.2.10"


def test_client_ip_uses_request_client_host():
    request = SimpleNamespace(headers={}, client=SimpleNamespace(host="192.0.2.11"))

    assert client_ip(request) == "192.0.2.11"


def test_client_ip_falls_back_when_request_client_is_missing():
    request = SimpleNamespace(headers={}, client=None)

    assert client_ip(request) == "-"
