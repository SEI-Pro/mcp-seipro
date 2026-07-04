"""Tools de configuração e descoberta automática do SEI (RFC 0010).

Gerencia a detecção automática do formato de número de protocolo desta instância
SEI, com persistência via keyring para uso automático nas sessões futuras.

Exporta `_ProtocoloFormatado` (tipo Annotated com a descrição do argumento) e
`_validar_protocolo` — a validação lazy do formato do protocolo, feita no corpo
de cada tool de processo (RFC 0017), não mais via `Field(pattern=...)` em
import-time.

Sem `from __future__ import annotations`: o FastMCP introspecta os type hints em
tempo de execução para montar o schema de cada tool, então as anotações precisam
ser objetos reais (não strings adiadas).
"""

import asyncio
import concurrent.futures
import logging
import re
from functools import lru_cache
from types import ModuleType
from typing import Annotated
from urllib.parse import urlparse

from fastmcp import Context
from pydantic import Field

from todos.backends.choice import requires_backend
from todos.catalog_cache import get_catalog_cache
from todos.exceptions import SEIValidationError
from todos.mcp_app import _IDEM, _READ, _backend, _get_web_client, _json, mcp
from todos.settings import get_settings

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
    """Retorna o netloc da instância SEI configurada (startup only)."""
    settings = get_settings()
    for url in (settings.sei_web_url, settings.sei_url):
        if url.strip():
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
        except (ValueError, AttributeError, OSError, RuntimeError) as exc:
            logger.debug(
                "_sei_host_from_ctx: fallback para env var — %s: %s", type(exc).__name__, exc
            )
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
    except (OSError, RuntimeError) as exc:
        logger.warning("keyring read failed: %s", exc)
        return ""
    except (TimeoutError, AttributeError, ValueError, _KeyringError) as exc:
        logger.debug("keyring read failed: %s", exc)
        return ""
    finally:
        # cancel_futures=True (Python 3.9+) abandons the thread if get_password is
        # still running after the timeout — avoids blocking server startup.
        pool.shutdown(wait=False, cancel_futures=True)


# RFC 0017: a validação do protocolo é lazy, no corpo de cada tool (via
# _validar_protocolo), não mais via Field(pattern=...) compilado em import-time.
# Isso torna sei_redefinir/sei_detectar_formato_protocolo efetivos na sessão
# corrente e o padrão testável com monkeypatch + cache_clear. O schema MCP do
# argumento continua `{"type": "string"}` — a constraint é enforced pelo servidor.
_ProtocoloFormatado = Annotated[str, Field(description=_PROTOCOLO_DESC)]


@lru_cache(maxsize=8)
def _compilar_pattern(raw: str) -> re.Pattern[str] | None:
    """Compila um regex de protocolo; retorna None (com aviso) se inválido."""
    try:
        return re.compile(raw)
    except re.error as exc:
        logger.warning(
            "SEI_PROTOCOLO_PATTERN=%r é um regex inválido — constraint ignorado: %s",
            raw,
            exc,
        )
        return None


# Cache do padrão lido do keyring, por host. A env var tem prioridade e é lida fresh
# a cada chamada (get_settings é cacheado por processo); só a leitura do keyring — que
# pode bloquear até 2 s — é cacheada. Invalidado por sei_detectar/sei_redefinir.
_keyring_pattern_cache: dict[str, str] = {}


def _resolver_pattern_sync(host: str) -> re.Pattern[str] | None:
    """Resolve o regex de protocolo (env var > keyring) e compila; cacheia o keyring.

    Env var sempre tem prioridade, garantindo que SEI_PROTOCOLO_PATTERN sobreponha
    uma entrada de keyring obsoleta sem exigir sei_redefinir_formato_protocolo.
    """
    env_pattern = get_settings().sei_protocolo_pattern.strip()
    if env_pattern:
        return _compilar_pattern(env_pattern)
    if host not in _keyring_pattern_cache:
        _keyring_pattern_cache[host] = _read_keyring_pattern_sync(host).strip()
    raw = _keyring_pattern_cache[host]
    return _compilar_pattern(raw) if raw else None


async def _validar_protocolo(protocolo: str, ctx: Context | None = None) -> None:
    """Valida protocolo_formatado contra o padrão configurado (env var > keyring).

    No-op quando nenhum padrão está configurado. Levanta SEIValidationError quando
    o protocolo não bate no padrão. Lazy: lê o padrão a cada chamada, então
    sei_detectar/sei_redefinir_formato_protocolo passam a valer na sessão corrente.
    """
    host = await _sei_host_from_ctx(ctx)
    pattern = await asyncio.to_thread(_resolver_pattern_sync, host)
    if pattern is not None and not pattern.fullmatch(protocolo):
        msg = (
            f"protocolo_formatado {protocolo!r} não bate no padrão configurado para esta "
            f"instância SEI ({pattern.pattern}). Verifique o número ou rode "
            "sei_detectar_formato_protocolo."
        )
        raise SEIValidationError(msg)


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
@requires_backend
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
    # Invalida o cache do keyring para este host — a próxima validação relê o novo padrão.
    _keyring_pattern_cache.pop(host, None)
    persistido = False
    if _keyring_mod is not None and host:
        key = _keyring_pattern_key(host)
        try:
            await asyncio.wait_for(
                asyncio.to_thread(_keyring_mod.set_password, _KEYRING_SERVICE, key, padrao),
                timeout=2.0,
            )
            persistido = True
        except (OSError, RuntimeError) as exc:
            logger.warning("keyring write failed for pattern: %s", exc)
            persistido = False
        except (TimeoutError, AttributeError, ValueError, _KeyringError) as exc:
            logger.debug("keyring write failed for pattern: %s", exc)
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
            "_next": [{"tool": "sei_listar_processos", "args": {}}],
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
    # Invalida o cache do keyring para este host — a próxima validação relê (padrão removido).
    _keyring_pattern_cache.pop(host, None)
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
    except (OSError, RuntimeError) as exc:
        logger.warning("keyring delete failed: %s", exc)
        removido = False
    except (TimeoutError, AttributeError, ValueError, _KeyringError) as exc:
        logger.debug("keyring delete failed: %s", exc)
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
            "_next": [{"tool": "sei_detectar_formato_protocolo", "args": {}}],
        }
    )


# ---------------------------------------------------------------------------
# Cache de catálogos (RFC 0019 §2.2)
# ---------------------------------------------------------------------------


@mcp.tool(annotations=_READ)
async def sei_cache_status() -> str:
    """Retorna estatísticas do cache de catálogos em disco (total, fresh, bytes).

    - total: número de entradas armazenadas (válidas ou expiradas, ainda não varridas)
    - fresh: entradas ainda dentro do TTL (não expiradas)
    - bytes: tamanho em disco do arquivo de cache (inclui WAL/SHM não sincronizados)
    """
    stats = await get_catalog_cache().stats()
    return _json(stats)


@mcp.tool(annotations=_IDEM)
async def sei_cache_clear(
    older_than_seconds: Annotated[
        float | None,
        Field(
            description="Remove só entradas gravadas há mais tempo que isso; omitido = limpa tudo."
        ),
    ] = None,
) -> str:
    """Limpa o cache de catálogos em disco, total ou seletivamente.

    - older_than_seconds: remove só entradas com essa idade ou mais. Entradas de
      antes desta funcionalidade (sem data de gravação registrada) contam como
      antigas e são removidas. Omitido: limpa o cache inteiro.

    Retorna quantas entradas foram removidas.
    """
    removidas = await get_catalog_cache().clear(older_than_seconds=older_than_seconds)
    return _json({"removidas": removidas})
