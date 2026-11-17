"""SEI simulado (wssei v2) para testes — nenhuma requisição sai da máquina.

Conta cada POST /autenticar: é o número que importa quando senha errada
repetida bloqueia a conta no SEI real. NUNCA apontar estes testes para um SEI
de verdade.
"""
from __future__ import annotations

import asyncio
import itertools
from urllib.parse import parse_qs

import httpx

URL = "https://sei.exemplo.gov.br/sei/modulos/wssei/controlador_ws.php/api/v2"
SENHA_CERTA = "senha-certa"


class SEISimulado:
    def __init__(self, senha: str = SENHA_CERTA):
        self.senha = senha
        self.chamadas: list[str] = []
        self.tokens_validos: set[str] = set()
        self.negados: set[str] = set()        # paths com 403 de permissão do wssei
        self.fora_do_ar = False                # ConnectError em tudo
        self.borda_403 = False                 # 403 HTML com server: cloudflare
        self.autenticar_status: int | None = None  # força 401/403 JSON no /autenticar
        self._seq = itertools.count(1)
        self.fechados = 0

    @property
    def logins(self) -> int:
        return self.chamadas.count("/autenticar")

    def invalidar_tokens(self) -> None:
        """Como a virada de data do wssei (a chave do token muda)."""
        self.tokens_validos.clear()

    async def handler(self, request: httpx.Request) -> httpx.Response:
        caminho = request.url.path.split("/api/v2", 1)[-1]
        self.chamadas.append(caminho)
        # Cede o event loop como uma rede de verdade: sem isto, chamadas
        # "simultâneas" rodariam em fila e uma corrida de login nunca apareceria.
        await asyncio.sleep(0.01)
        if self.fora_do_ar:
            raise httpx.ConnectError("SEI fora do ar (simulado)", request=request)
        if self.borda_403:
            return httpx.Response(403, headers={"server": "cloudflare", "cf-ray": "sim"},
                                  content=b"<html>error 1020</html>", request=request)
        if caminho == "/autenticar":
            if self.autenticar_status:
                return httpx.Response(self.autenticar_status,
                                      json={"sucesso": False, "mensagem": "Acesso negado!"},
                                      request=request)
            form = parse_qs(request.content.decode())
            if form.get("senha", [""])[0] != self.senha:
                return httpx.Response(200, json={"sucesso": False,
                                                 "mensagem": "Usuário ou senha inválido!"},
                                      request=request)
            token = f"tok{next(self._seq)}"
            self.tokens_validos.add(token)
            return httpx.Response(200, json={"sucesso": True, "data": {
                "token": token,
                "loginData": {"IdUsuario": "100", "IdOrgao": "0", "IdUnidadeAtual": "110"},
            }}, request=request)

        if request.headers.get("token") not in self.tokens_validos:
            return httpx.Response(403, json={"sucesso": False, "mensagem": "Token inválido!"},
                                  request=request)
        if caminho in self.negados:
            return httpx.Response(403, json={"sucesso": False,
                                             "mensagem": "Acesso ao protocolo não autorizado."},
                                  request=request)
        if caminho == "/versao":
            return httpx.Response(200, json={"sucesso": True,
                                             "data": {"sei": "5.0.4", "wssei": "3.0.2"}},
                                  request=request)
        return httpx.Response(200, json={"sucesso": True, "data": [], "total": 0},
                              request=request)


def instalar(monkeypatch, sim: SEISimulado) -> None:
    """Todo httpx.AsyncClient criado pelo SEIClient passa a falar com `sim`."""
    import mcp_seipro.sei_client as sc

    original = httpx.AsyncClient

    class _Cliente(original):
        def __init__(self, *a, **kw):
            kw["transport"] = httpx.MockTransport(sim.handler)
            super().__init__(*a, **kw)

        async def aclose(self):
            sim.fechados += 1
            await super().aclose()

    monkeypatch.setattr(sc.httpx, "AsyncClient", _Cliente)
    monkeypatch.setenv("SEI_TRANSPORT", "httpx")  # nada de Chromium nos testes


def creds(senha: str = SENHA_CERTA, usuario: str = "fulano") -> dict:
    return {
        "sei_url": URL,
        "sei_usuario": usuario,
        "sei_senha": senha,
        "sei_orgao": "0",
        "sei_verify_ssl": "true",
    }
