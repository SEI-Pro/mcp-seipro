"""Tools de configuração e descoberta automática do SEI (RFC 0010).

Gerencia a detecção automática do formato de número de protocolo desta instância
SEI, com persistência via keyring para uso automático nas sessões futuras.

Exporta `_ProtocoloFormatado` — o tipo Annotated usado pelas tools de processos
para validar o protocolo_formatado recebido como argumento.

Sem `from __future__ import annotations`: o FastMCP introspecta os type hints em
tempo de execução para montar o schema de cada tool, então as anotações precisam
ser objetos reais (não strings adiadas).
"""

import asyncio
import concurrent.futures
import logging
import os
import re
from types import ModuleType
from typing import Annotated
from urllib.parse import urlparse

from fastmcp import Context
from pydantic import Field, TypeAdapter
from pydantic_core import SchemaError as _SchemaError

from todos.exceptions import SEIValidationError
from todos.mcp_app import _IDEM, _backend, _get_web_client, _json, mcp

logger = logging.getLogger(__name__)

_keyring_mod: ModuleType | None = None
# Sentinel: never raised naturally; replaced by keyring.errors.KeyringError when keyring is
# installed so KeyringLocked and other backend-specific errors are caught alongside builtins.
_KeyringError = type("_NullError", (Exception,), {})
try:
    import keyring as _keyring_mod
    from keyring.errors import KeyringError as _KeyringError
except ImportError:
    logger.info("keyring not available — use SEI_PROTOCOLO_PATTERN env var.")

_PROTOCOLO_DESC = (
    "Número de protocolo SEI no formato NNNNN.NNNNNN/AAAA-DD, ex: 50300.000123/2025-00"
)
_CANONICAL_PROTOCOLO_RE = re.compile(r"^(\d+)\.(\d{6})/(\d{4})-(\d{2})$")
_KEYRING_SERVICE = "todos-mcp"


def _sei_host() -> str:
    """Retorna o netloc da instância SEI configurada via env var (startup only)."""
    for var in ("SEI_WEB_URL", "SEI_URL"):
        url = os.environ.get(var, "").strip()
        if url:
            return urlparse(url).netloc
    return ""


async def _sei_host_from_ctx(ctx: Context | None) -> str:
    """Deriva o netloc da instância ativa: web client quando ctx disponível, env var como fallback.

    Em modo HTTP/OAuth a URL vem dos tokens por usuário, não de env vars, por isso o web client
    é a fonte canônica quando ctx está presente.
    """
    if ctx is not None:
        try:
            web = await _get_web_client(ctx)
            if web.sei_root:
                return urlparse(web.sei_root).netloc
        except (ValueError, AttributeError, OSError, RuntimeError):
            pass
    return _sei_host()


def _keyring_pattern_key(host: str) -> str:
    """Chave do keyring para o padrão de protocolo desta instância."""
    return f"SEI_PROTOCOLO_PATTERN@{host}"


def _read_keyring_pattern_sync(host: str) -> str:
    """Lê SEI_PROTOCOLO_PATTERN do keyring com timeout de 2 s; retorna "" em caso de falha."""
    if _keyring_mod is None or not host:
        return ""
    key = _keyring_pattern_key(host)
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    try:
        future = pool.submit(_keyring_mod.get_password, _KEYRING_SERVICE, key)
        return future.result(timeout=2.0) or ""
    except (TimeoutError, OSError, RuntimeError, AttributeError, ValueError, _KeyringError) as exc:
        logger.debug("keyring read failed: %s", exc)
        return ""
    finally:
        # cancel_futures=True (Python 3.9+) abandons the thread if get_password is
        # still running after the timeout — avoids blocking server startup.
        pool.shutdown(wait=False, cancel_futures=True)


_host = _sei_host()
# Env var takes priority so an explicit SEI_PROTOCOLO_PATTERN always overrides a stale
# keyring entry without requiring sei_redefinir_formato_protocolo to be run first.
_SEI_PROTOCOLO_PATTERN = os.environ.get("SEI_PROTOCOLO_PATTERN", "") or _read_keyring_pattern_sync(
    _host
)
_ProtocoloFormatadoBase = Annotated[str, Field(description=_PROTOCOLO_DESC)]
if _SEI_PROTOCOLO_PATTERN:
    _candidate = Annotated[str, Field(pattern=_SEI_PROTOCOLO_PATTERN, description=_PROTOCOLO_DESC)]
    try:
        TypeAdapter(_candidate)  # forces Pydantic/Rust to compile the regex now
        _ProtocoloFormatado = _candidate
    except _SchemaError:
        logger.warning(
            "SEI_PROTOCOLO_PATTERN=%r é um regex inválido "
            "para o motor Pydantic/Rust — constraint ignorado, qualquer string será aceita.",
            _SEI_PROTOCOLO_PATTERN,
        )
        _ProtocoloFormatado = _ProtocoloFormatadoBase
else:
    _ProtocoloFormatado = _ProtocoloFormatadoBase

# ---------------------------------------------------------------------------
# Descoberta automática do formato de protocolo
# ---------------------------------------------------------------------------

_MIN_AMOSTRAS = 3


def _inferir_padrao_protocolo(amostras: list[str]) -> tuple[str, int, int]:
    """Infere o regex de protocolo a partir de uma lista de protocolos reais.

    Retorna (padrao, prefixo_min, prefixo_max).
    Levanta SEIValidationError se nenhuma amostra bater no formato canônico.
    """
    prefix_lens = [len(m.group(1)) for s in amostras if (m := _CANONICAL_PROTOCOLO_RE.match(s))]
    if not prefix_lens:
        msg = (
            "Nenhum protocolo no formato esperado (NNNNN.NNNNNN/AAAA-DD) foi encontrado "
            "nas amostras coletadas. Verifique se há processos na caixa da unidade."
        )
        raise SEIValidationError(msg)
    min_len = min(prefix_lens)
    max_len = max(prefix_lens)
    if min_len == max_len:
        padrao = rf"^\d{{{min_len}}}\.\d{{6}}/\d{{4}}-\d{{2}}$"
    else:
        padrao = rf"^\d{{{min_len},{max_len}}}\.\d{{6}}/\d{{4}}-\d{{2}}$"
    return padrao, min_len, max_len


@mcp.tool(annotations=_IDEM)
async def sei_detectar_formato_protocolo(
    ctx: Context | None = None,
) -> str:
    """Detecta o formato do número de processo desta instância SEI e persiste no keyring.

    Lê os processos reais da caixa de entrada, extrai os números de protocolo,
    infere o regex e persiste no keyring para uso automático nas sessões futuras.

    Use na primeira sessão com uma nova instância SEI. O padrão detectado é
    carregado automaticamente no próximo startup, ativando validação de formato
    em todas as tools que recebem protocolo_formatado.

    Retorna o padrão detectado, o número de amostras e se foi persistido.
    """
    backend = await _backend(ctx)
    result = await backend.listar_processos(pagina=0)
    processos = result.get("processos") or []
    todas_strings = [
        proto
        for p in processos
        if (
            proto := str(
                # web backend: "protocolo" or "Processo"
                # REST backend: atributos.numero (used by sei_resumo_processos)
                p.get("protocolo")
                or p.get("Processo")
                or (p.get("atributos") or {}).get("numero")
                or ""
            ).strip()
        )
    ]
    # Only count entries that actually match the canonical format — mixed inboxes
    # may contain legacy or non-protocol rows that _inferir_padrao_protocolo ignores.
    amostras = [s for s in todas_strings if _CANONICAL_PROTOCOLO_RE.match(s)]
    if len(amostras) < _MIN_AMOSTRAS:
        msg = (
            f"Amostras válidas insuficientes: {len(amostras)} protocolo(s) no formato "
            f"esperado (de {len(todas_strings)} linha(s) encontradas), "
            f"mínimo {_MIN_AMOSTRAS}. Verifique se há processos na caixa da unidade "
            "ou tente sei_listar_processos para confirmar."
        )
        raise SEIValidationError(msg)

    padrao, prefixo_min, prefixo_max = _inferir_padrao_protocolo(amostras)

    host = await _sei_host_from_ctx(ctx)
    persistido = False
    if _keyring_mod is not None and host:
        key = _keyring_pattern_key(host)
        try:
            await asyncio.wait_for(
                asyncio.to_thread(_keyring_mod.set_password, _KEYRING_SERVICE, key, padrao),
                timeout=2.0,
            )
            persistido = True
        except (TimeoutError, OSError, RuntimeError, AttributeError, ValueError, _KeyringError):
            persistido = False

    if not persistido:
        logger.warning(
            "sei_detectar_formato_protocolo: padrão detectado mas não persistido no keyring"
        )
    return _json(
        {
            "padrao": padrao,
            "amostras": len(amostras),
            "prefixo_min": prefixo_min,
            "prefixo_max": prefixo_max,
            "persistido": persistido,
            "chave_keyring": _keyring_pattern_key(host) if host else None,
            "aviso": (
                None
                if persistido
                else "keyring indisponível — configure SEI_PROTOCOLO_PATTERN manualmente."
            ),
            "_next": ["sei_listar_processos"],
        }
    )


@mcp.tool(annotations=_IDEM)
async def sei_redefinir_formato_protocolo(
    ctx: Context | None = None,
) -> str:
    """Remove o padrão de protocolo persistido no keyring desta instância SEI.

    Use quando a instância SEI for migrada ou o padrão detectado estiver incorreto.
    Na próxima chamada a sei_detectar_formato_protocolo() a descoberta recomeça do zero.

    Não afeta a env var SEI_PROTOCOLO_PATTERN (configuração manual).
    """
    host = await _sei_host_from_ctx(ctx)
    if not host:
        msg = (
            "Não foi possível determinar o host da instância SEI. Configure SEI_WEB_URL ou SEI_URL."
        )
        raise SEIValidationError(msg)
    key = _keyring_pattern_key(host)
    if _keyring_mod is None:
        msg = (
            "keyring não está instalado — não há padrão persistido para remover. "
            "Se você configurou SEI_PROTOCOLO_PATTERN manualmente, remova a env var."
        )
        raise SEIValidationError(msg)
    removido = False
    try:
        await asyncio.wait_for(
            asyncio.to_thread(_keyring_mod.delete_password, _KEYRING_SERVICE, key),
            timeout=2.0,
        )
        removido = True
    except (TimeoutError, OSError, RuntimeError, AttributeError, ValueError, _KeyringError):
        removido = False
    return _json(
        {
            "removido": removido,
            "chave_keyring": key,
            "mensagem": (
                f"Padrão removido do keyring (chave: {key}). "
                "Na próxima sessão, chame sei_detectar_formato_protocolo para redescobrir."
            )
            if removido
            else (
                f"Não foi possível remover a entrada do keyring (chave: {key}). "
                "A entrada pode já ter sido removida anteriormente, ou o keyring está "
                "bloqueado/indisponível. Verifique se o padrão persiste na próxima sessão."
            ),
            "_next": ["sei_detectar_formato_protocolo"],
        }
    )
