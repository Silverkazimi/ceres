"""Run the real Ceres CLI with a process-wide, loopback-only socket guard.

Fixture upstream supplies the responses. This backstop catches any new outbound
code path that has not yet been wired through the fixture transport.
"""
from __future__ import annotations

import ipaddress
import os
import runpy
import socket


def _loopback(address) -> bool:
    if not isinstance(address, tuple):  # Unix domain socket
        return True
    host = str(address[0])
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def main() -> None:
    if not os.environ.get("CERES_FIXTURE_UPSTREAM"):
        raise RuntimeError("The browser test server requires fixture upstream")
    getaddrinfo = socket.getaddrinfo

    def guarded_lookup(host, *args, **kwargs):
        if host is not None and not _loopback((host, 0)):
            raise OSError(f"Browser test server refused external DNS lookup: {host!r}")
        return getaddrinfo(host, *args, **kwargs)

    socket.getaddrinfo = guarded_lookup
    connect = socket.socket.connect
    connect_ex = socket.socket.connect_ex

    def guarded(self, address):
        if not _loopback(address):
            raise OSError(f"Browser test server refused external connection: {address!r}")
        return connect(self, address)

    def guarded_ex(self, address):
        if not _loopback(address):
            raise OSError(f"Browser test server refused external connection: {address!r}")
        return connect_ex(self, address)

    socket.socket.connect = guarded
    socket.socket.connect_ex = guarded_ex
    runpy.run_module("ceres", run_name="__main__")


if __name__ == "__main__":
    main()
