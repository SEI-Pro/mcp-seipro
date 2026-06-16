"""Backend composto: roteia REST-first com fallback para web.

`CompositeBackend` recebe um backend REST (opcional) e um backend web e expõe o
mesmo contrato `SEIBackend`. Para cada operação:

- Por padrão tenta o REST primeiro; se o REST não implementa a operação
  (`NotImplementedError`, herdado do stub da base), cai para o web.
- Operações em `_WEB_FIRST` invertem a ordem (web primeiro) — são casos onde o
  scraper é a fonte canônica ou muito mais rápido que a REST.
- Se nenhum backend disponível implementa a operação, levanta
  `SEINotImplementedError` (subclasse de `SEIError`, capturável pelas tools).

`consultar_processo` é sobrescrito explicitamente: combina metadados ricos da
REST com a árvore de documentos do web em paralelo (composição genuína de
backends, que não cabe no padrão REST-first/web-fallback).

Os dispatchers genéricos são gerados a partir do contrato `SEIBackend` em
`_install_dispatchers`, evitando ~100 métodos de delegação idênticos.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
from typing import TYPE_CHECKING

import httpx

from todos.backends.base import SEIBackend
from todos.backends.rest import SEIRestBackend
from todos.backends.web import SEIWebBackend
from todos.exceptions import (
    SEIConnectionError,
    SEIError,
    SEINotFoundError,
    SEINotImplementedError,
    SEIParseError,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from todos.sei_client import SEIClient
    from todos.sei_web_client import SEIWebClient

logger = logging.getLogger(__name__)


# Operações onde o scraper web é preferido mesmo quando a REST está disponível
# (fonte canônica da árvore/andamentos, ou desempenho muito superior ao REST).
_WEB_FIRST = frozenset(
    {
        "listar_processos",
        "listar_documentos",
        "listar_atividades",
    }
)


class CompositeBackend(SEIBackend):
    """Backend que combina REST + web sob o contrato `SEIBackend`."""

    name = "composite"

    def __init__(self, rest: SEIRestBackend | None, web: SEIWebBackend) -> None:
        """Armazena o backend REST (opcional) e o backend web."""
        self._rest = rest
        self._web = web

    async def trocar_unidade(self, id_unidade: str) -> dict:
        """Troca a unidade no web (que controla a sessão) e sincroniza a REST."""
        try:
            result = await self._web.trocar_unidade(id_unidade)
        except httpx.RequestError as exc:
            msg = f"SEI inacessível: {exc}"
            raise SEIConnectionError(msg) from exc
        if self._rest is not None:
            # Sincroniza a REST para que tools REST usem a mesma unidade (best-effort).
            try:
                await self._rest.trocar_unidade(result.get("id_unidade", id_unidade))
            except (SEIError, httpx.HTTPError) as exc:
                logger.warning(
                    "Falha ao sincronizar unidade no backend REST (%s); "
                    "chamadas REST subsequentes podem operar na unidade errada.",
                    exc,
                )
        return result

    async def consultar_processo(self, processo: str) -> dict:
        """Combina metadados da REST com a árvore de documentos do web."""
        if self._rest is None:
            return await self._web.consultar_processo(processo)

        rest_task = asyncio.create_task(self._rest.consultar_processo(processo))
        web_task = asyncio.create_task(self._web.consultar_processo(processo))
        rest_result, web_result = await asyncio.gather(rest_task, web_task, return_exceptions=True)

        merged: dict = {}
        avisos: list[str] = []

        if isinstance(rest_result, dict):
            merged.update(rest_result)
        elif isinstance(rest_result, Exception):
            avisos.append(f"REST falhou: {rest_result}")

        if isinstance(web_result, dict):
            # Web complementa com documentos[]/relacionados[]; REST é canônica
            # para metadata, então só preenchemos chaves ainda ausentes.
            for chave, valor in web_result.items():
                merged.setdefault(chave, valor)
        elif isinstance(web_result, Exception):
            avisos.append(f"Web scraper falhou: {web_result}")

        if not merged:
            msg = "Ambas as fontes (REST e Web) falharam: " + " | ".join(avisos)
            raise SEIConnectionError(msg)

        if avisos:
            merged["_warnings"] = avisos
        return merged

    async def criar_documento_externo(self, processo: str, dados: object) -> dict:
        """Cria documento externo escolhendo a rota pela forma do upload.

        Upload por base64 é nativo do scraper web (modo remoto/sem REST), então
        é tentado web-first; upload por caminho de arquivo é REST-first com
        fallback web. Em ambos os casos vale o fallback padrão se o preferido
        não atender.
        """
        web_first = bool(getattr(dados, "arquivo_base64", ""))
        ordered = (self._web, self._rest) if web_first else (self._rest, self._web)
        result = await _dispatch_in_order("criar_documento_externo", ordered, (processo, dados), {})
        if not isinstance(result, dict):
            msg = f"criar_documento_externo: esperado dict, backend retornou {type(result).__name__!r}"
            raise SEIError(msg)
        return result


# Exceções que significam "este backend não atendeu" → tenta o próximo. Erros
# definitivos de domínio (permissão, validação, auth) NÃO estão aqui: propagam.
_FALLBACK_EXCS = (
    NotImplementedError,
    SEINotImplementedError,
    SEINotFoundError,
    SEIParseError,
    SEIConnectionError,
)


def _prioridade_erro(exc: Exception) -> int:
    """Prioridade de um erro de fallback (maior = mais informativo, prevalece).

    Um erro real de "este backend tentou e não conseguiu" (rede/404/HTML) deve
    prevalecer sobre um "este backend não serve esta op" (SEINotImplementedError),
    que por sua vez prevalece sobre o stub genérico (`NotImplementedError`).

    Lista fechada: adicione novos subtipos de SEIError aqui para que recebam
    a prioridade correta; subtipos não listados caem no bucket padrão (SEIError = 2).
    """
    if isinstance(exc, (SEINotFoundError, SEIParseError, SEIConnectionError)):
        return 3
    if isinstance(exc, SEIError):  # SEINotImplementedError carrega orientação útil
        return 2
    return 1  # NotImplementedError (stub da base, sem mensagem acionável)


async def _dispatch_in_order(
    op_name: str,
    backends: tuple[SEIBackend | None, ...],
    args: tuple[object, ...],
    kwargs: dict[str, object],
) -> object:
    """Tenta a operação nos backends em ordem, com a semântica de fallback padrão.

    Cai para o próximo backend nos sinais de "este backend não atendeu"
    (`_FALLBACK_EXCS`) e em erros de transporte (`httpx.RequestError`), mantendo o
    erro mais informativo via `_prioridade_erro`. Erros definitivos de domínio
    (permissão, validação, auth) propagam imediatamente, sem fallback.
    """
    ultimo: Exception | None = None
    for backend in backends:
        if backend is None:
            continue
        try:
            return await getattr(backend, op_name)(*args, **kwargs)
        except _FALLBACK_EXCS as exc:
            if ultimo is None or _prioridade_erro(exc) >= _prioridade_erro(ultimo):
                ultimo = exc
        except httpx.RequestError as exc:
            conn = SEIConnectionError(f"SEI inacessível: {exc}")
            conn.__cause__ = exc
            if ultimo is None or _prioridade_erro(conn) >= _prioridade_erro(ultimo):
                ultimo = conn
    if isinstance(ultimo, SEIError):
        raise ultimo
    msg = f"Operação '{op_name}' não é suportada por nenhum backend disponível."
    raise SEINotImplementedError(msg) from ultimo


def _make_dispatcher(op_name: str) -> Callable[..., Awaitable[object]]:
    """Cria um dispatcher REST-first (ou web-first) para uma operação do contrato."""

    async def _dispatch(self: CompositeBackend, *args: object, **kwargs: object) -> object:
        ordered = (self._web, self._rest) if op_name in _WEB_FIRST else (self._rest, self._web)
        return await _dispatch_in_order(op_name, ordered, args, kwargs)

    _dispatch.__name__ = op_name
    _dispatch.__qualname__ = f"CompositeBackend.{op_name}"
    return _dispatch


def _install_dispatchers() -> None:
    """Gera dispatchers genéricos para toda operação do contrato não sobrescrita."""
    explicitas = {nome for nome, membro in vars(CompositeBackend).items() if callable(membro)}
    for nome, _ in inspect.getmembers(SEIBackend, inspect.isfunction):
        if nome.startswith("_") or nome in explicitas:
            continue
        setattr(CompositeBackend, nome, _make_dispatcher(nome))


_install_dispatchers()


def build_backend(rest_client: SEIClient, web_client: SEIWebClient) -> SEIBackend:
    """Monta o backend composto a partir dos clientes REST e web.

    O backend REST só é incluído quando há `base_url` configurada (mod-wssei
    disponível); caso contrário, todas as operações caem para o web.
    """
    rest = SEIRestBackend(rest_client) if rest_client.base_url else None
    web = SEIWebBackend(web_client)
    return CompositeBackend(rest, web)
