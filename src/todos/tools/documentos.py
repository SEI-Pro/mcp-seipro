"""Tools do domínio de documentos do SEI.

Reúne leitura (interno HTML / externo PDF, com gates de acesso restrito),
criação e alteração de documentos internos e externos, edição de seções,
referências dinâmicas, estilos CSS e consultas auxiliares (assuntos, blocos).

As tools roteiam pelo backend composto (`_backend`) — REST-first com fallback
web — e o gate de acesso restrito (`_aplicar_gate_documento`) consulta os
metadados pelo mesmo backend. Os helpers de formatação (`_formatar_doc_externo`,
`_formatar_doc_interno`, `_ler_documento_via_backend`) são usados apenas por
estas tools e ficam aqui. A resolução número SEI → id interno permanece via
pesquisa Solr (REST-only), por ser uma capacidade inerente ao mod-wssei.

Sem `from __future__ import annotations`: o FastMCP introspecta os type hints em
tempo de execução para montar o schema de cada tool, então as anotações precisam
ser objetos reais (não strings adiadas).
"""

import base64
import html as html_module
from typing import Literal

import httpx
from fastmcp import Context

from todos import access_control
from todos.backends import NovoDocumentoExterno, NovoDocumentoInterno
from todos.backends.base import SEIBackend
from todos.exceptions import SEIConnectionError, SEIError
from todos.html_utils import (
    html_to_markdown,
    html_to_text,
    pdf_to_markdown,
    pdf_to_text,
    sanitize_iso8859,
)
from todos.mcp_app import (
    _IDEM,
    _READ,
    _WRITE,
    MAX_BINARY_SIZE,
    _aplicar_gate_documento,
    _backend,
    _error,
    _get_client,
    _has_rest,
    _http_mode,
    _json,
    _resolver_documento,
    mcp,
)
from todos.sei_styles import (
    SEI_STYLES,
    STYLE_SHORTCUTS,
    html_referencia_sei,
)


def _formatar_doc_externo(content: bytes, formato: str, disclaimer: dict | None) -> str:
    """Formata conteúdo de documento externo (PDF) com disclaimer opcional."""
    if len(content) > MAX_BINARY_SIZE:
        return _error(
            f"Documento muito grande ({len(content)} bytes). "
            "Use sei_baixar_anexo para obter o base64."
        )
    if content[:4] != b"%PDF":
        return _error(
            "Documento externo não é PDF. Use sei_baixar_anexo para obter o arquivo em base64."
        )
    if formato == "markdown":
        resultado = pdf_to_markdown(content)
        if disclaimer:
            resultado = access_control.prefixar_markdown(disclaimer, resultado)
        return resultado
    resultado = pdf_to_text(content)
    if disclaimer:
        resultado = access_control.prefixar_texto(disclaimer, resultado)
    return resultado


def _formatar_doc_interno(raw: str, formato: str, disclaimer: dict | None) -> str:
    """Formata conteúdo de documento interno (HTML) com disclaimer opcional."""
    if formato == "markdown":
        resultado = html_to_markdown(raw)
        if disclaimer:
            resultado = access_control.prefixar_markdown(disclaimer, resultado)
        return resultado
    if formato == "texto":
        resultado = html_to_text(raw)
        if disclaimer:
            resultado = access_control.prefixar_texto(disclaimer, resultado)
        return resultado
    if disclaimer:
        return access_control.envelopar_html(disclaimer, raw)
    return raw


async def _ler_documento_via_backend(
    ctx: Context | None,
    backend: SEIBackend,
    id_documento: str,
    tipo_documento: str,
    formato: str,
    processo: str | None,
    *,
    confirmou: bool,
) -> str:
    """Aplica o gate e lê o conteúdo de um documento pelo backend composto.

    `tipo_documento` já vem resolvido ("I"/"X") quando há REST; em modo web-only
    pode ser "auto" — nesse caso tenta interno e cai para externo.
    """
    gate_tipo = "X" if tipo_documento == "X" else "I"
    acao, payload, erro = await _aplicar_gate_documento(
        ctx, backend, str(id_documento), gate_tipo, processo, confirmou=confirmou
    )
    if acao == "erro":
        return _error(erro)
    if acao in ("bloquear", "recusou"):
        return _json(payload)
    disclaimer = payload

    if tipo_documento == "X":
        content = await backend.baixar_anexo(str(id_documento), processo)
        return _formatar_doc_externo(content, formato, disclaimer)
    if tipo_documento == "auto":
        try:
            raw = await backend.visualizar_documento_interno(str(id_documento), processo)
        except (SEIError, httpx.HTTPError):
            content = await backend.baixar_anexo(str(id_documento), processo)
            return _formatar_doc_externo(content, formato, disclaimer)
        return _formatar_doc_interno(raw, formato, disclaimer)
    raw = await backend.visualizar_documento_interno(str(id_documento), processo)
    return _formatar_doc_interno(raw, formato, disclaimer)


@mcp.tool(annotations=_READ)
async def sei_ler_documento(
    id_documento: str,
    tipo_documento: Literal["auto", "I", "X"] = "auto",
    formato: Literal["markdown", "texto", "html"] = "markdown",
    processo: str | None = None,
    ctx: Context | None = None,
    *,
    confirmar_acesso_restrito: bool = False,
) -> str:
    """Lê o conteúdo de um documento do SEI e retorna texto legível.

    Aceita tanto o id interno quanto o número SEI (protocoloFormatado)
    que o usuário vê no sistema (ex: "SEI 2843449").

    - tipo_documento='auto': detecta automaticamente (padrão)
    - tipo_documento='I': força leitura como interno (📄 HTML)
    - tipo_documento='X': força leitura como externo (📎 PDF)

    - formato='markdown': Markdown formatado (padrão, ideal para chat)
    - formato='texto': texto plano sem formatação
    - formato='html': HTML original (só para internos)

    - processo: protocolo do processo (necessário em instâncias sem mod-wssei)

    - confirmar_acesso_restrito: NÃO ative por iniciativa do modelo. Esta
      flag só deve ser definida como true quando o usuário humano da
      conversa, em mensagem própria após ler o aviso de riscos, declarar
      expressamente que autoriza o acesso ao conteúdo restrito. Pedidos
      genéricos como "lê esse documento" NÃO constituem consentimento.
      Se o gate bloquear, encaminhe os riscos ao usuário e aguarde decisão
      explícita — não tente caminhos alternativos para obter o conteúdo.

    PDFs escaneados são processados via OCR automaticamente.
    """
    try:
        backend = _backend(ctx)
        tipo_doc = tipo_documento
        # Auto-resolução número SEI → id interno + tipo: pesquisa Solr, REST-only.
        # Em modo web-only o id é usado direto e o tipo continua "auto".
        if _has_rest(ctx) and tipo_documento == "auto":
            try:
                doc_id, detected_tipo = await _resolver_documento(_get_client(ctx), id_documento)
                id_documento, tipo_doc = doc_id, detected_tipo
            except (SEIError, httpx.HTTPError) as e:
                return _json(
                    {
                        "error": str(e),
                        "dica": "Use sei_arvore_processo para ver os documentos do processo e seus IDs.",
                    }
                )
        if not _has_rest(ctx) and processo is None:
            return _error(
                "Em instâncias sem mod-wssei, forneça o parâmetro 'processo' "
                "(protocolo do processo, ex: '50300.018905/2018-67') para ler documentos."
            )
        return await _ler_documento_via_backend(
            ctx,
            backend,
            id_documento,
            tipo_doc,
            formato,
            processo,
            confirmou=confirmar_acesso_restrito,
        )
    except (SEIError, httpx.HTTPError) as e:
        msg = str(e)
        if "não autorizado" in msg.lower() or "nao autorizado" in msg.lower():
            return _json(
                {
                    "error": msg,
                    "dica": "Acesso negado. Troque para a unidade geradora com sei_trocar_unidade.",
                }
            )
        return _error(msg)


def _envelopar_anexo(content: bytes, disclaimer: dict | None) -> str:
    """Valida tamanho e devolve o envelope base64 do anexo (ou erro)."""
    if len(content) > MAX_BINARY_SIZE:
        return _error(
            f"Documento muito grande ({len(content)} bytes, limite {MAX_BINARY_SIZE}). "
            "Baixe manualmente pelo SEI."
        )
    resposta: dict = {
        "base64": base64.b64encode(content).decode(),
        "size_bytes": len(content),
    }
    if disclaimer:
        resposta["aviso_acesso"] = disclaimer
    return _json(resposta)


@mcp.tool(annotations=_READ)
async def sei_baixar_anexo(
    id_documento: str,
    processo: str | None = None,
    ctx: Context | None = None,
    *,
    confirmar_acesso_restrito: bool = False,
) -> str:
    """Baixa um documento externo (anexo) do SEI em base64.

    Aceita tanto o id interno (ex: "3149544") quanto o número SEI /
    protocoloFormatado (ex: "2867926") — auto-resolve via pesquisa Solr.

    - processo: protocolo do processo (necessário em instâncias sem mod-wssei)

    Use para documentos com tipoDocumento='X' (📎).
    Para PDFs com texto, prefira sei_ler_documento(tipo_documento='X')
    que já extrai o texto legível.

    Retorna base64 + tamanho. Limite: 10 MB.

    confirmar_acesso_restrito: NÃO ative por iniciativa do modelo. Esta
    flag só deve ser definida como true quando o usuário humano da conversa,
    em mensagem própria após ler o aviso de riscos, declarar expressamente
    que autoriza o acesso. Se o gate bloquear, encaminhe os riscos ao
    usuário e aguarde decisão explícita — não tente caminhos alternativos.
    """
    try:
        backend = _backend(ctx)
        # Auto-resolução número SEI → id interno (Solr, REST-only).
        if _has_rest(ctx):
            try:
                doc_id, _ = await _resolver_documento(_get_client(ctx), id_documento)
                id_documento = doc_id
            except (SEIError, httpx.HTTPError) as e:
                return _json(
                    {
                        "error": str(e),
                        "dica": "Use sei_arvore_processo ou sei_buscar_documento para "
                        "encontrar o id correto do documento.",
                    }
                )
        elif processo is None:
            return _error(
                "Em instâncias sem mod-wssei, forneça o parâmetro 'processo' para baixar anexos."
            )

        acao, payload, erro = await _aplicar_gate_documento(
            ctx, backend, str(id_documento), "X", processo, confirmou=confirmar_acesso_restrito
        )
        if acao == "erro":
            return _error(erro)
        if acao in ("bloquear", "recusou"):
            return _json(payload)

        content = await backend.baixar_anexo(str(id_documento), processo)
        return _envelopar_anexo(content, payload)
    except httpx.RequestError as e:
        msg = f"SEI inacessível: {e}"
        raise SEIConnectionError(msg) from e


@mcp.tool(annotations=_WRITE)
async def sei_criar_documento(
    processo: str,
    id_serie: str = "",
    descricao: str = "",
    nivel_acesso: str = "0",
    hipotese_legal: str = "",
    id_unidade: str = "",
    ctx: Context | None = None,
) -> str:
    """Cria um novo documento interno (nativo) em um processo SEI.

    Parâmetros:
    - processo: protocolo formatado (ex: 50300.018905/2018-67) ou IdProcedimento
    - id_serie: ID do tipo de documento (use sei_pesquisar_tipos_documento).
      Deixe vazio para ver os tipos disponíveis via web.
    - descricao: descrição/título do documento
    - nivel_acesso: 0=público, 1=restrito, 2=sigiloso
    - hipotese_legal: ID da hipótese legal (obrigatório se restrito/sigiloso)
    - id_unidade: ID da unidade geradora (apenas REST, opcional)

    O documento é criado vazio. Use sei_listar_secoes e sei_editar_secao
    para inserir conteúdo.
    """
    # id_serie é obrigatório no caminho REST; no web vazio retorna os tipos.
    if _has_rest(ctx) and not id_serie:
        return _error(
            "id_serie é obrigatório no modo REST. "
            "Use sei_pesquisar_tipos_documento para listar os tipos disponíveis."
        )
    dados = NovoDocumentoInterno(
        id_serie=id_serie,
        descricao=descricao,
        nivel_acesso=nivel_acesso,
        hipotese_legal=hipotese_legal,
        id_unidade=id_unidade,
    )
    result = await _backend(ctx).criar_documento_interno(processo, dados)
    return _json(result)


@mcp.tool(annotations=_READ)
async def sei_listar_secoes(id_documento: str, ctx: Context | None = None) -> str:
    """Lista as seções editáveis de um documento interno SEI.

    Retorna as seções com seus IDs, conteúdo atual (HTML),
    e a versão do documento (campo ultimaVersaoDocumento),
    necessária para usar sei_editar_secao.
    """
    result = await _backend(ctx).listar_secoes(id_documento)
    return _json(result)


@mcp.tool(annotations=_READ)
async def sei_gerar_referencia(
    numero_sei: str,
    id_documento: str = "",
    ctx: Context | None = None,
) -> str:
    """Gera o HTML de referência (hiperlink dinâmico) para um documento SEI.

    Dado um número SEI (ex: 2599818), resolve o id interno e retorna
    o snippet HTML pronto para inserir no conteúdo de um documento.

    O SEI renderiza isso como link clicável na interface web.
    Use ao citar documentos SEI no texto de Despachos, Notas Técnicas, etc.

    Exemplo: "SEI nº <resultado>" vira link clicável para o documento.

    Se o documento pertence a outra unidade ou foi criado recentemente
    (Solr pode não ter indexado), informe id_documento diretamente.
    O id_documento é o número interno do documento (visível na URL do SEI).
    """
    try:
        if id_documento:
            doc_id = id_documento.strip()
        else:
            client = _get_client(ctx)
            doc_id, _ = await _resolver_documento(client, numero_sei)
        snippet = html_referencia_sei(doc_id, numero_sei)
        return _json(
            {
                "numero_sei": numero_sei,
                "id_documento": doc_id,
                "html": snippet,
                "uso": f"...SEI n&ordm; {snippet}...",
            }
        )
    except httpx.RequestError as e:
        msg = f"SEI inacessível: {e}"
        raise SEIConnectionError(msg) from e


@mcp.tool(annotations=_READ)
async def sei_estilos(categoria: str = "") -> str:
    """Lista os estilos CSS disponíveis para formatação de documentos no SEI.

    O SEI usa classes CSS padronizadas em todos os documentos governamentais.
    Use esta tool para descobrir a classe correta para cada tipo de parágrafo.

    Categorias: "texto", "titulo", "lista", "tabela", "destaque", "todos"
    Sem parâmetro: retorna os atalhos rápidos (intenção → classe).

    CONVENÇÃO para documentos (Despachos, Notas Técnicas, etc.):
    - Corpo/mérito do texto: usar Paragrafo_Numerado_Nivel1 (autonumera 1. 2. 3.)
    - Endereçamento (À SFC...): usar Texto_Alinhado_Esquerda
    - Assunto: usar Texto_Justificado com <strong> para o título
    - Fecho (Atenciosamente): usar Texto_Justificado_Recuo_Primeira_Linha
    - Nome do signatário: usar Texto_Centralizado_Maiusculas
    - Cargo: usar Texto_Centralizado
    """
    try:
        if not categoria or categoria == "atalhos":
            return _json(
                {
                    "atalhos": STYLE_SHORTCUTS,
                    "dica": "Use sei_estilos('todos') para ver todos os estilos com exemplos.",
                }
            )

        if categoria == "todos":
            return _json(SEI_STYLES)

        filtros = {
            "texto": ["Texto_"],
            "titulo": [
                "Texto_Centralizado_Maiusculas",
                "Texto_Fundo_Cinza",
                "Texto_Espaco_Duplo",
            ],
            "lista": ["Paragrafo_Numerado", "Item_Nivel", "Item_Alinea", "Item_Inciso"],
            "tabela": ["Tabela_"],
            "destaque": ["Citacao", "Tachado", "Texto_Fundo_Cinza", "Texto_Mono"],
        }

        prefixos = filtros.get(categoria, [])
        if not prefixos:
            return _json(
                {
                    "error": f"Categoria '{categoria}' não encontrada",
                    "categorias": [*filtros.keys(), "todos", "atalhos"],
                }
            )

        resultado = {
            nome: info
            for nome, info in SEI_STYLES.items()
            if any(nome.startswith(p) for p in prefixos)
        }

        return _json(resultado)
    except httpx.RequestError as e:
        msg = f"SEI inacessível: {e}"
        raise SEIConnectionError(msg) from e


@mcp.tool(annotations=_IDEM)
async def sei_editar_secao(
    id_documento: str,
    secoes: list[dict],
    versao: str = "",
    ctx: Context | None = None,
) -> str:
    """Altera o conteúdo de seções editáveis de um documento interno SEI.

    Parâmetros:
    - id_documento: ID do documento
    - secoes: lista de seções a alterar, cada uma com:
        - idSecaoModelo: ID do modelo da seção (obtido via sei_listar_secoes)
        - conteudo: novo conteúdo HTML da seção
      (não é necessário incluir seções somenteLeitura — são preenchidas
       automaticamente com o conteúdo original)
    - versao: versão do documento (se omitida, obtida automaticamente)

    O conteúdo deve ser HTML com as classes CSS do SEI (ex: Texto_Justificado).
    Caracteres fora do ISO-8859-1 são convertidos automaticamente.

    IMPORTANTE: O SEI exige que TODAS as seções sejam enviadas. Esta tool
    faz isso automaticamente — basta informar as seções que deseja alterar.
    """
    backend = _backend(ctx)

    # Buscar todas as seções atuais do documento
    secoes_data = await backend.listar_secoes(id_documento)
    secoes_atuais = secoes_data.get("secoes", [])
    if not versao:
        versao = str(secoes_data.get("ultimaVersaoDocumento", "1"))

    # Indexar seções novas por idSecaoModelo
    alteracoes = {}
    for s in secoes:
        modelo = s.get("idSecaoModelo", "")
        if modelo:
            alteracoes[modelo] = s.get("conteudo", "")

    # Montar payload completo com TODAS as seções
    secoes_enviar = []
    for s in secoes_atuais:
        if not isinstance(s, dict):
            continue
        sid = s.get("id") or s.get("IdSecaoDocumento")
        modelo = s.get("idSecaoModelo") or s.get("IdSecaoModelo")
        if not sid or not modelo:
            continue

        if str(modelo) in alteracoes:
            # Seção alterada pelo usuário
            conteudo = alteracoes[str(modelo)]
        else:
            # Seção original — fazer unescape do HTML-escaped
            conteudo = html_module.unescape(s.get("conteudo", "") or "")

        secoes_enviar.append(
            {
                "id": str(sid),
                "idSecaoModelo": str(modelo),
                "conteudo": sanitize_iso8859(conteudo),
            }
        )

    result = await backend.alterar_secoes(id_documento, secoes_enviar, versao)
    return _json(result)


@mcp.tool(annotations=_WRITE)
async def sei_criar_documento_externo(
    processo: str,
    id_serie: str,
    arquivo_path: str,
    descricao: str = "",
    nivel_acesso: str = "0",
    ctx: Context | None = None,
) -> str:
    """Cria um documento externo (upload de arquivo) em um processo SEI.

    - processo: protocolo formatado ou IdProcedimento
    - id_serie: tipo do documento (use sei_pesquisar_tipos_documento)
    - arquivo_path: caminho local do arquivo (PDF, imagem, etc.)
    - descricao: descrição do documento
    - nivel_acesso: 0=público (padrão), 1=restrito, 2=sigiloso
    """
    dados = NovoDocumentoExterno(
        id_serie=id_serie,
        arquivo_path=arquivo_path,
        descricao=descricao,
        nivel_acesso=nivel_acesso,
    )
    result = await _backend(ctx).criar_documento_externo(processo, dados)
    return _json(result)


async def _reconsultar_documento_externo(
    ctx: Context | None, backend: SEIBackend, id_documento: str
) -> tuple[dict | None, str]:
    """Tenta recuperar um 'não autorizado' resolvendo número SEI → id interno.

    A resolução número SEI → id é uma pesquisa Solr (REST-only); a reconsulta
    dos metadados volta a passar pelo backend composto. Retorna
    `(result, id_resolvido)` em caso de sucesso; `(None, id_documento)` quando o
    id não pôde ser resolvido para um id interno diferente.
    """
    if not _has_rest(ctx):
        return None, id_documento
    try:
        doc_id, _ = await _resolver_documento(_get_client(ctx), id_documento)
        if doc_id == id_documento:
            return None, id_documento
        result = await backend.consultar_documento_externo(doc_id, None)
    except (SEIError, httpx.HTTPError):
        return None, id_documento
    return result, doc_id


@mcp.tool(annotations=_READ)
async def sei_consultar_documento_externo(
    id_documento: str,
    processo: str | None = None,
    ctx: Context | None = None,
) -> str:
    """Consulta metadados de um documento externo pelo ID.

    Aceita tanto o id interno (ex: "3149544") quanto o número SEI /
    protocoloFormatado (ex: "2867926") — auto-resolve via pesquisa Solr
    quando necessário.

    - processo: protocolo do processo (necessário em instâncias sem mod-wssei)

    Retorna informações como tipo, data, nível de acesso, etc.
    Para baixar o conteúdo use sei_baixar_anexo ou sei_ler_documento.
    Disponível desde mod-wssei 2.0.0 (SEI 4.0.x).

    Quando o documento é restrito ou sigiloso (nivelAcesso 1 ou 2), a
    resposta inclui o campo `_aviso_acesso` — aviso INFORMATIVO de
    privacidade, NÃO erro de permissão. Os metadados foram retornados
    normalmente; não tente trocar de unidade ou rotas alternativas.
    Se falhar com erro inesperado, use sei_versao para verificar a versão.
    """
    try:
        backend = _backend(ctx)
        try:
            result = await backend.consultar_documento_externo(id_documento, processo)
        except (SEIError, httpx.HTTPError) as primeira:
            msg = str(primeira)
            low = msg.lower()
            if (
                "não autorizado" not in low
                and "nao autorizado" not in low
                and "acesso negado" not in low
            ):
                raise
            # Se não autorizado, pode ser id errado (passou número SEI). Tenta resolver.
            result, id_documento = await _reconsultar_documento_externo(ctx, backend, id_documento)
            if result is None:
                return _json(
                    {
                        "error": msg,
                        "dica": (
                            "SEI retornou 'não autorizado' para o id "
                            f"{id_documento!r}. Verifique se você passou o id "
                            "INTERNO do documento (ex.: 3149544) e não o número "
                            "SEI / protocoloFormatado (ex.: 2867926). Use "
                            "sei_buscar_documento para resolver número SEI → id."
                        ),
                    }
                )

        nivel, hipotese = access_control.extrair_nivel(result)
        if nivel is None:
            nivel = access_control.extrair_nivel_web(result)
        if access_control.precisa_disclaimer(nivel):
            result["_aviso_acesso"] = access_control.construir_disclaimer_acompanhante(
                nivel,
                hipotese,
                alvo={
                    "tipo": "documento",
                    "id": str(id_documento),
                    "tipo_documento": "X",
                },
            )
        return _json(result)
    except httpx.RequestError as e:
        msg = f"SEI inacessível: {e}"
        raise SEIConnectionError(msg) from e


@mcp.tool(annotations=_IDEM)
async def sei_alterar_documento_interno(
    id_documento: str,
    descricao: str = "",
    nivel_acesso: str = "",
    hipotese_legal: str = "",
    ctx: Context | None = None,
) -> str:
    """Altera metadados de um documento interno (não o conteúdo HTML).

    Para alterar o conteúdo, use sei_editar_secao.
    - id_documento: ID interno do documento
    - descricao: nova descrição
    - nivel_acesso: 0=público, 1=restrito, 2=sigiloso
    - hipotese_legal: ID da hipótese (obrigatório se restrito/sigiloso)

    Disponível desde mod-wssei 2.0.0 (SEI 4.0.x).
    Se falhar com erro inesperado, use sei_versao para verificar a versão instalada.
    """
    result = await _backend(ctx).alterar_documento_interno(
        id_documento=id_documento,
        descricao=descricao,
        nivel_acesso=nivel_acesso,
        hipotese_legal=hipotese_legal,
    )
    return _json(result)


@mcp.tool(annotations=_IDEM)
async def sei_alterar_documento_externo(
    id_documento: str,
    descricao: str = "",
    nivel_acesso: str = "",
    hipotese_legal: str = "",
    arquivo_path: str = "",
    ctx: Context | None = None,
) -> str:
    """Altera metadados de um documento externo (e opcionalmente substitui o arquivo).

    - id_documento: ID interno do documento
    - descricao: nova descrição
    - nivel_acesso: 0=público, 1=restrito, 2=sigiloso
    - hipotese_legal: ID da hipótese (obrigatório se restrito/sigiloso)
    - arquivo_path: caminho local de novo arquivo para substituir (opcional)

    Disponível desde mod-wssei 2.0.0 (SEI 4.0.x).
    Se falhar com erro inesperado, use sei_versao para verificar a versão instalada.
    """
    result = await _backend(ctx).alterar_documento_externo(
        id_documento=id_documento,
        descricao=descricao,
        nivel_acesso=nivel_acesso,
        hipotese_legal=hipotese_legal,
        arquivo_path=arquivo_path,
    )
    return _json(result)


@mcp.tool(annotations=_READ)
async def sei_sugestao_assuntos_documento(
    id_serie: str,
    ctx: Context | None = None,
) -> str:
    """Lista sugestões de assuntos para um tipo de documento (série).

    Use o id_serie obtido via sei_pesquisar_tipos_documento.
    Disponível desde mod-wssei 2.0.0 (SEI 4.0.x).
    Se falhar com erro inesperado, use sei_versao para verificar a versão instalada.
    """
    result = await _backend(ctx).sugestao_assuntos_documento(id_serie)
    return _json(result)


@mcp.tool(annotations=_READ)
async def sei_listar_blocos_documento(
    id_documento: str,
    ctx: Context | None = None,
) -> str:
    """Lista blocos de assinatura em que um documento está incluído.

    Disponível desde mod-wssei 2.0.0 (SEI 4.0.x).
    Se falhar com erro inesperado, use sei_versao para verificar a versão instalada.
    """
    result = await _backend(ctx).listar_blocos_documento(id_documento)
    return _json(result)


@mcp.tool(annotations=_WRITE)
async def sei_incluir_documento_externo(
    processo: str,
    arquivo_path: str = "",
    arquivo_base64: str = "",
    nome_arquivo: str = "",
    id_serie: str = "",
    data_elaboracao: str = "",
    nivel_acesso: str = "0",
    hipotese_legal: str = "",
    ctx: Context | None = None,
) -> str:
    """Inclui documento externo (PDF, imagem, etc.) em um processo SEI via web scraper.

    Implementação via scraper web — funciona em instâncias sem mod-wssei REST.

    Parâmetros:
    - processo: protocolo formatado (ex: 0020.008886/2026-49)
    - arquivo_path: caminho local do arquivo (apenas em modo stdio/local;
      ex: C:/Users/frank/Downloads/NF52.pdf)
    - arquivo_base64: conteúdo do arquivo em base64 (obrigatório em modo
      remoto/HTTP; alternativa a arquivo_path)
    - nome_arquivo: nome do arquivo com extensão (obrigatório com arquivo_base64;
      ex: NF52.pdf)
    - id_serie: ID do tipo de documento no SEI. Se vazio, retorna lista de tipos disponíveis.
      Para Nota Fiscal, use sei_pesquisar_tipos_documento para descobrir o id.
    - data_elaboracao: data de elaboração no formato dd/mm/aaaa (padrão: hoje)
    - nivel_acesso: 0=público (padrão), 1=restrito, 2=sigiloso
    - hipotese_legal: ID da hipótese legal (obrigatório se nivel_acesso=1 ou 2).
      Use sei_listar_hipoteses_legais para descobrir os IDs disponíveis.

    Se id_serie não for informado, retorna os tipos disponíveis para que você
    possa escolher e chamar novamente com o id correto.

    Nota: o processo deve estar aberto na caixa da unidade atual.
    Se o processo estiver concluído, use sei_reabrir_processo primeiro.
    """
    try:
        if arquivo_base64:
            if not nome_arquivo:
                return _error("nome_arquivo é obrigatório quando arquivo_base64 é usado.")
            try:
                base64.b64decode(arquivo_base64, validate=True)
            except ValueError:
                return _error("arquivo_base64 inválido (não é base64 válido).")
        elif arquivo_path:
            # Em modo remoto o caminho apontaria para o filesystem do SERVIDOR,
            # permitindo exfiltrar arquivos do host — exigir base64.
            if _http_mode:
                return _error(
                    "Em modo remoto use arquivo_base64 + nome_arquivo "
                    "(caminhos do servidor não são permitidos)."
                )
        elif id_serie:
            return _error("Informe arquivo_path (local) ou arquivo_base64 (remoto).")

        # O composite roteia web-first quando há base64 (upload nativo do scraper),
        # e REST-first quando só há caminho de arquivo.
        dados = NovoDocumentoExterno(
            id_serie=id_serie,
            arquivo_path=arquivo_path,
            arquivo_base64=arquivo_base64,
            nome_arquivo=nome_arquivo,
            data_elaboracao=data_elaboracao,
            nivel_acesso=nivel_acesso,
            hipotese_legal=hipotese_legal,
        )
        result = await _backend(ctx).criar_documento_externo(processo, dados)
        return _json(result)
    except httpx.RequestError as e:
        msg = f"SEI inacessível: {e}"
        raise SEIConnectionError(msg) from e
