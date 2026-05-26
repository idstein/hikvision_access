"""Tests for the RFC 7616 Digest auth helper."""

from __future__ import annotations

import hashlib
import re

import pytest

from custom_components.hikvision_access.api.digest import DigestAuth, _parse_challenge


def _md5(s: str) -> str:
    return hashlib.md5(s.encode()).hexdigest()


def test_parse_challenge_basic() -> None:
    header = 'Digest realm="testrealm@host.com", qop="auth,auth-int", nonce="abc", opaque="o"'
    parsed = _parse_challenge(header)
    assert parsed["realm"] == "testrealm@host.com"
    assert parsed["qop"] == "auth,auth-int"
    assert parsed["nonce"] == "abc"
    assert parsed["opaque"] == "o"


def test_parse_challenge_handles_no_scheme_prefix() -> None:
    parsed = _parse_challenge('realm="r", nonce="n"')
    assert parsed == {"realm": "r", "nonce": "n"}


def test_parse_challenge_unquoted_algorithm() -> None:
    parsed = _parse_challenge('Digest realm="r", nonce="n", algorithm=MD5, qop="auth"')
    assert parsed["algorithm"] == "MD5"
    assert parsed["qop"] == "auth"


def test_build_header_requires_challenge_first() -> None:
    d = DigestAuth("u", "p")
    with pytest.raises(RuntimeError):
        d.build_header("GET", "/foo")


def test_build_header_matches_rfc7616_md5_qop_auth() -> None:
    """Verify the response field against a hand-computed RFC 7616 vector.

    Using the algorithm spec from section 3.4.6:
        HA1 = MD5(username:realm:password)
        HA2 = MD5(method:uri)
        response = MD5(HA1:nonce:nc:cnonce:qop:HA2)
    We can't pin down the random ``cnonce``, so we recompute the expected
    response with the cnonce the helper actually picked.
    """
    d = DigestAuth("Mufasa", "Circle Of Life")
    d.handle_challenge(
        'Digest realm="testrealm@host.com", qop="auth", '
        'nonce="dcd98b7102dd2f0e8b11d0f600bfb0c093", '
        'opaque="5ccc069c403ebaf9f0171e9517f40e41"'
    )
    header = d.build_header("GET", "/dir/index.html")
    # Extract cnonce/nc/response that the helper produced.
    fields = dict(re.findall(r'(\w+)=("[^"]*"|\w+)', header))
    cnonce = fields["cnonce"].strip('"')
    nc = fields["nc"]
    response = fields["response"].strip('"')

    ha1 = _md5("Mufasa:testrealm@host.com:Circle Of Life")
    ha2 = _md5("GET:/dir/index.html")
    expected = _md5(f"{ha1}:dcd98b7102dd2f0e8b11d0f600bfb0c093:{nc}:{cnonce}:auth:{ha2}")
    assert response == expected
    # opaque must be echoed back.
    assert fields["opaque"].strip('"') == "5ccc069c403ebaf9f0171e9517f40e41"
    # First build_header call → nc=00000001.
    assert nc == "00000001"


def test_nc_increments_monotonically() -> None:
    d = DigestAuth("u", "p")
    d.handle_challenge('Digest realm="r", qop="auth", nonce="n"')
    h1 = d.build_header("GET", "/a")
    h2 = d.build_header("GET", "/a")
    h3 = d.build_header("GET", "/a")
    assert 'nc=00000001' in h1
    assert 'nc=00000002' in h2
    assert 'nc=00000003' in h3


def test_handle_challenge_resets_nc() -> None:
    d = DigestAuth("u", "p")
    d.handle_challenge('Digest realm="r", qop="auth", nonce="n1"')
    d.build_header("GET", "/")
    d.build_header("GET", "/")
    assert d._nc == 2
    d.handle_challenge('Digest realm="r", qop="auth", nonce="n2"')
    assert d._nc == 0
    h = d.build_header("GET", "/")
    assert 'nc=00000001' in h


def test_qop_picks_auth_when_offered() -> None:
    d = DigestAuth("u", "p")
    d.handle_challenge('Digest realm="r", qop="auth-int,auth", nonce="n"')
    assert d._qop == "auth"


def test_no_qop_falls_back_to_rfc2069() -> None:
    """When the server doesn't advertise qop, omit nc/cnonce and use RFC 2069 response."""
    d = DigestAuth("u", "p")
    d.handle_challenge('Digest realm="r", nonce="n"')
    header = d.build_header("GET", "/")
    assert "qop=" not in header
    assert "cnonce=" not in header
    assert "nc=" not in header
    ha1 = _md5("u:r:p")
    ha2 = _md5("GET:/")
    expected = _md5(f"{ha1}:n:{ha2}")
    fields = dict(re.findall(r'(\w+)=("[^"]*"|\w+)', header))
    assert fields["response"].strip('"') == expected


def test_missing_nonce_raises() -> None:
    d = DigestAuth("u", "p")
    with pytest.raises(ValueError, match="missing nonce"):
        d.handle_challenge('Digest realm="r"')


def test_has_challenge_flag() -> None:
    d = DigestAuth("u", "p")
    assert not d.has_challenge
    d.handle_challenge('Digest realm="r", nonce="n"')
    assert d.has_challenge
