"""Limite de logins recusados por usuário do SEI (modo HTTP).

Senha errada repetida BLOQUEIA a conta no SEI. Este módulo garante que o MCP
não seja o caminho desse bloqueio — nem pelo formulário /login (que, ao
conferir a senha, vira um oráculo de senha), nem pelas tools:

  - cada login recusado pelo SEI conta uma falha para (host do SEI, órgão, usuário);
  - com `max_falhas` falhas dentro de `janela_s`, novos logins desse usuário
    são recusados SEM requisição ao SEI (SEILoginSuspenso) até a falha mais
    antiga sair da janela;
  - logins em andamento contam como falha até terminarem — dez POSTs paralelos
    não passam juntos pela checagem antes de a primeira recusa ser registrada;
  - sucesso não zera o contador: um atacante que intercala tentativas com
    logins legítimos do dono continua limitado a `max_falhas` por janela.

A chave é o usuário, não a sessão: o bloqueio no SEI é por conta. Consequência
aceita: sessões antigas com senha velha consomem a cota do mesmo usuário.

Estado EM MEMÓRIA, por processo (ver sessao_cache.py sobre vários workers).

Variáveis de ambiente:
  SEI_LOGIN_MAX_FALHAS (padrão 3) / SEI_LOGIN_JANELA_S (padrão 900 = 15 min)
"""

from __future__ import annotations

import os
import time
from collections import deque
from urllib.parse import urlparse

from .sei_client import SEILoginSuspenso


def chave_usuario(sei_url: str, usuario: str, orgao: str) -> str:
    host = (urlparse(str(sei_url or "")).hostname or "").lower()
    return f"{host}|{str(orgao or '0').strip()}|{str(usuario or '').strip().lower()}"


class LimiteLogin:
    def __init__(self, max_falhas: int = 3, janela_s: float = 900.0, relogio=time.monotonic):
        self.max_falhas = int(max_falhas)
        self.janela_s = float(janela_s)
        self._relogio = relogio
        self._falhas: dict[str, deque[float]] = {}
        self._em_andamento: dict[str, int] = {}

    def _podar(self, chave: str) -> deque[float]:
        fila = self._falhas.setdefault(chave, deque())
        limite = self._relogio() - self.janela_s
        while fila and fila[0] <= limite:
            fila.popleft()
        return fila

    def segundos_suspenso(self, chave: str) -> float:
        """>0 se um novo login deste usuário seria recusado agora."""
        fila = self._podar(chave)
        ocupados = len(fila) + self._em_andamento.get(chave, 0)
        if ocupados < self.max_falhas:
            return 0.0
        if len(fila) >= self.max_falhas:
            # libera quando a falha que estoura o limite sair da janela
            return fila[len(fila) - self.max_falhas] + self.janela_s - self._relogio()
        return 60.0  # vagas presas por logins em andamento: liberam em segundos

    def reservar(self, chave: str) -> None:
        espera = self.segundos_suspenso(chave)
        if espera > 0:
            raise SEILoginSuspenso(espera, self.max_falhas, self.janela_s)
        self._em_andamento[chave] = self._em_andamento.get(chave, 0) + 1

    def liberar(self, chave: str, recusada: bool) -> None:
        n = self._em_andamento.get(chave, 0) - 1
        if n > 0:
            self._em_andamento[chave] = n
        else:
            self._em_andamento.pop(chave, None)
        if recusada:
            self._podar(chave).append(self._relogio())
        elif not self._falhas.get(chave):
            self._falhas.pop(chave, None)

    def limpar(self) -> None:
        self._falhas.clear()
        self._em_andamento.clear()


class GuardaLogin:
    """O LimiteLogin já ligado a um usuário — é o que o SEIClient recebe."""

    def __init__(self, limite: LimiteLogin, chave: str):
        self._limite = limite
        self.chave = chave

    def reservar(self) -> None:
        self._limite.reservar(self.chave)

    def liberar(self, recusada: bool) -> None:
        self._limite.liberar(self.chave, recusada)


LIMITE = LimiteLogin(
    max_falhas=int(os.environ.get("SEI_LOGIN_MAX_FALHAS", "3")),
    janela_s=float(os.environ.get("SEI_LOGIN_JANELA_S", "900")),
)


def guarda_para(creds: dict) -> GuardaLogin:
    return GuardaLogin(
        LIMITE,
        chave_usuario(creds.get("sei_url", ""), creds.get("sei_usuario", ""),
                      creds.get("sei_orgao", "0")),
    )
