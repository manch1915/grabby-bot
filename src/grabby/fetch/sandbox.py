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


def _allowed(address: Any, exceptions: frozenset[tuple[str, int]]) -> bool:
    if not isinstance(address, tuple) or not address:
        return True  # unix sockets and the like
    host = str(address[0]).split("%")[0]
    if len(address) > 1 and (host, int(address[1])) in exceptions:
        return True
    try:
        return ipaddress.ip_address(host).is_global
    except ValueError:
        return False


def install(allow: Iterable[tuple[str, int]] = ()) -> None:
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


_DEFAULT_PORTS = {"http": 80, "https": 443, "socks5": 1080, "socks5h": 1080, "socks4": 1080}


def proxy_endpoints(proxy: str | None) -> list[tuple[str, int]]:
    """The configured proxy may sit on a private address. Only its exact ip:port is let
    through; everything else on that host stays blocked."""
    if not proxy:
        return []
    parts = urlsplit(proxy)
    port = parts.port or _DEFAULT_PORTS.get(parts.scheme, 1080)
    if not parts.hostname:
        return []
    try:
        infos = socket.getaddrinfo(parts.hostname, port)
    except OSError:
        return []
    return sorted({(str(info[4][0]), port) for info in infos})
