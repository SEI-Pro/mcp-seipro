"""Tools de bloco interno do SEI.

Bloco interno agrupa processos para fins organizacionais (revisão coletiva,
despacho conjunto), sem envolver assinatura. Operações: criar, incluir/retirar
processos, listar, alterar, excluir, concluir, reabrir e anotar processos.

Sem `from __future__ import annotations`: o FastMCP introspecta os type hints em
tempo de execução para montar o schema de cada tool, então as anotações precisam
ser objetos reais (não strings adiadas).
"""

from fastmcp import Context

from todos.mcp_app import _DEST, _IDEM, _READ, _WRITE, _backend, _json, mcp


@mcp.tool(annotations=_WRITE)
async def sei_criar_bloco_interno(
    descricao: str,
    ctx: Context | None = None,
) -> str:
    """Cria um bloco interno no SEI.

    Blocos internos são usados para organizar processos em lotes.
    """
    backend = await _backend(ctx)
    result = await backend.criar_bloco_interno(descricao)
    return _json(result)


@mcp.tool(annotations=_IDEM)
async def sei_incluir_processo_bloco_interno(
    id_bloco: str,
    processos: str,
    ctx: Context | None = None,
) -> str:
    """Inclui processo(s) em um bloco interno.

    - id_bloco: ID do bloco
    - processos: IdProcedimento(s) separados por vírgula
    """
    backend = await _backend(ctx)
    result = await backend.incluir_processo_bloco_interno(id_bloco, processos)
    return _json(result)


@mcp.tool(annotations=_DEST)
async def sei_retirar_processo_bloco_interno(
    id_bloco: str,
    processos: str,
    ctx: Context | None = None,
) -> str:
    """Remove processo(s) de um bloco interno.

    - id_bloco: ID do bloco
    - processos: IdProcedimento(s) separados por vírgula
    """
    backend = await _backend(ctx)
    result = await backend.retirar_processo_bloco_interno(id_bloco, processos)
    return _json(result)


@mcp.tool(annotations=_READ)
async def sei_listar_processos_bloco_interno(
    id_bloco: str,
    ctx: Context | None = None,
) -> str:
    """Lista processos de um bloco interno.

    Disponível desde mod-wssei 2.0.0 (SEI 4.0.x).
    Se falhar com erro inesperado, use sei_versao para verificar a versão instalada.
    """
    backend = await _backend(ctx)
    result = await backend.listar_processos_bloco_interno(id_bloco)
    return _json(result)


@mcp.tool(annotations=_IDEM)
async def sei_alterar_bloco_interno(
    id_bloco: str,
    descricao: str,
    ctx: Context | None = None,
) -> str:
    """Altera descrição de um bloco interno.

    Disponível desde mod-wssei 2.0.0 (SEI 4.0.x).
    Se falhar com erro inesperado, use sei_versao para verificar a versão instalada.
    """
    backend = await _backend(ctx)
    result = await backend.alterar_bloco_interno(id_bloco, descricao)
    return _json(result)


@mcp.tool(annotations=_DEST)
async def sei_excluir_bloco_interno(
    ids_blocos: str,
    ctx: Context | None = None,
) -> str:
    """Exclui bloco(s) interno(s). IDs separados por vírgula.

    Disponível desde mod-wssei 2.0.0 (SEI 4.0.x).
    Se falhar com erro inesperado, use sei_versao para verificar a versão instalada.
    """
    backend = await _backend(ctx)
    result = await backend.excluir_blocos_internos(ids_blocos)
    return _json(result)


@mcp.tool(annotations=_IDEM)
async def sei_concluir_bloco_interno(
    ids_blocos: str,
    ctx: Context | None = None,
) -> str:
    """Conclui bloco(s) interno(s). IDs separados por vírgula.

    Disponível desde mod-wssei 2.0.0 (SEI 4.0.x).
    Se falhar com erro inesperado, use sei_versao para verificar a versão instalada.
    """
    backend = await _backend(ctx)
    result = await backend.concluir_blocos_internos(ids_blocos)
    return _json(result)


@mcp.tool(annotations=_IDEM)
async def sei_reabrir_bloco_interno(
    id_bloco: str,
    ctx: Context | None = None,
) -> str:
    """Reabre bloco interno concluído.

    Disponível desde mod-wssei 2.0.0 (SEI 4.0.x).
    Se falhar com erro inesperado, use sei_versao para verificar a versão instalada.
    """
    backend = await _backend(ctx)
    result = await backend.reabrir_bloco_interno(id_bloco)
    return _json(result)


@mcp.tool(annotations=_WRITE)
async def sei_anotar_processo_bloco_interno(
    id_bloco: str,
    processo: str,
    descricao: str,
    ctx: Context | None = None,
) -> str:
    """Cria anotação em processo dentro de um bloco interno.

    Disponível desde mod-wssei 2.0.0 (SEI 4.0.x).
    Se falhar com erro inesperado, use sei_versao para verificar a versão instalada.
    """
    backend = await _backend(ctx)
    result = await backend.anotar_processo_bloco_interno(id_bloco, processo, descricao)
    return _json(result)


@mcp.tool(annotations=_IDEM)
async def sei_alterar_anotacao_bloco_interno(
    id_bloco: str,
    processo: str,
    descricao: str,
    ctx: Context | None = None,
) -> str:
    """Altera anotação de processo em um bloco interno.

    Disponível desde mod-wssei 2.0.0 (SEI 4.0.x).
    Se falhar com erro inesperado, use sei_versao para verificar a versão instalada.
    """
    backend = await _backend(ctx)
    result = await backend.alterar_anotacao_bloco_interno(id_bloco, processo, descricao)
    return _json(result)
