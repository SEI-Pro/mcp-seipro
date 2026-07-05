"""Testes para captura de tela via browser real (RFC 0021).

Sem browser real nenhum destes testes — cobrem só (a) a conversão de cookies
httpx -> Playwright e (b) a validação de mesma origem (SSRF), que devem
acontecer ANTES de qualquer tentativa de importar/usar Playwright ou tocar
rede. Um teste manual ao vivo (fora do pytest) contra a instância real do SEI
complementa esta suíte — ver docs/rfc/0021-captura-de-tela-via-browser.md.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from todos import browser_capture
from todos.backends.models import SEIWebClientConfig
from todos.exceptions import SEIError, SEIValidationError
from todos.sei_web_client import SEIWebClient


def make_client() -> SEIWebClient:
    return SEIWebClient(
        SEIWebClientConfig(sei_web_url="http://sei.test", sei_usuario="u", sei_senha="p")
    )


def _cookies_from_set_cookie(set_cookie_header: str, url: str = "http://sei.test/sei/x"):
    """Constrói um `httpx.Cookies` populado a partir de um cabeçalho Set-Cookie cru,
    exatamente como uma sessão httpx real acumularia após o login SIP."""
    cookies = httpx.Cookies()
    request = httpx.Request("GET", url)
    response = httpx.Response(200, headers={"set-cookie": set_cookie_header}, request=request)
    cookies.extract_cookies(response)
    return cookies


class TestHttpxCookiesToPlaywright:
    def test_converte_nome_valor_domain_path(self) -> None:
        cookies = _cookies_from_set_cookie("PHPSESSID=abc123; Path=/sei")
        convertidos = browser_capture._httpx_cookies_to_playwright(cookies)
        assert len(convertidos) == 1
        item = convertidos[0]
        assert item["name"] == "PHPSESSID"
        assert item["value"] == "abc123"
        assert item["domain"] == "sei.test"
        assert item["path"] == "/sei"

    def test_secure_e_httponly_propagados(self) -> None:
        cookies = _cookies_from_set_cookie("PHPSESSID=abc123; Path=/sei; HttpOnly; Secure")
        item = browser_capture._httpx_cookies_to_playwright(cookies)[0]
        assert item["secure"] is True
        assert item["httpOnly"] is True

    def test_sem_secure_httponly_expires_nao_aparecem_no_dict(self) -> None:
        cookies = _cookies_from_set_cookie("foo=bar; Path=/")
        item = browser_capture._httpx_cookies_to_playwright(cookies)[0]
        assert "secure" not in item
        assert "httpOnly" not in item
        assert "expires" not in item

    def test_expires_convertido_para_float(self) -> None:
        cookies = _cookies_from_set_cookie("foo=bar; Max-Age=3600")
        item = browser_capture._httpx_cookies_to_playwright(cookies)[0]
        assert isinstance(item["expires"], float)

    def test_multiplos_cookies_da_mesma_sessao(self) -> None:
        cookies = httpx.Cookies()
        request = httpx.Request("GET", "http://sei.test/sei/x")
        for header in ("PHPSESSID=abc; Path=/sei", "infra_hash=xyz; Path=/sei"):
            response = httpx.Response(200, headers={"set-cookie": header}, request=request)
            cookies.extract_cookies(response)
        convertidos = browser_capture._httpx_cookies_to_playwright(cookies)
        nomes = {item["name"] for item in convertidos}
        assert nomes == {"PHPSESSID", "infra_hash"}


class TestCapturarTelaOrigemESSRF:
    def test_rejeita_url_externa_antes_de_qualquer_coisa(self) -> None:
        """URL fora da instância SEI configurada é rejeitada sem autenticar,
        sem checar Playwright e sem tocar rede — mesmo hardening SSRF de
        `_validar_mesma_origem` usado por inspecionar_pagina_web/submeter_form_web."""
        client = make_client()

        async def _run() -> None:
            with (
                patch.object(client, "ensure_authenticated", AsyncMock()) as mock_auth,
            ):
                await browser_capture.capturar_tela(client, "http://attacker.evil/roubar")
                mock_auth.assert_not_called()

        with pytest.raises(SEIValidationError):
            asyncio.run(_run())

    def test_rejeita_scheme_diferente(self) -> None:
        client = make_client()

        async def _run() -> None:
            await browser_capture.capturar_tela(
                client, "https://sei.test/mesmo-host-scheme-diferente"
            )

        with pytest.raises(SEIValidationError):
            asyncio.run(_run())

    def test_aceita_url_mesma_origem_mas_falha_sem_playwright(self) -> None:
        """URL válida passa a validação de origem; com Playwright indisponível
        (simulado aqui via monkeypatch, independente do que está instalado no
        ambiente de teste), levanta SEIError claro em vez de estourar
        ImportError/AttributeError obscuro."""
        client = make_client()

        async def _run() -> None:
            with (
                patch.object(browser_capture, "_PLAYWRIGHT_AVAILABLE", False),
                patch.object(client, "ensure_authenticated", AsyncMock()) as mock_auth,
            ):
                await browser_capture.capturar_tela(client, "http://sei.test/sei/pagina")
                mock_auth.assert_not_called()

        with pytest.raises(SEIError, match="playwright"):
            asyncio.run(_run())

    def test_rejeita_pagina_apos_redirect_para_fora_da_origem(self) -> None:
        """`url` de entrada é same-origin, mas o browser segue redirects sozinho
        (ao contrário de httpx, sem validação por salto) — se a página final
        (`page.url` pós-goto) tiver saído da origem configurada, a sessão
        autenticada transplantada não deve ser exposta a esse destino: a
        captura deve abortar em vez de fotografar e devolver o conteúdo."""
        client = make_client()
        mock_page = MagicMock()
        mock_page.url = "http://attacker.evil/roubar"
        mock_page.goto = AsyncMock()

        mock_context = AsyncMock()
        mock_context.new_page = AsyncMock(return_value=mock_page)

        mock_browser = AsyncMock()
        mock_browser.new_context = AsyncMock(return_value=mock_context)

        mock_chromium = AsyncMock()
        mock_chromium.launch = AsyncMock(return_value=mock_browser)

        mock_pw_instance = MagicMock()
        mock_pw_instance.chromium = mock_chromium

        mock_pw_cm = MagicMock()
        mock_pw_cm.__aenter__ = AsyncMock(return_value=mock_pw_instance)
        mock_pw_cm.__aexit__ = AsyncMock(return_value=False)

        async def _run() -> None:
            with (
                patch.object(browser_capture, "_PLAYWRIGHT_AVAILABLE", True),
                patch.object(client, "ensure_authenticated", AsyncMock()),
                patch.object(browser_capture, "async_playwright", return_value=mock_pw_cm),
            ):
                await browser_capture.capturar_tela(client, "http://sei.test/sei/pagina")

        with pytest.raises(SEIValidationError):
            asyncio.run(_run())
        mock_browser.close.assert_awaited_once()
