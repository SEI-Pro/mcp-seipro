"""Testes de `SEIWebClient.assinar_documento_web` (RFC 0023).

Confirmado ao vivo em sei.sistemas.ro.gov.br, 2026-07-04: o payload precisa
replicar exatamente o que o JS real `assinarSenha()` faz antes de
`frmAssinaturas.submit()` — `pwdSenha` e `hdnFormaAutenticacao="S"`. Uma
primeira versão do código enviava `btnAssinar` (um `<button type="button">`,
não um submit real — irrelevante como campo de POST) e não enviava
`hdnFormaAutenticacao`, o que o SEI aceita silenciosamente sem executar a
assinatura (reexibe o mesmo form, sem nenhuma mensagem de erro explícita).
O override de `orgao` também usava o nome de campo errado
(`selOrgaoAssinante` em vez de `selOrgao`, o nome real do select no form).

Estes testes mockam `_get_arvore_visualizar_link_var`/`client._http.get`/
`_post_form_preservando` e cobrem:
- sucesso: form não reaparece na resposta do POST, overrides corretos
  (`hdnFormaAutenticacao="S"`, sem `btnAssinar`) são os que de fato chegam
  a `_post_form_preservando`;
- override de `orgao` usa o nome de campo certo (`selOrgao`);
- sem senha disponível: recusa antes de qualquer chamada HTTP;
- erro explícito do SEI na resposta do POST: propaga a mensagem;
- assinatura sem efeito: form `frmAssinaturas` ainda presente na resposta do
  POST, sem erro explícito — deve levantar erro, não reportar sucesso falso
  (exatamente a falha ao vivo que motivou esta correção).
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from todos.backends.models import SEIWebClientConfig
from todos.exceptions import SEIConnectionError, SEIValidationError
from todos.sei_web_client import SEIWebClient

PROTOCOLO = "0016.004284/2026-81"
ID_DOCUMENTO = "76861508"

_FORM_ASSINATURA = """
<html><body>
<form id="frmAssinaturas" method="post" action="controlador.php?acao=documento_assinar">
  <input type="hidden" name="hdnInfraTipoPagina" value="2" />
  <select name="selOrgao"><option value="9" selected>PGE</option></select>
  <input type="text" name="txtUsuario" value="FRANKLIN SILVEIRA BALDO" />
  <input type="hidden" name="hdnIdUsuario" value="100002586" />
  <select name="selCargoFuncao"><option value="Procurador do Estado" selected>Procurador do Estado</option></select>
  <input type="password" name="pwdSenha" value="" />
  <input type="hidden" name="hdnFormaAutenticacao" value="" />
  <input type="hidden" name="hdnIdDocumentos" value="76861508" />
</form>
</body></html>
"""

_RESPOSTA_SUCESSO = """
<html><body>
<p>Documento assinado eletronicamente por Franklin Silveira Baldo.</p>
</body></html>
"""

_RESPOSTA_SEM_EFEITO = _FORM_ASSINATURA  # SEI reexibe o mesmo form, sem erro explícito


def make_client() -> SEIWebClient:
    return SEIWebClient(
        SEIWebClientConfig(sei_web_url="http://sei.test", sei_usuario="u", sei_senha="p")
    )


def _resp(body: str) -> httpx.Response:
    return httpx.Response(
        200,
        content=body.encode("iso-8859-1"),
        headers={"content-type": "text/html; charset=iso-8859-1"},
        request=httpx.Request("GET", "http://sei.test/x"),
    )


class TestAssinarDocumentoWebSemSenha:
    def test_sem_senha_recusa_antes_de_qualquer_chamada(self) -> None:
        client = SEIWebClient(
            SEIWebClientConfig(sei_web_url="http://sei.test", sei_usuario="u", sei_senha="")
        )
        with (
            patch.object(client, "ensure_authenticated", AsyncMock()) as mock_auth,
            patch.object(client, "_get_arvore_visualizar_link_var", AsyncMock()) as mock_link,
            pytest.raises(SEIValidationError, match="enha"),
        ):
            asyncio.run(client.assinar_documento_web(PROTOCOLO, ID_DOCUMENTO))

        mock_auth.assert_not_called()
        mock_link.assert_not_called()


class TestAssinarDocumentoWebSucesso:
    def test_sucesso_envia_overrides_corretos(self) -> None:
        client = make_client()
        with (
            patch.object(client, "ensure_authenticated", AsyncMock()),
            patch.object(
                client,
                "_get_arvore_visualizar_link_var",
                AsyncMock(
                    return_value=("http://sei.test/documento_assinar", "http://sei.test/ref")
                ),
            ),
            patch.object(client._http, "get", AsyncMock(return_value=_resp(_FORM_ASSINATURA))),
            patch.object(
                client, "_post_form_preservando", AsyncMock(return_value=_resp(_RESPOSTA_SUCESSO))
            ) as mock_post,
        ):
            resultado = asyncio.run(client.assinar_documento_web(PROTOCOLO, ID_DOCUMENTO))

        assert resultado == {"status": "ok", "id_documento": ID_DOCUMENTO}
        _form, _base_url, overrides = mock_post.call_args.args
        assert overrides["hdnFormaAutenticacao"] == "S"
        assert overrides["pwdSenha"] == "p"
        assert "btnAssinar" not in overrides

    def test_override_de_orgao_usa_nome_de_campo_certo(self) -> None:
        client = make_client()
        with (
            patch.object(client, "ensure_authenticated", AsyncMock()),
            patch.object(
                client,
                "_get_arvore_visualizar_link_var",
                AsyncMock(
                    return_value=("http://sei.test/documento_assinar", "http://sei.test/ref")
                ),
            ),
            patch.object(client._http, "get", AsyncMock(return_value=_resp(_FORM_ASSINATURA))),
            patch.object(
                client, "_post_form_preservando", AsyncMock(return_value=_resp(_RESPOSTA_SUCESSO))
            ) as mock_post,
        ):
            asyncio.run(client.assinar_documento_web(PROTOCOLO, ID_DOCUMENTO, orgao="10"))

        _form, _base_url, overrides = mock_post.call_args.args
        assert overrides["selOrgao"] == "10"
        assert "selOrgaoAssinante" not in overrides


class TestAssinarDocumentoWebFalhas:
    def test_erro_explicito_do_sei_propaga(self) -> None:
        client = make_client()
        resposta_erro = (
            '<html><body><div class="infraMensagemErro">'
            "X Documento já foi assinado por outro usuário.</div></body></html>"
        )
        with (
            patch.object(client, "ensure_authenticated", AsyncMock()),
            patch.object(
                client,
                "_get_arvore_visualizar_link_var",
                AsyncMock(
                    return_value=("http://sei.test/documento_assinar", "http://sei.test/ref")
                ),
            ),
            patch.object(client._http, "get", AsyncMock(return_value=_resp(_FORM_ASSINATURA))),
            patch.object(
                client, "_post_form_preservando", AsyncMock(return_value=_resp(resposta_erro))
            ),
            pytest.raises(SEIConnectionError, match="já foi assinado"),
        ):
            asyncio.run(client.assinar_documento_web(PROTOCOLO, ID_DOCUMENTO))

    def test_form_ainda_presente_sem_erro_explicito_levanta_erro(self) -> None:
        """Reproduz a falha ao vivo: SEI reexibe o form sem erro explícito."""
        client = make_client()
        with (
            patch.object(client, "ensure_authenticated", AsyncMock()),
            patch.object(
                client,
                "_get_arvore_visualizar_link_var",
                AsyncMock(
                    return_value=("http://sei.test/documento_assinar", "http://sei.test/ref")
                ),
            ),
            patch.object(client._http, "get", AsyncMock(return_value=_resp(_FORM_ASSINATURA))),
            patch.object(
                client,
                "_post_form_preservando",
                AsyncMock(return_value=_resp(_RESPOSTA_SEM_EFEITO)),
            ),
            pytest.raises(SEIConnectionError, match="não teve efeito"),
        ):
            asyncio.run(client.assinar_documento_web(PROTOCOLO, ID_DOCUMENTO))
