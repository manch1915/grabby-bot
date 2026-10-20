"""Network guard for the fetch subprocess.

The extractors follow redirects and fetch media URLs found inside pages, all chosen by
whoever sent the link. Checking the link once is not enough, so every outgoing Python
socket connection is checked against the actual IP being dialled.
"""

import ipaddress
import socket
from collections.abc import Iterable
from typing import Any
from urllib.parse import urlsplit

_original_connect = socket.socket.connect
_original_connect_ex = socket.socket.connect_ex


def _allowed(address: Any, exceptions: frozenset[str]) -> bool:
    if not isinstance(address, tuple) or not address:
        return True  # unix sockets and the like
    host = str(address[0]).split("%")[0]
    if host in exceptions:
        return True
    try:
        return ipaddress.ip_address(host).is_global
    except ValueError:
        return False


def install(allow: Iterable[str] = ()) -> None:
    """Refuse connections to loopback, private, link-local and reserved addresses."""
    exceptions = frozenset(allow)

    def connect(self: socket.socket, address: Any) -> None:
        if not _allowed(address, exceptions):
            raise ConnectionRefusedError(f"blocked connection to non-public address {address[0]}")
        _original_connect(self, address)

    def connect_ex(self: socket.socket, address: Any) -> int:
        if not _allowed(address, exceptions):
            raise ConnectionRefusedError(f"blocked connection to non-public address {address[0]}")
        return _original_connect_ex(self, address)

    socket.socket.connect = connect  # type: ignore[method-assign, assignment]
    socket.socket.connect_ex = connect_ex  # type: ignore[method-assign, assignment]


def proxy_hosts(proxy: str | None) -> list[str]:
    """The configured proxy may live on a private address; resolve it so it stays reachable."""
    if not proxy:
        return []
    host = urlsplit(proxy).hostname
    if not host:
        return []
    try:
        return sorted({str(info[4][0]) for info in socket.getaddrinfo(host, None)})
    except OSError:
        return []
