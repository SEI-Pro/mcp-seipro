"""Tools de unidades, usuários e informações do sistema.

Agrupa as operações de contexto da sessão SEI — unidade ativa, troca e pesquisa
de unidades, listagem e pesquisa de usuários — e as consultas informativas do
sistema: versão do mod-wssei, órgãos, contextos, assinantes e parâmetros de
upload.

Sem `from __future__ import annotations`: o FastMCP introspecta os type hints em
tempo de execução para montar o schema de cada tool, então as anotações precisam
ser objetos reais (não strings adiadas).
"""

from fastmcp import Context

from todos.mcp_app import _IDEM, _READ, _add_cursor, _backend, _decode_cursor, _json, mcp
from todos.responses import ResultadoUnidades, ResultadoUsuarios

_DEFAULT_LIMIT = 50


@mcp.tool(annotations=_READ)
async def sei_unidade_atual(ctx: Context) -> str:
    """Retorna a unidade/setor ativo na sessao atual do SEI.

    Informa id_unidade, sigla e nome. Use antes de listar ou alterar processos
    para confirmar em qual caixa as operacoes serao executadas.
    """
    backend = await _backend(ctx)
    result = await backend.unidade_atual()
    return _json(result)


@mcp.tool(annotations=_READ)
async def sei_listar_unidades(ctx: Context) -> str:
    """Lista as unidades às quais o usuário autenticado tem acesso no SEI.

    Retorna id, sigla e nome de cada unidade. Use o id para trocar
    de unidade com sei_trocar_unidade.
    """
    backend = await _backend(ctx)
    units = await backend.listar_unidades()
    return _json({"data": units, "total": len(units)})


@mcp.tool(annotations=_IDEM)
async def sei_trocar_unidade(id_unidade: str, ctx: Context) -> str:
    """Troca a unidade ativa do usuário no SEI.

    Aceita o ID interno ou a sigla da unidade, por exemplo `PGE-PPI`.
    Após trocar, operações como sei_listar_processos mostrarão
    a caixa da nova unidade. Use sei_listar_unidades para ver
    as unidades disponíveis.
    """
    backend = await _backend(ctx)
    result = await backend.trocar_unidade(id_unidade)
    return _json(result)


@mcp.tool(annotations=_READ)
async def sei_pesquisar_unidades(
    filtro: str = "",
    limit: int = _DEFAULT_LIMIT,
    pagina: int = 0,
    cursor: str = "",
    ctx: Context | None = None,
) -> ResultadoUnidades:
    """Pesquisa unidades disponíveis no SEI por nome ou sigla.

    Útil para encontrar o ID de uma unidade destino ao tramitar processos.
    Em instâncias sem mod-wssei, requer filtro não-vazio (busca via autocomplete AJAX).

    Paginação: passe `cursor` = `proximo_cursor` da resposta anterior, ou use
    `pagina` (0-indexado) para acesso direto.

    """
    if cursor:
        decoded = _decode_cursor(cursor)
        pagina = decoded.get("p", pagina)
        filtro = decoded.get("filtro", filtro)
        limit = decoded.get("limit", limit)
    backend = await _backend(ctx)
    result = await backend.pesquisar_unidades(filtro=filtro, limit=limit, pagina=pagina)
    extra: dict = {}
    if filtro:
        extra["filtro"] = filtro
    extra["limit"] = limit
    return ResultadoUnidades.model_validate(
        _add_cursor(
            result,
            pagina=pagina,
            limit=limit,
            tool_name="sei_pesquisar_unidades",
            cursor_extra=extra,
        )
    )


@mcp.tool(annotations=_READ)
async def sei_listar_usuarios(
    filtro: str = "",
    ctx: Context | None = None,
    *,
    apenas_unidade: bool = True,
) -> str:
    """Lista usuários no SEI, com filtro por nome ou sigla.

    - apenas_unidade=true (padrão): só usuários com permissão na unidade
      atual — ideal para atribuição de processos
    - apenas_unidade=false: todos os usuários do órgão

    Use o campo id_usuario retornado para sei_atribuir_processo.

    """
    backend = await _backend(ctx)
    result = await backend.listar_usuarios(filtro=filtro, apenas_unidade=apenas_unidade)
    return _json(result)


@mcp.tool(annotations=_READ)
async def sei_versao(ctx: Context) -> str:
    """Retorna a versão do SEI e do módulo wssei instalado.

    Útil para verificar compatibilidade de funcionalidades.
    Disponível desde mod-wssei 2.0.0 (SEI 4.0.x).
    Se falhar com erro inesperado, use sei_versao para verificar a versão instalada.
    """
    backend = await _backend(ctx)
    result = await backend.versao()
    return _json(result)


@mcp.tool(annotations=_READ)
async def sei_listar_orgaos(ctx: Context) -> str:
    """Lista os órgãos cadastrados na instalação do SEI.

    Disponível desde mod-wssei 2.0.0 (SEI 4.0.x).
    Se falhar com erro inesperado, use sei_versao para verificar a versão instalada.
    """
    backend = await _backend(ctx)
    result = await backend.listar_orgaos()
    return _json(result)


@mcp.tool(annotations=_READ)
async def sei_listar_contextos(id_orgao: str, ctx: Context) -> str:
    """Lista os contextos disponíveis para um órgão.

    Disponível desde mod-wssei 2.0.0 (SEI 4.0.x).
    Se falhar com erro inesperado, use sei_versao para verificar a versão instalada.
    """
    backend = await _backend(ctx)
    result = await backend.listar_contextos(id_orgao)
    return _json(result)


@mcp.tool(annotations=_READ)
async def sei_pesquisar_usuarios(
    filtro: str = "",
    id_orgao: str = "",
    limit: int = _DEFAULT_LIMIT,
    pagina: int = 0,
    cursor: str = "",
    ctx: Context | None = None,
) -> ResultadoUsuarios:
    """Pesquisa usuários por palavra-chave no órgão.

    Diferente de sei_listar_usuarios (que lista por unidade),
    este pesquisa no servidor por nome/sigla em todo o órgão.
    Disponível desde mod-wssei 2.0.0 (SEI 4.0.x).
    Se falhar com erro inesperado, use sei_versao para verificar a versão instalada.

    Paginação: passe `cursor` = `proximo_cursor` da resposta anterior.

    """
    if cursor:
        decoded = _decode_cursor(cursor)
        pagina = decoded.get("p", pagina)
        filtro = decoded.get("filtro", filtro)
        id_orgao = decoded.get("id_orgao", id_orgao)
        limit = decoded.get("limit", limit)
    backend = await _backend(ctx)
    result = await backend.pesquisar_usuarios(
        filtro=filtro, id_orgao=id_orgao, limit=limit, pagina=pagina
    )
    extra: dict = {}
    if filtro:
        extra["filtro"] = filtro
    if id_orgao:
        extra["id_orgao"] = id_orgao
    extra["limit"] = limit
    return ResultadoUsuarios.model_validate(
        _add_cursor(
            result,
            pagina=pagina,
            limit=limit,
            tool_name="sei_pesquisar_usuarios",
            cursor_extra=extra,
        )
    )


@mcp.tool(annotations=_READ)
async def sei_pesquisar_outras_unidades(
    filtro: str = "",
    limit: int = _DEFAULT_LIMIT,
    pagina: int = 0,
    cursor: str = "",
    ctx: Context | None = None,
) -> ResultadoUnidades:
    """Pesquisa unidades excluindo a unidade atual.

    Útil para tramitação — já filtra a unidade do usuário.
    Disponível desde mod-wssei 2.0.0 (SEI 4.0.x).
    Se falhar com erro inesperado, use sei_versao para verificar a versão instalada.

    Paginação: passe `cursor` = `proximo_cursor` da resposta anterior.

    """
    if cursor:
        decoded = _decode_cursor(cursor)
        pagina = decoded.get("p", pagina)
        filtro = decoded.get("filtro", filtro)
        limit = decoded.get("limit", limit)
    backend = await _backend(ctx)
    result = await backend.pesquisar_outras_unidades(filtro=filtro, limit=limit, pagina=pagina)
    extra: dict = {}
    if filtro:
        extra["filtro"] = filtro
    extra["limit"] = limit
    return ResultadoUnidades.model_validate(
        _add_cursor(
            result,
            pagina=pagina,
            limit=limit,
            tool_name="sei_pesquisar_outras_unidades",
            cursor_extra=extra,
        )
    )


@mcp.tool(annotations=_READ)
async def sei_parametros_upload(ctx: Context) -> str:
    """Retorna parâmetros de upload do SEI (extensões permitidas, tamanhos máximos).

    Útil antes de criar documentos externos para saber os limites.
    Em instâncias sem mod-wssei, retorna valores padrão do SEI 4.x.
    """
    backend = await _backend(ctx)
    result = await backend.parametros_upload()
    return _json(result)


@mcp.tool(annotations=_READ)
async def sei_listar_assinantes(ctx: Context) -> str:
    """Lista signatários (cargos/funções) disponíveis na unidade atual.

    Retorna os cargos que podem ser usados em sei_assinar_documento.
    Disponível desde mod-wssei 2.0.0 (SEI 4.0.x).
    Se falhar com erro inesperado, use sei_versao para verificar a versão instalada.
    """
    backend = await _backend(ctx)
    result = await backend.listar_assinantes()
    return _json(result)


@mcp.tool(annotations=_READ)
async def sei_listar_orgaos_assinante(ctx: Context) -> str:
    """Lista órgãos disponíveis para assinatura.

    Disponível desde mod-wssei 2.0.0 (SEI 4.0.x).
    Se falhar com erro inesperado, use sei_versao para verificar a versão instalada.
    """
    backend = await _backend(ctx)
    result = await backend.listar_orgaos_assinante()
    return _json(result)
