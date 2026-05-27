"""Tests for the RFC 7616 Digest auth helper."""

from __future__ import annotations

import hashlib
import re

import pytest

from custom_components.hikvision_access.api.digest import DigestAuth, _parse_challenge


def _md5(s: str) -> str:
    return hashlib.md5(s.encode()).hexdigest()


def _fields(header: str) -> dict[str, str]:
    out = dict(re.findall(r'(\w+)=("[^"]*"|\w+)', header))
    return {k: v.strip('"') for k, v in out.items()}


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


def test_authorization_matches_rfc7616_md5_qop_auth() -> None:
    """Verify the response field against a hand-computed RFC 7616 vector.

    Using the algorithm spec from section 3.4.6:
        HA1 = MD5(username:realm:password)
        HA2 = MD5(method:uri)
        response = MD5(HA1:nonce:nc:cnonce:qop:HA2)
    The cnonce is random, so we recompute the expected response with the
    cnonce the helper actually picked.
    """
    d = DigestAuth("Mufasa", "Circle Of Life")
    header = d.authorization(
        'Digest realm="testrealm@host.com", qop="auth", '
        'nonce="dcd98b7102dd2f0e8b11d0f600bfb0c093", '
        'opaque="5ccc069c403ebaf9f0171e9517f40e41"',
        "GET",
        "/dir/index.html",
    )
    fields = _fields(header)
    cnonce = fields["cnonce"]
    nc = fields["nc"]
    response = fields["response"]

    ha1 = _md5("Mufasa:testrealm@host.com:Circle Of Life")
    ha2 = _md5("GET:/dir/index.html")
    expected = _md5(f"{ha1}:dcd98b7102dd2f0e8b11d0f600bfb0c093:{nc}:{cnonce}:auth:{ha2}")
    assert response == expected
    # opaque must be echoed back.
    assert fields["opaque"] == "5ccc069c403ebaf9f0171e9517f40e41"
    # Stateless: every call starts a fresh count at nc=00000001.
    assert nc == "00000001"
    assert fields["username"] == "Mufasa"
    assert fields["realm"] == "testrealm@host.com"
    assert fields["nonce"] == "dcd98b7102dd2f0e8b11d0f600bfb0c093"
    assert fields["uri"] == "/dir/index.html"


def test_authorization_is_stateless_nc_always_one() -> None:
    """Each call is independent; nc never increments across calls."""
    d = DigestAuth("u", "p")
    challenge = 'Digest realm="r", qop="auth", nonce="n"'
    for _ in range(3):
        header = d.authorization(challenge, "GET", "/a")
        assert "nc=00000001" in header


def test_authorization_uses_fresh_cnonce_each_call() -> None:
    """A new cnonce is generated per call so concurrent callers don't collide."""
    d = DigestAuth("u", "p")
    challenge = 'Digest realm="r", qop="auth", nonce="n"'
    cnonces = {_fields(d.authorization(challenge, "GET", "/a"))["cnonce"] for _ in range(5)}
    assert len(cnonces) == 5


def test_authorization_picks_auth_when_qop_list_offered() -> None:
    d = DigestAuth("u", "p")
    header = d.authorization('Digest realm="r", qop="auth-int,auth", nonce="n"', "GET", "/")
    assert "qop=auth" in header
    assert "qop=auth-int" not in header


def test_authorization_no_qop_falls_back_to_rfc2069() -> None:
    """When the server doesn't advertise qop, omit nc/cnonce and use RFC 2069 response."""
    d = DigestAuth("u", "p")
    header = d.authorization('Digest realm="r", nonce="n"', "GET", "/")
    assert "qop=" not in header
    assert "cnonce=" not in header
    assert "nc=" not in header
    ha1 = _md5("u:r:p")
    ha2 = _md5("GET:/")
    expected = _md5(f"{ha1}:n:{ha2}")
    assert _fields(header)["response"] == expected


def test_authorization_missing_nonce_raises() -> None:
    d = DigestAuth("u", "p")
    with pytest.raises(ValueError, match="missing nonce"):
        d.authorization('Digest realm="r"', "GET", "/")


def test_authorization_unsupported_algorithm_raises() -> None:
    d = DigestAuth("u", "p")
    with pytest.raises(NotImplementedError):
        d.authorization('Digest realm="r", nonce="n", algorithm=SHA-256', "GET", "/")


def test_authorization_md5_sess_supported() -> None:
    d = DigestAuth("u", "p")
    header = d.authorization(
        'Digest realm="r", nonce="n", qop="auth", algorithm=MD5-sess', "GET", "/"
    )
    assert "algorithm=MD5-SESS" in header
