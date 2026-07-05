"""Testes de regressão para a extração do link 'Incluir Documento'
(`documento_escolher_tipo`) na árvore do processo.

Bug real (2026-07): `_obter_soup_documento_receber` (usado por
`pesquisar_tipos_documento_web` e `pesquisar_tipos_conferencia_web`) fazia
`soup.find_all("a", href=re.compile("documento_escolher_tipo"))` direto sobre
o HTML bruto da árvore — mas esse link nunca existe como `<a>` de verdade no
DOM: ele vem embutido como HTML escapado dentro de uma string JS
(`Nos[0].acoes = '<a href="...">...'`). Isso fazia a extração falhar
*sempre*, para todo processo — confirmado ao vivo contra a instância real
(15/15 processos testados falhavam com esse erro).

`criar_documento_interno_web` já tinha a correção certa (extrair
`Nos[0].acoes` da string JS antes de fazer parse com BeautifulSoup), mas essa
lógica não era compartilhada — daí o bug em `_obter_soup_documento_receber`.
A correção extrai a lógica para o helper de módulo
`_extrair_incluir_documento_href`, usado por ambos.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from todos.backends.models import SEIWebClientConfig
from todos.exceptions import SEIParseError
from todos.sei_web_client import SEIWebClient, _extrair_incluir_documento_href


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


class TestExtrairIncluirDocumentoHref:
    """Cobre o helper compartilhado em isolamento — sem HTTP."""

    def test_link_apenas_dentro_de_nos0_acoes_aspas_simples(self) -> None:
        """Caso do bug real: não há <a href="...documento_escolher_tipo..."> solto
        no DOM — o link só existe escapado dentro de `Nos[0].acoes = '...'`."""
        html_arvore = (
            "<html><body><script>"
            "Nos[0] = new infraArvoreNo('proc', 'p1', null, '', '', 'P', '', '');"
            "Nos[0].acoes = '<a href=\"controlador.php?acao=documento_escolher_tipo"
            "&id_procedimento=123&infra_hash=abc123\">Incluir Documento</a>';"
            "</script></body></html>"
        )
        href = _extrair_incluir_documento_href(html_arvore)
        assert href == (
            "controlador.php?acao=documento_escolher_tipo&id_procedimento=123&infra_hash=abc123"
        )

    def test_link_apenas_dentro_de_nos0_acoes_aspas_duplas(self) -> None:
        html_arvore = (
            'Nos[0].acoes = "<a href=\\"controlador.php?acao=documento_escolher_tipo'
            '&id_procedimento=456&infra_hash=def456\\">Incluir</a>";'
        )
        href = _extrair_incluir_documento_href(html_arvore)
        assert href == (
            "controlador.php?acao=documento_escolher_tipo&id_procedimento=456&infra_hash=def456"
        )

    def test_entidade_amp_escapada_e_decodificada(self) -> None:
        """`&amp;` (comum dentro de atributo HTML) deve virar `&` no resultado."""
        html_arvore = (
            "Nos[0].acoes = '<a href=\"controlador.php?acao=documento_escolher_tipo"
            "&amp;id_procedimento=1&amp;infra_hash=abc\">Incluir</a>';"
        )
        href = _extrair_incluir_documento_href(html_arvore)
        assert (
            href == "controlador.php?acao=documento_escolher_tipo&id_procedimento=1&infra_hash=abc"
        )

    def test_fallback_imagem_incluir_com_link_pai(self) -> None:
        """Algumas instâncias renderizam a ação como <img title="Incluir Documento">
        dentro de um <a>, sem que o próprio <a> tenha texto 'documento_escolher_tipo'
        visível de outra forma."""
        html_arvore = (
            "Nos[0].acoes = '<a href=\"controlador.php?acao=documento_escolher_tipo"
            '&infra_hash=xyz"><img title="Incluir Documento" src="infra_icone.svg"/></a>\';'
        )
        href = _extrair_incluir_documento_href(html_arvore)
        assert href == "controlador.php?acao=documento_escolher_tipo&infra_hash=xyz"

    def test_fallback_regex_bruto_sem_nos0_acoes(self) -> None:
        """Sem `Nos[0].acoes` algum (ex.: variante de instância), o link solto em
        outro script ainda é encontrado via regex bruto no HTML inteiro."""
        html_arvore = (
            "<script>outraCoisa();</script>"
            "controlador.php?acao=documento_escolher_tipo&id_procedimento=9&infra_hash=999"
        )
        href = _extrair_incluir_documento_href(html_arvore)
        assert (
            href == "controlador.php?acao=documento_escolher_tipo&id_procedimento=9&infra_hash=999"
        )

    def test_nenhum_link_encontrado_retorna_none(self) -> None:
        assert _extrair_incluir_documento_href("<html><body>nada aqui</body></html>") is None

    def test_link_solto_como_a_normal_tambem_funciona(self) -> None:
        """Caso hipotético/defensivo: se algum dia o link vier como <a> de
        verdade no DOM (não só dentro de Nos[0].acoes), ainda deve ser achado
        (fallback: soup_acoes usa html_arvore inteiro quando acoes_html vazio)."""
        html_arvore = (
            '<a href="controlador.php?acao=documento_escolher_tipo&infra_hash=abc">Incluir</a>'
        )
        href = _extrair_incluir_documento_href(html_arvore)
        assert href == "controlador.php?acao=documento_escolher_tipo&infra_hash=abc"


def _html_arvore_com_acoes(infra_hash: str = "abc123") -> str:
    return (
        "<html><body><script>"
        "Nos[0] = new infraArvoreNo('proc', 'p1', null, '', '', 'P', '', '');"
        "Nos[0].acoes = '<a href=\"controlador.php?acao=documento_escolher_tipo"
        f"&id_procedimento=1&infra_hash={infra_hash}\">Incluir Documento</a>';"
        "</script></body></html>"
    )


async def _run_obter_soup_documento_receber(html_arvore: str) -> object:
    client = make_client()
    escolher_page = (
        '<form id="frmDocumentoEscolherTipo" action="controlador.php?acao=documento_escolher_tipo">'
        '<input type="hidden" name="hdnFoo" value="1">'
        "</form>"
    )
    documento_receber_page = '<select name="selSerie"><option value="1">Ofício</option></select>'
    with (
        patch.object(
            client,
            "_arvore_do_processo",
            AsyncMock(return_value=(html_arvore, "http://sei.test/arvore")),
        ),
        patch.object(client._http, "get", AsyncMock(return_value=_resp(content=escolher_page))),
        patch.object(
            client._http, "post", AsyncMock(return_value=_resp(content=documento_receber_page))
        ),
    ):
        return await client._obter_soup_documento_receber("0016.004284/2026-81")


class TestObterSoupDocumentoReceber:
    """Regressão do bug real: antes da correção, esse método SEMPRE levantava
    SEIParseError("Link documento_escolher_tipo não encontrado..."), porque
    fazia find_all("a", ...) direto sobre a árvore bruta, sem antes extrair
    a string Nos[0].acoes."""

    def test_encontra_link_quando_so_existe_dentro_de_nos0_acoes(self) -> None:
        soup = asyncio.run(_run_obter_soup_documento_receber(_html_arvore_com_acoes()))
        sel = soup.find("select", {"name": "selSerie"})
        assert sel is not None
        assert sel.find("option")["value"] == "1"

    def test_levanta_erro_claro_quando_realmente_nao_ha_link(self) -> None:
        with pytest.raises(SEIParseError, match="documento_escolher_tipo"):
            asyncio.run(_run_obter_soup_documento_receber("<html><body>sem acoes</body></html>"))
