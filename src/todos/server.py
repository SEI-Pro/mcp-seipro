"""MCP Server genérico para o SEI (Sistema Eletrônico de Informações)."""

import re
from collections.abc import Callable
from typing import Annotated, TypeAlias, TypedDict, TypeGuard

import httpx
import typer as _typer
from fastmcp import Context

from todos.backends import EnvioProcesso
from todos.backends.models import FiltroListagemProcessos, FiltrosPesquisaProcessos
from todos.exceptions import (
    SEIConnectionError,
    SEIError,
    SEIValidationError,
)
from todos.hints import get_hints
from todos.mcp_app import (
    _DEST,
    _IDEM,
    _MAX_GRUPO_INLINE,
    _READ,
    _WRITE,
    _add_cursor,
    _backend,
    _decode_cursor,
    _get_client,
    _get_web_client,
    _http_mode,
    _http_port,
    _json,
    mcp,
)
from todos.remote import run_remote
from todos.responses import NextAction, ResultadoPesquisaProcessos
from todos.sei_web_client import SEI_WEB_PAGE_SIZE
from todos.setup_wizard import run_set_password, run_setup_wizard
from todos.tools import (
    acompanhamento,
    assinatura,
    blocos_assinatura,
    blocos_internos,
    catalogos,
    configuracao,
    credenciamento,
    documentos,
    marcadores,
    processos,
    unidades,
)

# Submódulos de tools por domínio. Importá-los registra suas @mcp.tool no `mcp`
# compartilhado; a tupla mantém a referência viva (e satisfaz o linter). As tools
# que permanecem em server.py são orquestrações especiais (pesquisa/agregação REST,
# resolução sigla→id) ainda não absorvidas pelo contrato do backend.
_TOOL_MODULES = (
    acompanhamento,
    assinatura,
    blocos_assinatura,
    blocos_internos,
    catalogos,
    configuracao,
    credenciamento,
    documentos,
    marcadores,
    processos,
    unidades,
)


# ---------------------------------------------------------------------------
# Tools de orquestração que ainda vivem aqui (não absorvidas pelo contrato do
# backend): busca multi-estratégia, agregação/pesquisa REST e resolução sigla→id.
# As demais tools estão nos módulos por domínio em todos/tools/.
# ---------------------------------------------------------------------------


@mcp.tool(annotations=_READ)
async def sei_buscar_documento(
    numero_sei: str,
    processo: str = "",
    ctx: Context | None = None,
) -> str:
    """Busca um documento pelo número SEI (ex: SEI 2843449, SEI nº 2843449).

    O número SEI é o protocoloFormatado que o usuário vê no sistema.
    A API do SEI não busca documentos diretamente por esse número,
    então esta tool usa a estratégia:

    1. Se processo informado: busca direto nesse processo (rápido).
       Aceita protocolo formatado (ex: 50300.018905/2018-67) ou IdProcedimento.
    2. Se não: pesquisa o número via busca textual (Solr) para encontrar
       o processo, depois lista os documentos para localizar o id interno

    Retorna o documento com seu id interno (necessário para sei_ler_documento),
    tipo, metadados e o processo onde está.
    """
    backend = await _backend(ctx)
    # Composite: estratégia Solr (REST) quando há mod-wssei, senão árvore web.
    result = await backend.buscar_documento(numero_sei, processo)
    doc_id = result.get("id") or result.get("idDocumento")
    if doc_id:
        result.setdefault("_next", [{"tool": "sei_ler_documento", "args": {"documento": doc_id}}])
    return _json(result)


# §29.4 — Compiled regex for validating the `filtro` parameter.
# Allows word chars (\w = letters incl. accented/Unicode, digits, underscores),
# whitespace, and Brazilian protocol separators (. / -).
# Rejects anything else to prevent injection into backend search queries.
_RE_FILTRO_VALIDO = re.compile(r"^[\w\s./\-]*$")


def _validar_filtro(filtro: str) -> None:
    """Validate the `filtro` search parameter; raise SEIValidationError on invalid chars."""
    if filtro and not _RE_FILTRO_VALIDO.match(filtro):
        msg = (
            f"Parâmetro 'filtro' contém caracteres inválidos: {filtro!r}. "
            "Permitidos: letras (incluindo acentuadas), dígitos, sublinhado, espaços e separadores de protocolo (. / -)."
        )
        raise SEIValidationError(msg)


# Extratores em _CAMPOS_AGRUPAMENTO: todos aceitam (atributos, status) mesmo que
# cada implementação individual use apenas um dos argumentos — assinatura uniforme
# permite iterar o dict sem tratamentos especiais.
_Extrator: TypeAlias = Callable[[dict, dict], str]

_CAMPOS_AGRUPAMENTO: dict[str, dict[str, str | _Extrator]] = {
    "tipo": {
        "desc": "Tipo processual",
        "extract": lambda a, _s: a.get("tipoProcesso", "Sem tipo"),
    },
    "atribuido": {
        "desc": "Usuário atribuído",
        "extract": lambda a, _s: a.get("usuarioAtribuido") or "Sem atribuição",
    },
    "acesso": {
        "desc": "Nível de acesso",
        "extract": lambda _a, s: {"0": "Público", "1": "Restrito", "2": "Sigiloso"}.get(
            s.get("nivelAcessoGlobal", "0"), "Desconhecido"
        ),
    },
    "tramitacao": {
        "desc": "Em tramitação",
        "extract": lambda _a, s: (
            "Em tramitação" if s.get("processoEmTramitacao") == "S" else "Fora de tramitação"
        ),
    },
    "sobrestado": {
        "desc": "Sobrestamento",
        "extract": lambda _a, s: "Sobrestado" if s.get("processoSobrestado") == "S" else "Ativo",
    },
    "bloqueado": {
        "desc": "Bloqueio",
        "extract": lambda _a, s: (
            "Bloqueado" if s.get("processoBloqueado") == "S" else "Desbloqueado"
        ),
    },
    "novo": {
        "desc": "Documento novo",
        "extract": lambda _a, s: (
            "Com documentos novos" if s.get("documentoNovo") == "S" else "Sem documentos novos"
        ),
    },
    "anotacao": {
        "desc": "Anotação",
        "extract": lambda _a, s: (
            "Anotação prioritária"
            if s.get("anotacaoPrioridade") == "S"
            else "Com anotação"
            if s.get("anotacao") == "S"
            else "Sem anotação"
        ),
    },
    "retorno": {
        "desc": "Retorno programado",
        "extract": lambda _a, s: (
            f"Atrasado ({s.get('retornoData', '')})"
            if s.get("retornoAtrasado") == "S"
            else f"Programado ({s.get('retornoData', '')})"
            if s.get("retornoProgramado") == "S"
            else "Sem retorno"
        ),
    },
    "lido_usuario": {
        "desc": "Acessado pelo usuário",
        "extract": lambda _a, s: "Lido" if s.get("processoAcessadoUsuario") == "S" else "Não lido",
    },
    "lido_unidade": {
        "desc": "Acessado pela unidade",
        "extract": lambda _a, s: "Lido" if s.get("processoAcessadoUnidade") == "S" else "Não lido",
    },
    "origem": {
        "desc": "Gerado/Recebido",
        "extract": lambda _a, s: (
            "Gerado na unidade" if s.get("processoGeradoRecebido") == "G" else "Recebido"
        ),
    },
    "anexado": {
        "desc": "Anexado",
        "extract": lambda _a, s: "Anexado" if s.get("processoAnexado") == "S" else "Independente",
    },
    "unidades": {
        "desc": "Unidades de abertura",
        "extract": lambda a, _s: (
            ", ".join(u.get("sigla", "") for u in a.get("dadosAbertura", {}).get("lista", []))
            or "N/A"
        ),
    },
    "marcador": {
        "desc": "Marcador",
        "extract": lambda a, _s: (
            ", ".join(m.get("nome", "") for m in a.get("marcador", [])) or "Sem marcador"
        ),
    },
    "ciencia": {
        "desc": "Ciência",
        "extract": lambda _a, s: "Com ciência" if s.get("ciencia") == "S" else "Sem ciência",
    },
}


def _validar_campo(nome: str) -> dict[str, str | _Extrator]:
    """Valida e retorna a entrada de _CAMPOS_AGRUPAMENTO para `nome`."""
    campo = _CAMPOS_AGRUPAMENTO.get(nome)
    if not campo:
        campos = ", ".join(sorted(_CAMPOS_AGRUPAMENTO.keys()))
        msg = f"Campo '{nome}' inválido. Disponíveis: {campos}"
        raise SEIValidationError(msg)
    return campo


def _is_extrator(val: str | _Extrator | None) -> TypeGuard[_Extrator]:
    """TypeGuard: retorna True quando val é um callable _Extrator, não uma str."""
    return callable(val) and not isinstance(val, str)


def _extrator_de_campo(campo: dict[str, str | _Extrator], nome_campo: str) -> _Extrator:
    """Retorna o extrator callable de um campo, com erro contextualizado se ausente."""
    ext = campo.get("extract")
    if not _is_extrator(ext):
        msg = f"campo 'extract' não é callable para campo={nome_campo!r}"
        raise SEIValidationError(msg)
    return ext


def _agrupar_processos(
    todos: list[dict],
    extrator1: _Extrator,
    extrator2: _Extrator | None,
) -> dict[str, dict]:
    """Agrupa processos por chave(s) derivada(s) dos extratores."""
    grupos: dict[str, dict] = {}
    for p in todos:
        a = p.get("atributos") or {}
        s = a.get("status") or {}
        chave1 = extrator1(a, s)
        chave = f"{chave1} | {extrator2(a, s)}" if extrator2 is not None else chave1
        if chave not in grupos:
            grupos[chave] = {"quantidade": 0, "processos": []}
        grupos[chave]["quantidade"] += 1
        grupos[chave]["processos"].append(a.get("numero", ""))
    return grupos


def _ordenar_resumo(grupos: dict[str, dict]) -> list[dict]:
    """Ordena grupos por quantidade decrescente; inclui lista de processos se ≤ _MAX_GRUPO_INLINE."""
    resumo = []
    for chave in sorted(grupos.keys(), key=lambda k: -grupos[k]["quantidade"]):
        g = grupos[chave]
        item: dict = {"grupo": chave, "quantidade": g["quantidade"]}
        if g["quantidade"] <= _MAX_GRUPO_INLINE:
            item["processos"] = g["processos"]
        resumo.append(item)
    return resumo


@mcp.tool(annotations=_READ)
async def sei_resumo_processos(
    agrupar_por: str = "tipo",
    agrupar_por_2: str = "",
    apenas_meus: str = "",
    filtro: str = "",
    ctx: Context | None = None,
) -> str:
    """Gera um resumo agrupado dos processos da caixa da unidade atual.

    Busca TODOS os processos e agrupa por um ou dois campos.

    Campos disponíveis para agrupar_por e agrupar_por_2:
    - tipo: Tipo processual
    - atribuido: Usuário atribuído
    - acesso: Nível de acesso (Público/Restrito/Sigiloso)
    - tramitacao: Em tramitação ou não
    - sobrestado: Sobrestado ou ativo
    - bloqueado: Bloqueado ou não
    - novo: Com/sem documentos novos
    - anotacao: Com/sem anotação (inclui prioridade)
    - retorno: Retorno programado (inclui data e atraso)
    - lido_usuario: Acessado pelo usuário
    - lido_unidade: Acessado pela unidade
    - origem: Gerado na unidade ou recebido
    - anexado: Anexado a outro processo
    - unidades: Unidades onde está aberto
    - marcador: Marcador/etiqueta
    - ciencia: Com/sem ciência

    Exemplos:
    - agrupar_por="tipo" → quantidade por tipo processual
    - agrupar_por="atribuido" → distribuição por pessoa
    - agrupar_por="tipo", agrupar_por_2="atribuido" → cruzamento tipo x pessoa
    - agrupar_por="retorno" → processos com prazo vencido
    """
    try:
        _validar_filtro(filtro)  # §29.4 — reject chars outside allowlist before forwarding

        campo1 = _validar_campo(agrupar_por)
        campo2 = _validar_campo(agrupar_por_2) if agrupar_por_2 else None

        extrator1 = _extrator_de_campo(campo1, agrupar_por)
        extrator2 = _extrator_de_campo(campo2, agrupar_por_2) if campo2 else None

        client = await _get_client(ctx)

        # Busca todos os processos
        todos = []
        pg = 0
        while True:
            if ctx:
                await ctx.report_progress(len(todos), None, f"Buscando página {pg + 1}…")
            result = await client.listar_processos(
                FiltroListagemProcessos(
                    limit=200, pagina=pg, apenas_meus=apenas_meus, filtro=filtro
                )
            )
            todos.extend(result["processos"])
            if not result.get("tem_proxima"):
                break
            pg += 1

        grupos = _agrupar_processos(todos, extrator1, extrator2)
        resumo = _ordenar_resumo(grupos)

        header = str(campo1["desc"])
        if campo2:
            header += f" x {campo2['desc']}"

        return _json(
            {
                "agrupamento": header,
                "total_processos": len(todos),
                "total_grupos": len(resumo),
                "grupos": resumo,
                "_hints": get_hints(),
            }
        )
    except httpx.RequestError as e:
        msg = f"SEI inacessível: {e}"
        raise SEIConnectionError(msg) from e


_DEFAULT_PESQUISA_LIMIT = 50

# String filter keys that must be round-tripped through the opaque cursor.
_PESQUISA_STR_KEYS = (
    "palavras_chave",
    "descricao",
    "busca_rapida",
    "data_inicio",
    "data_fim",
    "sta_tipo_data",
    "id_unidade_geradora",
    "id_assunto",
    "grupo",
)


class _PesquisaArgs(TypedDict):
    """Resolved search parameters after cursor decoding."""

    pagina: int
    palavras_chave: str
    descricao: str
    busca_rapida: str
    data_inicio: str
    data_fim: str
    sta_tipo_data: str
    id_unidade_geradora: str
    id_assunto: str
    grupo: str
    limit: int
    cursor_extra: dict


def _pesquisa_cursor_args(
    cursor: str,
    pagina: int,
    palavras_chave: str,
    descricao: str,
    busca_rapida: str,
    data_inicio: str,
    data_fim: str,
    sta_tipo_data: str,
    id_unidade_geradora: str,
    id_assunto: str,
    grupo: str,
    limit: int,
) -> _PesquisaArgs:
    """Resolve paginação por cursor e monta cursor_extra para pesquisa de processos."""
    vals: dict[str, str] = dict(
        zip(
            _PESQUISA_STR_KEYS,
            (
                palavras_chave,
                descricao,
                busca_rapida,
                data_inicio,
                data_fim,
                sta_tipo_data,
                id_unidade_geradora,
                id_assunto,
                grupo,
            ),
            strict=True,
        )
    )
    if cursor:
        decoded = _decode_cursor(cursor)
        pagina = decoded.get("p", pagina)
        limit = decoded.get("limit", limit)
        for k in _PESQUISA_STR_KEYS:
            vals[k] = decoded.get(k, vals[k])
    extra: dict = {k: v for k, v in vals.items() if v}
    extra["limit"] = limit
    return _PesquisaArgs(
        pagina=pagina,
        palavras_chave=vals["palavras_chave"],
        descricao=vals["descricao"],
        busca_rapida=vals["busca_rapida"],
        data_inicio=vals["data_inicio"],
        data_fim=vals["data_fim"],
        sta_tipo_data=vals["sta_tipo_data"],
        id_unidade_geradora=vals["id_unidade_geradora"],
        id_assunto=vals["id_assunto"],
        grupo=vals["grupo"],
        limit=limit,
        cursor_extra=extra,
    )


def _wrap_pesquisa(
    result: dict, *, include_raw: bool, pagina: int, limit: int, cursor_extra: dict
) -> ResultadoPesquisaProcessos | str:
    """Retorna modelo shaped ou JSON bruto segundo include_raw."""
    if include_raw:
        return _json(result)
    paginado = _add_cursor(
        result,
        pagina=pagina,
        limit=limit,
        tool_name="sei_pesquisar_processos",
        cursor_extra=cursor_extra,
    )
    return ResultadoPesquisaProcessos(
        processos=paginado.get("processos", []),
        total_itens=paginado.get("total_itens"),
        proximo_cursor=paginado.get("proximo_cursor"),
        tem_proxima_inferida=paginado.get("tem_proxima_inferida", False),
        next_actions=[NextAction(**a) for a in paginado.get("next_actions", [])],
        fonte=paginado.get("fonte", "rest"),
        aviso=paginado.get("aviso"),
    )


@mcp.tool(annotations=_READ)
async def sei_pesquisar_processos(
    palavras_chave: str = "",
    descricao: str = "",
    busca_rapida: str = "",
    data_inicio: str = "",
    data_fim: str = "",
    sta_tipo_data: str = "",
    id_unidade_geradora: str = "",
    id_assunto: str = "",
    grupo: str = "",
    limit: int = 50,
    pagina: int = 0,
    cursor: str = "",
    ctx: Context | None = None,
    *,
    include_raw: bool = False,
) -> ResultadoPesquisaProcessos | str:
    """Pesquisa processos no SEI por texto, descrição, datas, unidade ou assunto.

    Use palavras_chave para busca geral ou busca_rapida para busca simplificada.
    Datas no formato DD/MM/AAAA.

    Filtros adicionais (REST only):
    - sta_tipo_data: tipo de período — "30" (últimos 30 dias), "60" (últimos 60 dias)
      ou "0" (personalizado, requer data_inicio/data_fim)
    - id_unidade_geradora: id da unidade que gerou o processo (use sei_listar_unidades)
    - id_assunto: id do assunto (use sei_pesquisar_assuntos para obter o id)
    - grupo: id do grupo de acompanhamento (use sei_listar_grupos_acompanhamento)

    Paginação: passe `cursor` = `proximo_cursor` da resposta anterior, ou use
    `pagina` (0-indexado) para acesso direto.

    Busca via web (instâncias sem mod-wssei, ex: SEI-RO):
    - Quando REST não está disponível, a busca usa o formulário de pesquisa
      avançada do SEI via scraping. O retorno inclui "fonte": "web".
    - Use aspas para frase exata: palavras_chave='"NOME COMPLETO" aposentadoria'
      é muito mais preciso do que palavras soltas.
    - A busca web varre todo o SEI (não filtrada por unidade do usuário).
    - Os filtros estruturais acima são ignorados no caminho web; quando isso
      ocorre, o campo "aviso" no retorno lista os filtros descartados.
    - Máximo de 10 resultados por página no caminho web.

    Use include_raw=true para o payload bruto sem envelope de paginação.
    """
    args = _pesquisa_cursor_args(
        cursor,
        pagina,
        palavras_chave,
        descricao,
        busca_rapida,
        data_inicio,
        data_fim,
        sta_tipo_data,
        id_unidade_geradora,
        id_assunto,
        grupo,
        limit,
    )
    pagina = args["pagina"]
    limit = args["limit"]
    cursor_extra = args["cursor_extra"]

    _rest_unavailable = False
    try:
        client = await _get_client(ctx)
        result = await client.pesquisar_processos(
            FiltrosPesquisaProcessos(
                palavras_chave=args["palavras_chave"],
                descricao=args["descricao"],
                busca_rapida=args["busca_rapida"],
                data_inicio=args["data_inicio"],
                data_fim=args["data_fim"],
                sta_tipo_data=args["sta_tipo_data"],
                id_unidade_geradora=args["id_unidade_geradora"],
                id_assunto=args["id_assunto"],
                grupo=args["grupo"],
                limit=limit,
                pagina=pagina,
            )
        )
        return _wrap_pesquisa(
            result, include_raw=include_raw, pagina=pagina, limit=limit, cursor_extra=cursor_extra
        )
    except (ValueError, httpx.UnsupportedProtocol, SEIConnectionError):
        _rest_unavailable = True  # REST não configurado (sem SEI_URL) ou URL inválida
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code in (404, 501):
            _rest_unavailable = True  # mod-wssei ausente ou endpoint não encontrado
        else:
            msg = str(exc)
            raise SEIError(msg) from exc
    except httpx.RequestError as e:
        msg = f"SEI inacessível: {e}"
        raise SEIConnectionError(msg) from e

    # Fallback via web scraper (instâncias sem mod-wssei)
    q_web = " ".join(filter(None, [args["palavras_chave"], args["busca_rapida"]]))
    dropped = [
        n for n in ("sta_tipo_data", "id_unidade_geradora", "id_assunto", "grupo") if args[n]
    ]
    try:
        web = await _get_web_client(ctx)
        result_dict = await web.pesquisar_processos_web(
            q=q_web,
            descricao=args["descricao"],
            data_inicio=args["data_inicio"],
            data_fim=args["data_fim"],
            pagina=pagina,
        )
        items = result_dict["processos"]
        parsed_total = result_dict.get("total_itens")

        page_items = items[:limit]

        if parsed_total is not None:
            total_itens = parsed_total
            tem_proxima = total_itens > (pagina + 1) * SEI_WEB_PAGE_SIZE
        else:
            total_itens = None  # unknown; let _add_cursor use tem_proxima heuristic
            tem_proxima = len(items) >= SEI_WEB_PAGE_SIZE

        paged: dict = {
            "processos": page_items,
            "pagina_atual": pagina,
            "itens_pagina": len(page_items),
            "total_itens": total_itens,
            "tem_proxima": tem_proxima,
            "fonte": "web",
        }
        avisos: list[str] = []
        if dropped:
            avisos.append(
                f"filtros ignorados (não suportados na pesquisa web): {', '.join(dropped)}"
            )
        if limit < SEI_WEB_PAGE_SIZE and len(items) > limit:
            avisos.append(
                f"resultados truncados para limit={limit} (página web retorna até {SEI_WEB_PAGE_SIZE})"
            )
        if avisos:
            paged["aviso"] = "; ".join(avisos).capitalize()
        return _wrap_pesquisa(
            paged, include_raw=include_raw, pagina=pagina, limit=limit, cursor_extra=cursor_extra
        )
    except (SEIError, httpx.HTTPError) as e2:
        msg = f"Web: {e2}"
        raise SEIError(msg) from e2


@mcp.tool(annotations=_DEST)
async def sei_enviar_processo(
    numero_processo: str,
    unidades_destino: str,
    manter_aberto: str = "N",
    remover_anotacao: str = "N",
    enviar_email: str = "N",
    data_retorno: str = "",
    dias_retorno: str = "",
    ctx: Context | None = None,
) -> str:
    """Envia (tramita) um processo para outra(s) unidade(s) no SEI.

    Parâmetros:
    - numero_processo: protocolo formatado (ex: 50300.000123/2025-00)
    - unidades_destino: sigla da unidade (ex: "SFC", "ECP-SFC") OU ID numérico.
      Para múltiplas unidades, separe por vírgula.
      Se informar sigla, resolve o ID automaticamente via REST ou AJAX web.
    - manter_aberto: "N" fechar na unidade atual (padrão), "S" manter aberto
    - remover_anotacao: "S" remover anotações, "N" manter (padrão)
    - enviar_email: "S" notificar por email (só se o usuário pedir)
    - data_retorno: data de retorno programado DD/MM/AAAA (só se o usuário pedir)
    - dias_retorno: prazo em dias para retorno (alternativa à data, só se pedir)

    """
    backend = await _backend(ctx)
    # O backend resolve sigla→id da(s) unidade(s) destino (REST via
    # pesquisar_unidades, web via autocomplete) e levanta SEIValidationError
    # com os candidatos quando uma sigla não casa.
    result = await backend.enviar_processo(
        numero_processo,
        EnvioProcesso(
            unidades_destino=unidades_destino,
            manter_aberto=manter_aberto,
            remover_anotacao=remover_anotacao,
            enviar_email=enviar_email,
            data_retorno=data_retorno,
            dias_retorno=dias_retorno,
        ),
    )
    return _json(result)


@mcp.tool(annotations=_IDEM)
async def sei_atribuir_processo(
    numero_processo: str,
    usuario: str,
    ctx: Context | None = None,
) -> str:
    """Atribui um processo a um usuário da unidade.

    Parâmetros:
    - numero_processo: protocolo formatado (ex: 50300.000123/2025-00)
    - usuario: ID numérico do usuário OU nome/parte do nome
      (ex: "100001860" ou "Karina" ou "Karina Shimoishi")

    Via web, o usuário é escolhido de um <select> no form — use
    sei_atribuir_processo(usuario="?") para listar os usuários disponíveis.
    """
    backend = await _backend(ctx)
    if usuario == "?":
        # Descoberta: lista os usuários atribuíveis na unidade atual.
        return _json(await backend.listar_usuarios())
    # O backend resolve nome→id (REST tenta os candidatos de listar_usuarios; web
    # casa no <select> do form) e levanta erro tipado quando nada casa/tem permissão.
    result = await backend.atribuir_processo(numero_processo, usuario)
    return _json(result)


@mcp.tool(annotations=_WRITE)
async def sei_sobrestar_processo(
    processo: str,
    motivo: str,
    processo_vinculado: str = "",
    ctx: Context | None = None,
) -> str:
    """Sobresta um processo no SEI.

    Parâmetros:
    - processo: protocolo formatado (ex: 50300.018905/2018-67) ou IdProcedimento
    - motivo: motivo do sobrestamento (obrigatório)
    - processo_vinculado: protocolo de outro processo para vincular (opcional)

    """
    backend = await _backend(ctx)
    # O backend resolve processo/vinculado → id; se o processo estiver aberto em
    # outra unidade, o SEI rejeita e o erro original (com a mensagem do SEI)
    # propaga ao agente.
    result = await backend.sobrestar_processo(processo, motivo, processo_vinculado)
    return _json(result)


_app = _typer.Typer(
    no_args_is_help=False,
    add_completion=False,
    pretty_exceptions_show_locals=False,
    rich_markup_mode="rich",
)


@_app.command("setup")
def _cmd_setup(
    *,
    force: Annotated[
        bool,
        _typer.Option(
            "--force", help="Reconfigurar do zero, sobrescrevendo a configuração existente."
        ),
    ] = False,
) -> None:
    """Configurar o MCP SEI interativamente (wizard de primeira vez)."""
    run_setup_wizard(force=force)


@_app.command("set-password")
def _cmd_set_password() -> None:
    """Atualizar apenas a senha no Keyring sem alterar a configuração MCP."""
    run_set_password()


@_app.callback(invoke_without_command=True)
def _cmd_default(ctx: _typer.Context) -> None:
    """MCP Server para o SEI — 126 tools, scraper HTTP + REST híbrido."""
    if ctx.invoked_subcommand is not None:
        return
    if _http_mode:
        run_remote(mcp, port=_http_port)
    else:
        mcp.run(transport="stdio", show_banner=False)


def main() -> None:
    """Entry point do console script `todos`."""
    _app()
