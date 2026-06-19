"""Tools de catálogos do SEI.

Catálogos são as tabelas de domínio que alimentam a criação de processos e
documentos: tipos de processo/documento, hipóteses legais, assuntos, contatos,
textos padrão e modelos. Operações de leitura (paginadas) mais a criação de
contatos.

Sem `from __future__ import annotations`: o FastMCP introspecta os type hints em
tempo de execução para montar o schema de cada tool, então as anotações precisam
ser objetos reais (não strings adiadas).
"""

from fastmcp import Context

from todos.mcp_app import _READ, _WRITE, _add_cursor, _backend, _decode_cursor, _json, mcp

_DEFAULT_LIMIT = 50


@mcp.tool(annotations=_READ)
async def sei_pesquisar_hipoteses_legais(
    filtro: str = "",
    limit: int = _DEFAULT_LIMIT,
    pagina: int = 0,
    cursor: str = "",
    ctx: Context | None = None,
) -> str:
    """Pesquisa hipóteses legais disponíveis no SEI.

    Necessário ao criar processos ou documentos com nível de acesso
    restrito ou sigiloso. Use o 'id' retornado no parâmetro
    hipotese_legal de sei_criar_processo.

    Exemplos: "pessoal", "controle interno", "sigilo fiscal"

    Paginação: passe `cursor` = `proximo_cursor` da resposta anterior para
    avançar páginas. Omita para começar do início.

    """
    if cursor:
        decoded = _decode_cursor(cursor)
        pagina = decoded.get("p", pagina)
        filtro = decoded.get("filtro", filtro)
        limit = decoded.get("limit", limit)
    backend = await _backend(ctx)
    result = await backend.pesquisar_hipoteses_legais(
        filtro=filtro,
        limit=limit,
        pagina=pagina,
    )
    extra: dict = {}
    if filtro:
        extra["filtro"] = filtro
    extra["limit"] = limit
    return _json(
        _add_cursor(
            result,
            pagina=pagina,
            limit=limit,
            tool_name="sei_pesquisar_hipoteses_legais",
            cursor_extra=extra,
        )
    )


@mcp.tool(annotations=_READ)
async def sei_pesquisar_tipos_processo(
    filtro: str = "",
    favoritos: str = "",
    limit: int = _DEFAULT_LIMIT,
    pagina: int = 0,
    cursor: str = "",
    ctx: Context | None = None,
) -> str:
    """Pesquisa tipos de processo disponíveis no SEI.

    Parâmetros:
    - filtro: texto para filtrar por nome (ex: "Plano Anual", "Fiscalização")
    - favoritos: "S" para apenas favoritos (REST apenas)
    - limit/pagina: paginação (REST apenas)
    - cursor: cursor opaco de paginação — passe `proximo_cursor` da resposta anterior

    Use o 'id' retornado como tipo_processo em sei_criar_processo.

    """
    if cursor:
        decoded = _decode_cursor(cursor)
        pagina = decoded.get("p", pagina)
        filtro = decoded.get("filtro", filtro)
        favoritos = decoded.get("favoritos", favoritos)
        limit = decoded.get("limit", limit)
    backend = await _backend(ctx)
    result = await backend.pesquisar_tipos_processo(
        filtro=filtro,
        favoritos=favoritos,
        limit=limit,
        pagina=pagina,
    )
    extra: dict = {}
    if filtro:
        extra["filtro"] = filtro
    if favoritos:
        extra["favoritos"] = favoritos
    extra["limit"] = limit
    return _json(
        _add_cursor(
            result,
            pagina=pagina,
            limit=limit,
            tool_name="sei_pesquisar_tipos_processo",
            cursor_extra=extra,
        )
    )


@mcp.tool(annotations=_READ)
async def sei_pesquisar_tipos_documento(
    filtro: str = "",
    favoritos: str = "",
    aplicabilidade: str = "",
    limit: int = _DEFAULT_LIMIT,
    pagina: int = 0,
    cursor: str = "",
    ctx: Context | None = None,
) -> str:
    """Pesquisa tipos de documento (séries) disponíveis no SEI.

    Parâmetros:
    - filtro: texto para filtrar por nome do tipo
    - favoritos: "S" para apenas favoritos (REST apenas)
    - aplicabilidade: "I" para internos, "F" para externos (REST apenas)
    - limit: quantidade por página (REST apenas)
    - pagina: número da página (REST apenas)
    - cursor: cursor opaco de paginação — passe `proximo_cursor` da resposta anterior

    Use o 'id' retornado como id_serie em sei_criar_documento.

    """
    if cursor:
        decoded = _decode_cursor(cursor)
        pagina = decoded.get("p", pagina)
        filtro = decoded.get("filtro", filtro)
        favoritos = decoded.get("favoritos", favoritos)
        aplicabilidade = decoded.get("aplicabilidade", aplicabilidade)
        limit = decoded.get("limit", limit)
    backend = await _backend(ctx)
    result = await backend.pesquisar_tipos_documento(
        filtro=filtro,
        favoritos=favoritos,
        aplicabilidade=aplicabilidade,
        limit=limit,
        pagina=pagina,
    )
    extra: dict = {}
    if filtro:
        extra["filtro"] = filtro
    if favoritos:
        extra["favoritos"] = favoritos
    if aplicabilidade:
        extra["aplicabilidade"] = aplicabilidade
    extra["limit"] = limit
    return _json(
        _add_cursor(
            result,
            pagina=pagina,
            limit=limit,
            tool_name="sei_pesquisar_tipos_documento",
            cursor_extra=extra,
        )
    )


@mcp.tool(annotations=_READ)
async def sei_pesquisar_tipos_documento_externo(
    filtro: str = "",
    limit: int = _DEFAULT_LIMIT,
    pagina: int = 0,
    cursor: str = "",
    ctx: Context | None = None,
) -> str:
    """Pesquisa tipos de documento para documentos externos (séries externas).

    Diferente de sei_pesquisar_tipos_documento que lista todos os tipos,
    este retorna apenas os tipos aplicáveis a documentos externos.
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
    result = await backend.pesquisar_tipos_documento_externo(
        filtro=filtro,
        limit=limit,
        pagina=pagina,
    )
    extra: dict = {}
    if filtro:
        extra["filtro"] = filtro
    extra["limit"] = limit
    return _json(
        _add_cursor(
            result,
            pagina=pagina,
            limit=limit,
            tool_name="sei_pesquisar_tipos_documento_externo",
            cursor_extra=extra,
        )
    )


@mcp.tool(annotations=_READ)
async def sei_pesquisar_tipos_conferencia(
    filtro: str = "",
    limit: int = _DEFAULT_LIMIT,
    pagina: int = 0,
    cursor: str = "",
    ctx: Context | None = None,
) -> str:
    """Pesquisa tipos de conferência para documentos externos.

    Tipo de conferência indica se o documento externo é cópia autenticada,
    cópia simples, original, etc.
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
    result = await backend.pesquisar_tipos_conferencia(
        filtro=filtro,
        limit=limit,
        pagina=pagina,
    )
    extra: dict = {}
    if filtro:
        extra["filtro"] = filtro
    extra["limit"] = limit
    return _json(
        _add_cursor(
            result,
            pagina=pagina,
            limit=limit,
            tool_name="sei_pesquisar_tipos_conferencia",
            cursor_extra=extra,
        )
    )


@mcp.tool(annotations=_READ)
async def sei_pesquisar_assuntos(
    filtro: str = "",
    limit: int = _DEFAULT_LIMIT,
    pagina: int = 0,
    cursor: str = "",
    ctx: Context | None = None,
) -> str:
    """Pesquisa assuntos disponíveis para processos.

    Use o ID retornado no campo 'assuntos' ao criar processos.
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
    result = await backend.pesquisar_assuntos(
        filtro=filtro,
        limit=limit,
        pagina=pagina,
    )
    extra: dict = {}
    if filtro:
        extra["filtro"] = filtro
    extra["limit"] = limit
    return _json(
        _add_cursor(
            result,
            pagina=pagina,
            limit=limit,
            tool_name="sei_pesquisar_assuntos",
            cursor_extra=extra,
        )
    )


@mcp.tool(annotations=_READ)
async def sei_pesquisar_contatos(
    filtro: str = "",
    limit: int = _DEFAULT_LIMIT,
    pagina: int = 0,
    cursor: str = "",
    ctx: Context | None = None,
) -> str:
    """Pesquisa contatos cadastrados no SEI.

    Use para encontrar id de interessados ao criar/alterar processos.
    Em instâncias sem mod-wssei, requer filtro não-vazio (busca via autocomplete AJAX).
    Para listar usuários internos da unidade, use sei_listar_usuarios.

    O campo `id` retornado é o id interno do contato no SEI.

    Paginação: passe cursor = proximo_cursor da resposta anterior.
    """
    if cursor:
        decoded = _decode_cursor(cursor)
        pagina = decoded.get("p", pagina)
        limit = decoded.get("limit", limit)
    backend = await _backend(ctx)
    result = await backend.pesquisar_contatos(filtro=filtro, limit=limit, pagina=pagina)
    cursor_extra: dict = {"limit": limit}
    if filtro:
        cursor_extra["filtro"] = filtro
    return _json(
        _add_cursor(
            result,
            pagina=pagina,
            limit=limit,
            tool_name="sei_pesquisar_contatos",
            cursor_extra=cursor_extra,
        )
    )


@mcp.tool(annotations=_WRITE)
async def sei_criar_contato(
    nome: str,
    tipo: str = "",
    email: str = "",
    telefone: str = "",
    ctx: Context | None = None,
) -> str:
    """Cria novo contato no SEI.

    Disponível desde mod-wssei 2.0.0 (SEI 4.0.x).
    Se falhar com erro inesperado, use sei_versao para verificar a versão instalada.
    """
    backend = await _backend(ctx)
    result = await backend.criar_contato(nome, tipo=tipo, email=email, telefone=telefone)
    return _json(result)


@mcp.tool(annotations=_READ)
async def sei_pesquisar_textos_padrao(
    filtro: str = "",
    limit: int = _DEFAULT_LIMIT,
    pagina: int = 0,
    cursor: str = "",
    ctx: Context | None = None,
) -> str:
    """Pesquisa textos padrão internos disponíveis na unidade.

    Textos padrão são modelos reutilizáveis para preencher documentos
    automaticamente ao criar um novo documento interno.
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
    result = await backend.pesquisar_textos_padrao(
        filtro=filtro,
        limit=limit,
        pagina=pagina,
    )
    extra: dict = {}
    if filtro:
        extra["filtro"] = filtro
    extra["limit"] = limit
    return _json(
        _add_cursor(
            result,
            pagina=pagina,
            limit=limit,
            tool_name="sei_pesquisar_textos_padrao",
            cursor_extra=extra,
        )
    )


@mcp.tool(annotations=_READ)
async def sei_listar_grupos_modelos(
    limit: int = _DEFAULT_LIMIT,
    pagina: int = 0,
    cursor: str = "",
    ctx: Context | None = None,
) -> str:
    """Lista grupos de modelos de documento disponíveis.

    Disponível desde mod-wssei 2.0.0 (SEI 4.0.x).
    Se falhar com erro inesperado, use sei_versao para verificar a versão instalada.

    Paginação: passe `cursor` = `proximo_cursor` da resposta anterior.

    """
    if cursor:
        decoded = _decode_cursor(cursor)
        pagina = decoded.get("p", pagina)
        limit = decoded.get("limit", limit)
    backend = await _backend(ctx)
    result = await backend.listar_grupos_modelos(limit=limit, pagina=pagina)
    extra: dict = {}
    extra["limit"] = limit
    return _json(
        _add_cursor(
            result,
            pagina=pagina,
            limit=limit,
            tool_name="sei_listar_grupos_modelos",
            cursor_extra=extra,
        )
    )


@mcp.tool(annotations=_READ)
async def sei_listar_modelos(
    id_grupo: str = "",
    filtro: str = "",
    limit: int = _DEFAULT_LIMIT,
    pagina: int = 0,
    cursor: str = "",
    ctx: Context | None = None,
) -> str:
    """Lista modelos de documento disponíveis.

    - id_grupo: filtrar por grupo (use sei_listar_grupos_modelos)
    - filtro: texto para filtrar por nome
    - cursor: cursor opaco de paginação — passe `proximo_cursor` da resposta anterior

    Disponível desde mod-wssei 2.0.0 (SEI 4.0.x).
    Se falhar com erro inesperado, use sei_versao para verificar a versão instalada.

    """
    if cursor:
        decoded = _decode_cursor(cursor)
        pagina = decoded.get("p", pagina)
        id_grupo = decoded.get("id_grupo", id_grupo)
        filtro = decoded.get("filtro", filtro)
        limit = decoded.get("limit", limit)
    backend = await _backend(ctx)
    result = await backend.listar_modelos(
        id_grupo=id_grupo,
        filtro=filtro,
        limit=limit,
        pagina=pagina,
    )
    extra: dict = {}
    if id_grupo:
        extra["id_grupo"] = id_grupo
    if filtro:
        extra["filtro"] = filtro
    extra["limit"] = limit
    return _json(
        _add_cursor(
            result, pagina=pagina, limit=limit, tool_name="sei_listar_modelos", cursor_extra=extra
        )
    )


@mcp.tool(annotations=_READ)
async def sei_sugestao_assuntos_processo(
    id_tipo_processo: str,
    ctx: Context | None = None,
) -> str:
    """Lista sugestões de assuntos para um tipo de processo.

    Use o id do tipo obtido via sei_pesquisar_tipos_processo.
    Disponível desde mod-wssei 2.0.0 (SEI 4.0.x).
    Se falhar com erro inesperado, use sei_versao para verificar a versão instalada.
    """
    backend = await _backend(ctx)
    result = await backend.sugestao_assuntos_processo(id_tipo_processo)
    return _json(result)
