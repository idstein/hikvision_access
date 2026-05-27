"""HTTP Digest authentication (RFC 7616) for the Hikvision ISAPI client.

Hikvision controllers reject HTTP Basic with 401 and challenge for Digest via
``WWW-Authenticate: Digest realm=..., nonce=..., qop=...``. aiohttp doesn't
ship a Digest auth helper, so we implement just enough of RFC 7616 to talk
to the device: MD5 algorithm + ``qop=auth``.

The tested firmware (DS-K2702WX V1.7.4) issues STRICTLY single-use nonces:
replaying a nonce — even with an incremented nc, which RFC 7616 permits — is
rejected with a 401 that carries no fresh challenge. Caching a challenge is
therefore worthless and, worse, a shared mutable cache races when concurrent
requests interleave (one caches nonce X, another consumes it, the first then
replays the dead nonce). So :class:`DigestAuth` is intentionally STATELESS:
it holds only credentials and computes an Authorization header from a fresh
challenge handed to it per call, generating a new cnonce and ``nc=1`` every
time. That makes a single instance safe for unsynchronized concurrent use.
"""

from __future__ import annotations

import hashlib
import os
import re
from typing import Final

_TOKEN_RE: Final = re.compile(
    r'(?P<key>[a-zA-Z0-9_-]+)\s*=\s*(?:"(?P<qval>(?:[^"\\]|\\.)*)"|(?P<val>[^,\s]+))',
)


def _parse_challenge(header: str) -> dict[str, str]:
    """Parse a ``WWW-Authenticate: Digest ...`` header value into a dict.

    Strips an optional leading ``Digest`` scheme token and supports both
    quoted and unquoted parameter values, per RFC 7616 section 3.3.
    """
    stripped = header.strip()
    if stripped.lower().startswith("digest"):
        stripped = stripped[len("digest"):].lstrip()
    out: dict[str, str] = {}
    for m in _TOKEN_RE.finditer(stripped):
        key = m.group("key").lower()
        val = m.group("qval") if m.group("qval") is not None else m.group("val")
        # Unescape quoted-string backslash escapes (RFC 7230 quoted-string).
        out[key] = val.replace("\\\\", "\\").replace('\\"', '"') if m.group("qval") else val
    return out


def _md5_hex(data: str) -> str:
    # RFC 7616 mandates MD5 — Hikvision firmware does not implement SHA-256.
    return hashlib.md5(data.encode("utf-8")).hexdigest()


class DigestAuth:
    """Stateless RFC 7616 Digest auth helper.

    Holds only credentials. :meth:`authorization` takes a fresh
    ``WWW-Authenticate`` challenge plus the request method/URI and returns
    the ``Authorization`` header value. Because it keeps no challenge state,
    a single instance is safe for concurrent use across requests — each
    caller computes its own response from its own challenge with a fresh
    cnonce and ``nc=00000001``.

    Only ``algorithm=MD5`` / ``MD5-SESS`` and ``qop=auth`` are exercised —
    that covers every Hikvision firmware we've tested.
    """

    def __init__(self, username: str, password: str) -> None:
        self._username = username
        self._password = password

    def authorization(self, www_authenticate: str, method: str, uri: str) -> str:
        """Parse a fresh WWW-Authenticate challenge and return the
        Authorization header value. Stateless — a new cnonce and nc=1 are
        generated each call, so concurrent callers never interfere.

        ``uri`` must be the request-URI exactly as it appears on the request
        line (path plus an optional ``?query``); it feeds the HA2 hash, so
        callers MUST send the same URI they pass here.
        """
        params = _parse_challenge(www_authenticate)
        nonce = params.get("nonce")
        if nonce is None:
            raise ValueError(f"WWW-Authenticate missing nonce: {www_authenticate!r}")
        realm = params.get("realm", "")
        qop_raw = params.get("qop")
        qop = None
        if qop_raw:
            qops = [q.strip() for q in qop_raw.split(",")]
            qop = "auth" if "auth" in qops else qops[0]
        algorithm = params.get("algorithm", "MD5").upper()
        if algorithm not in {"MD5", "MD5-SESS"}:
            raise NotImplementedError(f"Digest algorithm {algorithm!r} not supported")
        opaque = params.get("opaque")

        nc_hex = "00000001"
        cnonce = os.urandom(8).hex()
        ha1 = _md5_hex(f"{self._username}:{realm}:{self._password}")
        ha2 = _md5_hex(f"{method}:{uri}")
        if qop in {"auth", "auth-int"}:
            response = _md5_hex(f"{ha1}:{nonce}:{nc_hex}:{cnonce}:{qop}:{ha2}")
        else:
            response = _md5_hex(f"{ha1}:{nonce}:{ha2}")

        parts = [
            f'username="{self._username}"',
            f'realm="{realm}"',
            f'nonce="{nonce}"',
            f'uri="{uri}"',
            f'response="{response}"',
            f"algorithm={algorithm}",
        ]
        if qop:
            parts.append(f"qop={qop}")
            parts.append(f"nc={nc_hex}")
            parts.append(f'cnonce="{cnonce}"')
        if opaque is not None:
            parts.append(f'opaque="{opaque}"')
        return "Digest " + ", ".join(parts)
