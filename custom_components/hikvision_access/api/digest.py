"""HTTP Digest authentication (RFC 7616) for the Hikvision ISAPI client.

Hikvision controllers reject HTTP Basic with 401 and challenge for Digest via
``WWW-Authenticate: Digest realm=..., nonce=..., qop=...``. aiohttp doesn't
ship a Digest auth helper, so we implement just enough of RFC 7616 to talk
to the device: MD5 algorithm + ``qop=auth``. The class is intentionally
small and stateful — the same instance is reused across all requests for a
single client so the nonce counter increments monotonically.
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
    """Stateful Digest auth helper.

    Workflow:
    1. Caller issues a request without auth. On 401, it passes the
       ``WWW-Authenticate`` header to :meth:`handle_challenge`.
    2. Caller calls :meth:`build_header` for each subsequent request,
       passing the HTTP method and the request-URI (path + optional query).
    3. The challenge cache stays valid until a fresh 401 invalidates it
       (e.g. ``stale=true``), at which point ``handle_challenge`` is called
       again and ``_nc`` is reset.

    Only ``algorithm=MD5`` and ``qop=auth`` are implemented — that covers
    every Hikvision firmware we've tested. ``qop=auth-int`` and SHA-256
    aren't used by the device.
    """

    def __init__(self, username: str, password: str) -> None:
        self._username = username
        self._password = password
        self._realm: str | None = None
        self._nonce: str | None = None
        self._qop: str | None = None
        self._algorithm: str = "MD5"
        self._opaque: str | None = None
        self._nc: int = 0

    @property
    def has_challenge(self) -> bool:
        return self._nonce is not None

    def invalidate(self) -> None:
        """Drop the cached challenge so the next request re-negotiates."""
        self._realm = None
        self._nonce = None
        self._qop = None
        self._opaque = None
        self._nc = 0

    def handle_auth_info(self, auth_info_header: str) -> None:
        """Fold a server-sent ``Authentication-Info: nextnonce=...`` into the cache.

        RFC 7616 §3.5: when the server provides a fresh nonce on a 200
        response, the client should switch to it for the *next* request.
        This lets us authenticate every call on the first try (single
        round-trip per request) without forcing the device to issue a
        fresh 401 challenge — which Hikvision firmware counts as a failed
        login attempt and uses to trip its IP-filter lockout.
        """
        params = _parse_challenge(auth_info_header)
        nextnonce = params.get("nextnonce")
        if nextnonce:
            self._nonce = nextnonce
            self._nc = 0

    def handle_challenge(self, www_authenticate: str) -> None:
        """Cache the parsed challenge and reset the nonce counter."""
        params = _parse_challenge(www_authenticate)
        nonce = params.get("nonce")
        if nonce is None:
            raise ValueError(
                f"WWW-Authenticate header missing nonce: {www_authenticate!r}"
            )
        self._realm = params.get("realm", "")
        self._nonce = nonce
        # qop may be a comma-separated list ("auth,auth-int"); pick "auth"
        # if present, fall back to the first listed value.
        qop_raw = params.get("qop")
        if qop_raw:
            qops = [q.strip() for q in qop_raw.split(",")]
            self._qop = "auth" if "auth" in qops else qops[0]
        else:
            self._qop = None
        algorithm = params.get("algorithm", "MD5").upper()
        # Strip trailing "-sess" — we don't implement session variants and
        # Hikvision doesn't use them; fall back to plain MD5.
        self._algorithm = "MD5" if algorithm in {"MD5", "MD5-SESS"} else algorithm
        self._opaque = params.get("opaque")
        self._nc = 0

    def build_header(self, method: str, uri: str) -> str:
        """Compute the ``Authorization: Digest ...`` value for one request.

        ``uri`` must be the request-URI exactly as it appears on the
        request line — typically the path plus an optional ``?query``.
        Different URIs produce different HA2 hashes, so callers MUST pass
        the same URI they then send.
        """
        if self._nonce is None or self._realm is None:
            raise RuntimeError(
                "DigestAuth.build_header called before a challenge was cached"
            )
        if self._algorithm != "MD5":
            raise NotImplementedError(
                f"Digest algorithm {self._algorithm!r} not supported"
            )

        self._nc += 1
        nc_hex = f"{self._nc:08x}"
        # 16 hex chars of OS randomness; RFC 7616 only requires "a string of
        # data, which is chosen by the client" with enough entropy that the
        # server can detect replay.
        cnonce = os.urandom(8).hex()

        ha1 = _md5_hex(f"{self._username}:{self._realm}:{self._password}")
        ha2 = _md5_hex(f"{method}:{uri}")
        if self._qop in {"auth", "auth-int"}:
            response = _md5_hex(
                f"{ha1}:{self._nonce}:{nc_hex}:{cnonce}:{self._qop}:{ha2}"
            )
        else:
            # Legacy RFC 2069 form (no qop) — kept for compatibility.
            response = _md5_hex(f"{ha1}:{self._nonce}:{ha2}")

        parts = [
            f'username="{self._username}"',
            f'realm="{self._realm}"',
            f'nonce="{self._nonce}"',
            f'uri="{uri}"',
            f'response="{response}"',
            f"algorithm={self._algorithm}",
        ]
        if self._qop:
            parts.append(f"qop={self._qop}")
            parts.append(f"nc={nc_hex}")
            parts.append(f'cnonce="{cnonce}"')
        if self._opaque is not None:
            parts.append(f'opaque="{self._opaque}"')
        return "Digest " + ", ".join(parts)
