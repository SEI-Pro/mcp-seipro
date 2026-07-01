"""Session-expiry detection and auto-relogin in SEIWebClient's web scraper.

The SEI SIP session doesn't fail with 401/403 when it expires — the server
answers HTTP 200 with the login page's HTML. These tests verify (a) the
shared ``_is_login_page`` marker check, and (b) that ``_gerar_arquivo_processo``
(the multi-step PDF/ZIP generation flow, ~180s worth of requests — plenty of
time for a session to expire mid-flow) detects this at every step and
self-heals via relogin+retry, instead of failing with a confusing parse error.

No pytest-asyncio plugin is installed in this repo (see test_keyring_reread.py
for the established pattern) — each test wraps its async body in
``asyncio.run()``.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest

from todos.backends.models import SEIWebClientConfig
from todos.exceptions import SEIAuthError, SEIParseError
from todos.sei_web_client import SEIWebClient, _is_login_page

_LOGIN_PAGE = '<html><body><form><input name="txtUsuario"></form></body></html>'
_PROTOCOLO = "7000000-00.2024.8.22.0001"


def make_client() -> SEIWebClient:
    return SEIWebClient(
        SEIWebClientConfig(sei_web_url="http://sei.test", sei_usuario="u", sei_senha="p")
    )


class TestIsLoginPage:
    def test_detects_name_attribute(self) -> None:
        assert _is_login_page('<input name="txtUsuario" value="">') is True

    def test_detects_id_attribute(self) -> None:
        assert _is_login_page('<input id="txtUsuario" value="">') is True

    def test_false_for_unrelated_page(self) -> None:
        assert _is_login_page("<html><body>Caixa de entrada</body></html>") is False

    def test_false_for_empty_string(self) -> None:
        assert _is_login_page("") is False


# ---------------------------------------------------------------------------
# _gerar_arquivo_processo — full 5-step flow, with session-expiry injection
# ---------------------------------------------------------------------------

_TRAB_URL = "http://sei.test/sei/controlador.php?acao=procedimento_trabalhar&id=1"
_ARVORE_URL = "http://sei.test/sei/controlador.php?acao=arvore_visualizar&id=1"
_FORM_URL = (
    "http://sei.test/sei/controlador.php?acao=procedimento_gerar_pdf&id=1&infra_hash=deadbeef"
)
_POST_URL = "http://sei.test/sei/controlador.php?acao=procedimento_gerar_pdf_montar"
_DOWNLOAD_URL = "http://sei.test/sei/controlador.php?acao=procedimento_exibir_arquivo"

_R1_OK = f'<html><body><iframe id="ifrArvore" src="{_ARVORE_URL}"></iframe></body></html>'
_R2_OK = (
    '<a href="controlador.php?acao=procedimento_gerar_pdf&id=1&infra_hash=deadbeef">Gerar PDF</a>'
)
_R3_OK = (
    f'<form id="frmProcedimentoPdf" action="{_POST_URL}">'
    f'<input name="hdnProcedimento" value="1"></form>'
)
_R4_OK = f"<script>document.getElementById('ifrDownload').src = '{_DOWNLOAD_URL}';</script>"
_R5_OK = b"%PDF-1.4 fake content"


def _resp(url: str, *, text: str | None = None, content: bytes | None = None) -> httpx.Response:
    request = httpx.Request("GET", url)
    if content is not None:
        return httpx.Response(200, content=content, request=request)
    return httpx.Response(200, text=text or "", request=request)


def _client_with_session() -> SEIWebClient:
    c = make_client()
    c._inbox_url = httpx.URL("http://sei.test/sei/controlador.php?acao=procedimento_controlar")
    c._trabalhar_links[_PROTOCOLO] = _TRAB_URL
    return c


def _fake_login(client: SEIWebClient, calls: list[int] | None = None) -> Any:
    async def fake_login() -> None:
        if calls is not None:
            calls.append(1)
        client._inbox_url = httpx.URL(
            "http://sei.test/sei/controlador.php?acao=procedimento_controlar"
        )
        client._trabalhar_links[_PROTOCOLO] = _TRAB_URL

    return fake_login


class TestGerarArquivoProcessoHappyPath:
    def test_returns_the_downloaded_bytes(self) -> None:
        client = _client_with_session()

        async def fake_get(url: str, **_kw: Any) -> httpx.Response:
            if url == _TRAB_URL:
                return _resp(_TRAB_URL, text=_R1_OK)
            if url == _ARVORE_URL:
                return _resp(_ARVORE_URL, text=_R2_OK)
            if url == _FORM_URL:
                return _resp(_FORM_URL, text=_R3_OK)
            if url == _DOWNLOAD_URL:
                return _resp(_DOWNLOAD_URL, content=_R5_OK)
            msg = f"unexpected GET {url}"
            raise AssertionError(msg)

        async def fake_post(url: str, **_kw: Any) -> httpx.Response:
            assert url == _POST_URL
            return _resp(_POST_URL, text=_R4_OK)

        client._http.get = AsyncMock(side_effect=fake_get)  # type: ignore[method-assign]
        client._http.post = AsyncMock(side_effect=fake_post)  # type: ignore[method-assign]

        content = asyncio.run(client._gerar_arquivo_processo(_PROTOCOLO, "procedimento_gerar_pdf"))
        assert content == _R5_OK


class TestGerarArquivoProcessoSessionExpiry:
    def test_expiry_between_step1_and_step2_self_heals(self) -> None:
        """Session expires right after step 1 (r2 comes back as the login page).

        Before this fix, only step 1's response was checked — an expired
        session surfacing at step 2 (or later) fell straight through to
        SEIParseError with a misleading "link not found" message. This must
        now relogin and retry the whole flow instead.
        """
        client = _client_with_session()
        calls = {"arvore": 0}

        async def fake_get(url: str, **_kw: Any) -> httpx.Response:
            if url == _TRAB_URL:
                return _resp(_TRAB_URL, text=_R1_OK)
            if url == _ARVORE_URL:
                calls["arvore"] += 1
                if calls["arvore"] == 1:
                    return _resp(_ARVORE_URL, text=_LOGIN_PAGE)
                return _resp(_ARVORE_URL, text=_R2_OK)
            if url == _FORM_URL:
                return _resp(_FORM_URL, text=_R3_OK)
            if url == _DOWNLOAD_URL:
                return _resp(_DOWNLOAD_URL, content=_R5_OK)
            msg = f"unexpected GET {url}"
            raise AssertionError(msg)

        async def fake_post(url: str, **_kw: Any) -> httpx.Response:
            assert url == _POST_URL
            return _resp(_POST_URL, text=_R4_OK)

        client._http.get = AsyncMock(side_effect=fake_get)  # type: ignore[method-assign]
        client._http.post = AsyncMock(side_effect=fake_post)  # type: ignore[method-assign]

        relogin_calls: list[int] = []
        client.login = _fake_login(client, relogin_calls)  # type: ignore[method-assign]

        content = asyncio.run(client._gerar_arquivo_processo(_PROTOCOLO, "procedimento_gerar_pdf"))
        assert content == _R5_OK
        assert len(relogin_calls) == 1
        assert calls["arvore"] == 2  # first hit (expired) + retry (fresh)

    def test_expiry_persisting_after_relogin_raises_typed_error(self) -> None:
        """If the session is STILL expired after one relogin, fail clearly
        (SEIAuthError) instead of looping forever or raising a confusing
        SEIParseError from downstream HTML parsing."""
        client = _client_with_session()

        async def fake_get(url: str, **_kw: Any) -> httpx.Response:
            if url == _TRAB_URL:
                return _resp(_TRAB_URL, text=_LOGIN_PAGE)
            msg = f"unexpected GET {url}"
            raise AssertionError(msg)

        client._http.get = AsyncMock(side_effect=fake_get)  # type: ignore[method-assign]
        client.login = _fake_login(client)  # type: ignore[method-assign]

        with pytest.raises(SEIAuthError, match="Sessão SEI expirou"):
            asyncio.run(client._gerar_arquivo_processo(_PROTOCOLO, "procedimento_gerar_pdf"))

    def test_expiry_after_download_step_also_self_heals(self) -> None:
        """Step 5 (the actual file download) returning the login page is the
        least likely spot for a real PDF/ZIP response, but must still be
        caught rather than silently returned as if it were file content."""
        client = _client_with_session()
        calls = {"download": 0}

        async def fake_get(url: str, **_kw: Any) -> httpx.Response:
            if url == _TRAB_URL:
                return _resp(_TRAB_URL, text=_R1_OK)
            if url == _ARVORE_URL:
                return _resp(_ARVORE_URL, text=_R2_OK)
            if url == _FORM_URL:
                return _resp(_FORM_URL, text=_R3_OK)
            if url == _DOWNLOAD_URL:
                calls["download"] += 1
                if calls["download"] == 1:
                    return _resp(_DOWNLOAD_URL, text=_LOGIN_PAGE)
                return _resp(_DOWNLOAD_URL, content=_R5_OK)
            msg = f"unexpected GET {url}"
            raise AssertionError(msg)

        async def fake_post(url: str, **_kw: Any) -> httpx.Response:
            assert url == _POST_URL
            return _resp(_POST_URL, text=_R4_OK)

        client._http.get = AsyncMock(side_effect=fake_get)  # type: ignore[method-assign]
        client._http.post = AsyncMock(side_effect=fake_post)  # type: ignore[method-assign]
        client.login = _fake_login(client)  # type: ignore[method-assign]

        content = asyncio.run(client._gerar_arquivo_processo(_PROTOCOLO, "procedimento_gerar_pdf"))
        assert content == _R5_OK
        assert calls["download"] == 2


class TestGerarArquivoProcessoStillRaisesOnGenuineParseFailures:
    def test_missing_iframe_raises_parse_error_not_auth_error(self) -> None:
        """A page that's neither the expected frameset nor a login page is a
        real parse failure — must not be swallowed or misreported as auth."""
        client = _client_with_session()

        async def fake_get(url: str, **_kw: Any) -> httpx.Response:
            if url == _TRAB_URL:
                return _resp(_TRAB_URL, text="<html><body>unexpected page</body></html>")
            msg = f"unexpected GET {url}"
            raise AssertionError(msg)

        client._http.get = AsyncMock(side_effect=fake_get)  # type: ignore[method-assign]

        with pytest.raises(SEIParseError, match="ifrArvore"):
            asyncio.run(client._gerar_arquivo_processo(_PROTOCOLO, "procedimento_gerar_pdf"))
