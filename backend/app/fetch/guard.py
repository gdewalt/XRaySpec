"""SSRF / rebinding guard for outbound fetches (DESIGN.md §11.2, §17.4).

Every hop (initial URL and every redirect target) must pass:
  1. scheme is https,
  2. host is on the explicit allowlist,
  3. *all* IPs the host resolves to are public — no private, loopback, link-local,
     reserved, multicast, or unspecified addresses.

Re-resolving and re-checking on every hop defeats DNS-rebinding: a name that
resolved public once cannot be swapped for an internal address on a later request.
"""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Callable
from urllib.parse import ParseResult, urlparse

from .errors import FetchError

Resolver = Callable[[str], list[str]]


def default_resolver(host: str) -> list[str]:
    infos = socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
    return list({info[4][0] for info in infos})


def check_url(url: str, allowed_hosts: set[str]) -> ParseResult:
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise FetchError("forbidden_scheme", f"scheme {parsed.scheme!r} is not allowed")
    host = parsed.hostname
    if not host:
        raise FetchError("malformed_url", "URL has no host")
    if host.lower() not in allowed_hosts:
        raise FetchError("forbidden_host", f"host {host!r} is not on the allowlist")
    return parsed


def assert_public_host(host: str, resolve: Resolver) -> None:
    try:
        ips = resolve(host)
    except OSError as exc:
        raise FetchError("dns_failure", f"could not resolve {host!r}") from exc
    if not ips:
        raise FetchError("dns_failure", f"{host!r} did not resolve")
    for ip in ips:
        addr = ipaddress.ip_address(ip)
        if (
            addr.is_private
            or addr.is_loopback
            or addr.is_link_local
            or addr.is_reserved
            or addr.is_multicast
            or addr.is_unspecified
        ):
            raise FetchError("private_ip", f"{host!r} resolves to non-public address {ip}")


def validate_hop(url: str, allowed_hosts: set[str], resolve: Resolver) -> ParseResult:
    """Full per-hop check: scheme + allowlist + public-IP resolution."""
    parsed = check_url(url, allowed_hosts)
    assert_public_host(parsed.hostname, resolve)
    return parsed
