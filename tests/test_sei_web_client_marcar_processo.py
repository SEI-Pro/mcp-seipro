"""Testes de `SEIWebClient.marcar_processo_web` (RFC 0026).

Bug confirmado ao vivo em sei.sistemas.ro.gov.br (2026-07-05): a versão
anterior postava para a ação `andamento_marcador_gerenciar` (tela de
GERENCIAR/remover marcadores já aplicados — só tem checkbox por marcador
existente, sem `selMarcador` nenhum) com um campo `selMarcador` inventado. O
SEI aceitava o POST sem erro explícito, mas não aplicava nada — reproduzido
duas vezes seguidas contra processos reais, confirmado via
`sei_consultar_marcador_processo` que o marcador não estava lá.

A correção usa a ação certa, `andamento_marcador_cadastrar` (alcançada pelo
botão "Adicionar" da tela de gerenciar), e reconfirma o resultado relendo os
marcadores aplicados antes de reportar sucesso.

Estes testes mockam `ensure_authenticated`/`_pagina_marcador`/
`client._http.get`/`_post_form_preservando` e cobrem:
- sucesso: o marcador aparece na releitura pós-POST (`consultar_marcador_
  processo_web`), overrides corretos (`selMarcador`/`hdnIdMarcador`) chegam
  a `_post_form_preservando`;
- falha silenciosa detectada: o POST não levanta erro explícito, mas o
  marcador NÃO aparece na releitura — deve levantar erro, não reportar
  sucesso falso (exatamente a falha ao vivo que motivou esta correção);
- erro explícito do SEI na resposta do POST: propaga a mensagem;
- botão "Adicionar" ausente da tela de gerenciar: levanta erro claro.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from bs4 import BeautifulSoup

from todos.backends.models import SEIWebClientConfig
from todos.exceptions import SEIConnectionError, SEIParseError
from todos.sei_web_client import SEIWebClient

PROTOCOLO = "0016.004284/2026-81"
MARCADOR_ID = "123"

_GERENCIAR_BODY = """
<html><body>
<button id="btnAdicionar" onclick="location.href='controlador.php?acao=andamento_marcador_cadastrar&id_procedimento=999&infra_hash=abc123'">Adicionar</button>
</body></html>
"""

_GERENCIAR_BODY_SEM_BOTAO = """
<html><body>
<p>Nenhum marcador disponível para adicionar.</p>
</body></html>
"""

_CADASTRO_BODY = """
<html><body>
<form id="frmAndamentoMarcadorCadastro" method="post" action="controlador.php?acao=andamento_marcador_cadastrar_salvar">
  <input type="hidden" name="hdnInfraTipoPagina" value="2" />
  <select name="selMarcador">
    <option value="">Selecione</option>
    <option value="123">Urgente</option>
  </select>
  <input type="hidden" name="hdnIdMarcador" value="" />
  <textarea name="txaTexto"></textarea>
  <input type="hidden" name="hdnIdProtocolo" value="999" />
  <button type="submit" name="sbmSalvar" value="Salvar">Salvar</button>
</form>
</body></html>
"""

_RESPOSTA_POST_SEM_ERRO = """
<html><body><p>Andamento registrado.</p></body></html>
"""

_RECONFIRM_COM_MARCADOR = """
<html><body>
<script>acaoRemover('123','Urgente #ff0000');</script>
</body></html>
"""

_RECONFIRM_SEM_MARCADOR = _GERENCIAR_BODY  # nenhum acaoRemover presente


def make_client() -> SEIWebClient:
    return SEIWebClient(
        SEIWebClientConfig(sei_web_url="http://sei.test", sei_usuario="u", sei_senha="p")
    )


def _resp(body: str, url: str = "http://sei.test/x") -> httpx.Response:
    return httpx.Response(
        200,
        content=body.encode("iso-8859-1"),
        headers={"content-type": "text/html; charset=iso-8859-1"},
        request=httpx.Request("GET", url),
    )


class TestMarcarProcessoWebSucesso:
    def test_sucesso_confirma_marcador_aplicado(self) -> None:
        client = make_client()
        with (
            patch.object(client, "ensure_authenticated", AsyncMock()),
            patch.object(
                client,
                "_pagina_marcador",
                AsyncMock(
                    side_effect=[
                        (_GERENCIAR_BODY, BeautifulSoup(_GERENCIAR_BODY, "html.parser"), "ref"),
                        (
                            _RECONFIRM_COM_MARCADOR,
                            BeautifulSoup(_RECONFIRM_COM_MARCADOR, "html.parser"),
                            "ref",
                        ),
                    ]
                ),
            ),
            patch.object(
                client._http,
                "get",
                AsyncMock(
                    return_value=_resp(
                        _CADASTRO_BODY,
                        url="http://sei.test/controlador.php?acao=andamento_marcador_cadastrar",
                    )
                ),
            ),
            patch.object(
                client,
                "_post_form_preservando",
                AsyncMock(return_value=_resp(_RESPOSTA_POST_SEM_ERRO)),
            ) as mock_post,
        ):
            resultado = asyncio.run(client.marcar_processo_web(PROTOCOLO, MARCADOR_ID, texto="obs"))

        assert resultado == {
            "ok": True,
            "mensagem": "Marcador aplicado.",
            "protocolo": PROTOCOLO,
            "marcador": MARCADOR_ID,
        }
        _form, _base_url, overrides = mock_post.call_args.args
        assert overrides["selMarcador"] == MARCADOR_ID
        assert overrides["hdnIdMarcador"] == MARCADOR_ID
        assert overrides["txaTexto"] == "obs"
        # O PHP do SEI ignora o POST silenciosamente sem o par name=value do
        # botão submit (_coletar_estado_form o exclui de propósito) — sem
        # isso o marcador "seria salvo" sem erro, mas sem efeito real.
        assert overrides["sbmSalvar"] == "Salvar"


class TestMarcarProcessoWebFalhas:
    def test_falha_silenciosa_marcador_nao_aplicado_levanta_erro(self) -> None:
        """Reproduz a falha ao vivo: POST sem erro explícito, mas sem efeito real."""
        client = make_client()
        with (
            patch.object(client, "ensure_authenticated", AsyncMock()),
            patch.object(
                client,
                "_pagina_marcador",
                AsyncMock(
                    side_effect=[
                        (_GERENCIAR_BODY, BeautifulSoup(_GERENCIAR_BODY, "html.parser"), "ref"),
                        (
                            _RECONFIRM_SEM_MARCADOR,
                            BeautifulSoup(_RECONFIRM_SEM_MARCADOR, "html.parser"),
                            "ref",
                        ),
                    ]
                ),
            ),
            patch.object(
                client._http,
                "get",
                AsyncMock(
                    return_value=_resp(
                        _CADASTRO_BODY,
                        url="http://sei.test/controlador.php?acao=andamento_marcador_cadastrar",
                    )
                ),
            ),
            patch.object(
                client,
                "_post_form_preservando",
                AsyncMock(return_value=_resp(_RESPOSTA_POST_SEM_ERRO)),
            ),
            pytest.raises(SEIConnectionError, match="não aparece aplicado"),
        ):
            asyncio.run(client.marcar_processo_web(PROTOCOLO, MARCADOR_ID))

    def test_erro_explicito_do_sei_propaga(self) -> None:
        client = make_client()
        resposta_erro = (
            '<html><body><div class="infraMensagemErro">X Marcador inválido.</div></body></html>'
        )
        with (
            patch.object(client, "ensure_authenticated", AsyncMock()),
            patch.object(
                client,
                "_pagina_marcador",
                AsyncMock(
                    return_value=(
                        _GERENCIAR_BODY,
                        BeautifulSoup(_GERENCIAR_BODY, "html.parser"),
                        "ref",
                    )
                ),
            ),
            patch.object(
                client._http,
                "get",
                AsyncMock(
                    return_value=_resp(
                        _CADASTRO_BODY,
                        url="http://sei.test/controlador.php?acao=andamento_marcador_cadastrar",
                    )
                ),
            ),
            patch.object(
                client, "_post_form_preservando", AsyncMock(return_value=_resp(resposta_erro))
            ),
            pytest.raises(SEIConnectionError, match="Marcador inválido"),
        ):
            asyncio.run(client.marcar_processo_web(PROTOCOLO, MARCADOR_ID))

    def test_botao_adicionar_ausente_levanta_erro(self) -> None:
        client = make_client()
        with (
            patch.object(client, "ensure_authenticated", AsyncMock()),
            patch.object(
                client,
                "_pagina_marcador",
                AsyncMock(
                    return_value=(
                        _GERENCIAR_BODY_SEM_BOTAO,
                        BeautifulSoup(_GERENCIAR_BODY_SEM_BOTAO, "html.parser"),
                        "ref",
                    )
                ),
            ),
            pytest.raises(SEIParseError, match="Adicionar"),
        ):
            asyncio.run(client.marcar_processo_web(PROTOCOLO, MARCADOR_ID))
