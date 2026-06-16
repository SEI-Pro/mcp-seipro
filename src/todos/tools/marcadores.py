"""Tools de marcadores.

Marcador é uma etiqueta colorida e pessoal da unidade, aplicável a processos
para organização e triagem. Este módulo cobre o ciclo de vida do marcador
(criar, excluir, desativar, reativar), sua aplicação a processos e as consultas
de marcadores disponíveis, ativos e histórico.

Sem `from __future__ import annotations`: o FastMCP introspecta os type hints em
tempo de execução para montar o schema de cada tool, então as anotações precisam
ser objetos reais (não strings adiadas).
"""

from fastmcp import Context

from todos.exceptions import SEIValidationError
from todos.mcp_app import (
    _DEST,
    _IDEM,
    _READ,
    _WRITE,
    _backend,
    _json,
    mcp,
)

_DEFAULT_LIMIT = 50


@mcp.tool(annotations=_WRITE)
async def sei_criar_marcador(
    nome: str,
    id_cor: str = "",
    ctx: Context | None = None,
) -> str:
    """Cria um marcador na unidade atual.

    - nome: nome do marcador
    - id_cor: ID da cor (use sei_listar_cores_marcador para ver opções).
      Se omitido, lista as cores disponíveis para escolha.
    """
    backend = await _backend(ctx)
    if not id_cor:
        cores = await backend.listar_cores_marcador()
        disponiveis = ", ".join(str(c) for c in cores) if cores else "(nenhuma retornada)"
        msg = f"id_cor não informado — escolha uma das cores disponíveis: {disponiveis}"
        raise SEIValidationError(msg)
    result = await backend.criar_marcador(nome, id_cor)
    return _json(result)


@mcp.tool(annotations=_DEST)
async def sei_excluir_marcador(
    ids_marcadores: str,
    ctx: Context | None = None,
) -> str:
    """Exclui marcador(es). IDs separados por vírgula."""
    backend = await _backend(ctx)
    result = await backend.excluir_marcadores(ids_marcadores)
    return _json(result)


@mcp.tool(annotations=_IDEM)
async def sei_marcar_processo(
    processo: str,
    marcador: str,
    texto: str = "",
    ctx: Context | None = None,
) -> str:
    """Adiciona ou altera marcador (etiqueta colorida) em um processo.

    Parâmetros:
    - processo: protocolo formatado ou IdProcedimento
    - marcador: ID do marcador.
      Passe "?" (ponto de interrogação) para entrar no modo de descoberta:
      em vez de marcar o processo, a tool lista os marcadores disponíveis
      na unidade para que você possa escolher o ID correto e chamar novamente.
    - texto: texto/comentário associado ao marcador (opcional)

    """
    backend = await _backend(ctx)
    if marcador == "?":
        # Modo de descoberta: "?" lista os marcadores disponíveis em vez de operar.
        disponiveis = await backend.pesquisar_marcadores()
        return _json({"marcadores_disponiveis": disponiveis})
    result = await backend.marcar_processo(processo, marcador, texto)
    return _json(result)


@mcp.tool(annotations=_IDEM)
async def sei_desmarcar_processo(
    processo: str,
    marcador: str = "",
    ctx: Context | None = None,
) -> str:
    """Remove marcador(es) de um processo.

    Parâmetros:
    - processo: protocolo formatado ou IdProcedimento
    - marcador: ID ou parte do nome do marcador a remover; vazio remove TODOS
      os marcadores aplicados no processo

    """
    backend = await _backend(ctx)
    result = await backend.desmarcar_processo(processo, marcador)
    return _json(result)


@mcp.tool(annotations=_READ)
async def sei_pesquisar_marcadores(
    filtro: str = "",
    limit: int = _DEFAULT_LIMIT,
    ctx: Context | None = None,
) -> str:
    """Lista marcadores disponíveis na unidade atual.

    Use o 'id' retornado em sei_marcar_processo.

    """
    backend = await _backend(ctx)
    result = await backend.pesquisar_marcadores(filtro=filtro, limit=limit)
    return _json(result)


@mcp.tool(annotations=_READ)
async def sei_consultar_marcador_processo(
    processo: str,
    ctx: Context | None = None,
) -> str:
    """Consulta os marcadores ativos de um processo."""
    backend = await _backend(ctx)
    result = await backend.consultar_marcador_processo(processo)
    return _json(result)


@mcp.tool(annotations=_READ)
async def sei_historico_marcador_processo(
    processo: str,
    ctx: Context | None = None,
) -> str:
    """Lista histórico de marcadores de um processo.

    Mostra quais marcadores foram aplicados/removidos ao longo do tempo.
    Disponível desde mod-wssei 2.0.0 (SEI 4.0.x).
    Se falhar com erro inesperado, use sei_versao para verificar a versão instalada.
    """
    backend = await _backend(ctx)
    result = await backend.historico_marcador_processo(processo)
    return _json(result)


@mcp.tool(annotations=_IDEM)
async def sei_desativar_marcador(
    ids_marcadores: str,
    ctx: Context | None = None,
) -> str:
    """Desativa marcador(es) sem excluir. IDs separados por vírgula.

    Marcadores desativados deixam de aparecer nas pesquisas mas
    mantêm o histórico. Use sei_reativar_marcador para reativar.
    """
    backend = await _backend(ctx)
    result = await backend.desativar_marcadores(ids_marcadores)
    return _json(result)


@mcp.tool(annotations=_IDEM)
async def sei_reativar_marcador(
    ids_marcadores: str,
    ctx: Context | None = None,
) -> str:
    """Reativa marcador(es) desativados. IDs separados por vírgula."""
    backend = await _backend(ctx)
    result = await backend.reativar_marcadores(ids_marcadores)
    return _json(result)
