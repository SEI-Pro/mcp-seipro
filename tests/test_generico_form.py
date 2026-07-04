"""Testes para inspeção e submissão genérica de formulário (RFC 0020)."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from bs4 import BeautifulSoup

from todos.backends.models import SEIWebClientConfig
from todos.exceptions import SEIParseError
from todos.sei_web_client import SEIWebClient, _descobrir_acoes, _parse_form_info


def make_client() -> SEIWebClient:
    return SEIWebClient(
        SEIWebClientConfig(sei_web_url="http://sei.test", sei_usuario="u", sei_senha="p")
    )


def _resp(*, content: str, url: str = "http://sei.test/x") -> httpx.Response:
    return httpx.Response(
        200,
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
        info = _parse_form_info(form)
        assert info["id"] == "frmX"
        assert info["action"] == "controlador.php?acao=x&infra_hash=abc"
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
        info = _parse_form_info(form)
        campos = {c["nome"]: c for c in info["campos"]}
        assert campos["txaObs"]["tipo"] == "textarea"
        assert campos["txaObs"]["valor_atual"] == "texto atual"
        assert campos["selTipo"]["valor_atual"] == "2"
        assert campos["selTipo"]["opcoes"] == [
            {"valor": "1", "texto": "Um"},
            {"valor": "2", "texto": "Dois"},
        ]

    def test_extrai_botoes_com_onclick_funcao(self) -> None:
        html = (
            '<form id="frmZ">'
            '<button type="button" name="btnExcluir" onclick="acaoExcluir(123)">Excluir</button>'
            '<input type="submit" name="sbmSalvar" value="Salvar">'
            "</form>"
        )
        form = BeautifulSoup(html, "html.parser").find("form")
        info = _parse_form_info(form)
        botoes = {b["nome"]: b for b in info["botoes"]}
        assert botoes["btnExcluir"]["onclick_funcao"] == "acaoExcluir"
        assert botoes["sbmSalvar"]["tipo"] == "submit"
        assert botoes["sbmSalvar"]["onclick_funcao"] is None


class TestDescobrirAcoes:
    def test_acao_via_href_direto(self) -> None:
        html = '<a href="controlador.php?acao=bloco_excluir&infra_hash=abc">Excluir</a>'
        acoes = _descobrir_acoes(html)
        assert acoes == [
            {
                "nome_acao": "bloco_excluir",
                "origem": "href",
                "url": "controlador.php?acao=bloco_excluir&infra_hash=abc",
            }
        ]

    def test_acao_via_variavel_js(self) -> None:
        html = (
            "<script>"
            'var linkEditarConteudo = "controlador.php?acao=editor_montar&infra_hash=xyz";'
            "</script>"
        )
        acoes = _descobrir_acoes(html)
        assert len(acoes) == 1
        assert acoes[0]["nome_acao"] == "editor_montar"
        assert acoes[0]["origem"] == "js_variable"
        assert acoes[0]["variavel"] == "linkEditarConteudo"

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
        acoes = _descobrir_acoes(html)
        achou = next(a for a in acoes if a["nome_acao"] == "bloco_excluir")
        assert achou["origem"] == "js_function"
        assert achou["funcao"] == "acaoExcluir"
        assert achou["url"] == "controlador.php?acao=bloco_excluir&infra_hash=deadbeef"

    def test_nao_duplica_mesma_acao_href(self) -> None:
        html = (
            '<a href="controlador.php?acao=x&infra_hash=1">A</a>'
            '<a href="controlador.php?acao=x&infra_hash=1">B (mesmo link)</a>'
        )
        acoes = _descobrir_acoes(html)
        assert len(acoes) == 1


class TestInspecionarPaginaWeb:
    def test_devolve_forms_e_acoes_sem_post(self) -> None:
        client = make_client()
        html = (
            '<form id="frmX" action="controlador.php?acao=x">'
            '<input type="text" name="campo" value="v">'
            "</form>"
            '<a href="controlador.php?acao=y&infra_hash=abc">Y</a>'
        )

        async def _run() -> dict:
            with (
                patch.object(client, "ensure_authenticated", AsyncMock()),
                patch.object(client._http, "get", AsyncMock(return_value=_resp(content=html))),
                patch.object(client._http, "post") as mock_post,
            ):
                result = await client.inspecionar_pagina_web("http://sei.test/pagina")
                mock_post.assert_not_called()
            return result

        result = asyncio.run(_run())
        assert result["formularios"][0]["id"] == "frmX"
        assert any(a["nome_acao"] == "y" for a in result["acoes_descobertas"])
        assert "raw_html" not in result

    def test_incluir_raw_inclui_html_bruto(self) -> None:
        client = make_client()
        html = "<form id='frmX'></form>"

        async def _run() -> dict:
            with (
                patch.object(client, "ensure_authenticated", AsyncMock()),
                patch.object(client._http, "get", AsyncMock(return_value=_resp(content=html))),
            ):
                return await client.inspecionar_pagina_web(
                    "http://sei.test/pagina", incluir_raw=True
                )

        result = asyncio.run(_run())
        assert "frmX" in result["raw_html"]


class TestSubmeterFormWeb:
    def test_sem_url_destino_usa_action_do_proprio_form(self) -> None:
        client = make_client()
        html = '<form id="frmX" action="controlador.php?acao=proprio"></form>'
        captured: dict[str, object] = {}

        async def _fake_post(url: str, **_kwargs: object) -> httpx.Response:
            captured["url"] = url
            return _resp(content="<html>ok</html>")

        async def _run() -> dict:
            with (
                patch.object(client, "ensure_authenticated", AsyncMock()),
                patch.object(client._http, "get", AsyncMock(return_value=_resp(content=html))),
                patch.object(client._http, "post", _fake_post),
            ):
                return await client.submeter_form_web(
                    "http://sei.test/pagina", "frmX", {"campo": "valor"}
                )

        result = asyncio.run(_run())
        assert "acao=proprio" in captured["url"]
        assert result["status_code"] == 200

    def test_com_url_destino_ignora_action_do_form(self) -> None:
        client = make_client()
        html = '<form id="frmX" action="controlador.php?acao=listagem"></form>'
        captured: dict[str, object] = {}

        async def _fake_post(url: str, **_kwargs: object) -> httpx.Response:
            captured["url"] = url
            return _resp(content="<html>ok</html>")

        async def _run() -> dict:
            with (
                patch.object(client, "ensure_authenticated", AsyncMock()),
                patch.object(client._http, "get", AsyncMock(return_value=_resp(content=html))),
                patch.object(client._http, "post", _fake_post),
            ):
                return await client.submeter_form_web(
                    "http://sei.test/pagina",
                    "frmX",
                    {"hdnInfraItemId": "123"},
                    "http://sei.test/controlador.php?acao=excluir",
                )

        asyncio.run(_run())
        assert "acao=excluir" in captured["url"]
        assert "acao=listagem" not in captured["url"]

    def test_form_nao_encontrado_levanta_erro(self) -> None:
        client = make_client()
        html = '<form id="frmOutro"></form>'

        async def _run() -> None:
            with (
                patch.object(client, "ensure_authenticated", AsyncMock()),
                patch.object(client._http, "get", AsyncMock(return_value=_resp(content=html))),
            ):
                await client.submeter_form_web("http://sei.test/pagina", "frmX", {})

        with pytest.raises(SEIParseError, match="frmX"):
            asyncio.run(_run())

    def test_rebusca_pagina_em_vez_de_reusar_form_antigo(self) -> None:
        """A cada chamada, `url_pagina` é buscada de novo — não reusa hidden fields
        de uma cópia anterior, que podem estar stale (hash de uso único)."""
        client = make_client()
        get_calls: list[str] = []

        async def _fake_get(url: str, **_kwargs: object) -> httpx.Response:
            get_calls.append(url)
            return _resp(content='<form id="frmX" action="controlador.php?acao=x"></form>')

        async def _run() -> None:
            with (
                patch.object(client, "ensure_authenticated", AsyncMock()),
                patch.object(client._http, "get", _fake_get),
                patch.object(
                    client._http, "post", AsyncMock(return_value=_resp(content="<html/>"))
                ),
            ):
                await client.submeter_form_web("http://sei.test/pagina", "frmX", {})
                await client.submeter_form_web("http://sei.test/pagina", "frmX", {})

        asyncio.run(_run())
        assert len(get_calls) == 2
