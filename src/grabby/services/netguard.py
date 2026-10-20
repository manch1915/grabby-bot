"""Keeps user-supplied links away from the server's own network.

Anyone can send the bot a URL, so links to unknown sites must not point at loopback,
private ranges, the Docker gateway or cloud metadata addresses.
"""

import asyncio
import ipaddress
import socket
from typing import Any

from aiohttp.abc import ResolveResult
from aiohttp.resolver import ThreadedResolver


def blocked_literal(host: str) -> bool:
    """True for hosts that are internal without even resolving them."""
    host = host.strip("[]").lower().rstrip(".")
    try:
        return not ipaddress.ip_address(host).is_global
    except ValueError:
        pass
    return "." not in host or host == "localhost" or host.endswith((".localhost", ".internal", ".local"))


async def is_public_host(host: str) -> bool:
    if blocked_literal(host):
        return False
    loop = asyncio.get_running_loop()
    try:
        infos = await asyncio.wait_for(loop.getaddrinfo(host, 443, type=socket.SOCK_STREAM), timeout=5)
    except (OSError, TimeoutError):
        return False
    addresses = {info[4][0] for info in infos}
    return bool(addresses) and all(ipaddress.ip_address(str(a).split("%")[0]).is_global for a in addresses)


class PublicOnlyResolver(ThreadedResolver):
    """aiohttp resolver that refuses names pointing inside. The connection goes to the very
    addresses checked here, so DNS rebinding between check and connect doesn't help."""

    async def resolve(self, host: str, port: int = 0, family: Any = socket.AF_INET) -> list[ResolveResult]:
        if blocked_literal(host):
            raise OSError(f"{host} is not a public host")
        results: list[ResolveResult] = await super().resolve(host, port, family)
        if not results or not all(ipaddress.ip_address(r["host"].split("%")[0]).is_global for r in results):
            raise OSError(f"{host} resolves to a non-public address")
        return results
