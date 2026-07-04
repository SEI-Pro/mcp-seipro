"""Testes de regressão para bloco de assinatura no backend web.

Cobre os três achados de code review de 2026-07-04 sobre a implementação
inicial (PR fix/bloco-assinatura-web):

1. `DocumentoBloco.protocolo` ficava vazio no backend web (o normalizador só
   aceita `numero` como fonte, o dict retornado só tinha `processo`).
2. Extração do id do bloco recém-criado por regex sobre descrição era frágil
   a HTML escapado e descrições duplicadas — substituída por diff de ids
   (via BeautifulSoup) antes/depois do POST.
3. `incluir_documento_bloco_assinatura` no backend web aceitava múltiplos
   documentos com um único `processo`, contradizendo a semântica de bloco
   (agrupa documentos de processos DIFERENTES) — agora rejeita mais de 1
   documento por chamada com erro claro.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from bs4 import BeautifulSoup

from todos.backends.models import SEIWebClientConfig
from todos.backends.web.blocos import BlocosWeb
from todos.exceptions import SEIParseError, SEIValidationError
from todos.responses import DocumentoBloco
from todos.sei_web_client import SEIWebClient, _extrair_ids_bloco


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


def _linha_bloco(id_bloco: str, descricao: str) -> str:
    return (
        f'<tr><td><a class="ancoraBlocoAberto">{id_bloco}</a></td>'
        f'<td data-label="Descrição">{descricao}</td></tr>'
    )


class TestExtrairIdsBloco:
    def test_extrai_ids_de_multiplas_linhas(self) -> None:
        html = _linha_bloco("111", "Foo") + _linha_bloco("222", "Bar")
        assert _extrair_ids_bloco(html) == {"111", "222"}

    def test_descricoes_duplicadas_nao_confundem_ids(self) -> None:
        """Duas linhas com a MESMA descrição — ids continuam distintos e corretos."""
        html = _linha_bloco("111", "Todos 2026-07-04") + _linha_bloco("222", "Todos 2026-07-04")
        assert _extrair_ids_bloco(html) == {"111", "222"}

    def test_descricao_com_entidade_html_escapada(self) -> None:
        """Descrição com `&` vira `&amp;` no HTML — BeautifulSoup decodifica,
        então o id ainda é extraído normalmente (diferente de um regex sobre
        HTML bruto, que precisaria conhecer a entidade)."""
        html = _linha_bloco("333", "Processos &amp; Documentos")
        assert _extrair_ids_bloco(html) == {"333"}


async def _run_criar(*, antes_html: str, depois_html: str, descricao: str) -> dict:
    client = make_client()
    create_form = (
        '<form id="frmBlocoCadastro" action="controlador.php?acao=bloco_assinatura_cadastrar">'
        '<textarea name="txaDescricao"></textarea>'
        '<button type="submit" name="sbmCadastrarBloco" value="Salvar">Salvar</button>'
        "</form>"
    )
    listar_incluir_link = (
        antes_html
        + '<a href="controlador.php?acao=bloco_assinatura_cadastrar&infra_hash=abc123">Novo</a>'
    )
    with (
        patch.object(client, "ensure_authenticated", AsyncMock()),
        patch.object(
            client, "_obter_link_toolbar", AsyncMock(return_value="http://sei.test/listar")
        ),
        patch.object(
            client._http,
            "get",
            AsyncMock(
                side_effect=[
                    _resp(content=listar_incluir_link),
                    _resp(content=create_form),
                ]
            ),
        ),
        patch.object(client._http, "post", AsyncMock(return_value=_resp(content=depois_html))),
    ):
        return await client.criar_bloco_assinatura_web(descricao)


class TestCriarBlocoAssinaturaWebIdExtraction:
    """Cobre a extração do id do bloco recém-criado via diff antes/depois."""

    def test_id_unico_novo_e_identificado_corretamente(self) -> None:
        antes = _linha_bloco("100", "Bloco Antigo")
        depois = antes + _linha_bloco("200", "Meu Bloco Novo")
        result = asyncio.run(
            _run_criar(antes_html=antes, depois_html=depois, descricao="Meu Bloco Novo")
        )
        assert result == {"ok": True, "idBloco": "200", "descricao": "Meu Bloco Novo"}

    def test_descricao_duplicada_preexistente_nao_confunde_extracao(self) -> None:
        """Já existe um bloco com a MESMA descrição antes de criar — o diff de
        ids (não uma busca por texto da descrição) garante que o id certo
        (o novo, não o antigo) seja retornado."""
        antes = _linha_bloco("100", "Todos 2026-07-04")
        depois = antes + _linha_bloco("999", "Todos 2026-07-04")
        result = asyncio.run(
            _run_criar(antes_html=antes, depois_html=depois, descricao="Todos 2026-07-04")
        )
        assert result["idBloco"] == "999"

    def test_nenhum_id_novo_levanta_erro_em_vez_de_ok_com_id_vazio(self) -> None:
        """Se o POST não criou nada detectável (0 ids novos), deve levantar
        erro claro — não devolver ok=True com idBloco=''."""
        antes = _linha_bloco("100", "Bloco Antigo")
        with pytest.raises(SEIParseError, match="0 candidato"):
            asyncio.run(_run_criar(antes_html=antes, depois_html=antes, descricao="Nunca Criado"))

    def test_multiplos_ids_novos_levanta_erro_ambiguo(self) -> None:
        """Se por algum motivo aparecerem 2+ ids novos, é ambíguo — erro, não
        um chute silencioso do primeiro/último."""
        antes = _linha_bloco("100", "Bloco Antigo")
        depois = antes + _linha_bloco("201", "X") + _linha_bloco("202", "Y")
        with pytest.raises(SEIParseError, match="2 candidato"):
            asyncio.run(_run_criar(antes_html=antes, depois_html=depois, descricao="X"))


class TestDocumentoBlocoProtocoloSerialization:
    """O backend web devolve `processo`/`numeroSei`, não `numero` — o
    normalizador do modelo só aceita `numero` como fonte de `protocolo`.
    """

    def test_protocolo_populado_a_partir_do_campo_numero(self) -> None:
        doc = DocumentoBloco.model_validate(
            {
                "idDocumento": "76858997",
                "numeroSei": "74078183",
                "tipo": "Ofício",
                "processo": "0020.010296/2026-86",
                "numero": "0020.010296/2026-86",
            }
        )
        assert doc.id == "76858997"
        assert doc.protocolo == "0020.010296/2026-86"

    def test_sem_numero_protocolo_fica_vazio(self) -> None:
        """Documenta o bug original: sem o campo `numero`, `protocolo` não é
        preenchido a partir de `processo` (o normalizador não conhece esse
        nome) — regressão de sanidade caso o normalizador mude no futuro."""
        doc = DocumentoBloco.model_validate(
            {
                "idDocumento": "76858997",
                "numeroSei": "74078183",
                "tipo": "Ofício",
                "processo": "0020.010296/2026-86",
            }
        )
        assert doc.protocolo == ""


class TestIncluirDocumentoBlocoAssinaturaWebRejeitaMultiplosProcessos:
    """Um bloco agrupa documentos de processos diferentes — um único
    `processo` não serve pra resolver múltiplos `documentos`."""

    def test_dois_documentos_levanta_erro_sem_chamar_o_scraper(self) -> None:
        web_mixin = BlocosWeb()
        web_mixin._web = AsyncMock()  # type: ignore[attr-defined]

        async def _run() -> None:
            with pytest.raises(SEIValidationError, match="1 documento por chamada"):
                await web_mixin.incluir_documento_bloco_assinatura(
                    "1864524", "76858997,99999999", processo="0020.010296/2026-86"
                )
            web_mixin._web.incluir_documento_bloco_assinatura_web.assert_not_called()

        asyncio.run(_run())

    def test_um_documento_funciona_normalmente(self) -> None:
        web_mixin = BlocosWeb()
        web_mixin._web = AsyncMock()  # type: ignore[attr-defined]
        web_mixin._web.incluir_documento_bloco_assinatura_web = AsyncMock(
            return_value={"ok": True, "idBloco": "1864524", "idDocumento": "76858997"}
        )

        async def _run() -> dict:
            return await web_mixin.incluir_documento_bloco_assinatura(
                "1864524", "76858997", processo="0020.010296/2026-86"
            )

        result = asyncio.run(_run())
        assert result["ok"] is True
        assert result["resultados"] == [
            {"ok": True, "idBloco": "1864524", "idDocumento": "76858997"}
        ]
        web_mixin._web.incluir_documento_bloco_assinatura_web.assert_called_once_with(
            "0020.010296/2026-86", "76858997", "1864524"
        )


class TestPostFormComAcaoOverride:
    """Regressão para o bug real encontrado em 2026-07-04: `excluir_bloco_assinatura_web`
    e `retirar_documento_bloco_assinatura_web` retornavam `ok: True` sem
    executar nada, porque `_post_form_preservando` sempre POSTa pro `action`
    PRÓPRIO do form — que nesses dois casos é a página de LISTAGEM
    (auto-referente), não a ação de destino (`bloco_excluir`/
    `rel_bloco_protocolo_excluir`). Confirmado ao vivo: os 3 blocos "excluídos"
    continuavam existindo até essa correção. `_post_form_com_acao_override`
    existe especificamente pra esse padrão JS "sobrescreve form.action antes
    de submeter".
    """

    def test_post_form_com_acao_override_ignora_action_proprio_do_form(self) -> None:
        client = make_client()
        form_html = (
            '<form id="frmBlocoLista" '
            'action="controlador.php?acao=bloco_assinatura_listar">'
            '<input type="hidden" name="hdnInfraItemId" value="">'
            "</form>"
        )
        form = BeautifulSoup(form_html, "html.parser").find("form")

        captured: dict[str, object] = {}

        async def _fake_post(url: str, **_kwargs: object) -> httpx.Response:
            captured["url"] = url
            return _resp(content="ok")

        with patch.object(client._http, "post", _fake_post):
            asyncio.run(
                client._post_form_com_acao_override(
                    form,
                    "http://sei.test/controlador.php?acao=bloco_excluir&infra_hash=abc",
                    {"hdnInfraItemId": "123"},
                    referer="http://sei.test/listar",
                )
            )

        # O POST precisa ir pra acao=bloco_excluir (o destino real), NÃO pra
        # acao=bloco_assinatura_listar (o action próprio do <form>, que
        # _post_form_preservando usaria por engano).
        assert "acao=bloco_excluir" in captured["url"]
        assert "acao=bloco_assinatura_listar" not in captured["url"]

    def test_executar_acao_bloco_form_posta_para_acao_de_destino(self) -> None:
        """Reproduz o bug real: form de listagem auto-referente + ação de destino diferente."""
        client = make_client()
        listar_html = (
            "<html><script>"
            "function acaoExcluir(id){"
            "document.getElementById('frmBlocoLista').action="
            "'controlador.php?acao=bloco_excluir&acao_origem=bloco_assinatura_listar"
            "&infra_hash=deadbeef';"
            "}</script>"
            '<form id="frmBlocoLista" '
            'action="controlador.php?acao=bloco_assinatura_listar">'
            '<input type="hidden" name="hdnInfraItemId" value="">'
            "</form></html>"
        )
        captured: dict[str, object] = {}

        async def _fake_post(url: str, **_kwargs: object) -> httpx.Response:
            captured["url"] = url
            return _resp(content="<html>excluido</html>")

        with (
            patch.object(client, "ensure_authenticated", AsyncMock()),
            patch.object(
                client, "_obter_link_toolbar", AsyncMock(return_value="http://sei.test/listar")
            ),
            patch.object(client._http, "get", AsyncMock(return_value=_resp(content=listar_html))),
            patch.object(client._http, "post", _fake_post),
        ):
            result = asyncio.run(
                client._executar_acao_bloco_form("999", "bloco_excluir", "Bloco excluído.")
            )

        assert result == {"ok": True, "idBloco": "999", "mensagem": "Bloco excluído."}
        assert "acao=bloco_excluir" in captured["url"]
        assert "acao=bloco_assinatura_listar" not in captured["url"]
