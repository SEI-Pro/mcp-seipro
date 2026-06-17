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

from fastmcp import Context

from todos.backends import EnvioProcesso, NovoProcesso
from todos.exceptions import SEIValidationError
from todos.mcp_app import (
    _IDEM,
    _MAX_PDF_MB,
    _MAX_ZIP_MB,
    _READ,
    _WRITE,
    _backend,
    _json,
    access_control,
    mcp,
)

# ---------------------------------------------------------------------------
# Leitura
# ---------------------------------------------------------------------------


_DOCS_INLINE_LIMIT = 30


def _shape_consultar_processo(merged: dict) -> dict:
    """Retorna um subconjunto legível do payload bruto de consultar_processo.

    Preserva todos os campos de metadata (pequenos) e trunca documentos[]
    se exceder _DOCS_INLINE_LIMIT, para evitar payloads de centenas de entradas.
    Para a lista completa de documentos use sei_arvore_processo.
    """
    docs_raw: list = merged.get("documentos", [])
    total_docs: int = merged.get("total_documentos", len(docs_raw))

    docs_shaped = docs_raw[:_DOCS_INLINE_LIMIT]
    has_more_docs = total_docs > _DOCS_INLINE_LIMIT

    out: dict = {}
    # Campos de identidade (REST ou web, normalize para snake_case)
    out["id_procedimento"] = merged.get("IdProcedimento") or merged.get("id_procedimento", "")
    out["protocolo"] = merged.get("ProtocoloProcedimentoFormatado") or merged.get("protocolo", "")
    out["tipo"] = merged.get("NomeTipoProcedimento") or merged.get("tipo", "")
    out["especificacao"] = merged.get("especificacao", "")
    out["nivel_acesso"] = merged.get("nivelAcesso", merged.get("nivel_acesso", ""))
    out["hipotese_legal"] = merged.get("hipoteseLegal", merged.get("hipotese_legal", ""))
    out["grau_sigilo"] = merged.get("grauSigilo", "")
    # Arrays de metadata
    out["assuntos"] = merged.get("assuntos", [])
    out["interessados"] = merged.get("interessados", [])
    out["observacoes"] = merged.get("observacoes", [])
    out["relacionados"] = merged.get("relacionados", [])
    # Documentos (possivelmente truncados)
    out["total_documentos"] = total_docs
    out["documentos"] = docs_shaped
    if has_more_docs:
        out["_documentos_truncados"] = (
            f"Exibindo {len(docs_shaped)} de {total_docs}. "
            "Use sei_arvore_processo para a lista completa."
        )
    # Avisos do composite (falha parcial de backend)
    if "_warnings" in merged:
        out["_warnings"] = merged["_warnings"]
    if "_aviso_acesso" in merged:
        out["_aviso_acesso"] = merged["_aviso_acesso"]
    return out


@mcp.tool(annotations=_READ)
async def sei_consultar_processo(protocolo_formatado: str, ctx: Context) -> str:
    """Consulta um processo SEI pelo número de protocolo formatado.

    Exemplo de protocolo: 50300.000123/2025-00

    Implementação híbrida: combina REST mod-wssei (campos estruturados) com
    scraper web (árvore de documentos), ambos em paralelo.

    Campos retornados:
    - id_procedimento, protocolo, tipo, especificacao
    - nivel_acesso, hipotese_legal, grau_sigilo
    - assuntos[], interessados[], observacoes[], relacionados[]
    - documentos[] (primeiros 30; use sei_arvore_processo para a lista completa)
    - total_documentos

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

    return _json(_shape_consultar_processo(merged))


@mcp.tool(annotations=_READ)
async def sei_arvore_processo(
    protocolo_formatado: str,
    ctx: Context | None = None,
) -> str:
    """Mostra a árvore completa de documentos de um processo SEI.

    Implementação via scraper web (~10× mais rápido que REST: ~1 s vs ~12 s).
    Parseia arvore_montar.php para extrair id, tipo, sigla da unidade geradora
    e número SEI de cada documento.

    Aceita o protocolo formatado (ex: 50300.000123/2025-00).

    Para ler o conteúdo de um documento, use sei_ler_documento com o id.
    """
    backend = await _backend(ctx)
    if ctx:
        await ctx.report_progress(0, 100, "Buscando árvore do processo…")
    result = await backend.arvore_processo(protocolo_formatado)
    if ctx:
        await ctx.report_progress(100, 100)
    return _json(result)


@mcp.tool(annotations=_READ)
async def sei_listar_documentos(
    protocolo_formatado: str,
    ctx: Context | None = None,
) -> str:
    """Lista todos os documentos de um processo SEI.

    Implementação via scraper web (~10× mais rápido que REST).
    Aceita o protocolo formatado (ex: 50300.000123/2025-00).

    Cada documento tem: id, nome_composto, tipo_documento, sigla_unidade,
    numero_sei. Para ler o conteúdo, use sei_ler_documento com o id.
    """
    backend = await _backend(ctx)
    result = await backend.listar_documentos(protocolo_formatado)
    return _json(result)


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
) -> str:
    """Lista o histórico de atividades/andamentos de um processo.

    Implementação via scraper web (procedimento_consultar_historico.php).
    Retorna todas as ações registradas (tramitações, assinaturas, edições, etc.)
    com data/hora, unidade, usuário e descrição.

    Aceita protocolo formatado (ex: 50300.000123/2025-00).
    """
    backend = await _backend(ctx)
    result = await backend.listar_atividades(processo)
    return _json(result)


@mcp.tool(annotations=_READ)
async def sei_listar_processos(
    pagina: int = 0,
    apenas_meus: str = "",
    tipo: str = "",
    filtro: str = "",
    ctx: Context | None = None,
) -> str:
    """Lista processos da caixa da unidade atual no SEI (Controle de Processos).

    Implementação via scraper do frontend web (~20× mais rápida que a REST API).
    O SEI pagina em até 500 processos por página; use `pagina` para navegar.

    Parâmetros:
    - pagina: número da página (0=primeira, 1=segunda, etc.)
    - apenas_meus: "S" para apenas processos atribuídos ao usuário logado
      (filtro server-side via hdnMeusProcessos=M)
    - tipo: substring (case-insensitive) para filtrar pelo nome do tipo processual
      (filtro client-side, sobre a coluna "Tipo")
    - filtro: substring (case-insensitive) aplicada a qualquer campo do processo
      (protocolo, tipo, especificação, interessados — filtro client-side)

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
    backend = await _backend(ctx)
    result = await backend.listar_processos(
        pagina=pagina,
        apenas_meus=apenas_meus,
        tipo=tipo,
        filtro=filtro,
    )
    return _json(result)


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
    nivel_acesso: str = "0",
    hipotese_legal: str = "",
    ctx: Context | None = None,
) -> str:
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
    return _json(result)


@mcp.tool(annotations=_WRITE)
async def sei_alterar_processo(
    processo: str,
    especificacao: str = "",
    nivel_acesso: str = "",
    hipotese_legal: str = "",
    observacao: str = "",
    ctx: Context | None = None,
) -> str:
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
    return _json(result)


# sei_enviar_processo permanece em server.py: a resolução sigla→id da unidade de
# destino ainda é orquestração de camada de tool (o backend REST não a faz).


@mcp.tool(annotations=_IDEM)
async def sei_concluir_processo(numero_processo: str, ctx: Context | None = None) -> str:
    """Conclui um processo na unidade atual do SEI.

    O processo é removido da caixa da unidade mas permanece acessível.
    Use sei_reabrir_processo para reverter.
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
