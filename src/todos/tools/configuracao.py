"""Tools de configuração do SEI e validação de `protocolo_formatado`.

RFC 0010 (descoberta automática via keyring) e RFC 0017 (validação lazy sobre
essa descoberta) foram **revogadas**: a validação de `protocolo_formatado`
hoje é um regex fixo, sem descoberta, sem persistência em keyring e sem tools
extras — ver `_validar_protocolo` abaixo.

Exporta `_ProtocoloFormatado` (tipo Annotated com a descrição do argumento) e
`_validar_protocolo`, usados pelas tools de processo em `processos.py`.

Sem `from __future__ import annotations`: o FastMCP introspecta os type hints em
tempo de execução para montar o schema de cada tool, então as anotações precisam
ser objetos reais (não strings adiadas).
"""

import re
from typing import Annotated

from pydantic import Field

from todos.catalog_cache import get_catalog_cache
from todos.exceptions import SEIValidationError
from todos.mcp_app import _IDEM, _READ, _json, mcp

_PROTOCOLO_DESC = (
    "Número de protocolo do processo. Aceita o formato SEI administrativo clássico "
    "(NNNN[NN].NNNNNN/AAAA-DD, prefixo de 4 a 6 dígitos — ex: 50300.000123/2025-00) "
    "ou o formato CNJ usado pelo SEI de tribunais como o TJRO "
    "(NNNNNNN-DD.AAAA.J.TR.OOOO — ex: 7002098-94.2026.8.22.0014). Outros formatos "
    "também são aceitos: instâncias SEI com um formato ainda não mapeado não ficam bloqueadas."
)

# Dois formatos fixos, tentados em sequência — sem descoberta nem configuração externa
# (RFC 0010/0017 revogadas). Cobrem os dois padrões de numeração processual conhecidos
# hoje neste repo: o SEI administrativo clássico e o CNJ (usado pelo SEI do Judiciário,
# como o TJRO, que roda sobre a numeração unificada do CNJ).
_SEI_PROTOCOLO_RE = re.compile(r"^\d{4,6}\.\d{6}/\d{4}-\d{2}$")
_CNJ_PROTOCOLO_RE = re.compile(r"^\d{7}-\d{2}\.\d{4}\.\d\.\d{2}\.\d{4}$")

_ProtocoloFormatado = Annotated[str, Field(description=_PROTOCOLO_DESC)]


def _formato_protocolo(protocolo: str) -> str | None:
    """Classifica protocolo_formatado como 'sei', 'cnj', ou None (formato não mapeado).

    Uso só para normalização/diagnóstico — nunca bloqueia uma entrada não
    reconhecida (essa é a responsabilidade de `_validar_protocolo`, que trata
    o retorno None como aceitável via fallback permissivo).
    """
    if _SEI_PROTOCOLO_RE.fullmatch(protocolo):
        return "sei"
    if _CNJ_PROTOCOLO_RE.fullmatch(protocolo):
        return "cnj"
    return None


def _validar_protocolo(protocolo: str) -> None:
    """Valida protocolo_formatado com regex fixo (RFC 0010/0017 revogadas).

    Reconhece dois formatos, tentados em sequência (ver `_formato_protocolo`):
    SEI administrativo clássico e CNJ. Fallback permissivo **obrigatório**: uma
    string não vazia que não bata em nenhum dos dois formatos ainda é aceita —
    o objetivo é normalizar/validar os casos já mapeados, nunca bloquear uma
    instância SEI com um formato ainda desconhecido. Só rejeita entrada
    vazia/só espaços, que não é um protocolo em nenhuma leitura.
    """
    if not protocolo.strip():
        msg = "protocolo_formatado não pode ser vazio."
        raise SEIValidationError(msg)
    # Formato reconhecido ou não, seguimos em frente — _formato_protocolo() já
    # documentou os casos conhecidos; strings desconhecidas passam pelo fallback.


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
