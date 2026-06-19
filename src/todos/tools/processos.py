"""Tools de processos do SEI.

Agrupa as operações sobre processos (expedientes): leitura (consulta, árvore,
documentos, interessados, sobrestamentos, atividades, relacionamentos),
escrita/tramitação (criar, alterar, concluir, reabrir, receber, registrar
andamento, anotar/observar) e geração de PDF/ZIP consolidados.

Todas as tools roteiam pelo backend composto (`_backend`) — REST-first com
fallback web — usando o protocolo formatado direto, sem resolução prévia de id.

Sem `from __future__ import annotations`: o FastMCP introspecta os type hints em
tempo de execução para montar o schema de cada tool, então as anotações precisam
ser objetos reais (não strings adiadas).
"""

import base64
import re
import tempfile
from pathlib import Path
from typing import Annotated, Literal

from fastmcp import Context
from pydantic import Field

from todos.backends import EnvioProcesso, NovoProcesso
from todos.exceptions import SEIValidationError
from todos.hints import get_hints
from todos.mcp_app import (
    _DEST,
    _IDEM,
    _MAX_PDF_MB,
    _MAX_ZIP_MB,
    _READ,
    _WRITE,
    _add_cursor,
    _backend,
    _decode_cursor,
    _json,
    _shape_resposta_escrita,
    access_control,
    mcp,
)
from todos.responses import (
    Andamento,
    DocumentoResumo,
    ListaAtividades,
    ListaDocumentos,
    NextAction,
    ProcessoDetalhe,
    ProcessoInfo,
    RespostaEscrita,
    ResultadoListaProcessos,
)
from todos.tools.configuracao import _ProtocoloFormatado

# Nível de acesso: 0=público, 1=restrito, 2=sigiloso
_NIVEL_ACESSO = r"^[012]$"

# ---------------------------------------------------------------------------
# Leitura
# ---------------------------------------------------------------------------


_LISTA_DOCS_LIMIT = 50
_ATIVIDADES_LIMIT = 50


def _to_str_list(items: list) -> list[str]:
    """Normaliza lista de strings ou dicts para list[str]."""
    out = []
    for item in items:
        if isinstance(item, str):
            out.append(item)
        elif isinstance(item, dict):
            label = (
                item.get("nome")
                or item.get("sigla")
                or item.get("unidade")
                or item.get("codigo")
                or item.get("descricao")
                or ""
            )
            if label:
                out.append(label)
    return out


def _shape_lista_documentos(result: dict, protocolo: str, *, tool_name: str) -> ListaDocumentos:
    """Retorna ListaDocumentos a partir do payload bruto de arvore_processo/listar_documentos."""
    docs_raw: list = result.get("documentos", [])
    total: int = result.get("total_documentos", len(docs_raw))
    docs = docs_raw[:_LISTA_DOCS_LIMIT]
    resumos = [
        DocumentoResumo(
            id=str(d.get("id", "")),
            numero_sei=str(d.get("numero_sei", "")),
            tipo_documento=str(d.get("tipo_documento", "")),
            nome_composto=str(d.get("nome_composto", "")),
            sigla_unidade=str(d.get("sigla_unidade", "")),
            assinado=d.get("assinado"),
            cancelado=d.get("cancelado"),
        )
        for d in docs
    ]
    actions: list[NextAction] = []
    if total > _LISTA_DOCS_LIMIT:
        actions = [
            NextAction(
                tool=tool_name,
                args={"protocolo_formatado": protocolo, "include_raw": True},
                reason=(
                    f"Exibindo {len(resumos)} de {total} documentos; "
                    "use include_raw=true para a lista completa."
                ),
            )
        ]
    return ListaDocumentos(
        processo=protocolo,
        total_documentos=total,
        documentos=resumos,
        next_actions=actions,
    )


def _shape_atividades(result: dict, *, ordem: str = "desc") -> ListaAtividades:
    """Retorna andamentos em formato shaped a partir do payload bruto de listar_atividades."""
    raw: list[dict] = list(result.get("andamentos", []))
    # Raw list from web scraper is newest-first; reverse only when ascending order is requested.
    if ordem == "asc":
        raw.reverse()
    total = result.get("total_andamentos", len(raw))
    truncated = raw[:_ATIVIDADES_LIMIT]
    processo_info = ProcessoInfo.model_validate(result.get("processo", {}))
    return ListaAtividades(
        processo=processo_info,
        total_andamentos=total,
        andamentos=[Andamento(**a) for a in truncated],
        truncado=total > _ATIVIDADES_LIMIT,
    )


def _shape_consultar_processo(merged: dict, protocolo: str) -> ProcessoDetalhe:
    """Retorna ProcessoDetalhe shaped do payload bruto de consultar_processo.

    Com include_raw=False (padrão), retorna um resumo compacto com next_actions
    apontando para sei_arvore_processo quando há documentos.
    """
    total_docs = merged.get("total_documentos", len(merged.get("documentos", [])))

    interessados = _to_str_list(merged.get("interessados", []))

    actions: list[NextAction] = []
    if total_docs > 0:
        actions.append(
            NextAction(
                tool="sei_arvore_processo",
                args={"protocolo_formatado": protocolo},
                reason=f"Processo tem {total_docs} documento(s); use sei_arvore_processo para ver a lista.",
            )
        )

    return ProcessoDetalhe(
        id_procedimento=merged.get("IdProcedimento") or merged.get("id_procedimento", ""),
        protocolo=merged.get("ProtocoloProcedimentoFormatado")
        or merged.get("protocolo", protocolo),
        tipo=merged.get("NomeTipoProcedimento") or merged.get("tipo", ""),
        especificacao=merged.get("especificacao", ""),
        nivel_acesso=merged.get("nivelAcesso", merged.get("nivel_acesso", "")),
        interessados=interessados,
        total_documentos=total_docs,
        next_actions=actions,
        warnings=merged.get("_warnings"),
        aviso_acesso=merged.get("_aviso_acesso"),
    )


@mcp.tool(annotations=_READ)
async def sei_consultar_processo(
    protocolo_formatado: _ProtocoloFormatado,
    ctx: Context,
    *,
    include_raw: bool = False,
) -> ProcessoDetalhe | str:
    """Consulta um processo SEI pelo número de protocolo formatado.

    Exemplo de protocolo: 50300.000123/2025-00

    Implementação híbrida: combina REST mod-wssei (campos estruturados) com
    scraper web (árvore de documentos), ambos em paralelo.

    Campos retornados (include_raw=false, padrão):
    - protocolo, tipo, especificacao, situacao, nivel_acesso
    - interessados[], unidades_abertas[], total_documentos
    - next_actions: sugere sei_arvore_processo quando há documentos

    Use include_raw=true para o payload bruto completo com todos os campos
    (assuntos, hipotese_legal, grau_sigilo, observacoes, relacionados, documentos[]).

    Quando o processo é restrito ou sigiloso (nivel_acesso 1 ou 2), a resposta
    inclui `_aviso_acesso` — aviso informativo, não erro de permissão.
    """
    backend = await _backend(ctx)
    merged = await backend.consultar_processo(protocolo_formatado)

    nivel, hipotese = access_control.extrair_nivel(merged)
    if access_control.precisa_disclaimer(nivel):
        merged["_aviso_acesso"] = access_control.construir_disclaimer_acompanhante(
            nivel,
            hipotese,
            alvo={"tipo": "processo", "protocolo": protocolo_formatado},
        )

    if include_raw:
        return _json(merged)
    return _shape_consultar_processo(merged, protocolo_formatado)


@mcp.tool(annotations=_READ)
async def sei_arvore_processo(
    protocolo_formatado: _ProtocoloFormatado,
    ctx: Context | None = None,
    *,
    include_raw: bool = False,
) -> ListaDocumentos | str:
    """Mostra a árvore completa de documentos de um processo SEI.

    Implementação via scraper web (~10x mais rápido que REST: ~1 s vs ~12 s).
    Parseia arvore_montar.php para extrair id, tipo, sigla da unidade geradora
    e número SEI de cada documento.

    Aceita o protocolo formatado (ex: 50300.000123/2025-00).

    Retorno padrão (include_raw=false): lista shaped com até 50 documentos
    (DocumentoResumo: id, numero_sei, tipo_documento, nome_composto, sigla_unidade).
    Use include_raw=true para o payload bruto completo (todos os docs, todos os campos).

    Para ler o conteúdo de um documento, use sei_ler_documento com o id.
    """
    backend = await _backend(ctx)
    if ctx:
        await ctx.report_progress(0, 100, "Buscando árvore do processo…")
    result = await backend.arvore_processo(protocolo_formatado)
    if ctx:
        await ctx.report_progress(100, 100)
    if include_raw:
        return _json(result)
    return _shape_lista_documentos(result, protocolo_formatado, tool_name="sei_arvore_processo")


@mcp.tool(annotations=_READ)
async def sei_listar_documentos(
    protocolo_formatado: _ProtocoloFormatado,
    ctx: Context | None = None,
    *,
    include_raw: bool = False,
) -> ListaDocumentos | str:
    """Lista todos os documentos de um processo SEI.

    Implementação via scraper web (~10x mais rápido que REST).
    Aceita o protocolo formatado (ex: 50300.000123/2025-00).

    Retorno padrão (include_raw=false): até 50 documentos shaped
    (id, numero_sei, tipo_documento, nome_composto, sigla_unidade).
    Use include_raw=true para o payload bruto completo.

    Para ler o conteúdo de um documento, use sei_ler_documento com o id.
    """
    backend = await _backend(ctx)
    raw = await backend.listar_documentos(protocolo_formatado)
    if include_raw:
        return _json(raw)
    # REST backend returns list[dict]; normalize to the same dict format as the web backend.
    if isinstance(raw, list):
        docs = [
            {
                "id": str(d.get("id", "")),
                "numero_sei": d.get("atributos", {}).get("protocoloFormatado", ""),
                "tipo_documento": d.get("atributos", {}).get("tipoDocumento", ""),
                "nome_composto": " ".join(
                    filter(
                        None,
                        [
                            d.get("atributos", {}).get("tipoDocumento", ""),
                            d.get("atributos", {}).get("siglaUnidade", ""),
                            str(d.get("id", "")),
                        ],
                    )
                ),
                "sigla_unidade": d.get("atributos", {}).get("siglaUnidade", ""),
            }
            for d in raw
        ]
        result: dict = {"documentos": docs, "total_documentos": len(docs)}
    else:
        result = raw
    return _shape_lista_documentos(result, protocolo_formatado, tool_name="sei_listar_documentos")


@mcp.tool(annotations=_READ)
async def sei_listar_unidades_processo(
    processo: str,
    ctx: Context | None = None,
) -> str:
    """Lista as unidades onde o processo está aberto atualmente.

    - processo: protocolo formatado (ex: 50300.000123/2025-00) ou IdProcedimento

    Retorna lista de objetos com id_unidade, sigla e nome. Útil para saber em
    quais setores o processo está distribuído antes de tramitar ou consultar.
    """
    backend = await _backend(ctx)
    result = await backend.listar_unidades_processo(processo)
    return _json(result)


@mcp.tool(annotations=_READ)
async def sei_listar_interessados(
    processo: str,
    ctx: Context | None = None,
) -> str:
    """Lista os interessados cadastrados em um processo.

    - processo: protocolo formatado (ex: 50300.000123/2025-00) ou IdProcedimento

    Retorna lista de objetos com id e nome de cada interessado. Use para verificar
    ou auditar os interessados antes de alterar o processo via sei_alterar_processo.
    """
    backend = await _backend(ctx)
    result = await backend.listar_interessados(processo)
    return _json(result)


@mcp.tool(annotations=_READ)
async def sei_listar_sobrestamentos(
    processo: str,
    ctx: Context | None = None,
) -> str:
    """Lista o histórico de sobrestamentos de um processo.

    - processo: protocolo formatado (ex: 50300.000123/2025-00) ou IdProcedimento

    Retorna lista cronológica de eventos de sobrestamento (data, motivo, processo
    vinculado) e dessobrestamento. Use sei_remover_sobrestamento para dessobrestar
    o processo ativo.
    """
    backend = await _backend(ctx)
    result = await backend.listar_sobrestamentos(processo)
    return _json(result)


@mcp.tool(annotations=_READ)
async def sei_consultar_atribuicao(
    processo: str,
    ctx: Context | None = None,
) -> str:
    """Consulta a atribuição atual de um processo (quem está responsável).

    Disponível desde mod-wssei 2.0.0 (SEI 4.0.x).
    Se falhar com erro inesperado, use sei_versao para verificar a versão instalada.

    """
    backend = await _backend(ctx)
    result = await backend.consultar_atribuicao(processo)
    return _json(result)


@mcp.tool(annotations=_READ)
async def sei_historico_atribuicoes(
    processo: str,
    ctx: Context | None = None,
) -> str:
    """Lista o histórico de atribuições de um processo.

    Retorna os eventos de atribuição/remoção em ordem cronológica e derivações:
    - `atribuidos`: logins distintos a quem o processo já foi atribuído
    - `atual`: login atualmente atribuído (vazio se a última ação foi remoção)
    - `anterior`: login atribuído imediatamente antes do atual

    Útil para fluxos de trabalho (devolver ao responsável anterior).

    """
    backend = await _backend(ctx)
    result = await backend.listar_historico_atribuicoes(processo)
    return _json(result)


@mcp.tool(annotations=_READ)
async def sei_verificar_acesso(
    processo: str,
    ctx: Context | None = None,
) -> str:
    """Verifica se o usuário tem acesso a um processo.

    Útil para checar permissão antes de operações em processos restritos.
    Disponível desde mod-wssei 2.0.0 (SEI 4.0.x).
    Se falhar com erro inesperado, use sei_versao para verificar a versão instalada.

    """
    backend = await _backend(ctx)
    result = await backend.verificar_acesso(processo)
    return _json(result)


@mcp.tool(annotations=_READ)
async def sei_listar_relacionamentos(
    processo: str,
    ctx: Context | None = None,
) -> str:
    """Lista processos relacionados a um processo.

    REQUER mod-wssei 3.0.2+ (SEI 5.0.x). Não disponível em versões anteriores.
    Se falhar, use sei_versao para verificar. Precisa ser >= 3.0.2.
    """
    backend = await _backend(ctx)
    result = await backend.listar_relacionamentos(processo)
    return _json(result)


@mcp.tool(annotations=_READ)
async def sei_listar_atividades(
    processo: str,
    ctx: Context | None = None,
    *,
    ordem: Literal["desc", "asc"] = "desc",
    include_raw: bool = False,
) -> ListaAtividades | str:
    """Lista o histórico de atividades/andamentos de um processo.

    Implementação via scraper web (procedimento_consultar_historico.php).
    Retorna ações registradas (tramitações, assinaturas, edições, etc.)
    com data/hora, unidade, usuário e descrição.

    Aceita protocolo formatado (ex: 50300.000123/2025-00).

    Parâmetros:
    - ordem: "desc" (padrão — mais recente primeiro) ou "asc" (cronológica)
    - include_raw: false (padrão) retorna até 50 andamentos shaped;
      true retorna o payload bruto completo.
    """
    backend = await _backend(ctx)
    result = await backend.listar_atividades(processo)
    if include_raw:
        return _json(result)
    return _shape_atividades(result, ordem=ordem)


@mcp.tool(annotations=_READ)
async def sei_listar_processos(
    pagina: int = 0,
    apenas_meus: str = "",
    tipo: str = "",
    filtro: str = "",
    cursor: str = "",
    ctx: Context | None = None,
) -> ResultadoListaProcessos:
    """Lista processos da caixa da unidade atual no SEI (Controle de Processos).

    Implementação via scraper do frontend web (~20x mais rápida que a REST API).
    O SEI pagina em até 500 processos por página; use `pagina` para navegar.

    Parâmetros:
    - pagina: número da página (0=primeira, 1=segunda, etc.)
    - apenas_meus: "S" para apenas processos atribuídos ao usuário logado
      (filtro server-side via hdnMeusProcessos=M)
    - tipo: substring (case-insensitive) para filtrar pelo nome do tipo processual
      (filtro client-side, sobre a coluna "Tipo")
    - filtro: substring (case-insensitive) aplicada a qualquer campo do processo
      (protocolo, tipo, especificação, interessados — filtro client-side)
    - cursor: cursor opaco de paginação — passe `proximo_cursor` da resposta anterior.

    Campos retornados por processo (visualização Detalhada):
    - id_procedimento: id interno do SEI
    - protocolo: número formatado (ex: 50300.007186/2026-69)
    - Tipo: tipo processual
    - atribuicao: usuário ao qual está atribuído
    - Especificação, Interessados, Marcadores, etc. — conforme as colunas
      configuradas no painel da unidade

    Campos de paginação na resposta:
    - total_itens: total de processos no servidor (antes de filtros client-side)
    - total_filtrados: após filtros tipo/filtro
    - pagina_atual: página corrente
    - tem_proxima: true se há mais páginas (repita com pagina+1)

    NOTAS:
    - Processos sobrestados e concluídos não aparecem nesta listagem.
    - Para agrupamento estatístico use sei_resumo_processos (REST, com flags
      estruturadas de tramitação, sobrestamento, acesso, etc.).
    - Login web é executado uma vez por sessão (~3 s); listagens subsequentes
      custam ~600 ms cada, contra ~14 s da REST API.
    """
    if cursor:
        decoded = _decode_cursor(cursor)
        pagina = decoded.get("p", pagina)
        apenas_meus = decoded.get("apenas_meus", apenas_meus)
        tipo = decoded.get("tipo", tipo)
        filtro = decoded.get("filtro", filtro)
    backend = await _backend(ctx)
    result = await backend.listar_processos(
        pagina=pagina,
        apenas_meus=apenas_meus,
        tipo=tipo,
        filtro=filtro,
    )
    result["hints"] = get_hints()
    cursor_extra: dict = {}
    if apenas_meus:
        cursor_extra["apenas_meus"] = apenas_meus
    if tipo:
        cursor_extra["tipo"] = tipo
    if filtro:
        cursor_extra["filtro"] = filtro
    return ResultadoListaProcessos.model_validate(
        _add_cursor(
            result,
            pagina=pagina,
            limit=500,
            tool_name="sei_listar_processos",
            cursor_extra=cursor_extra,
        )
    )


# ---------------------------------------------------------------------------
# Escrita e tramitação
# ---------------------------------------------------------------------------


@mcp.tool(annotations=_WRITE)
async def sei_criar_processo(
    tipo_processo: str,
    especificacao: str = "",
    assuntos: str = "",
    interessados: str = "",
    observacoes: str = "",
    nivel_acesso: Annotated[str, Field(pattern=_NIVEL_ACESSO)] = "0",
    hipotese_legal: str = "",
    ctx: Context | None = None,
) -> RespostaEscrita:
    """Cria um novo processo no SEI.

    Parâmetros:
    - tipo_processo: ID do tipo de processo (use sei_pesquisar_tipos_processo)
    - especificacao: descrição do processo (recomendado para organizar a caixa)
    - assuntos: IDs dos assuntos separados por vírgula
    - interessados: IDs dos interessados separados por vírgula
    - observacoes: observações adicionais (apenas REST)
    - nivel_acesso: 0=público (padrão), 1=restrito, 2=sigiloso
    - hipotese_legal: ID da hipótese legal (obrigatório se restrito/sigiloso).
      Use sei_pesquisar_hipoteses_legais para descobrir o ID.

    Retorna o IdProcedimento e ProtocoloFormatado do processo criado.
    """
    if nivel_acesso in ("1", "2") and not hipotese_legal:
        msg = f"nivel_acesso={nivel_acesso} requer hipotese_legal. Informe o ID da hipótese legal."
        raise SEIValidationError(
            msg,
            error_code="HIPOTESE_LEGAL_OBRIGATORIA",
            recoverable=True,
            suggested_next_tool="sei_pesquisar_hipoteses_legais",
            suggested_args={"filtro": ""},
        )
    backend = await _backend(ctx)
    result = await backend.criar_processo(
        NovoProcesso(
            tipo_processo=tipo_processo,
            especificacao=especificacao,
            assuntos=assuntos,
            interessados=interessados,
            observacoes=observacoes,
            nivel_acesso=nivel_acesso,
            hipotese_legal=hipotese_legal,
        )
    )
    return _shape_resposta_escrita(result, "criar_processo")


@mcp.tool(annotations=_WRITE)
async def sei_alterar_processo(
    processo: str,
    especificacao: str = "",
    nivel_acesso: str = "",
    hipotese_legal: str = "",
    observacao: str = "",
    ctx: Context | None = None,
) -> RespostaEscrita:
    """Altera metadados de um processo no SEI.

    Parâmetros:
    - processo: protocolo formatado (ex: 50300.009752/2026-77) ou IdProcedimento
    - especificacao: nova descrição/especificação do processo
    - nivel_acesso: 0=público, 1=restrito, 2=sigiloso
    - hipotese_legal: ID da hipótese legal (obrigatório se restrito/sigiloso).
      Use sei_pesquisar_hipoteses_legais para descobrir o ID.
    - observacao: observações adicionais

    Informe apenas os campos que deseja alterar.
    """
    backend = await _backend(ctx)
    result = await backend.alterar_processo(
        processo,
        especificacao=especificacao,
        nivel_acesso=nivel_acesso,
        hipotese_legal=hipotese_legal,
        observacao=observacao,
    )
    return _shape_resposta_escrita(result, "alterar_processo")


# sei_enviar_processo permanece em server.py: a resolução sigla→id da unidade de
# destino ainda é orquestração de camada de tool (o backend REST não a faz).


@mcp.tool(annotations=_DEST)
async def sei_concluir_processo(numero_processo: str, ctx: Context | None = None) -> str:
    """Conclui um processo na unidade atual do SEI.

    O processo é removido da caixa da unidade mas permanece acessível.
    Use sei_reabrir_processo para reverter.

    CONFIRMAÇÃO OBRIGATÓRIA: solicite confirmação do usuário antes de executar —
    o efeito é imediato e visível para outros usuários da unidade.
    """
    backend = await _backend(ctx)
    result = await backend.concluir_processo(numero_processo)
    return _json(result)


@mcp.tool(annotations=_IDEM)
async def sei_reabrir_processo(processo: str, ctx: Context | None = None) -> str:
    """Reabre um processo que foi concluído na unidade.

    - processo: protocolo formatado (ex: 50300.018905/2018-67) ou IdProcedimento

    O processo volta para a caixa da unidade atual.
    """
    backend = await _backend(ctx)
    result = await backend.reabrir_processo(processo)
    return _json(result)


@mcp.tool(annotations=_IDEM)
async def sei_receber_processo(
    processo: str,
    ctx: Context | None = None,
) -> str:
    """Confirma o recebimento de um processo na unidade atual.

    - processo: protocolo formatado ou IdProcedimento
    """
    backend = await _backend(ctx)
    result = await backend.receber_processo(processo)
    return _json(result)


@mcp.tool(annotations=_IDEM)
async def sei_remover_atribuicao(
    processo: str,
    ctx: Context | None = None,
) -> str:
    """Remove a atribuição de um processo (desatribui de qualquer usuário).

    - processo: protocolo formatado ou IdProcedimento
    """
    backend = await _backend(ctx)
    result = await backend.remover_atribuicao(processo)
    return _json(result)


@mcp.tool(annotations=_IDEM)
async def sei_remover_sobrestamento(
    processo: str,
    ctx: Context | None = None,
) -> str:
    """Remove o sobrestamento de um processo no SEI.

    - processo: protocolo formatado (ex: 50300.018905/2018-67) ou IdProcedimento

    """
    backend = await _backend(ctx)
    result = await backend.remover_sobrestamento(processo)
    return _json(result)


@mcp.tool(annotations=_WRITE)
async def sei_registrar_andamento(
    processo: str,
    descricao: str,
    ctx: Context | None = None,
) -> str:
    """Registra um andamento (atividade) no processo.

    - processo: protocolo formatado ou IdProcedimento
    - descricao: texto do andamento

    """
    backend = await _backend(ctx)
    result = await backend.registrar_andamento(processo, descricao)
    return _json(result)


@mcp.tool(annotations=_IDEM)
async def sei_criar_anotacao(
    processo: str,
    descricao: str,
    prioridade: str = "1",
    ctx: Context | None = None,
) -> str:
    """Cria uma anotação (post-it) em um processo no SEI.

    Parâmetros:
    - processo: protocolo formatado (ex: 50300.018905/2018-67) ou IdProcedimento
    - descricao: texto da anotação
    - prioridade: nível de prioridade (1=normal, 2=alta)

    """
    backend = await _backend(ctx)
    result = await backend.criar_anotacao(processo, descricao, prioridade)
    return _json(result)


@mcp.tool(annotations=_IDEM)
async def sei_remover_anotacao(
    processo: str,
    ctx: Context | None = None,
) -> str:
    """Remove a anotação (post-it) de um processo no SEI.

    Parâmetros:
    - processo: protocolo formatado (ex: 50300.018905/2018-67) ou IdProcedimento

    """
    backend = await _backend(ctx)
    result = await backend.remover_anotacao(processo)
    return _json(result)


@mcp.tool(annotations=_WRITE)
async def sei_criar_observacao(
    processo: str,
    descricao: str,
    ctx: Context | None = None,
) -> str:
    """Cria observação da unidade em um processo.

    Diferente da anotação (post-it individual), a observação é
    vinculada à unidade e visível por todos os usuários da unidade.
    Disponível desde mod-wssei 2.0.0 (SEI 4.0.x).
    Se falhar com erro inesperado, use sei_versao para verificar a versão instalada.
    """
    backend = await _backend(ctx)
    result = await backend.criar_observacao(processo, descricao)
    return _json(result)


@mcp.tool(annotations=_IDEM)
async def sei_marcar_nao_lido(
    numero_processo: str,
    ctx: Context | None = None,
) -> str:
    """Marca um processo como não lido na unidade atual.

    O SEI não possui funcionalidade nativa para isso. Esta tool usa
    o workaround de enviar o processo para a própria unidade, o que
    faz o SEI tratar como novo recebimento (não lido).

    - numero_processo: protocolo formatado (ex: 50300.012639/2023-26)
    """
    backend = await _backend(ctx)
    unidade = await backend.unidade_atual()
    id_unidade = unidade.get("id_unidade")
    if not id_unidade:
        msg = (
            "Não foi possível determinar o id da unidade atual "
            f"(sigla={unidade.get('sigla', '?')}); necessário para marcar como não lido."
        )
        raise SEIValidationError(msg)
    result = await backend.enviar_processo(
        numero_processo,
        EnvioProcesso(unidades_destino=id_unidade, manter_aberto="S"),
    )
    return _json(
        {
            "mensagem": "Processo marcado como não lido.",
            "detalhe": result.get("mensagem", ""),
        }
    )


@mcp.tool(annotations=_WRITE)
async def sei_executar_acao(
    processo: str,
    acao: str,
    ctx: Context | None = None,
    *,
    confirmar: bool = False,
) -> str:
    """Executa qualquer ação disponível no menu de um processo via scraper web.

    Parâmetros:
    - processo: protocolo formatado (ex: "50300.018905/2018-67")
    - acao: nome da ação no controlador SEI (ex: "procedimento_concluir")
    - confirmar: False (padrão) = dry-run que valida se a ação existe;
                 True = executa a ação de fato

    Esta é uma ferramenta de baixo nível — prefira as tools específicas
    (sei_concluir_processo, sei_reabrir_processo, etc.) quando disponíveis.
    Útil para ações sem tool dedicada ou para debugging.

    Exemplos:
    - sei_executar_acao("50300.018905/2018-67", "procedimento_concluir", confirmar=True)
    - sei_executar_acao("50300.018905/2018-67", "procedimento_visualizar")  # dry-run
    """
    if not confirmar:
        return _json(
            {
                "dry_run": True,
                "mensagem": (
                    f"Ação '{acao}' NÃO executada. Passe confirmar=True para executar. "
                    "Revise a ação antes de confirmar — algumas são irreversíveis."
                ),
                "processo": processo,
                "acao": acao,
            }
        )
    backend = await _backend(ctx)
    result = await backend.executar_acao(processo, acao)
    return _json(result)


# ---------------------------------------------------------------------------
# Geração de PDF/ZIP consolidado (binário)
# ---------------------------------------------------------------------------


def _salvar_arquivo_temp(
    protocolo: str,
    conteudo: bytes,
    extensao: str,
    max_mb: float,
) -> dict[str, object]:
    """Verifica tamanho, salva em temp e retorna dict com path e metadados."""
    tamanho_mb = len(conteudo) / 1024 / 1024
    if tamanho_mb > max_mb:
        msg = f"{extensao.upper()} muito grande ({tamanho_mb:.1f} MB). Baixe manualmente pelo SEI."
        raise SEIValidationError(msg)

    # O regex mantém apenas \w (letras/dígitos/_) e hífens; "." vira "_",
    # portanto "../" torna-se "___" — path traversal já está prevenido pelo regex.
    protocolo_safe = re.sub(r"[^\w\-]", "_", protocolo)
    caminho = Path(tempfile.gettempdir()) / f"SEI_{protocolo_safe}.{extensao}"
    try:
        caminho.write_bytes(conteudo)
    except OSError as exc:
        msg = f"Erro ao salvar {extensao.upper()} em disco: {exc}"
        raise SEIValidationError(msg) from exc

    return {
        "arquivo": str(caminho),
        "tamanho_mb": round(tamanho_mb, 2),
        "tamanho_bytes": len(conteudo),
        "base64": base64.b64encode(conteudo).decode(),
    }


@mcp.tool(annotations=_READ)
async def sei_gerar_pdf_processo(
    processo: str,
    ctx: Context | None = None,
) -> str:
    """Gera e baixa o PDF consolidado de um processo SEI.

    Consolida todos os documentos do processo num único PDF, exatamente
    como o botão "Gerar PDF" da interface web do SEI.

    Implementação via scraper web (procedimento_gerar_pdf).

    Parâmetros:
    - processo: protocolo formatado (ex: 0029.000123/2024-00)

    Retorna base64 do PDF, tamanho e caminho do arquivo salvo em disco.

    Nota: o processo precisa estar aberto na caixa da unidade atual.
    Para processos de outras unidades, use sei_trocar_unidade primeiro.
    """
    backend = await _backend(ctx)

    if ctx:
        await ctx.report_progress(0, 100, "Gerando PDF do processo…")
    pdf_bytes = await backend.gerar_pdf_processo(processo)
    if ctx:
        await ctx.report_progress(100, 100)

    return _json(_salvar_arquivo_temp(processo, pdf_bytes, "pdf", _MAX_PDF_MB))


@mcp.tool(annotations=_READ)
async def sei_gerar_zip_processo(
    processo: str,
    ctx: Context | None = None,
) -> str:
    """Gera e baixa o ZIP com todos os documentos de um processo SEI.

    Baixa todos os documentos do processo num único arquivo ZIP, exatamente
    como o botão "Gerar ZIP" da interface web do SEI.

    Implementação via scraper web (procedimento_gerar_zip).

    Parâmetros:
    - processo: protocolo formatado (ex: 0029.000123/2024-00)

    Retorna base64 do ZIP, tamanho e caminho do arquivo salvo em disco.
    """
    backend = await _backend(ctx)

    if ctx:
        await ctx.report_progress(0, 100, "Gerando ZIP do processo…")
    zip_bytes = await backend.gerar_zip_processo(processo)
    if ctx:
        await ctx.report_progress(100, 100)

    return _json(_salvar_arquivo_temp(processo, zip_bytes, "zip", _MAX_ZIP_MB))
