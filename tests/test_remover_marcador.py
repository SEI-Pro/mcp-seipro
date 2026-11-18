"""Remoção de marcador de processo (POST /marcador/processo/{id}/remover).

A rota NÃO existe no mod-wssei oficial (conferido até a 3.0.6): ela vem do
patch em `patches/mod-wssei-remover-marcador.patch`. O wssei só responde
JSON (`sucesso`) nas rotas que conhece; rota ausente cai no Slim sem error
middleware → 404/405/500 sem JSON. É esse o sinal que a tool usa para dizer
que a instalação não tem a rota, em vez de repassar um 500 opaco.

Também fica coberto que `sei_marcar_processo` deixou de sugerir "marcador
vazio" para remover: no SEI 4+ o core recusa ("Marcador não informado.").
"""
import asyncio
import json
import os
import sys
from urllib.parse import parse_qs

import httpx
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
os.environ.setdefault("SEI_URL", "https://exemplo.gov.br/api/v2")

import mcp_seipro.server as srv  # noqa: E402
from mcp_seipro.sei_client import SEIClient  # noqa: E402
from sei_simulado import SEISimulado, creds, instalar  # noqa: E402

ROTA = "/marcador/processo/555/remover"


class _SEIComRemocao(SEISimulado):
    """SEI simulado em que a rota de remoção responde como o `resposta` mandar."""

    def __init__(self, resposta):
        super().__init__()
        self.resposta = resposta  # (status, corpo: dict|bytes)
        self.forms: list[dict] = []

    async def handler(self, request: httpx.Request) -> httpx.Response:
        caminho = request.url.path.split("/api/v2", 1)[-1]
        if caminho == ROTA and request.headers.get("token") in self.tokens_validos:
            self.chamadas.append(caminho)
            self.forms.append({k: v[0] for k, v in parse_qs(request.content.decode()).items()})
            status, corpo = self.resposta
            if isinstance(corpo, dict):
                return httpx.Response(status, json=corpo, request=request)
            return httpx.Response(status, content=corpo,
                                  headers={"content-type": "text/html"}, request=request)
        return await super().handler(request)


def _sei(monkeypatch, resposta):
    sim = _SEIComRemocao(resposta)
    instalar(monkeypatch, sim)
    return sim


def _remover(**kw):
    cli = SEIClient(**creds())
    return asyncio.run(cli.remover_marcador_processo("555", "42", **kw))


# --- cliente ---------------------------------------------------------------

def test_cliente_posta_marcador_na_rota_de_remocao(monkeypatch):
    sim = _sei(monkeypatch, (200, {"sucesso": True,
                                   "mensagem": "Marcador removido do processo com sucesso.",
                                   "data": None}))
    res = _remover()
    assert ROTA in sim.chamadas
    assert sim.forms == [{"marcador": "42"}]
    assert res == {"mensagem": "Marcador removido do processo com sucesso."}


def test_recusa_do_sei_vira_erro_com_a_mensagem_dele(monkeypatch):
    _sei(monkeypatch, (200, {"sucesso": False,
                             "mensagem": "Marcador não encontrado no processo para a unidade atual."}))
    with pytest.raises(Exception) as e:
        _remover()
    assert "Marcador não encontrado no processo" in str(e.value)


@pytest.mark.parametrize("status,corpo", [
    (404, b"<html>404 Not Found</html>"),
    (405, b"Method not allowed. Must be one of: OPTIONS"),
    (500, b""),  # Slim sem error middleware + display_errors=Off
    (500, b"<br /><b>Fatal error</b>: Uncaught Slim\\Exception\\HttpNotFoundException"),
])
def test_rota_ausente_diz_que_a_instalacao_nao_tem_a_rota(monkeypatch, status, corpo):
    _sei(monkeypatch, (status, corpo))
    with pytest.raises(Exception) as e:
        _remover()
    msg = str(e.value)
    assert "/remover" in msg
    assert "não respondeu como rota do wssei" in msg
    assert f"HTTP {status}" in msg
    # Não chuta causa: lista o que se sabe e aponta a verificação.
    assert "sei_versao" in msg


def test_erro_http_com_json_do_wssei_nao_e_confundido_com_rota_ausente(monkeypatch):
    _sei(monkeypatch, (500, {"sucesso": False, "mensagem": "Erro removendo marcador do processo."}))
    with pytest.raises(Exception) as e:
        _remover()
    assert "Erro removendo marcador do processo." in str(e.value)
    assert "não respondeu como rota do wssei" not in str(e.value)


# --- tool ------------------------------------------------------------------

class _Ctx:
    def __init__(self, cli):
        self.request_context = type("R", (), {"lifespan_context": {"sei": cli}})()


class _FakeClient:
    def __init__(self):
        self.chamadas = []

    async def remover_marcador_processo(self, id_procedimento, id_marcador):
        self.chamadas.append((id_procedimento, id_marcador))
        return {"mensagem": "Marcador removido do processo com sucesso."}


def test_tool_resolve_processo_e_chama_o_cliente(monkeypatch):
    async def fake_resolver(c, ref):
        assert ref == "50300.018905/2018-67"
        return "555"

    monkeypatch.setattr(srv, "_resolver_processo", fake_resolver)
    cli = _FakeClient()
    res = json.loads(asyncio.run(srv.sei_remover_marcador_processo(
        processo="50300.018905/2018-67", marcador="42", ctx=_Ctx(cli))))
    assert cli.chamadas == [("555", "42")]
    assert res["mensagem"] == "Marcador removido do processo com sucesso."


def test_tool_exige_marcador(monkeypatch):
    cli = _FakeClient()
    res = json.loads(asyncio.run(srv.sei_remover_marcador_processo(
        processo="555", marcador=" ", ctx=_Ctx(cli))))
    assert "error" in res
    assert cli.chamadas == []


def test_tool_e_destrutiva_e_tem_titulo():
    t = srv.mcp._tool_manager._tools["sei_remover_marcador_processo"]
    assert t.annotations.destructiveHint is True
    assert t.annotations.readOnlyHint is False
    assert t.title


def test_marcar_processo_nao_sugere_marcador_vazio_para_remover():
    doc = srv.mcp._tool_manager._tools["sei_marcar_processo"].description
    assert "marcador vazio" not in doc
    assert "sei_remover_marcador_processo" in doc
