"""Tools de credenciamento em processos sigilosos.

Credenciamento (REST-only, mod-wssei) controla quem acessa um processo de nível
sigiloso: conceder, renunciar e cassar acesso, além de listar os credenciados.

Sem `from __future__ import annotations`: o FastMCP introspecta os type hints em
tempo de execução para montar o schema de cada tool, então as anotações precisam
ser objetos reais (não strings adiadas).
"""

from fastmcp import Context

from todos.mcp_app import _DEST, _READ, _json, _rest_backend, mcp
from todos.responses import CredenciamentoSEI, NextAction, PaginadoGenerico


@mcp.tool(annotations=_READ)
async def sei_listar_credenciamentos(
    processo: str,
    ctx: Context | None = None,
) -> PaginadoGenerico[CredenciamentoSEI]:
    """Lista credenciamentos de acesso a um processo sigiloso.

    Disponível desde mod-wssei 2.0.0 (SEI 4.0.x).
    Se falhar com erro inesperado, use sei_versao para verificar a versão instalada.
    """
    backend = await _rest_backend(ctx)
    raw = await backend.listar_credenciamentos(processo)
    itens = [CredenciamentoSEI.model_validate(c) for c in raw]
    if itens:
        actions = [
            NextAction(
                tool="sei_cassar_credenciamento",
                args={"processo": processo},
                reason="Revogar acesso de um usuário credenciado neste processo sigiloso.",
            )
        ]
    else:
        actions = [
            NextAction(
                tool="sei_conceder_credenciamento",
                args={"processo": processo},
                reason="Nenhum credenciamento encontrado — conceder acesso ao primeiro usuário.",
            )
        ]
    return PaginadoGenerico[CredenciamentoSEI](
        total_itens=len(itens),
        proximo_cursor=None,
        itens=itens,
        next_actions=actions,
    )


@mcp.tool(annotations=_DEST)
async def sei_conceder_credenciamento(
    processo: str,
    id_usuario: str,
    ctx: Context | None = None,
) -> str:
    """Concede credenciamento de acesso a um processo sigiloso para um usuário.

    Disponível desde mod-wssei 2.0.0 (SEI 4.0.x).
    Se falhar com erro inesperado, use sei_versao para verificar a versão instalada.
    """
    backend = await _rest_backend(ctx)
    result = await backend.conceder_credenciamento(processo, id_usuario)
    return _json(result)


@mcp.tool(annotations=_DEST)
async def sei_renunciar_credenciamento(
    processo: str,
    ctx: Context | None = None,
) -> str:
    """Renuncia ao credenciamento de acesso a um processo sigiloso.

    O próprio usuário perde o acesso ao processo.
    Disponível desde mod-wssei 2.0.0 (SEI 4.0.x).
    Se falhar com erro inesperado, use sei_versao para verificar a versão instalada.
    """
    backend = await _rest_backend(ctx)
    result = await backend.renunciar_credenciamento(processo)
    return _json(result)


@mcp.tool(annotations=_DEST)
async def sei_cassar_credenciamento(
    processo: str,
    id_usuario: str,
    ctx: Context | None = None,
) -> str:
    """Cassa (revoga) credenciamento de acesso de um usuário a processo sigiloso.

    Disponível desde mod-wssei 2.0.0 (SEI 4.0.x).
    Se falhar com erro inesperado, use sei_versao para verificar a versão instalada.
    """
    backend = await _rest_backend(ctx)
    result = await backend.cassar_credenciamento(processo, id_usuario)
    return _json(result)
