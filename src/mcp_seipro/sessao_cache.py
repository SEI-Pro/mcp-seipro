"""Cache de SEIClient já autenticado, por sessão OAuth (modo HTTP).

Sem isto, cada chamada de tool criava um SEIClient novo → um POST /autenticar
por chamada (segundos de latência, um registro de acesso no SEI por chamada) e
o cliente nunca era fechado (o lifespan do modo HTTP não fecha nada; no caminho
do browser, cada cliente sobe o próprio Chromium).

Chave: o `sid` da sessão OAuth (estável no refresh, distinto entre sessões do
MESMO usuário) — nunca só o usuário. A entrada guarda também uma impressão
(sha256) das credenciais; se não bater, a entrada é trocada, nunca reaproveitada.

Regras:
  - TTL absoluto desde a criação (SEI_CLIENT_CACHE_TTL_S, padrão 1800 s). O
    token do wssei não tem prazo fixo: a chave dele é sha1(segredo + data), então
    ele morre quando a data do servidor do SEI vira. Nenhum TTL fica sempre
    abaixo disso; o caso da meia-noite cai no re-login do SEIClient (token com
    mais de SEI_RELOGIN_IDADE_MIN_S recusado → um login novo).
  - LRU limitado: SEI_CLIENT_CACHE_MAX (padrão 32) entradas, das quais no máximo
    SEI_CLIENT_CACHE_MAX_BROWSER (padrão 3) com Chromium aberto — o contêiner
    tem 1280 MB, e cada Chromium leva algumas centenas de MB.
  - Criação síncrona (sem await entre conferir e inserir) + trava de login dentro
    do SEIClient → duas chamadas simultâneas da mesma sessão fazem UM login.
  - Despejo fecha o httpx/browser. Entrada emprestada a uma chamada em curso só
    é fechada quando a última chamada a devolve (lifespan do request).
  - Revogação/logout do OAuth invalida a sessão (auth._revogar_sessao).
  - Falha do autenticar invalida a entrada. Se foi RECUSA DE SENHA, a sessão
    fica marcada: as chamadas seguintes recebem a mesma "Falha na autenticação
    SEI" sem requisição ao SEI. As credenciais estão seladas no token e não
    mudam dentro da sessão — tentar de novo só somaria falhas para o bloqueio.
    Só um login novo (/login, sessão nova) sai desse estado.

VÁRIOS WORKERS: o cache é por processo. Com N processos, cada um mantém seu
cliente — até N logins por sessão, e o limite de recusas (limite_login.py) vale
por processo (até N × 3 por janela). Hoje o servidor roda um processo só
(uvicorn sem --workers, um contêiner), e o resto do estado OAuth em memória
(códigos de autorização, revogações) também já supõe isso.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Callable

from .sei_client import SEICredencialRecusada

logger = logging.getLogger(__name__)


def impressao_credenciais(creds: dict) -> str:
    bruto = json.dumps(creds, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(bruto.encode()).hexdigest()


@dataclass
class Entrada:
    chave: str
    cliente: Any
    impressao: str
    criado_em: float
    emprestimos: int = 0
    despejado: bool = False
    _fechado: bool = field(default=False, repr=False)


class CacheClientesSEI:
    def __init__(
        self,
        fabrica: Callable[[dict], Any],
        max_entradas: int = 32,
        max_browser: int = 3,
        ttl_s: float = 1800.0,
        max_recusadas: int = 1024,
        ttl_recusada_s: float = 86400.0 * 30,
        relogio: Callable[[], float] = time.monotonic,
    ):
        self.fabrica = fabrica
        self.max_entradas = max(1, int(max_entradas))
        self.max_browser = max(0, int(max_browser))
        self.ttl_s = float(ttl_s)
        self.max_recusadas = int(max_recusadas)
        self.ttl_recusada_s = float(ttl_recusada_s)
        self._relogio = relogio
        self._entradas: OrderedDict[str, Entrada] = OrderedDict()
        # chave -> (instante, mensagem do SEI). Sessões cuja senha o SEI recusou.
        self._recusadas: OrderedDict[str, tuple[float, str]] = OrderedDict()
        self._fechamentos: set[asyncio.Task] = set()

    # ------------------------------------------------------------- empréstimo

    def emprestar(self, chave: str, creds: dict) -> Entrada:
        """Entrada (cliente) da sessão, criando sob demanda. Síncrono de propósito."""
        agora = self._relogio()
        self._varrer(agora)
        recusa = self._recusadas.get(chave)
        if recusa is not None:
            self._recusadas.move_to_end(chave)
            raise SEICredencialRecusada(recusa[1], repetida=True)

        impressao = impressao_credenciais(creds)
        entrada = self._entradas.get(chave)
        if entrada is not None and entrada.impressao != impressao:
            self._despejar(chave)
            entrada = None
        if entrada is None:
            entrada = Entrada(chave=chave, cliente=self.fabrica(creds),
                              impressao=impressao, criado_em=agora)
            self._entradas[chave] = entrada
        self._entradas.move_to_end(chave)
        entrada.emprestimos += 1
        self._limitar()
        return entrada

    def devolver(self, entrada: Entrada) -> None:
        """Fim do request que usou a entrada: registra falhas e fecha se despejada."""
        entrada.emprestimos = max(0, entrada.emprestimos - 1)
        falha = getattr(entrada.cliente, "falha_autenticacao", None)
        if falha == "credencial":
            self.marcar_recusada(entrada.chave, getattr(entrada.cliente, "_credencial_recusada", "") or "")
        elif falha is not None and self._entradas.get(entrada.chave) is entrada:
            self._despejar(entrada.chave)
        if entrada.despejado and entrada.emprestimos == 0:
            self._fechar(entrada)
        self._limitar()

    # ------------------------------------------------------------- invalidação

    def invalidar(self, chave: str) -> None:
        """Logout/revogação da sessão OAuth."""
        self._despejar(chave)
        self._recusadas.pop(chave, None)

    def marcar_recusada(self, chave: str, mensagem: str) -> None:
        self._recusadas[chave] = (self._relogio(), mensagem)
        self._recusadas.move_to_end(chave)
        while len(self._recusadas) > self.max_recusadas:
            self._recusadas.popitem(last=False)
        self._despejar(chave)

    def _despejar(self, chave: str) -> None:
        entrada = self._entradas.pop(chave, None)
        if entrada is None:
            return
        entrada.despejado = True
        if entrada.emprestimos == 0:
            self._fechar(entrada)

    def _varrer(self, agora: float) -> None:
        for chave in [k for k, e in self._entradas.items() if agora - e.criado_em >= self.ttl_s]:
            self._despejar(chave)
        for chave in [k for k, (t, _) in self._recusadas.items() if agora - t >= self.ttl_recusada_s]:
            self._recusadas.pop(chave, None)

    def _limitar(self) -> None:
        while len(self._entradas) > self.max_entradas:
            self._despejar(next(iter(self._entradas)))
        com_browser = [k for k, e in self._entradas.items() if _usa_browser(e.cliente)]
        while len(com_browser) > self.max_browser:
            self._despejar(com_browser.pop(0))

    # ------------------------------------------------------------- fechamento

    def _fechar(self, entrada: Entrada) -> None:
        if entrada._fechado:
            return
        entrada._fechado = True
        fechar = getattr(entrada.cliente, "close", None)
        if fechar is None:
            return
        try:
            tarefa = asyncio.get_running_loop().create_task(_fechar_quieto(fechar))
        except RuntimeError:  # sem loop (teste síncrono): nada aberto de verdade
            return
        self._fechamentos.add(tarefa)
        tarefa.add_done_callback(self._fechamentos.discard)

    async def fechar_tudo(self) -> None:
        for chave in list(self._entradas):
            entrada = self._entradas[chave]
            entrada.emprestimos = 0
            self._despejar(chave)
        if self._fechamentos:
            await asyncio.gather(*list(self._fechamentos), return_exceptions=True)

    # ------------------------------------------------------------- inspeção

    def __len__(self) -> int:
        return len(self._entradas)

    def __contains__(self, chave: str) -> bool:
        return chave in self._entradas

    def limpar(self) -> None:
        """Só para testes: esquece tudo sem fechar."""
        self._entradas.clear()
        self._recusadas.clear()


def _usa_browser(cliente: Any) -> bool:
    try:
        return bool(cliente._is_browser_client())
    except Exception:
        return False


async def _fechar_quieto(fechar) -> None:
    try:
        await fechar()
    except Exception as e:  # noqa: BLE001 — fechar nunca derruba a chamada
        logger.warning("Falha ao fechar SEIClient despejado do cache: %s", e)


def _fabrica_padrao(creds: dict):
    from .limite_login import guarda_para
    from .sei_client import SEIClient

    return SEIClient(**creds, permitir_arquivo_local=False, guarda_login=guarda_para(creds))


CACHE = CacheClientesSEI(
    fabrica=_fabrica_padrao,
    max_entradas=int(os.environ.get("SEI_CLIENT_CACHE_MAX", "32")),
    max_browser=int(os.environ.get("SEI_CLIENT_CACHE_MAX_BROWSER", "3")),
    ttl_s=float(os.environ.get("SEI_CLIENT_CACHE_TTL_S", "1800")),
)
