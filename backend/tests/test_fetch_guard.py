"""SSRF guard (DESIGN.md §11.2, §17.4)."""

from __future__ import annotations

import pytest

from app.fetch.errors import FetchError
from app.fetch.guard import assert_public_host, check_url, validate_hop

ALLOWED = {"patents.google.com", "patentimages.storage.googleapis.com"}


def _resolve_to(*ips):
    def _r(_host):
        return list(ips)

    return _r


def test_https_allowlisted_ok():
    parsed = check_url("https://patents.google.com/patent/US123B2/en", ALLOWED)
    assert parsed.hostname == "patents.google.com"


def test_non_https_rejected():
    with pytest.raises(FetchError) as e:
        check_url("http://patents.google.com/x", ALLOWED)
    assert e.value.code == "forbidden_scheme"


def test_non_allowlisted_host_rejected():
    with pytest.raises(FetchError) as e:
        check_url("https://evil.example.com/x", ALLOWED)
    assert e.value.code == "forbidden_host"


def test_public_ip_ok():
    assert_public_host("patents.google.com", _resolve_to("142.250.72.14"))  # no raise


@pytest.mark.parametrize("ip", ["10.0.0.5", "127.0.0.1", "169.254.1.1", "192.168.1.1", "::1"])
def test_non_public_ip_rejected(ip):
    with pytest.raises(FetchError) as e:
        assert_public_host("host", _resolve_to(ip))
    assert e.value.code == "private_ip"


def test_validate_hop_blocks_rebinding():
    # Host is allowlisted, but it resolves to a private address (DNS rebinding).
    with pytest.raises(FetchError) as e:
        validate_hop("https://patents.google.com/x", ALLOWED, _resolve_to("10.1.2.3"))
    assert e.value.code == "private_ip"
