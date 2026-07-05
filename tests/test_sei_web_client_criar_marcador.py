"""Testes de `SEIWebClient.criar_marcador_web`/`listar_cores_marcador_web` (RFC 0026).

`sei_criar_marcador` usava `_rest_backend(ctx)` diretamente — em instâncias
sem mod-wssei (REST), falhava com "Backend REST não está configurado para
esta instância do SEI", mesmo sendo uma operação criável via scraping puro.
Confirmado ao vivo em sei.sistemas.ro.gov.br (2026-07-05): um marcador novo
foi criado com sucesso via scraping direto, sem tocar REST.

Fluxo real: `marcador_listar` (toolbar da inbox) → botão "Novo"
(`marcador_cadastrar`) → form `frmMarcadorCadastro` (`selStaIcone`/
`hdnStaIcone` para a cor, `txtNome`, `txaDescricao` opcional, `hdnIdMarcador`
vazio) → POST → reconfirma relendo `marcador_listar` e conferindo que o nome
aparece antes de reportar sucesso.

Estes testes mockam `ensure_authenticated`/`_obter_link_toolbar`/
`client._http.get`/`_post_form_preservando` e cobrem:
- sucesso: o nome aparece na releitura pós-POST, overrides corretos
  (incluindo o par name=value do botão submit) chegam a
  `_post_form_preservando`;
- extração de cores a partir do `<select selStaIcone>` do form de cadastro
  (nunca hardcoded);
- `id_cor` omitido: recusa com a lista de cores disponíveis antes de
  qualquer POST;
- falha silenciosa detectada: o POST não levanta erro explícito, mas o nome
  NÃO aparece na releitura — deve levantar erro, não reportar sucesso falso.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from todos.backends.models import SEIWebClientConfig
from todos.exceptions import SEIConnectionError, SEIParseError, SEIValidationError
from todos.sei_web_client import SEIWebClient

LISTAR_URL = "http://sei.test/controlador.php?acao=marcador_listar&infra_hash=xyz"

_MARCADOR_LISTAR_BODY = """
<html><body>
<button id="btnNovo" onclick="location.href='controlador.php?acao=marcador_cadastrar&acao_origem=marcador_listar&infra_hash=abc123'">Novo</button>
<table><tr><td>Urgente</td></tr></table>
</body></html>
"""

_MARCADOR_LISTAR_BODY_SEM_BOTAO = """
<html><body>
<p>Nenhuma ação disponível.</p>
</body></html>
"""

_MARCADOR_LISTAR_BODY_COM_NOVO = """
<html><body>
<table><tr><td>Urgente</td></tr><tr><td>Teste Novo</td></tr></table>
</body></html>
"""

_CADASTRO_BODY = """
<html><body>
<form id="frmMarcadorCadastro" method="post" action="controlador.php?acao=marcador_cadastrar_salvar">
  <input type="hidden" name="hdnInfraTipoPagina" value="2" />
  <select name="selStaIcone">
    <option value="0">Preto</option>
    <option value="3">Vermelho</option>
  </select>
  <input type="hidden" name="hdnStaIcone" value="" />
  <input type="text" name="txtNome" value="" />
  <textarea name="txaDescricao"></textarea>
  <input type="hidden" name="hdnIdMarcador" value="" />
  <button type="submit" name="sbmCadastrarMarcador" value="Salvar">Salvar</button>
</form>
</body></html>
"""

_RESPOSTA_POST_SEM_ERRO = """
<html><body><p>Marcador cadastrado.</p></body></html>
"""


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


class TestListarCoresMarcadorWeb:
    def test_extrai_cores_do_select(self) -> None:
        client = make_client()
        with (
            patch.object(client, "ensure_authenticated", AsyncMock()),
            patch.object(client, "_obter_link_toolbar", AsyncMock(return_value=LISTAR_URL)),
            patch.object(
                client._http,
                "get",
                AsyncMock(
                    side_effect=[
                        _resp(_MARCADOR_LISTAR_BODY, url=LISTAR_URL),
                        _resp(_CADASTRO_BODY, url="http://sei.test/marcador_cadastrar"),
                    ]
                ),
            ),
        ):
            cores = asyncio.run(client.listar_cores_marcador_web())

        assert cores == [{"id": "0", "nome": "Preto"}, {"id": "3", "nome": "Vermelho"}]


class TestCriarMarcadorWebSucesso:
    def test_sucesso_confirma_nome_na_listagem(self) -> None:
        client = make_client()
        with (
            patch.object(client, "ensure_authenticated", AsyncMock()),
            patch.object(client, "_obter_link_toolbar", AsyncMock(return_value=LISTAR_URL)),
            patch.object(
                client._http,
                "get",
                AsyncMock(
                    side_effect=[
                        _resp(_MARCADOR_LISTAR_BODY, url=LISTAR_URL),
                        _resp(_CADASTRO_BODY, url="http://sei.test/marcador_cadastrar"),
                        _resp(_MARCADOR_LISTAR_BODY_COM_NOVO, url=LISTAR_URL),
                    ]
                ),
            ),
            patch.object(
                client,
                "_post_form_preservando",
                AsyncMock(return_value=_resp(_RESPOSTA_POST_SEM_ERRO)),
            ) as mock_post,
        ):
            resultado = asyncio.run(
                client.criar_marcador_web("Teste Novo", id_cor="3", descricao="obs")
            )

        assert resultado == {
            "ok": True,
            "mensagem": "Marcador criado.",
            "nome": "Teste Novo",
            "id_cor": "3",
        }
        _form, _base_url, overrides = mock_post.call_args.args
        assert overrides["txtNome"] == "Teste Novo"
        assert overrides["selStaIcone"] == "3"
        assert overrides["hdnStaIcone"] == "3"
        assert overrides["txaDescricao"] == "obs"
        assert overrides["hdnIdMarcador"] == ""
        # O PHP do SEI ignora o POST silenciosamente sem o par name=value do
        # botão submit (_coletar_estado_form o exclui de propósito).
        assert overrides["sbmCadastrarMarcador"] == "Salvar"


class TestCriarMarcadorWebFalhas:
    def test_id_cor_omitido_recusa_com_cores_disponiveis_sem_postar(self) -> None:
        client = make_client()
        with (
            patch.object(client, "ensure_authenticated", AsyncMock()),
            patch.object(client, "_obter_link_toolbar", AsyncMock(return_value=LISTAR_URL)),
            patch.object(
                client._http,
                "get",
                AsyncMock(
                    side_effect=[
                        _resp(_MARCADOR_LISTAR_BODY, url=LISTAR_URL),
                        _resp(_CADASTRO_BODY, url="http://sei.test/marcador_cadastrar"),
                    ]
                ),
            ),
            patch.object(client, "_post_form_preservando", AsyncMock()) as mock_post,
            pytest.raises(SEIValidationError, match="0=Preto, 3=Vermelho"),
        ):
            asyncio.run(client.criar_marcador_web("Teste Novo"))

        mock_post.assert_not_called()

    def test_falha_silenciosa_nome_nao_aparece_levanta_erro(self) -> None:
        """Reproduz o mesmo tipo de falha ao vivo do bug de marcar_processo_web:
        POST sem erro explícito, mas sem efeito real."""
        client = make_client()
        with (
            patch.object(client, "ensure_authenticated", AsyncMock()),
            patch.object(client, "_obter_link_toolbar", AsyncMock(return_value=LISTAR_URL)),
            patch.object(
                client._http,
                "get",
                AsyncMock(
                    side_effect=[
                        _resp(_MARCADOR_LISTAR_BODY, url=LISTAR_URL),
                        _resp(_CADASTRO_BODY, url="http://sei.test/marcador_cadastrar"),
                        _resp(_MARCADOR_LISTAR_BODY, url=LISTAR_URL),  # sem "Teste Novo"
                    ]
                ),
            ),
            patch.object(
                client,
                "_post_form_preservando",
                AsyncMock(return_value=_resp(_RESPOSTA_POST_SEM_ERRO)),
            ),
            pytest.raises(SEIConnectionError, match="não aparece na listagem"),
        ):
            asyncio.run(client.criar_marcador_web("Teste Novo", id_cor="3"))

    def test_nome_vazio_recusa_antes_de_qualquer_chamada(self) -> None:
        client = make_client()
        with (
            patch.object(client, "ensure_authenticated", AsyncMock()) as mock_auth,
            pytest.raises(SEIValidationError, match="vazio"),
        ):
            asyncio.run(client.criar_marcador_web("   ", id_cor="3"))
        mock_auth.assert_not_called()

    def test_botao_novo_ausente_levanta_erro(self) -> None:
        client = make_client()
        with (
            patch.object(client, "ensure_authenticated", AsyncMock()),
            patch.object(client, "_obter_link_toolbar", AsyncMock(return_value=LISTAR_URL)),
            patch.object(
                client._http,
                "get",
                AsyncMock(return_value=_resp(_MARCADOR_LISTAR_BODY_SEM_BOTAO, url=LISTAR_URL)),
            ),
            pytest.raises(SEIParseError, match='"Novo"'),
        ):
            asyncio.run(client.criar_marcador_web("Teste Novo", id_cor="3"))
