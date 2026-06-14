"""Tools de bloco de assinatura.

Um bloco de assinatura agrupa documentos de processos diferentes para assinatura
em lote por um responsável. Fluxo: criar → incluir documentos → disponibilizar
(para o assinante ver) → assinante assina → concluir. Os documentos permanecem
em seus processos originais.

Sem `from __future__ import annotations`: o FastMCP introspecta os type hints em
tempo de execução para montar o schema de cada tool, então as anotações precisam
ser objetos reais (não strings adiadas).
"""

from fastmcp import Context

from todos.mcp_app import _DEST, _IDEM, _READ, _WRITE, _backend, _json, mcp


@mcp.tool(annotations=_WRITE)
async def sei_criar_bloco_assinatura(
    descricao: str,
    unidades: str = "",
    ctx: Context | None = None,
) -> str:
    """Cria um bloco de assinatura no SEI.

    Parâmetros:
    - descricao: descrição do bloco
    - unidades: sigla(s) ou ID(s) das unidades para disponibilizar
      (separados por vírgula). Se informar sigla, resolve automaticamente.
      Ignorado em modo web (bloco criado sem unidades pré-configuradas).

    """
    backend = _backend(ctx)
    result = await backend.criar_bloco_assinatura(descricao, unidades)
    return _json(result)


@mcp.tool(annotations=_WRITE)
async def sei_incluir_documento_bloco_assinatura(
    id_bloco: str,
    documentos: str,
    ctx: Context | None = None,
) -> str:
    """Inclui documento(s) em um bloco de assinatura.

    - id_bloco: ID do bloco de assinatura
    - documentos: ID(s) de documento(s) separados por vírgula
    """
    backend = _backend(ctx)
    result = await backend.incluir_documento_bloco_assinatura(id_bloco, documentos)
    return _json(result)


@mcp.tool(annotations=_IDEM)
async def sei_disponibilizar_bloco_assinatura(
    id_bloco: str,
    ctx: Context | None = None,
) -> str:
    """Disponibiliza um bloco de assinatura para as unidades configuradas.

    Após disponibilizar, os usuários das unidades podem assinar os documentos.

    """
    backend = _backend(ctx)
    result = await backend.disponibilizar_bloco_assinatura(id_bloco)
    return _json(result)


@mcp.tool(annotations=_IDEM)
async def sei_cancelar_disponibilizacao_bloco(
    id_bloco: str,
    ctx: Context | None = None,
) -> str:
    """Cancela a disponibilização de um bloco de assinatura.

    O bloco volta ao estado aberto e pode ser editado novamente.

    """
    backend = _backend(ctx)
    result = await backend.cancelar_disponibilizacao_bloco_assinatura(id_bloco)
    return _json(result)


@mcp.tool(annotations=_READ)
async def sei_pesquisar_blocos_assinatura(
    filtro: str = "",
    limit: int = 50,
    ctx: Context | None = None,
) -> str:
    """Pesquisa blocos de assinatura existentes."""
    backend = _backend(ctx)
    result = await backend.pesquisar_blocos_assinatura(filtro=filtro, limit=limit)
    return _json(result)


@mcp.tool(annotations=_READ)
async def sei_listar_documentos_bloco_assinatura(
    id_bloco: str,
    ctx: Context | None = None,
) -> str:
    """Lista documentos de um bloco de assinatura."""
    backend = _backend(ctx)
    result = await backend.listar_documentos_bloco_assinatura(id_bloco)
    return _json(result)


@mcp.tool(annotations=_DEST)
async def sei_retirar_documentos_bloco_assinatura(
    id_bloco: str,
    documentos: str,
    ctx: Context | None = None,
) -> str:
    """Retira documento(s) de um bloco de assinatura.

    - documentos: ID(s) de documento(s) separados por vírgula

    """
    backend = _backend(ctx)
    result = await backend.retirar_documentos_bloco_assinatura(id_bloco, documentos)
    return _json(result)


@mcp.tool(annotations=_IDEM)
async def sei_alterar_bloco_assinatura(
    id_bloco: str,
    descricao: str,
    ctx: Context | None = None,
) -> str:
    """Altera descrição de um bloco de assinatura.

    Disponível desde mod-wssei 2.0.0 (SEI 4.0.x).
    Se falhar com erro inesperado, use sei_versao para verificar a versão instalada.

    """
    backend = _backend(ctx)
    result = await backend.alterar_bloco_assinatura(id_bloco, descricao)
    return _json(result)


@mcp.tool(annotations=_DEST)
async def sei_excluir_bloco_assinatura(
    ids_blocos: str,
    ctx: Context | None = None,
) -> str:
    """Exclui bloco(s) de assinatura. IDs separados por vírgula.

    Disponível desde mod-wssei 2.0.0 (SEI 4.0.x).
    Se falhar com erro inesperado, use sei_versao para verificar a versão instalada.

    """
    backend = _backend(ctx)
    result = await backend.excluir_blocos_assinatura(ids_blocos)
    return _json(result)


@mcp.tool(annotations=_IDEM)
async def sei_concluir_bloco_assinatura(
    ids_blocos: str,
    ctx: Context | None = None,
) -> str:
    """Conclui bloco(s) de assinatura. IDs separados por vírgula.

    Disponível desde mod-wssei 2.0.0 (SEI 4.0.x).
    Se falhar com erro inesperado, use sei_versao para verificar a versão instalada.

    """
    backend = _backend(ctx)
    result = await backend.concluir_blocos_assinatura(ids_blocos)
    return _json(result)


@mcp.tool(annotations=_IDEM)
async def sei_reabrir_bloco_assinatura(
    id_bloco: str,
    ctx: Context | None = None,
) -> str:
    """Reabre bloco de assinatura concluído.

    Disponível desde mod-wssei 2.0.0 (SEI 4.0.x).
    Se falhar com erro inesperado, use sei_versao para verificar a versão instalada.

    """
    backend = _backend(ctx)
    result = await backend.reabrir_bloco_assinatura(id_bloco)
    return _json(result)


@mcp.tool(annotations=_IDEM)
async def sei_retornar_bloco_assinatura(
    id_bloco: str,
    ctx: Context | None = None,
) -> str:
    """Retorna bloco de assinatura para a unidade de origem.

    Disponível desde mod-wssei 2.0.0 (SEI 4.0.x).
    Se falhar com erro inesperado, use sei_versao para verificar a versão instalada.

    """
    backend = _backend(ctx)
    result = await backend.retornar_bloco_assinatura(id_bloco)
    return _json(result)


@mcp.tool(annotations=_WRITE)
async def sei_anotar_documento_bloco_assinatura(
    id_bloco: str,
    documento: str,
    descricao: str,
    ctx: Context | None = None,
) -> str:
    """Cria anotação em documento dentro de um bloco de assinatura.

    Disponível desde mod-wssei 2.0.0 (SEI 4.0.x).
    Se falhar com erro inesperado, use sei_versao para verificar a versão instalada.

    """
    backend = _backend(ctx)
    result = await backend.anotar_documento_bloco_assinatura(id_bloco, documento, descricao)
    return _json(result)


@mcp.tool(annotations=_IDEM)
async def sei_alterar_anotacao_bloco_assinatura(
    id_bloco: str,
    documento: str,
    descricao: str,
    ctx: Context | None = None,
) -> str:
    """Altera anotação de documento em um bloco de assinatura.

    Disponível desde mod-wssei 2.0.0 (SEI 4.0.x).
    Se falhar com erro inesperado, use sei_versao para verificar a versão instalada.

    """
    backend = _backend(ctx)
    result = await backend.alterar_anotacao_bloco_assinatura(id_bloco, documento, descricao)
    return _json(result)
