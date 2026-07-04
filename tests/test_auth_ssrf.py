"""SSRF guard in ``todos.auth`` — validation of user-supplied SEI URLs.

RFC 0014 §3.7. Regression coverage for a fail-open bug: when DNS resolution
of a hostname fails (network blip, NXDOMAIN, etc.), the guard must reject
the URL (fail-closed) instead of silently treating "couldn't check" as
"checked, safe".
"""

from __future__ import annotations

import asyncio
import socket

import pytest

from todos.auth import _validar_url_sei


class TestValidarUrlSeiDnsFailure:
    def test_dns_resolution_failure_is_rejected_not_allowed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A hostname that fails to resolve must be treated as unsafe
        (fail-closed), not silently accepted."""

        def _boom(*_args: object, **_kwargs: object) -> None:
            msg = "Name or service not known"
            raise socket.gaierror(msg)

        monkeypatch.setattr(socket, "getaddrinfo", _boom)

        with pytest.raises(ValueError, match="não foi possível resolver"):
            asyncio.run(_validar_url_sei("https://sei.example.org", "URL do SEI"))

    def test_dns_resolution_success_with_safe_address_is_allowed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Control case: a hostname that resolves to a public address must
        still pass (the fix must not make everything fail-closed)."""

        def _resolves_to_public_ip(
            _host: object, _port: object, _family: object, _type: object
        ) -> list[tuple]:
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 0))]

        monkeypatch.setattr(socket, "getaddrinfo", _resolves_to_public_ip)

        asyncio.run(_validar_url_sei("https://sei.example.org", "URL do SEI"))
