from __future__ import annotations

import ipaddress
import re
from functools import lru_cache
from urllib.parse import urlparse

import socket

from django.conf import settings


LOCAL_HOSTS = {"localhost", "localhost.localdomain", "ip6-localhost", "ip6-loopback"}


@lru_cache(maxsize=512)
def _resolved_ips(host: str) -> tuple[str, ...]:
    try:
        return tuple({item[4][0] for item in socket.getaddrinfo(host, None)})
    except (OSError, UnicodeError):
        return ()


def _literal_ip(host: str):
    try:
        return ipaddress.ip_address(host)
    except ValueError:
        pass
    try:
        if re.fullmatch(r"[0-9]+", host):
            return ipaddress.IPv4Address(int(host))
        if re.fullmatch(r"0[xX][0-9a-fA-F]+", host):
            return ipaddress.IPv4Address(int(host, 16))
        if re.fullmatch(r"0[0-7]+", host):
            return ipaddress.IPv4Address(int(host, 8))
    except (ValueError, ipaddress.AddressValueError):
        return None
    return None


def _unsafe_ip(address) -> bool:
    return any(
        (
            address.is_private,
            address.is_loopback,
            address.is_link_local,
            address.is_multicast,
            address.is_reserved,
            address.is_unspecified,
        )
    )


def _debug_allows(host: str, address) -> bool:
    if not getattr(settings, "DEBUG", False):
        return False
    return host.lower() in LOCAL_HOSTS or bool(address and address.is_loopback)


def is_safe_outbound_url(url: str) -> bool:
    try:
        parsed = urlparse(str(url))
    except (TypeError, ValueError):
        return False
    allowed_schemes = {"http", "https"} if getattr(settings, "DEBUG", False) else {"https"}
    if parsed.scheme.lower() not in allowed_schemes or not parsed.hostname:
        return False
    if parsed.username or parsed.password:
        return False
    host = parsed.hostname.strip("[]").rstrip(".").lower()
    literal = _literal_ip(host)
    if literal is not None:
        return _debug_allows(host, literal) or not _unsafe_ip(literal)
    addresses = []
    for value in _resolved_ips(host):
        address = _literal_ip(value)
        if address is None:
            return False
        addresses.append(address)
    if not addresses:
        return False
    return all(_debug_allows(host, address) or not _unsafe_ip(address) for address in addresses)
