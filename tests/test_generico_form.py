"""Testes para inspeção e submissão genérica de formulário (RFC 0020)."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from bs4 import BeautifulSoup

from todos.backends.models import SEIWebClientConfig
from todos.exceptions import SEIParseError, SEIValidationError
from todos.sei_web_client import SEIWebClient, _descobrir_acoes, _parse_form_info

_BASE = "http://sei.test/sei/controlador.php?acao=bloco_assinatura_listar"


def make_client() -> SEIWebClient:
    return SEIWebClient(
        SEIWebClientConfig(sei_web_url="http://sei.test", sei_usuario="u", sei_senha="p")
    )


def _resp(*, content: str, url: str = _BASE, status: int = 200) -> httpx.Response:
    return httpx.Response(
        status,
        content=content.encode("iso-8859-1", errors="replace"),
        headers={"content-type": "text/html; charset=iso-8859-1"},
        request=httpx.Request("GET", url),
    )


class TestParseFormInfo:
    def test_extrai_campos_ocultos_e_visiveis(self) -> None:
        html = (
            '<form id="frmX" action="controlador.php?acao=x&infra_hash=abc">'
            '<input type="hidden" name="infra_hash" value="abc">'
            '<input type="text" name="txtNome" value="atual">'
            "</form>"
        )
        form = BeautifulSoup(html, "html.parser").find("form")
        info = _parse_form_info(form, _BASE)
        assert info["id"] == "frmX"
        assert info["action"] == "http://sei.test/sei/controlador.php?acao=x&infra_hash=abc"
        assert info["escondidos"] == {"infra_hash": "abc"}
        assert info["campos"] == [{"nome": "txtNome", "tipo": "text", "valor_atual": "atual"}]

    def test_extrai_textarea_e_select(self) -> None:
        html = (
            '<form id="frmY">'
            '<textarea name="txaObs">texto atual</textarea>'
            '<select name="selTipo">'
            '<option value="1">Um</option>'
            '<option value="2" selected>Dois</option>'
            "</select>"
            "</form>"
        )
        form = BeautifulSoup(html, "html.parser").find("form")
        info = _parse_form_info(form, _BASE)
        campos = {c["nome"]: c for c in info["campos"]}
        assert campos["txaObs"]["tipo"] == "textarea"
        assert campos["txaObs"]["valor_atual"] == "texto atual"
        assert campos["selTipo"]["valor_atual"] == "2"
        assert campos["selTipo"]["opcoes"] == [
            {"valor": "1", "texto": "Um"},
            {"valor": "2", "texto": "Dois"},
        ]

    def test_select_sem_selected_assume_primeira_opcao(self) -> None:
        """HTML padrão (e o browser) tratam a 1a <option> como selecionada
        quando nenhuma tem `selected` — não "nenhuma" (bug real de code review)."""
        html = (
            '<form id="frmY">'
            '<select name="selTipo">'
            '<option value="1">Um</option>'
            '<option value="2">Dois</option>'
            "</select>"
            "</form>"
        )
        form = BeautifulSoup(html, "html.parser").find("form")
        info = _parse_form_info(form, _BASE)
        campos = {c["nome"]: c for c in info["campos"]}
        assert campos["selTipo"]["valor_atual"] == "1"

    def test_extrai_botoes_com_onclick_funcao(self) -> None:
        html = (
            '<form id="frmZ">'
            '<button type="button" name="btnExcluir" onclick="acaoExcluir(123)">Excluir</button>'
            '<input type="submit" name="sbmSalvar" value="Salvar">'
            "</form>"
        )
        form = BeautifulSoup(html, "html.parser").find("form")
        info = _parse_form_info(form, _BASE)
        botoes = {b["nome"]: b for b in info["botoes"]}
        assert botoes["btnExcluir"]["onclick_funcao"] == "acaoExcluir"
        assert botoes["sbmSalvar"]["tipo"] == "submit"
        assert botoes["sbmSalvar"]["onclick_funcao"] is None


class TestDescobrirAcoes:
    def test_acao_via_href_direto(self) -> None:
        html = '<a href="controlador.php?acao=bloco_excluir&infra_hash=abc">Excluir</a>'
        acoes = _descobrir_acoes(html, _BASE)
        assert acoes == [
            {
                "nome_acao": "bloco_excluir",
                "origem": "href",
                "url": "http://sei.test/sei/controlador.php?acao=bloco_excluir&infra_hash=abc",
            }
        ]

    def test_acao_via_variavel_js(self) -> None:
        html = (
            "<script>"
            'var linkEditarConteudo = "controlador.php?acao=editor_montar&infra_hash=xyz";'
            "</script>"
        )
        acoes = _descobrir_acoes(html, _BASE)
        assert len(acoes) == 1
        assert acoes[0]["nome_acao"] == "editor_montar"
        assert acoes[0]["origem"] == "js_variable"
        assert acoes[0]["variavel"] == "linkEditarConteudo"
        assert acoes[0]["url"].startswith("http://sei.test/")

    def test_acao_via_funcao_js(self) -> None:
        html = (
            "<script>"
            "function acaoExcluir(id){"
            "document.getElementById('frmBlocoLista').action="
            "'controlador.php?acao=bloco_excluir&infra_hash=deadbeef';"
            "document.getElementById('frmBlocoLista').submit();"
            "}"
            "function acaoOutraCoisa(id){ return id; }"
            "</script>"
        )
        acoes = _descobrir_acoes(html, _BASE)
        achou = next(a for a in acoes if a["nome_acao"] == "bloco_excluir")
        assert achou["origem"] == "js_function"
        assert achou["funcao"] == "acaoExcluir"
        assert (
            achou["url"]
            == "http://sei.test/sei/controlador.php?acao=bloco_excluir&infra_hash=deadbeef"
        )

    def test_nao_duplica_mesma_acao_href(self) -> None:
        html = (
            '<a href="controlador.php?acao=x&infra_hash=1">A</a>'
            '<a href="controlador.php?acao=x&infra_hash=1">B (mesmo link)</a>'
        )
        acoes = _descobrir_acoes(html, _BASE)
        assert len(acoes) == 1


def _patch_send(client: SEIWebClient, handler):
    """Substitui `client._http.send` por `handler(request) -> httpx.Response`."""
    return patch.object(client._http, "send", AsyncMock(side_effect=handler))


class TestValidarMesmaOrigem:
    def test_aceita_url_mesma_origem(self) -> None:
        client = make_client()
        assert client._validar_mesma_origem("controlador.php?acao=x").startswith("http://sei.test/")

    def test_rejeita_url_externa(self) -> None:
        client = make_client()
        with pytest.raises(SEIValidationError):
            client._validar_mesma_origem("http://attacker.evil/roubar")

    def test_rejeita_scheme_diferente(self) -> None:
        client = make_client()
        with pytest.raises(SEIValidationError):
            client._validar_mesma_origem("https://sei.test/mesmo-host-scheme-diferente")


class TestEnviarMesmaOrigem:
    def test_rejeita_request_inicial_externa(self) -> None:
        client = make_client()

        async def _run() -> None:
            req = client._http.build_request("GET", "http://attacker.evil/x")
            await client._enviar_mesma_origem(req)

        with pytest.raises(SEIValidationError):
            asyncio.run(_run())

    def test_segue_redirect_mesma_origem(self) -> None:
        client = make_client()

        async def _handler(request: httpx.Request, **_kwargs: object) -> httpx.Response:
            if str(request.url) == "http://sei.test/sei/a":
                redirect = _resp(content="", url="http://sei.test/sei/a", status=302)
                redirect.headers["location"] = "http://sei.test/sei/b"
                redirect.next_request = client._http.build_request("GET", "http://sei.test/sei/b")
                return redirect
            return _resp(content="destino final", url=str(request.url))

        async def _run() -> httpx.Response:
            with _patch_send(client, _handler):
                req = client._http.build_request("GET", "http://sei.test/sei/a")
                return await client._enviar_mesma_origem(req)

        resposta = asyncio.run(_run())
        assert resposta.text == "destino final"

    def test_rejeita_redirect_para_origem_externa(self) -> None:
        client = make_client()

        async def _handler(request: httpx.Request, **_kwargs: object) -> httpx.Response:
            redirect = _resp(content="", url=str(request.url), status=302)
            redirect.next_request = client._http.build_request("GET", "http://attacker.evil/roubar")
            return redirect

        async def _run() -> None:
            with _patch_send(client, _handler):
                req = client._http.build_request("GET", "http://sei.test/sei/a")
                await client._enviar_mesma_origem(req)

        with pytest.raises(SEIValidationError):
            asyncio.run(_run())


class TestInspecionarPaginaWeb:
    def test_devolve_forms_e_acoes_sem_post(self) -> None:
        client = make_client()
        html = (
            '<form id="frmX" action="controlador.php?acao=x">'
            '<input type="text" name="campo" value="v">'
            "</form>"
            '<a href="controlador.php?acao=y&infra_hash=abc">Y</a>'
        )

        async def _handler(request: httpx.Request, **_kwargs: object) -> httpx.Response:
            assert request.method == "GET"
            return _resp(content=html, url=str(request.url))

        async def _run() -> dict:
            with (
                patch.object(client, "ensure_authenticated", AsyncMock()),
                _patch_send(client, _handler),
            ):
                return await client.inspecionar_pagina_web("http://sei.test/sei/pagina")

        result = asyncio.run(_run())
        assert result["formularios"][0]["id"] == "frmX"
        assert any(a["nome_acao"] == "y" for a in result["acoes_descobertas"])
        assert "raw_html" not in result

    def test_incluir_raw_inclui_html_bruto(self) -> None:
        client = make_client()
        html = "<form id='frmX'></form>"

        async def _handler(request: httpx.Request, **_kwargs: object) -> httpx.Response:
            return _resp(content=html, url=str(request.url))

        async def _run() -> dict:
            with (
                patch.object(client, "ensure_authenticated", AsyncMock()),
                _patch_send(client, _handler),
            ):
                return await client.inspecionar_pagina_web(
                    "http://sei.test/sei/pagina", incluir_raw=True
                )

        result = asyncio.run(_run())
        assert "frmX" in result["raw_html"]

    def test_rejeita_url_externa_sem_fazer_request(self) -> None:
        client = make_client()

        async def _run() -> None:
            with (
                patch.object(client, "ensure_authenticated", AsyncMock()),
                patch.object(client._http, "send", AsyncMock()) as mock_send,
            ):
                await client.inspecionar_pagina_web("http://attacker.evil/x")
                mock_send.assert_not_called()

        with pytest.raises(SEIValidationError):
            asyncio.run(_run())


class TestSubmeterFormWeb:
    def test_sem_url_destino_usa_action_do_proprio_form(self) -> None:
        client = make_client()
        html = '<form id="frmX" action="controlador.php?acao=proprio"></form>'
        captured: dict[str, object] = {}

        async def _handler(request: httpx.Request, **_kwargs: object) -> httpx.Response:
            if request.method == "POST":
                captured["url"] = str(request.url)
                return _resp(content="<html>ok</html>", url=str(request.url))
            return _resp(content=html, url=str(request.url))

        async def _run() -> dict:
            with (
                patch.object(client, "ensure_authenticated", AsyncMock()),
                _patch_send(client, _handler),
            ):
                return await client.submeter_form_web(
                    "http://sei.test/sei/pagina", "frmX", {"campo": "valor"}
                )

        result = asyncio.run(_run())
        assert "acao=proprio" in captured["url"]
        assert result["status_code"] == 200

    def test_com_url_destino_ignora_action_do_form(self) -> None:
        client = make_client()
        html = '<form id="frmX" action="controlador.php?acao=listagem"></form>'
        captured: dict[str, object] = {}

        async def _handler(request: httpx.Request, **_kwargs: object) -> httpx.Response:
            if request.method == "POST":
                captured["url"] = str(request.url)
                return _resp(content="<html>ok</html>", url=str(request.url))
            return _resp(content=html, url=str(request.url))

        async def _run() -> dict:
            with (
                patch.object(client, "ensure_authenticated", AsyncMock()),
                _patch_send(client, _handler),
            ):
                return await client.submeter_form_web(
                    "http://sei.test/sei/pagina",
                    "frmX",
                    {"hdnInfraItemId": "123"},
                    "http://sei.test/sei/controlador.php?acao=excluir",
                )

        asyncio.run(_run())
        assert "acao=excluir" in captured["url"]
        assert "acao=listagem" not in captured["url"]

    def test_rejeita_url_destino_externa(self) -> None:
        client = make_client()
        html = '<form id="frmX" action="controlador.php?acao=proprio"></form>'

        async def _handler(request: httpx.Request, **_kwargs: object) -> httpx.Response:
            return _resp(content=html, url=str(request.url))

        async def _run() -> None:
            with (
                patch.object(client, "ensure_authenticated", AsyncMock()),
                _patch_send(client, _handler),
            ):
                await client.submeter_form_web(
                    "http://sei.test/sei/pagina",
                    "frmX",
                    {},
                    "http://attacker.evil/roubar",
                )

        with pytest.raises(SEIValidationError):
            asyncio.run(_run())

    def test_form_nao_encontrado_levanta_erro(self) -> None:
        client = make_client()
        html = '<form id="frmOutro"></form>'

        async def _handler(request: httpx.Request, **_kwargs: object) -> httpx.Response:
            return _resp(content=html, url=str(request.url))

        async def _run() -> None:
            with (
                patch.object(client, "ensure_authenticated", AsyncMock()),
                _patch_send(client, _handler),
            ):
                await client.submeter_form_web("http://sei.test/sei/pagina", "frmX", {})

        with pytest.raises(SEIParseError, match="frmX"):
            asyncio.run(_run())

    def test_rebusca_pagina_em_vez_de_reusar_form_antigo(self) -> None:
        """A cada chamada, `url_pagina` é buscada de novo — não reusa hidden fields
        de uma cópia anterior, que podem estar stale (hash de uso único)."""
        client = make_client()
        get_calls: list[str] = []

        async def _handler(request: httpx.Request, **_kwargs: object) -> httpx.Response:
            if request.method == "GET":
                get_calls.append(str(request.url))
                return _resp(
                    content='<form id="frmX" action="controlador.php?acao=x"></form>',
                    url=str(request.url),
                )
            return _resp(content="<html/>", url=str(request.url))

        async def _run() -> None:
            with (
                patch.object(client, "ensure_authenticated", AsyncMock()),
                _patch_send(client, _handler),
            ):
                await client.submeter_form_web("http://sei.test/sei/pagina", "frmX", {})
                await client.submeter_form_web("http://sei.test/sei/pagina", "frmX", {})

        asyncio.run(_run())
        assert len(get_calls) == 2
