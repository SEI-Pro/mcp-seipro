"""Tools de acompanhamento especial de processos.

Acompanhamento especial permite a um usuário ou unidade "seguir" um processo
para monitorar atualizações mesmo sem tê-lo na caixa, organizando os processos
seguidos em grupos.

Sem `from __future__ import annotations`: o FastMCP introspecta os type hints em
tempo de execução para montar o schema de cada tool, então as anotações precisam
ser objetos reais (não strings adiadas).
"""

from fastmcp import Context

from todos.mcp_app import (
    _DEST,
    _IDEM,
    _READ,
    _WRITE,
    _add_cursor,
    _backend,
    _decode_cursor,
    _json,
    mcp,
)
from todos.responses import AcompanhamentoSEI, PaginadoGenerico

_DEFAULT_LIMIT = 50


@mcp.tool(annotations=_WRITE)
async def sei_acompanhar_processo(
    processo: str,
    grupo: str = "",
    observacao: str = "",
    ctx: Context | None = None,
) -> str:
    """Adiciona acompanhamento especial em um processo.

    Parâmetros:
    - processo: protocolo formatado ou IdProcedimento
    - grupo: ID do grupo de acompanhamento.
      Passe "?" (ponto de interrogação) para entrar no modo de descoberta:
      em vez de criar o acompanhamento, a tool lista os grupos disponíveis
      para que você possa escolher o ID correto e chamar novamente.
    - observacao: observação/anotação do acompanhamento

    """
    backend = await _backend(ctx)
    if grupo == "?":
        # Modo de descoberta: "?" lista os grupos disponíveis em vez de operar.
        result = await backend.listar_grupos_acompanhamento(filtro="")
        return _json({"grupos_disponiveis": result})
    result = await backend.acompanhar_processo(processo, grupo, observacao)
    return _json(result)


@mcp.tool(annotations=_IDEM)
async def sei_remover_acompanhamento(
    processo: str,
    ctx: Context | None = None,
) -> str:
    """Remove acompanhamento especial de um processo.

    QUANDO NÃO USAR: se quiser excluir o GRUPO de acompanhamento (não apenas
    remover um processo do grupo), use sei_excluir_grupo_acompanhamento.
    """
    backend = await _backend(ctx)
    result = await backend.remover_acompanhamento(processo)
    return _json(result)


@mcp.tool(annotations=_WRITE)
async def sei_criar_grupo_acompanhamento(
    nome: str,
    ctx: Context | None = None,
) -> str:
    """Cria um grupo de acompanhamento especial no SEI.

    Grupos organizam os processos acompanhados em categorias.
    Exemplos de nome: "Processos prioritários", "Aguardando resposta".

    QUANDO NÃO USAR: para acompanhar um processo sem criar grupo, use
    sei_acompanhar_processo com grupo="" (grupo padrão da unidade).
    Retorna {"id": "<id_grupo>", "ok": True}.
    """
    backend = await _backend(ctx)
    result = await backend.criar_grupo_acompanhamento(nome)
    return _json(result)


@mcp.tool(annotations=_DEST)
async def sei_excluir_grupo_acompanhamento(
    ids_grupos: str,
    ctx: Context | None = None,
) -> str:
    """Exclui grupo(s) de acompanhamento especial. IDs separados por vírgula."""
    backend = await _backend(ctx)
    result = await backend.excluir_grupo_acompanhamento(ids_grupos)
    return _json(result)


@mcp.tool(annotations=_READ)
async def sei_listar_grupos_acompanhamento(
    filtro: str = "",
    ctx: Context | None = None,
) -> str:
    """Lista grupos de acompanhamento disponíveis.

    Em instâncias sem mod-wssei, extrai os grupos do formulário de acompanhamento
    via web scraper (requer ao menos um processo aberto na caixa da unidade).
    """
    backend = await _backend(ctx)
    result = await backend.listar_grupos_acompanhamento(filtro=filtro)
    return _json(result)


@mcp.tool(annotations=_READ)
async def sei_listar_meus_acompanhamentos(
    limit: int = _DEFAULT_LIMIT,
    pagina: int = 0,
    cursor: str = "",
    ctx: Context | None = None,
) -> PaginadoGenerico[AcompanhamentoSEI]:
    """Lista processos que o usuário está acompanhando (acompanhamento especial).

    Disponível desde mod-wssei 2.0.0 (SEI 4.0.x).
    Se falhar com erro inesperado, use sei_versao para verificar a versão instalada.

    Paginação: passe cursor = proximo_cursor da resposta anterior.
    """
    if cursor:
        decoded = _decode_cursor(cursor)
        pagina = decoded.get("p", pagina)
        limit = decoded.get("limit", limit)
    backend = await _backend(ctx)
    result = await backend.listar_meus_acompanhamentos(limit=limit, pagina=pagina)
    result["itens"] = result.pop("acompanhamentos", [])
    return PaginadoGenerico[AcompanhamentoSEI].model_validate(
        _add_cursor(
            result,
            pagina=pagina,
            limit=limit,
            tool_name="sei_listar_meus_acompanhamentos",
            cursor_extra={"limit": limit},
        )
    )


@mcp.tool(annotations=_READ)
async def sei_listar_acompanhamentos_unidade(
    limit: int = _DEFAULT_LIMIT,
    pagina: int = 0,
    cursor: str = "",
    ctx: Context | None = None,
) -> PaginadoGenerico[AcompanhamentoSEI]:
    """Lista processos com acompanhamento especial na unidade atual.

    Disponível desde mod-wssei 2.0.0 (SEI 4.0.x).
    Se falhar com erro inesperado, use sei_versao para verificar a versão instalada.

    Paginação: passe cursor = proximo_cursor da resposta anterior.
    """
    if cursor:
        decoded = _decode_cursor(cursor)
        pagina = decoded.get("p", pagina)
        limit = decoded.get("limit", limit)
    backend = await _backend(ctx)
    result = await backend.listar_acompanhamentos_unidade(limit=limit, pagina=pagina)
    result["itens"] = result.pop("acompanhamentos", [])
    return PaginadoGenerico[AcompanhamentoSEI].model_validate(
        _add_cursor(
            result,
            pagina=pagina,
            limit=limit,
            tool_name="sei_listar_acompanhamentos_unidade",
            cursor_extra={"limit": limit},
        )
    )


@mcp.tool(annotations=_IDEM)
async def sei_alterar_acompanhamento(
    processo: str,
    grupo: str = "",
    observacao: str = "",
    ctx: Context | None = None,
) -> str:
    """Altera acompanhamento especial de um processo.

    - processo: protocolo formatado ou IdProcedimento
    - grupo: novo grupo de acompanhamento
    - observacao: nova observação

    Disponível desde mod-wssei 2.0.0 (SEI 4.0.x).
    Se falhar com erro inesperado, use sei_versao para verificar a versão instalada.

    """
    backend = await _backend(ctx)
    result = await backend.alterar_acompanhamento(processo, grupo, observacao)
    return _json(result)
