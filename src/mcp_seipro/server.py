"""MCP Server genérico para o SEI (Sistema Eletrônico de Informações)."""

import asyncio
import base64
import json
import logging
import os
from contextlib import asynccontextmanager
from typing import Literal

from mcp.server.fastmcp import FastMCP, Context
from mcp.server.transport_security import TransportSecuritySettings

from mcp_seipro.sei_client import SEIClient
from mcp_seipro.sei_web_client import SEIWebClient
from mcp_seipro.html_utils import (
    html_to_text, html_to_markdown,
    pdf_to_text, pdf_to_markdown,
    sanitize_iso8859,
)
from mcp_seipro.sei_styles import (
    SEI_STYLES, STYLE_SHORTCUTS,
    html_referencia_sei, html_destinatario,
)

logger = logging.getLogger(__name__)

MAX_BINARY_SIZE = 10 * 1024 * 1024  # 10 MB

# Detecta modo HTTP (Railway injeta PORT)
_http_mode = bool(os.environ.get("PORT"))
_http_port = int(os.environ.get("PORT", 8000))


@asynccontextmanager
async def lifespan(server: FastMCP):
    if _http_mode:
        # Modo HTTP: clients criados por request com credenciais do token OAuth
        yield {"sei": None, "sei_web": None}
    else:
        # Modo stdio: clients com credenciais das env vars
        client = SEIClient()
        web_client = SEIWebClient()
        try:
            yield {"sei": client, "sei_web": web_client}
        finally:
            await client.close()
            await web_client.close()


def _get_client(ctx: Context) -> SEIClient:
    """Obtém o SEIClient REST, criando sob demanda em modo HTTP."""
    client = ctx.request_context.lifespan_context.get("sei")
    if client is not None:
        return client

    # Modo HTTP: extrai credenciais do token OAuth
    if _http_mode:
        from mcp.server.auth.middleware.auth_context import get_access_token
        from mcp_seipro.auth import get_sei_credentials_from_token

        access_token = get_access_token()
        if not access_token:
            raise ValueError("Autenticacao necessaria. Reconecte o MCP.")

        creds = get_sei_credentials_from_token(access_token.token)
        if not creds:
            raise ValueError("Token invalido ou expirado. Reconecte o MCP.")

        client = SEIClient(**creds)
        ctx.request_context.lifespan_context["sei"] = client
        return client

    raise ValueError("SEIClient nao configurado. Verifique as variaveis de ambiente.")


def _get_web_client(ctx: Context) -> SEIWebClient:
    """Obtém o SEIWebClient (scraper), criando sob demanda em modo HTTP.

    O scraper mantém estado de sessão (cookies + infra_hash) e por isso é
    instanciado uma vez por contexto, não por chamada.
    """
    client = ctx.request_context.lifespan_context.get("sei_web")
    if client is not None:
        return client

    if _http_mode:
        from mcp.server.auth.middleware.auth_context import get_access_token
        from mcp_seipro.auth import get_sei_credentials_from_token

        access_token = get_access_token()
        if not access_token:
            raise ValueError("Autenticacao necessaria. Reconecte o MCP.")
        creds = get_sei_credentials_from_token(access_token.token)
        if not creds:
            raise ValueError("Token invalido ou expirado. Reconecte o MCP.")
        client = SEIWebClient(**creds)
        ctx.request_context.lifespan_context["sei_web"] = client
        return client

    raise ValueError("SEIWebClient nao configurado.")


_http_kwargs = {}
if _http_mode:
    from pydantic import AnyHttpUrl
    from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions, RevocationOptions
    from mcp_seipro.auth import SEIProOAuthProvider

    _base_url = os.environ.get("BASE_URL", f"http://localhost:{_http_port}")
    _provider = SEIProOAuthProvider()

    _http_kwargs = {
        "host": "0.0.0.0",
        "port": _http_port,
        "stateless_http": True,
        "transport_security": TransportSecuritySettings(
            enable_dns_rebinding_protection=False,
        ),
        "auth": AuthSettings(
            issuer_url=AnyHttpUrl(_base_url),
            resource_server_url=AnyHttpUrl(f"{_base_url}/mcp"),
            client_registration_options=ClientRegistrationOptions(enabled=True),
            revocation_options=RevocationOptions(enabled=True),
        ),
        "auth_server_provider": _provider,
    }

mcp = FastMCP(
    "sei",
    **_http_kwargs,
    instructions=(
        "MCP Server para o SEI (Sistema Eletrônico de Informações). "
        "Permite gerenciar processos, documentos, tramitação e assinatura. "
        "ASSINATURA: as credenciais do usuário já estão configuradas no servidor. "
        "NUNCA peça login ou senha ao usuário para assinar. Basta chamar "
        "sei_assinar_documento com o id do documento e o cargo. Se não souber "
        "o cargo, chame sem cargo para obter a lista e pergunte ao usuário. "
        "Fluxo típico: sei_trocar_unidade → sei_listar_processos → "
        "sei_consultar_processo (obter IdProcedimento) → sei_arvore_processo → "
        "sei_ler_documento. Para criar docs: sei_pesquisar_tipos_documento → "
        "sei_criar_documento → sei_listar_secoes → sei_editar_secao. "
        "Ao gerar HTML para documentos, use as classes CSS padronizadas do SEI. "
        "DESPACHOS: Texto_Alinhado_Esquerda com âncora SEI no destinatário "
        "(<span class='ancoraSei interessadoSeiPro' data-id='ID_UNIDADE'>SIGLA - Nome</span>) "
        "para vincular à unidade na tramitação. "
        "Texto_Justificado+<strong> (assunto), "
        "Paragrafo_Numerado_Nivel1 (corpo, autonumera 1. 2. 3.), "
        "Texto_Justificado_Recuo_Primeira_Linha (fecho), "
        "Texto_Centralizado_Maiusculas (signatário), Texto_Centralizado (cargo). "
        "NOTAS TÉCNICAS e PARECERES: Item_Nivel1/2/3/4 para títulos de seção "
        "(equivalem a H1/H2/H3/H4 ou #/##/###/####, autonumeram 1. 1.1. 1.1.1.), "
        "Paragrafo_Numerado_Nivel1 para parágrafos do corpo, "
        "Item_Alinea_Letra para alíneas (autonumera a, b, c — NUNCA escrever a) b) no texto), "
        "Item_Inciso_Romano para incisos (autonumera I, II, III — NUNCA escrever I - II - no texto). "
        "REGRA: toda numeração/enumeração deve usar as classes CSS, nunca texto manual. "
        "Use sei_estilos para consultar todos os estilos disponíveis. "
        "Ao citar documentos SEI no texto, use sei_gerar_referencia para "
        "gerar hiperlinks dinâmicos (<a class='ancoraSei'>) que o SEI "
        "renderiza como links clicáveis na interface web. "
        "IMPORTANTE: Quando o usuário mencionar 'SEI XXXX', 'SEI nº XXXX' ou "
        "'número SEI XXXX', use sei_ler_documento diretamente com o número — "
        "a tool resolve automaticamente o id interno via pesquisa Solr. "
        "Para buscar sem ler, use sei_buscar_documento. "
        "Quando o usuário pedir para ver documentos/árvore de um processo, "
        "use sei_arvore_processo e apresente como tabela markdown. Use emojis "
        "para tipo de documento: 📄 = Interno (HTML), 📎 = Externo (PDF). "
        "Colunas: #, 📄/📎, Tipo do Documento, Protocolo, Unidade, Tamanho, "
        "✍️ Assinado, 🚫 Cancelado, 👁 Visualizar, 🔒 Bloqueado. "
        "Use ✅ para sim e · para não. Se houver múltiplos volumes "
        "(campo total_volumes > 1), separe visualmente por volume. "
        "VERSÃO: Todos os endpoints funcionam com mod-wssei 2.0.0+ (SEI 4.0.x+), "
        "exceto sei_listar_relacionamentos que requer mod-wssei 3.0.2+ (SEI 5.0.x). "
        "Compatibilidade: SEI 4.0.x→mod-wssei 2.0.x | SEI 4.1.1→2.2.0 | SEI 5.0.x→3.0.x. "
        "Se um endpoint falhar com erro inesperado (404, método não encontrado), "
        "use sei_versao para verificar a versão e informe ao usuário qual versão "
        "do SEI/mod-wssei é necessária. Pergunte a versão do SEI ao usuário caso precise."
    ),
    lifespan=lifespan,
)


async def _resolver_processo(client: SEIClient, referencia: str) -> str:
    """Resolve uma referência de processo para o IdProcedimento.

    Aceita:
    - IdProcedimento numérico (ex: "683589") — usa direto
    - Protocolo formatado (ex: "50300.018905/2018-67") — consulta na API

    Retorna o IdProcedimento (str).
    """
    referencia = referencia.strip()
    # Se contém ponto ou barra, é protocolo formatado
    if "." in referencia or "/" in referencia:
        proc = await client.consultar_processo(referencia)
        return str(proc.get("IdProcedimento", ""))
    return referencia


def _json(data) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)


def _error(msg: str) -> str:
    return json.dumps({"error": msg}, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Tools de unidade e usuário
# ---------------------------------------------------------------------------


@mcp.tool()
async def sei_listar_unidades(ctx: Context) -> str:
    """Lista as unidades às quais o usuário autenticado tem acesso no SEI.

    Retorna id, sigla e nome de cada unidade. Use o id para trocar
    de unidade com sei_trocar_unidade.
    """
    try:
        client = _get_client(ctx)
        result = await client.listar_unidades_usuario()
        return _json(result)
    except Exception as e:
        return _error(str(e))


@mcp.tool()
async def sei_trocar_unidade(id_unidade: str, ctx: Context) -> str:
    """Troca a unidade ativa do usuário no SEI.

    Após trocar, operações como sei_listar_processos mostrarão
    a caixa da nova unidade. Use sei_listar_unidades para ver
    as unidades disponíveis e seus IDs.
    """
    try:
        client = _get_client(ctx)
        result = await client.trocar_unidade(id_unidade)
        return _json(result)
    except Exception as e:
        return _error(str(e))


@mcp.tool()
async def sei_pesquisar_unidades(
    filtro: str = "",
    limit: int = 50,
    pagina: int = 0,
    ctx: Context = None,
) -> str:
    """Pesquisa unidades disponíveis no SEI por nome ou sigla.

    Útil para encontrar o ID de uma unidade destino ao tramitar processos.
    Paginação: pagina=0 é a primeira página, pagina=1 a segunda, etc.
    """
    try:
        client = _get_client(ctx)
        result = await client.pesquisar_unidades(filtro=filtro, limit=limit, start=pagina)
        return _json(result)
    except Exception as e:
        return _error(str(e))


@mcp.tool()
async def sei_listar_usuarios(
    filtro: str = "",
    apenas_unidade: bool = True,
    ctx: Context = None,
) -> str:
    """Lista usuários no SEI, com filtro por nome ou sigla.

    - apenas_unidade=true (padrão): só usuários com permissão na unidade
      atual — ideal para atribuição de processos
    - apenas_unidade=false: todos os usuários do órgão

    Use o campo id_usuario retornado para sei_atribuir_processo.
    """
    try:
        client = _get_client(ctx)
        result = await client.listar_usuarios(filtro=filtro, apenas_unidade=apenas_unidade)
        return _json(result)
    except Exception as e:
        return _error(str(e))


# ---------------------------------------------------------------------------
# Tools de leitura
# ---------------------------------------------------------------------------


@mcp.tool()
async def sei_consultar_processo(protocolo_formatado: str, ctx: Context) -> str:
    """Consulta um processo SEI pelo número de protocolo formatado.

    Exemplo de protocolo: 50300.000123/2025-00

    Implementação **híbrida**: combina REST mod-wssei (campos estruturados)
    com scraper do frontend web (lista completa de documentos da árvore).
    As duas fontes rodam em paralelo via asyncio.gather.

    Campos da REST (`/processo/consultar` + `/processo/consultar/{id}`):
    - IdProcedimento, ProtocoloProcedimentoFormatado, NomeTipoProcedimento
    - especificacao, assuntos[], interessados[], observacoes[]
    - nivelAcesso, hipoteseLegal, grauSigilo

    Campos do scraper web (`procedimento_visualizar` / arvore_montar.php):
    - documentos[]: lista completa de documentos com id, label, tipo
    - relacionados[]: processos relacionados (cards na sidebar)

    Se o scraper web falhar (ex: processo não está na inbox da unidade atual),
    a tool ainda retorna os campos REST. Se a REST falhar, retorna pelo menos
    o que o scraper conseguiu extrair.
    """
    try:
        client = _get_client(ctx)
        web = _get_web_client(ctx)
        if web._inbox_url is None:
            try:
                await web.login()
            except Exception as e:
                logger.warning(f"web login falhou, seguindo só com REST: {e}")

        # roda REST completo e web em paralelo; suporta falha individual
        rest_task = asyncio.create_task(client.consultar_processo_completo(protocolo_formatado))
        web_task = asyncio.create_task(web.consultar_processo(protocolo_formatado))
        rest_result, web_result = await asyncio.gather(
            rest_task, web_task, return_exceptions=True
        )

        merged: dict = {}
        warnings: list[str] = []

        if isinstance(rest_result, Exception):
            warnings.append(f"REST falhou: {rest_result}")
        else:
            merged.update(rest_result)

        if isinstance(web_result, Exception):
            warnings.append(f"Web scraper falhou: {web_result}")
        else:
            # Web traz documentos[], relacionados[] e id_procedimento (snake_case).
            # Não sobrescreve campos da REST que tenham nomes parecidos —
            # REST é a fonte canônica para metadata; web complementa com docs.
            for k, v in web_result.items():
                if k not in merged:
                    merged[k] = v

        if not merged:
            return _error("Ambas as fontes (REST e Web) falharam: " + " | ".join(warnings))

        if warnings:
            merged["_warnings"] = warnings

        return _json(merged)
    except Exception as e:
        return _error(str(e))


@mcp.tool()
async def sei_arvore_processo(
    protocolo_formatado: str,
    ctx: Context = None,
) -> str:
    """Mostra a árvore completa de documentos de um processo SEI.

    Implementação via scraper web (~10× mais rápido que REST: ~1 s vs ~12 s).
    Parseia arvore_montar.php para extrair id, tipo, sigla da unidade geradora
    e número SEI de cada documento.

    Aceita o protocolo formatado (ex: 50300.000123/2025-00).

    Para ler o conteúdo de um documento, use sei_ler_documento com o id.
    """
    try:
        web = _get_web_client(ctx)
        if web._inbox_url is None:
            await web.login()
        result = await web.listar_documentos(protocolo_formatado)
        return _json(result)
    except Exception as e:
        return _error(str(e))


@mcp.tool()
async def sei_listar_documentos(
    protocolo_formatado: str,
    ctx: Context = None,
) -> str:
    """Lista todos os documentos de um processo SEI.

    Implementação via scraper web (~10× mais rápido que REST).
    Aceita o protocolo formatado (ex: 50300.000123/2025-00).

    Cada documento tem: id, nome_composto, tipo_documento, sigla_unidade,
    numero_sei. Para ler o conteúdo, use sei_ler_documento com o id.
    """
    try:
        web = _get_web_client(ctx)
        if web._inbox_url is None:
            await web.login()
        result = await web.listar_documentos(protocolo_formatado)
        return _json(result)
    except Exception as e:
        return _error(str(e))


@mcp.tool()
async def sei_buscar_documento(
    numero_sei: str,
    processo: str = "",
    ctx: Context = None,
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
    try:
        client = _get_client(ctx)
        numero_sei = numero_sei.strip()

        def _match(proto: str) -> bool:
            return proto == numero_sei or proto.lstrip("0") == numero_sei.lstrip("0")

        # Estratégia 1: processo conhecido → busca direto
        if processo:
            id_procedimento = await _resolver_processo(client, processo)
            docs = await client.listar_documentos(id_procedimento, limit=200)
            for d in docs:
                proto = d.get("atributos", {}).get("protocoloFormatado", "")
                if _match(proto):
                    return _json({
                        "encontrado": True,
                        "id_procedimento": id_procedimento,
                        "documento": d,
                    })
            return _json({
                "encontrado": False,
                "mensagem": f"SEI {numero_sei} não encontrado no processo {id_procedimento}",
            })

        # Estratégia 2: pesquisa textual (Solr) para achar o processo
        result = await client.pesquisar_processos(palavras_chave=numero_sei, limit=20)
        processos_candidatos = result.get("processos", [])

        for p in processos_candidatos:
            id_proc = str(p.get("idProcedimento", ""))
            if not id_proc:
                continue
            try:
                docs = await client.listar_documentos(id_proc, limit=200)
                for d in docs:
                    proto = d.get("atributos", {}).get("protocoloFormatado", "")
                    if _match(proto):
                        return _json({
                            "encontrado": True,
                            "processo": p.get("protocoloFormatadoProcedimento", ""),
                            "id_procedimento": id_proc,
                            "documento": d,
                        })
            except Exception:
                continue

        return _json({
            "encontrado": False,
            "processos_pesquisados": len(processos_candidatos),
            "mensagem": f"SEI {numero_sei} não encontrado via pesquisa textual",
            "dica": "A pesquisa Solr pode não indexar esse documento. "
                    "Informe o número do processo (id_procedimento) para busca direta, "
                    "ou use sei_arvore_processo com o protocolo do processo.",
        })
    except Exception as e:
        return _error(str(e))


async def _resolver_documento(client: SEIClient, referencia: str) -> tuple[str, str]:
    """Resolve uma referência de documento para (id_interno, tipo_documento).

    Aceita:
    - id interno numérico (ex: "3121831") — usa direto
    - número SEI / protocoloFormatado (ex: "2843449") — pesquisa via Solr

    Estratégia otimizada:
    1. Pesquisa Solr primeiro (encontra pelo protocoloFormatado na maioria dos casos)
    2. Se Solr não encontrar, tenta como id direto (interno → externo)

    Retorna (id_documento, tipo_documento) ou levanta exceção.
    """
    referencia = referencia.strip()

    # Estratégia 1: Pesquisa Solr (mais confiável, evita confusão id/proto)
    try:
        result = await client.pesquisar_processos(palavras_chave=referencia, limit=20)
        processos = result.get("processos", [])

        for p in processos:
            id_proc = str(p.get("idProcedimento", ""))
            if not id_proc:
                continue
            try:
                docs = await client.listar_documentos(id_proc, limit=200)
                for d in docs:
                    proto = d.get("atributos", {}).get("protocoloFormatado", "")
                    if proto == referencia or proto.lstrip("0") == referencia.lstrip("0"):
                        doc_id = str(d["id"])
                        tipo = d.get("atributos", {}).get("tipoDocumento", "I")
                        return doc_id, tipo
            except Exception:
                continue
    except Exception:
        pass

    # Estratégia 2: Tentar como id direto (para quando o usuário informa o id interno)
    # Só tenta se o Solr não encontrou nada — para evitar confusão
    # entre protocoloFormatado e id (são números diferentes no SEI)
    try:
        raw = await client.visualizar_documento_interno(referencia)
        # Validar que realmente retornou conteúdo (não erro mascarado)
        if raw and len(raw) > 10:
            return referencia, "I"
    except Exception as e:
        msg = str(e)
        # "não autorizado" pode significar que o id existe mas sem permissão
        # OU que o protocoloFormatado coincidiu com outro id — não confiável
        if "não autorizado" not in msg.lower() and "nao autorizado" not in msg.lower():
            pass  # Erro diferente, tentar externo

    # Não tentar como externo automaticamente — risco alto de confusão id/proto
    # O fallback para externo só deve ser usado com id_procedimento conhecido

    raise Exception(
        f"Documento '{referencia}' não encontrado via pesquisa. "
        "Se é um documento recém-criado, o Solr pode não ter indexado ainda. "
        "Use sei_arvore_processo com o protocolo do processo para encontrá-lo."
    )


@mcp.tool()
async def sei_ler_documento(
    id_documento: str,
    tipo_documento: Literal["auto", "I", "X"] = "auto",
    formato: Literal["markdown", "texto", "html"] = "markdown",
    ctx: Context = None,
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

    PDFs escaneados são processados via OCR automaticamente.
    """
    try:
        client = _get_client(ctx)

        # Resolver referência → id interno + tipo
        if tipo_documento == "auto":
            try:
                doc_id, detected_tipo = await _resolver_documento(client, id_documento)
                id_documento = doc_id
                tipo_documento = detected_tipo
            except Exception as e:
                return _json({
                    "error": str(e),
                    "dica": "Use sei_arvore_processo para ver os documentos "
                            "do processo e seus IDs.",
                })

        if tipo_documento == "X":
            content = await client.baixar_anexo(id_documento)
            if len(content) > MAX_BINARY_SIZE:
                return _error(
                    f"Documento muito grande ({len(content)} bytes). "
                    "Use sei_baixar_anexo para obter o base64."
                )
            if content[:4] != b"%PDF":
                return _error(
                    "Documento externo não é PDF. Use sei_baixar_anexo "
                    "para obter o arquivo em base64."
                )
            if formato == "markdown":
                return pdf_to_markdown(content)
            return pdf_to_text(content)

        # Documento interno (I)
        raw = await client.visualizar_documento_interno(id_documento)
        if formato == "markdown":
            return html_to_markdown(raw)
        if formato == "texto":
            return html_to_text(raw)
        return raw
    except Exception as e:
        msg = str(e)
        if "não autorizado" in msg.lower() or "nao autorizado" in msg.lower():
            return _json({
                "error": msg,
                "dica": "Acesso negado. Troque para a unidade geradora "
                        "com sei_trocar_unidade.",
            })
        return _error(msg)


@mcp.tool()
async def sei_baixar_anexo(id_documento: str, ctx: Context = None) -> str:
    """Baixa um documento externo (anexo) do SEI em base64.

    Use para documentos com tipoDocumento='X' (📎).
    Para PDFs com texto, prefira sei_ler_documento(tipo_documento='X')
    que já extrai o texto legível.

    Retorna base64 + tamanho. Limite: 10 MB.
    """
    try:
        client = _get_client(ctx)
        content = await client.baixar_anexo(id_documento)
        if len(content) > MAX_BINARY_SIZE:
            return _error(
                f"Documento muito grande ({len(content)} bytes, limite {MAX_BINARY_SIZE}). "
                "Baixe manualmente pelo SEI."
            )
        return _json({
            "base64": base64.b64encode(content).decode(),
            "size_bytes": len(content),
        })
    except Exception as e:
        return _error(str(e))


# ---------------------------------------------------------------------------
# Tools de escrita
# ---------------------------------------------------------------------------


@mcp.tool()
async def sei_criar_documento(
    processo: str,
    id_serie: str,
    descricao: str = "",
    nivel_acesso: str = "0",
    id_unidade: str = "",
    ctx: Context = None,
) -> str:
    """Cria um novo documento interno (nativo) em um processo SEI.

    Parâmetros:
    - processo: protocolo formatado (ex: 50300.018905/2018-67) ou IdProcedimento
    - id_serie: código do tipo de documento (use sei_pesquisar_tipos_documento)
    - descricao: descrição/título do documento
    - nivel_acesso: 0=público, 1=restrito, 2=sigiloso
    - id_unidade: ID da unidade geradora (opcional)

    O documento é criado vazio. Use sei_listar_secoes e sei_editar_secao
    para inserir conteúdo.
    """
    try:
        client = _get_client(ctx)
        id_procedimento = await _resolver_processo(client, processo)
        result = await client.criar_documento_interno(
            id_procedimento=id_procedimento,
            id_serie=id_serie,
            descricao=descricao,
            nivel_acesso=nivel_acesso,
            id_unidade=id_unidade,
        )
        return _json(result)
    except Exception as e:
        return _error(str(e))


@mcp.tool()
async def sei_listar_secoes(id_documento: str, ctx: Context = None) -> str:
    """Lista as seções editáveis de um documento interno SEI.

    Retorna as seções com seus IDs, conteúdo atual (HTML),
    e a versão do documento (campo ultimaVersaoDocumento),
    necessária para usar sei_editar_secao.
    """
    try:
        client = _get_client(ctx)
        result = await client.listar_secao_documento(id_documento)
        return _json(result)
    except Exception as e:
        return _error(str(e))


@mcp.tool()
async def sei_gerar_referencia(
    numero_sei: str,
    ctx: Context = None,
) -> str:
    """Gera o HTML de referência (hiperlink dinâmico) para um documento SEI.

    Dado um número SEI (ex: 2599818), resolve o id interno e retorna
    o snippet HTML pronto para inserir no conteúdo de um documento.

    O SEI renderiza isso como link clicável na interface web.
    Use ao citar documentos SEI no texto de Despachos, Notas Técnicas, etc.

    Exemplo: "SEI nº <resultado>" vira link clicável para o documento.
    """
    try:
        client = _get_client(ctx)
        doc_id, _ = await _resolver_documento(client, numero_sei)
        snippet = html_referencia_sei(doc_id, numero_sei)
        return _json({
            "numero_sei": numero_sei,
            "id_documento": doc_id,
            "html": snippet,
            "uso": f'...SEI n&ordm; {snippet}...',
        })
    except Exception as e:
        return _error(str(e))


@mcp.tool()
async def sei_estilos(categoria: str = "", ctx: Context = None) -> str:
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
            return _json({
                "atalhos": STYLE_SHORTCUTS,
                "dica": "Use sei_estilos('todos') para ver todos os estilos com exemplos.",
            })

        if categoria == "todos":
            return _json(SEI_STYLES)

        filtros = {
            "texto": ["Texto_"],
            "titulo": ["Texto_Centralizado_Maiusculas", "Texto_Fundo_Cinza", "Texto_Espaco_Duplo"],
            "lista": ["Paragrafo_Numerado", "Item_Nivel", "Item_Alinea", "Item_Inciso"],
            "tabela": ["Tabela_"],
            "destaque": ["Citacao", "Tachado", "Texto_Fundo_Cinza", "Texto_Mono"],
        }

        prefixos = filtros.get(categoria, [])
        if not prefixos:
            return _json({
                "error": f"Categoria '{categoria}' não encontrada",
                "categorias": list(filtros.keys()) + ["todos", "atalhos"],
            })

        resultado = {}
        for nome, info in SEI_STYLES.items():
            if any(nome.startswith(p) for p in prefixos):
                resultado[nome] = info

        return _json(resultado)
    except Exception as e:
        return _error(str(e))


@mcp.tool()
async def sei_editar_secao(
    id_documento: str,
    secoes: list[dict],
    versao: str = "",
    ctx: Context = None,
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
    try:
        client = _get_client(ctx)
        import html as html_module

        # Buscar todas as seções atuais do documento
        secoes_data = await client.listar_secao_documento(id_documento)
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

            secoes_enviar.append({
                "id": str(sid),
                "idSecaoModelo": str(modelo),
                "conteudo": sanitize_iso8859(conteudo),
            })

        result = await client.alterar_secao_documento(
            id_documento=id_documento,
            secoes=secoes_enviar,
            versao=versao,
        )
        return _json(result)
    except Exception as e:
        return _error(str(e))


# ---------------------------------------------------------------------------
# Tools de processos — listar, pesquisar, criar, tramitar
# ---------------------------------------------------------------------------


@mcp.tool()
async def sei_listar_processos(
    pagina: int = 0,
    apenas_meus: str = "",
    tipo: str = "",
    filtro: str = "",
    ctx: Context = None,
) -> str:
    """Lista processos da caixa da unidade atual no SEI (Controle de Processos).

    Implementação via scraper do frontend web (~20× mais rápida que a REST API).
    Retorna a página inteira de uma vez (a paginação é controlada pelo SEI;
    para a maioria das unidades todos os processos cabem em poucas páginas).

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

    NOTAS:
    - Processos sobrestados e concluídos não aparecem nesta listagem.
    - Para agrupamento estatístico (sei_resumo_processos) usa-se a REST API
      diretamente (que tem flags estruturadas como tramitação, sobrestamento,
      acesso, etc.).
    - Login web é executado uma vez por sessão (~3 s); listagens subsequentes
      custam ~600 ms cada, contra ~14 s da REST API.
    """
    try:
        web = _get_web_client(ctx)
        if web._inbox_url is None:
            await web.login()
        result = await web.listar_processos(
            detalhada=True,
            pagina=pagina,
            apenas_meus=(apenas_meus.upper() == "S"),
            tipo=tipo,
            filtro=filtro,
        )
        return _json(result)
    except Exception as e:
        return _error(str(e))


_CAMPOS_AGRUPAMENTO = {
    "tipo": {
        "desc": "Tipo processual",
        "extract": lambda a, s: a.get("tipoProcesso", "Sem tipo"),
    },
    "atribuido": {
        "desc": "Usuário atribuído",
        "extract": lambda a, s: a.get("usuarioAtribuido") or "Sem atribuição",
    },
    "acesso": {
        "desc": "Nível de acesso",
        "extract": lambda a, s: {"0": "Público", "1": "Restrito", "2": "Sigiloso"}.get(
            s.get("nivelAcessoGlobal", "0"), "Desconhecido"
        ),
    },
    "tramitacao": {
        "desc": "Em tramitação",
        "extract": lambda a, s: "Em tramitação" if s.get("processoEmTramitacao") == "S" else "Fora de tramitação",
    },
    "sobrestado": {
        "desc": "Sobrestamento",
        "extract": lambda a, s: "Sobrestado" if s.get("processoSobrestado") == "S" else "Ativo",
    },
    "bloqueado": {
        "desc": "Bloqueio",
        "extract": lambda a, s: "Bloqueado" if s.get("processoBloqueado") == "S" else "Desbloqueado",
    },
    "novo": {
        "desc": "Documento novo",
        "extract": lambda a, s: "Com documentos novos" if s.get("documentoNovo") == "S" else "Sem documentos novos",
    },
    "anotacao": {
        "desc": "Anotação",
        "extract": lambda a, s: (
            "Anotação prioritária" if s.get("anotacaoPrioridade") == "S"
            else "Com anotação" if s.get("anotacao") == "S"
            else "Sem anotação"
        ),
    },
    "retorno": {
        "desc": "Retorno programado",
        "extract": lambda a, s: (
            f"Atrasado ({s.get('retornoData', '')})" if s.get("retornoAtrasado") == "S"
            else f"Programado ({s.get('retornoData', '')})" if s.get("retornoProgramado") == "S"
            else "Sem retorno"
        ),
    },
    "lido_usuario": {
        "desc": "Acessado pelo usuário",
        "extract": lambda a, s: "Lido" if s.get("processoAcessadoUsuario") == "S" else "Não lido",
    },
    "lido_unidade": {
        "desc": "Acessado pela unidade",
        "extract": lambda a, s: "Lido" if s.get("processoAcessadoUnidade") == "S" else "Não lido",
    },
    "origem": {
        "desc": "Gerado/Recebido",
        "extract": lambda a, s: "Gerado na unidade" if s.get("processoGeradoRecebido") == "G" else "Recebido",
    },
    "anexado": {
        "desc": "Anexado",
        "extract": lambda a, s: "Anexado" if s.get("processoAnexado") == "S" else "Independente",
    },
    "unidades": {
        "desc": "Unidades de abertura",
        "extract": lambda a, s: ", ".join(
            u.get("sigla", "") for u in a.get("dadosAbertura", {}).get("lista", [])
        ) or "N/A",
    },
    "marcador": {
        "desc": "Marcador",
        "extract": lambda a, s: ", ".join(
            m.get("nome", "") for m in a.get("marcador", [])
        ) or "Sem marcador",
    },
    "ciencia": {
        "desc": "Ciência",
        "extract": lambda a, s: "Com ciência" if s.get("ciencia") == "S" else "Sem ciência",
    },
}


@mcp.tool()
async def sei_resumo_processos(
    agrupar_por: str = "tipo",
    agrupar_por_2: str = "",
    apenas_meus: str = "",
    filtro: str = "",
    ctx: Context = None,
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
    - agrupar_por="tipo", agrupar_por_2="atribuido" → cruzamento tipo × pessoa
    - agrupar_por="retorno" → processos com prazo vencido
    """
    try:
        campo1 = _CAMPOS_AGRUPAMENTO.get(agrupar_por)
        if not campo1:
            campos = ", ".join(sorted(_CAMPOS_AGRUPAMENTO.keys()))
            return _error(f"Campo '{agrupar_por}' inválido. Disponíveis: {campos}")

        campo2 = None
        if agrupar_por_2:
            campo2 = _CAMPOS_AGRUPAMENTO.get(agrupar_por_2)
            if not campo2:
                campos = ", ".join(sorted(_CAMPOS_AGRUPAMENTO.keys()))
                return _error(f"Campo '{agrupar_por_2}' inválido. Disponíveis: {campos}")

        client = _get_client(ctx)

        # Busca todos os processos
        todos = []
        pg = 0
        while True:
            result = await client.listar_processos(
                limit=200, start=pg, apenas_meus=apenas_meus, filtro=filtro,
            )
            todos.extend(result["processos"])
            if not result.get("tem_proxima"):
                break
            pg += 1

        # Agrupar
        grupos: dict = {}
        for p in todos:
            a = p.get("atributos", {})
            s = a.get("status", {})
            chave1 = campo1["extract"](a, s)

            if campo2:
                chave2 = campo2["extract"](a, s)
                chave = f"{chave1} | {chave2}"
            else:
                chave = chave1

            if chave not in grupos:
                grupos[chave] = {"quantidade": 0, "processos": []}
            grupos[chave]["quantidade"] += 1
            grupos[chave]["processos"].append(a.get("numero", ""))

        # Ordenar por quantidade decrescente
        resumo = []
        for chave in sorted(grupos.keys(), key=lambda k: -grupos[k]["quantidade"]):
            g = grupos[chave]
            item = {"grupo": chave, "quantidade": g["quantidade"]}
            # Incluir lista de processos se grupo pequeno (≤ 20)
            if g["quantidade"] <= 20:
                item["processos"] = g["processos"]
            resumo.append(item)

        header = campo1["desc"]
        if campo2:
            header += f" × {campo2['desc']}"

        return _json({
            "agrupamento": header,
            "total_processos": len(todos),
            "total_grupos": len(resumo),
            "grupos": resumo,
        })
    except Exception as e:
        return _error(str(e))


@mcp.tool()
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
    ctx: Context = None,
) -> str:
    """Pesquisa processos no SEI por texto, descrição, datas, unidade ou assunto.

    Use palavras_chave para busca geral ou busca_rapida para busca simplificada.
    Datas no formato DD/MM/AAAA.

    Filtros adicionais:
    - sta_tipo_data: tipo de período — "30" (últimos 30 dias), "60" (últimos 60 dias)
      ou "0" (personalizado, requer data_inicio/data_fim)
    - id_unidade_geradora: id da unidade que gerou o processo (use sei_listar_unidades)
    - id_assunto: id do assunto (use sei_pesquisar_assuntos para obter o id)
    - grupo: id do grupo de acompanhamento (use sei_listar_grupos_acompanhamento)

    Paginação: pagina=0 é a primeira página, pagina=1 a segunda, etc.
    """
    try:
        client = _get_client(ctx)
        result = await client.pesquisar_processos(
            palavras_chave=palavras_chave,
            descricao=descricao,
            busca_rapida=busca_rapida,
            data_inicio=data_inicio,
            data_fim=data_fim,
            sta_tipo_data=sta_tipo_data,
            id_unidade_geradora=id_unidade_geradora,
            id_assunto=id_assunto,
            grupo=grupo,
            limit=limit,
            start=pagina,
        )
        return _json(result)
    except Exception as e:
        return _error(str(e))


@mcp.tool()
async def sei_pesquisar_hipoteses_legais(
    filtro: str = "",
    limit: int = 50,
    pagina: int = 0,
    ctx: Context = None,
) -> str:
    """Pesquisa hipóteses legais disponíveis no SEI.

    Necessário ao criar processos ou documentos com nível de acesso
    restrito ou sigiloso. Use o 'id' retornado no parâmetro
    hipotese_legal de sei_criar_processo.

    Exemplos: "pessoal", "controle interno", "sigilo fiscal"
    """
    try:
        client = _get_client(ctx)
        result = await client.pesquisar_hipoteses_legais(
            filtro=filtro, limit=limit, start=pagina,
        )
        return _json(result)
    except Exception as e:
        return _error(str(e))


@mcp.tool()
async def sei_pesquisar_tipos_processo(
    filtro: str = "",
    favoritos: str = "",
    limit: int = 50,
    pagina: int = 0,
    ctx: Context = None,
) -> str:
    """Pesquisa tipos de processo disponíveis no SEI.

    Parâmetros:
    - filtro: texto para filtrar por nome (ex: "Plano Anual", "Fiscalização")
    - favoritos: "S" para apenas favoritos
    - limit/pagina: paginação

    Use o 'id' retornado como tipo_processo em sei_criar_processo.
    """
    try:
        client = _get_client(ctx)
        result = await client.pesquisar_tipos_processo(
            filtro=filtro, favoritos=favoritos, limit=limit, start=pagina,
        )
        return _json(result)
    except Exception as e:
        return _error(str(e))


@mcp.tool()
async def sei_alterar_processo(
    processo: str,
    especificacao: str = "",
    nivel_acesso: str = "",
    hipotese_legal: str = "",
    observacao: str = "",
    ctx: Context = None,
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
    try:
        client = _get_client(ctx)
        id_proc = await _resolver_processo(client, processo)
        result = await client.alterar_processo(
            id_procedimento=id_proc,
            especificacao=especificacao,
            nivel_acesso=nivel_acesso,
            hipotese_legal=hipotese_legal,
            observacao=observacao,
        )
        return _json(result)
    except Exception as e:
        return _error(str(e))


@mcp.tool()
async def sei_criar_processo(
    tipo_processo: str,
    especificacao: str = "",
    assuntos: str = "",
    interessados: str = "",
    observacoes: str = "",
    nivel_acesso: str = "0",
    hipotese_legal: str = "",
    ctx: Context = None,
) -> str:
    """Cria um novo processo no SEI.

    Parâmetros:
    - tipo_processo: ID do tipo de processo (use sei_pesquisar_tipos_processo)
    - especificacao: descrição do processo (recomendado para organizar a caixa)
    - assuntos: IDs dos assuntos (separados por vírgula)
    - interessados: IDs dos interessados (separados por vírgula)
    - observacoes: observações adicionais
    - nivel_acesso: 0=público (padrão), 1=restrito, 2=sigiloso
    - hipotese_legal: ID da hipótese legal (obrigatório se restrito/sigiloso).
      Use sei_pesquisar_hipoteses_legais para descobrir o ID.

    Retorna o IdProcedimento e ProtocoloFormatado do processo criado.

    Para assuntos, use sei_pesquisar_tipos_processo para ver as sugestões
    de assunto do tipo de processo escolhido (endpoint /processo/assunto/sugestao).
    """
    try:
        client = _get_client(ctx)
        result = await client.criar_processo(
            tipo_processo=tipo_processo,
            especificacao=especificacao,
            assuntos=assuntos,
            interessados=interessados,
            observacoes=observacoes,
            nivel_acesso=nivel_acesso,
            hipotese_legal=hipotese_legal,
        )
        return _json(result)
    except Exception as e:
        return _error(str(e))


@mcp.tool()
async def sei_enviar_processo(
    numero_processo: str,
    unidades_destino: str,
    manter_aberto: str = "N",
    remover_anotacao: str = "N",
    enviar_email: str = "N",
    data_retorno: str = "",
    dias_retorno: str = "",
    ctx: Context = None,
) -> str:
    """Envia (tramita) um processo para outra(s) unidade(s) no SEI.

    Parâmetros:
    - numero_processo: protocolo formatado (ex: 50300.000123/2025-00)
    - unidades_destino: sigla da unidade (ex: "SFC", "ECP-SFC") OU ID numérico.
      Para múltiplas unidades, separe por vírgula.
      Se informar sigla, resolve o ID automaticamente via pesquisa.
    - manter_aberto: "N" fechar na unidade atual (padrão), "S" manter aberto
    - remover_anotacao: "S" remover anotações, "N" manter (padrão)
    - enviar_email: "S" notificar por email (só se o usuário pedir)
    - data_retorno: data de retorno programado DD/MM/AAAA (só se o usuário pedir)
    - dias_retorno: prazo em dias para retorno (alternativa à data, só se pedir)
    """
    try:
        client = _get_client(ctx)

        # Resolver unidades destino: aceita sigla ou ID
        destinos = [d.strip() for d in unidades_destino.split(",")]
        ids_resolvidos = []
        for destino in destinos:
            if destino.isdigit():
                ids_resolvidos.append(destino)
            else:
                # Pesquisar pela sigla/nome
                result = await client.pesquisar_unidades(filtro=destino, limit=10)
                unidades = result.get("unidades", [])
                encontrou = False
                for u in unidades:
                    sigla = u.get("sigla", "")
                    if sigla.upper() == destino.upper():
                        ids_resolvidos.append(str(u.get("id", "")))
                        encontrou = True
                        break
                if not encontrou:
                    if unidades:
                        # Usar a primeira que contém o texto
                        ids_resolvidos.append(str(unidades[0].get("id", "")))
                    else:
                        return _json({
                            "error": f"Unidade '{destino}' não encontrada",
                            "dica": "Use sei_pesquisar_unidades para buscar.",
                        })

        result = await client.enviar_processo(
            numero_processo=numero_processo,
            unidades_destino=",".join(ids_resolvidos),
            manter_aberto=manter_aberto,
            remover_anotacao=remover_anotacao,
            enviar_email=enviar_email,
            data_retorno=data_retorno,
            dias_retorno=dias_retorno,
        )
        return _json(result)
    except Exception as e:
        return _error(str(e))


@mcp.tool()
async def sei_marcar_nao_lido(
    numero_processo: str,
    ctx: Context = None,
) -> str:
    """Marca um processo como não lido na unidade atual.

    O SEI não possui funcionalidade nativa para isso. Esta tool usa
    o workaround de enviar o processo para a própria unidade, o que
    faz o SEI tratar como novo recebimento (não lido).

    - numero_processo: protocolo formatado (ex: 50300.012639/2023-26)
    """
    try:
        client = _get_client(ctx)
        if not client._unidade_ativa:
            return _error(
                "Unidade ativa não definida. Use sei_trocar_unidade primeiro."
            )
        result = await client.enviar_processo(
            numero_processo=numero_processo,
            unidades_destino=client._unidade_ativa,
            manter_aberto="S",
            remover_anotacao="N",
            enviar_email="N",
        )
        return _json({
            "mensagem": "Processo marcado como não lido.",
            "detalhe": result.get("mensagem", ""),
        })
    except Exception as e:
        return _error(str(e))


@mcp.tool()
async def sei_concluir_processo(numero_processo: str, ctx: Context = None) -> str:
    """Conclui um processo na unidade atual do SEI.

    O processo é removido da caixa da unidade mas permanece acessível.
    Use sei_reabrir_processo para reverter.
    """
    try:
        client = _get_client(ctx)
        result = await client.concluir_processo(numero_processo)
        return _json(result)
    except Exception as e:
        return _error(str(e))


@mcp.tool()
async def sei_reabrir_processo(processo: str, ctx: Context = None) -> str:
    """Reabre um processo que foi concluído na unidade.

    - processo: protocolo formatado (ex: 50300.018905/2018-67) ou IdProcedimento

    O processo volta para a caixa da unidade atual.
    """
    try:
        client = _get_client(ctx)
        id_proc = await _resolver_processo(client, processo)
        result = await client.reabrir_processo(id_proc)
        return _json(result)
    except Exception as e:
        return _error(str(e))


@mcp.tool()
async def sei_atribuir_processo(
    numero_processo: str,
    usuario: str,
    ctx: Context = None,
) -> str:
    """Atribui um processo a um usuário da unidade.

    Parâmetros:
    - numero_processo: protocolo formatado (ex: 50300.000123/2025-00)
    - usuario: ID numérico do usuário OU nome/parte do nome
      (ex: "100001860" ou "Karina" ou "Karina Shimoishi")

    Quando um nome é informado, busca os usuários correspondentes
    e tenta atribuir a cada um até encontrar um com permissão
    na unidade atual.
    """
    try:
        client = _get_client(ctx)

        # Se parece ser um ID numérico, usa direto
        if usuario.isdigit():
            result = await client.atribuir_processo(numero_processo, usuario)
            return _json(result)

        # Busca por nome
        result = await client.listar_usuarios(filtro=usuario)
        candidatos = result.get("usuarios", [])
        if not candidatos:
            return _json({
                "error": f"Nenhum usuário encontrado com '{usuario}'",
                "dica": "Use sei_listar_usuarios para ver os usuários disponíveis.",
            })

        # Tentar cada candidato até um funcionar
        erros = []
        for u in candidatos:
            id_u = u.get("id_usuario", "")
            nome = u.get("nome", "")
            sigla = u.get("sigla", "")
            try:
                result = await client.atribuir_processo(numero_processo, id_u)
                return _json({
                    "mensagem": result.get("mensagem", "Processo atribuído com sucesso!"),
                    "usuario": {"id": id_u, "nome": nome, "sigla": sigla},
                })
            except Exception as e:
                erros.append(f"{nome} ({sigla}): {e}")
                continue

        return _json({
            "error": f"Nenhum dos {len(candidatos)} usuários com '{usuario}' tem permissão na unidade atual",
            "tentativas": erros,
            "dica": "Verifique se está na unidade correta com sei_trocar_unidade.",
        })
    except Exception as e:
        return _error(str(e))


# ---------------------------------------------------------------------------
# Tools de documentos — assinar, pesquisar tipos
# ---------------------------------------------------------------------------


@mcp.tool()
async def sei_cancelar_assinatura(
    id_documento: str,
    ctx: Context = None,
) -> str:
    """Tenta cancelar (derrubar) a assinatura de um documento no SEI.

    Aceita id interno ou número SEI (protocoloFormatado).

    A API do SEI não possui endpoint direto para cancelar assinatura.
    Esta tool tenta forçar uma edição mínima no documento para que o
    SEI remova a assinatura automaticamente (comportamento padrão ao editar).

    LIMITAÇÃO: só funciona se o processo não foi enviado/lido por outra
    unidade. Se falhar, o usuário deve cancelar a assinatura pela
    interface web do SEI (botão "Editar Conteúdo" no documento).
    """
    try:
        client = _get_client(ctx)
        import html as html_module

        # Resolver número SEI → id interno
        doc_id = id_documento.strip()
        try:
            doc_id, _ = await _resolver_documento(client, doc_id)
        except Exception:
            pass

        # Verificar se está assinado
        secoes_data = await client.listar_secao_documento(doc_id)
        versao = str(secoes_data.get("ultimaVersaoDocumento", "1"))

        # Montar payload com todas as seções (mesmo conteúdo)
        secoes_enviar = []
        for s in secoes_data.get("secoes", []):
            if not isinstance(s, dict):
                continue
            sid = s.get("id")
            modelo = s.get("idSecaoModelo")
            conteudo = html_module.unescape(s.get("conteudo", "") or "")
            secoes_enviar.append({
                "id": str(sid),
                "idSecaoModelo": str(modelo),
                "conteudo": sanitize_iso8859(conteudo),
            })

        # Tentar editar (derruba assinatura se permitido)
        result = await client.alterar_secao_documento(doc_id, secoes_enviar, versao)
        return _json({
            "mensagem": "Assinatura cancelada com sucesso. O documento foi editado (nova versão).",
            "versao": result,
        })
    except Exception as e:
        msg = str(e)
        if "assinado" in msg.lower():
            return _json({
                "error": "Não foi possível cancelar a assinatura via API.",
                "motivo": msg,
                "dica": "O processo pode ter sido enviado ou lido por outra unidade. "
                        "Cancele a assinatura pela interface web do SEI: "
                        "abra o documento → clique em 'Editar Conteúdo'.",
            })
        return _error(msg)


@mcp.tool()
async def sei_assinar_documento(
    id_documento: str,
    cargo: str = "",
    orgao: str = "",
    ctx: Context = None,
) -> str:
    """Assina eletronicamente um documento no SEI.

    A autenticação é automática — basta informar o documento e o cargo.

    IMPORTANTE: o parâmetro `cargo` é OBRIGATÓRIO. Sem ele a assinatura falha.
    Se não souber o cargo, chame sem cargo para obter a lista de opções.
    Pergunte ao usuário qual cargo usar e chame novamente com o cargo escolhido.
    Grave o cargo escolhido para reutilizar nas próximas assinaturas.

    Parâmetros:
    - id_documento: ID interno do documento ou número SEI (protocoloFormatado).
      Se for número SEI, resolve automaticamente via pesquisa Solr.
    - cargo: cargo/função para assinatura (ex: "Agente Público").
      OBRIGATÓRIO. Se omitido, retorna a lista de cargos disponíveis.
    - orgao: código do órgão (usa o padrão se omitido)
    """
    try:
        client = _get_client(ctx)
        login = client._usuario
        senha = client._senha

        # Resolver número SEI → id interno se necessário
        doc_id = id_documento.strip()
        if not doc_id.isdigit() or len(doc_id) < 7:
            # Pode ser número SEI, resolver
            try:
                doc_id, _ = await _resolver_documento(client, doc_id)
            except Exception:
                doc_id = id_documento  # Manter original se falhar

        # Se cargo não informado, listar opções e pedir ao usuário
        if not cargo:
            try:
                resp = await client._request("GET", "/assinante/listar")
                data = resp.json()
                cargos = data.get("data", [])
            except Exception:
                cargos = []
            return _json({
                "error": "Cargo/Função não informado — é obrigatório para assinatura.",
                "cargos_disponiveis": cargos,
                "dica": "Pergunte ao usuário qual cargo/função usar para assinar. "
                        "Os cargos disponíveis estão listados acima. "
                        "IMPORTANTE: após o usuário escolher, salve o cargo na memória da conversa "
                        "para reutilizar em todas as próximas assinaturas sem perguntar novamente.",
            })

        # Garante que a autenticação rodou e captura IdUsuario da sessão
        await client._get_headers()
        id_usuario = client._id_usuario or ""

        # Fallback: procurar via /usuario/listar caso loginData não traga o id
        if not id_usuario:
            try:
                result = await client.listar_usuarios(filtro=login, apenas_unidade=False)
                for u in result.get("usuarios", []):
                    if u.get("sigla", "").lower() == login.lower():
                        id_usuario = str(u.get("id_usuario") or "")
                        break
            except Exception:
                pass

        result = await client.assinar_documento(
            id_documento=doc_id,
            login=login,
            senha=senha,
            cargo=cargo,
            orgao=orgao,
            id_usuario=id_usuario,
        )
        return _json(result)
    except Exception as e:
        return _error(str(e))


@mcp.tool()
async def sei_pesquisar_tipos_documento(
    filtro: str = "",
    favoritos: str = "",
    aplicabilidade: str = "",
    limit: int = 50,
    pagina: int = 0,
    ctx: Context = None,
) -> str:
    """Pesquisa tipos de documento (séries) disponíveis no SEI.

    Parâmetros:
    - filtro: texto para filtrar por nome do tipo
    - favoritos: "S" para apenas favoritos
    - aplicabilidade: "I" para internos, "F" para externos, ou "I,F" para ambos
    - limit: quantidade por página
    - pagina: número da página (0=primeira)

    Use o 'id' retornado como id_serie em sei_criar_documento.
    """
    try:
        client = _get_client(ctx)
        result = await client.pesquisar_tipos_documento(
            filtro=filtro,
            favoritos=favoritos,
            aplicabilidade=aplicabilidade,
            limit=limit,
            start=pagina,
        )
        return _json(result)
    except Exception as e:
        return _error(str(e))


# ---------------------------------------------------------------------------
# Tools de anotação
# ---------------------------------------------------------------------------


@mcp.tool()
async def sei_sobrestar_processo(
    processo: str,
    motivo: str,
    processo_vinculado: str = "",
    ctx: Context = None,
) -> str:
    """Sobresta um processo no SEI.

    Parâmetros:
    - processo: protocolo formatado (ex: 50300.018905/2018-67) ou IdProcedimento
    - motivo: motivo do sobrestamento (obrigatório)
    - processo_vinculado: protocolo de outro processo para vincular (opcional).
      Se informado, o sobrestamento fica vinculado ao andamento desse processo.
    """
    try:
        client = _get_client(ctx)
        id_proc = await _resolver_processo(client, processo)

        proto_vinculado = ""
        if processo_vinculado:
            proto_vinculado = await _resolver_processo(client, processo_vinculado)

        result = await client.sobrestar_processo(
            id_procedimento=id_proc,
            motivo=motivo,
            protocolo_vinculado=proto_vinculado,
        )
        return _json(result)
    except Exception as e:
        msg = str(e)
        # Erro comum: processo aberto em outras unidades
        if "aberto" in msg.lower() or "unidade" in msg.lower() or "sobrestar" in msg.lower():
            # Tentar listar unidades onde o processo está aberto
            try:
                proc = await client.consultar_processo(processo)
                resp = await client._request(
                    "GET", f"/processo/listar/unidades/{id_proc}"
                )
                data = resp.json()
                unidades = data.get("data", [])
                nomes = [f"{u.get('sigla', '')} ({u.get('id', '')})" for u in unidades]
                return _json({
                    "error": msg,
                    "unidades_abertas": nomes,
                    "dica": "O processo precisa estar aberto somente na unidade atual "
                            "para ser sobrestado. Conclua o processo nas unidades "
                            "listadas acima antes de sobrestar.",
                })
            except Exception:
                pass
        return _error(msg)


@mcp.tool()
async def sei_remover_sobrestamento(
    processo: str,
    ctx: Context = None,
) -> str:
    """Remove o sobrestamento de um processo no SEI.

    - processo: protocolo formatado (ex: 50300.018905/2018-67) ou IdProcedimento
    """
    try:
        client = _get_client(ctx)
        id_proc = await _resolver_processo(client, processo)
        result = await client.remover_sobrestamento(id_proc)
        return _json(result)
    except Exception as e:
        return _error(str(e))


@mcp.tool()
async def sei_dar_ciencia(
    referencia: str,
    tipo: Literal["documento", "processo"] = "documento",
    ctx: Context = None,
) -> str:
    """Dá ciência em um documento ou processo no SEI.

    Parâmetros:
    - referencia: número SEI do documento OU protocolo/IdProcedimento do processo
    - tipo: "documento" (padrão) ou "processo"

    Exemplos:
    - sei_dar_ciencia("1482875", tipo="documento")  → ciência na NT 16
    - sei_dar_ciencia("50300.018905/2018-67", tipo="processo")  → ciência no processo
    """
    try:
        client = _get_client(ctx)

        if tipo == "documento":
            # Resolver número SEI → id interno
            doc_id, _ = await _resolver_documento(client, referencia)
            result = await client.dar_ciencia_documento(doc_id)
            return _json(result)
        else:
            # Resolver protocolo → IdProcedimento
            id_proc = await _resolver_processo(client, referencia)
            result = await client.dar_ciencia_processo(id_proc)
            return _json(result)
    except Exception as e:
        return _error(str(e))


@mcp.tool()
async def sei_listar_ciencias(
    referencia: str,
    tipo: Literal["documento", "processo"] = "documento",
    ctx: Context = None,
) -> str:
    """Lista as ciências registradas em um documento ou processo.

    Parâmetros:
    - referencia: número SEI do documento OU protocolo/IdProcedimento do processo
    - tipo: "documento" (padrão) ou "processo"
    """
    try:
        client = _get_client(ctx)

        if tipo == "documento":
            doc_id, _ = await _resolver_documento(client, referencia)
            result = await client.listar_ciencias_documento(doc_id)
        else:
            id_proc = await _resolver_processo(client, referencia)
            result = await client.listar_ciencias_processo(id_proc)
        return _json(result)
    except Exception as e:
        return _error(str(e))


# ---------------------------------------------------------------------------
# Tools adicionais de processo
# ---------------------------------------------------------------------------


@mcp.tool()
async def sei_remover_atribuicao(
    processo: str,
    ctx: Context = None,
) -> str:
    """Remove a atribuição de um processo (desatribui de qualquer usuário).

    - processo: protocolo formatado ou IdProcedimento
    """
    try:
        client = _get_client(ctx)
        id_proc = await _resolver_processo(client, processo)
        result = await client.remover_atribuicao(id_proc)
        return _json(result)
    except Exception as e:
        return _error(str(e))


@mcp.tool()
async def sei_receber_processo(
    processo: str,
    ctx: Context = None,
) -> str:
    """Confirma o recebimento de um processo na unidade atual.

    - processo: protocolo formatado ou IdProcedimento
    """
    try:
        client = _get_client(ctx)
        id_proc = await _resolver_processo(client, processo)
        result = await client.receber_processo(id_proc)
        return _json(result)
    except Exception as e:
        return _error(str(e))


@mcp.tool()
async def sei_listar_unidades_processo(
    processo: str,
    ctx: Context = None,
) -> str:
    """Lista as unidades onde o processo está aberto."""
    try:
        client = _get_client(ctx)
        id_proc = await _resolver_processo(client, processo)
        result = await client.listar_unidades_processo(id_proc)
        return _json(result)
    except Exception as e:
        return _error(str(e))


@mcp.tool()
async def sei_listar_interessados(
    processo: str,
    ctx: Context = None,
) -> str:
    """Lista os interessados de um processo."""
    try:
        client = _get_client(ctx)
        id_proc = await _resolver_processo(client, processo)
        result = await client.listar_interessados(id_proc)
        return _json(result)
    except Exception as e:
        return _error(str(e))


@mcp.tool()
async def sei_listar_sobrestamentos(
    processo: str,
    ctx: Context = None,
) -> str:
    """Lista o histórico de sobrestamentos de um processo."""
    try:
        client = _get_client(ctx)
        id_proc = await _resolver_processo(client, processo)
        result = await client.listar_sobrestamentos(id_proc)
        return _json(result)
    except Exception as e:
        return _error(str(e))


@mcp.tool()
async def sei_listar_assinaturas(
    id_documento: str,
    ctx: Context = None,
) -> str:
    """Lista as assinaturas de um documento."""
    try:
        client = _get_client(ctx)
        result = await client.listar_assinaturas(id_documento)
        return _json(result)
    except Exception as e:
        return _error(str(e))


@mcp.tool()
async def sei_registrar_andamento(
    processo: str,
    descricao: str,
    ctx: Context = None,
) -> str:
    """Registra um andamento (atividade) no processo.

    - processo: protocolo formatado ou IdProcedimento
    - descricao: texto do andamento
    """
    try:
        client = _get_client(ctx)
        id_proc = await _resolver_processo(client, processo)
        result = await client.registrar_andamento(id_proc, descricao)
        return _json(result)
    except Exception as e:
        return _error(str(e))


@mcp.tool()
async def sei_pesquisar_contatos(
    filtro: str = "",
    limit: int = 50,
    ctx: Context = None,
) -> str:
    """Pesquisa contatos cadastrados no SEI."""
    try:
        client = _get_client(ctx)
        result = await client.pesquisar_contatos(filtro=filtro, limit=limit)
        return _json(result)
    except Exception as e:
        return _error(str(e))


@mcp.tool()
async def sei_criar_documento_externo(
    processo: str,
    id_serie: str,
    arquivo_path: str,
    descricao: str = "",
    nivel_acesso: str = "0",
    ctx: Context = None,
) -> str:
    """Cria um documento externo (upload de arquivo) em um processo SEI.

    - processo: protocolo formatado ou IdProcedimento
    - id_serie: tipo do documento (use sei_pesquisar_tipos_documento)
    - arquivo_path: caminho local do arquivo (PDF, imagem, etc.)
    - descricao: descrição do documento
    - nivel_acesso: 0=público (padrão), 1=restrito, 2=sigiloso
    """
    try:
        client = _get_client(ctx)
        id_proc = await _resolver_processo(client, processo)
        result = await client.criar_documento_externo(
            id_procedimento=id_proc, id_serie=id_serie,
            arquivo_path=arquivo_path, descricao=descricao,
            nivel_acesso=nivel_acesso,
        )
        return _json(result)
    except Exception as e:
        return _error(str(e))


@mcp.tool()
async def sei_assinar_bloco(
    id_bloco: str,
    cargo: str = "",
    ctx: Context = None,
) -> str:
    """Assina TODOS os documentos de um bloco de assinatura.

    A autenticação é automática — basta informar o bloco e o cargo.

    IMPORTANTE: o parâmetro `cargo` é OBRIGATÓRIO. Sem ele a assinatura falha.
    Se não souber o cargo, chame sem cargo para ver a lista de opções.
    Pergunte ao usuário e grave o cargo para reutilizar na mesma conversa.

    - id_bloco: ID do bloco
    - cargo: cargo/função — OBRIGATÓRIO (se omitido, lista opções disponíveis)
    """
    try:
        client = _get_client(ctx)
        login = client._usuario
        senha = client._senha
        if not cargo:
            try:
                resp = await client._request("GET", "/assinante/listar")
                data = resp.json()
                cargos = data.get("data", [])
            except Exception:
                cargos = []
            return _json({
                "error": "Cargo/Função não informado.",
                "cargos_disponiveis": cargos,
                "dica": "Pergunte ao usuário qual cargo usar. "
                        "IMPORTANTE: após o usuário escolher, salve o cargo na memória da conversa "
                        "para reutilizar em todas as próximas assinaturas sem perguntar novamente.",
            })
        result = await client.assinar_bloco(
            id_bloco=id_bloco, login=login, senha=senha, cargo=cargo,
        )
        return _json(result)
    except Exception as e:
        return _error(str(e))


@mcp.tool()
async def sei_assinar_documentos_bloco(
    documentos: str,
    cargo: str = "",
    ctx: Context = None,
) -> str:
    """Assina documentos específicos de um bloco de assinatura.

    A autenticação é automática — basta informar os documentos e o cargo.

    IMPORTANTE: o parâmetro `cargo` é OBRIGATÓRIO. Sem ele a assinatura falha.
    Se não souber o cargo, chame sem cargo para ver a lista de opções.
    Pergunte ao usuário e grave o cargo para reutilizar na mesma conversa.

    - documentos: ID(s) de documento(s) separados por vírgula
    - cargo: cargo/função — OBRIGATÓRIO (se omitido, lista opções disponíveis)
    """
    try:
        client = _get_client(ctx)
        login = client._usuario
        senha = client._senha
        if not cargo:
            try:
                resp = await client._request("GET", "/assinante/listar")
                data = resp.json()
                cargos = data.get("data", [])
            except Exception:
                cargos = []
            return _json({
                "error": "Cargo/Função não informado.",
                "cargos_disponiveis": cargos,
                "dica": "Pergunte ao usuário qual cargo usar. "
                        "IMPORTANTE: após o usuário escolher, salve o cargo na memória da conversa "
                        "para reutilizar em todas as próximas assinaturas sem perguntar novamente.",
            })
        result = await client.assinar_documentos_bloco(
            login=login, senha=senha, cargo=cargo, documentos=documentos,
        )
        return _json(result)
    except Exception as e:
        return _error(str(e))


# ---------------------------------------------------------------------------
# Tools de marcador
# ---------------------------------------------------------------------------


@mcp.tool()
async def sei_criar_marcador(
    nome: str,
    id_cor: str = "",
    ctx: Context = None,
) -> str:
    """Cria um marcador na unidade atual.

    - nome: nome do marcador
    - id_cor: ID da cor (use sei_listar_cores_marcador para ver opções).
      Se omitido, lista as cores disponíveis para escolha.
    """
    try:
        client = _get_client(ctx)
        if not id_cor:
            cores = await client.listar_cores_marcador()
            return _json({
                "error": "Cor não informada — escolha uma das cores disponíveis.",
                "cores": cores,
            })
        result = await client.criar_marcador(nome, id_cor)
        return _json(result)
    except Exception as e:
        return _error(str(e))


@mcp.tool()
async def sei_excluir_marcador(
    ids_marcadores: str,
    ctx: Context = None,
) -> str:
    """Exclui marcador(es). IDs separados por vírgula."""
    try:
        client = _get_client(ctx)
        result = await client.excluir_marcadores(ids_marcadores)
        return _json(result)
    except Exception as e:
        return _error(str(e))


@mcp.tool()
async def sei_marcar_processo(
    processo: str,
    marcador: str,
    texto: str = "",
    ctx: Context = None,
) -> str:
    """Adiciona ou altera marcador (etiqueta colorida) em um processo.

    Parâmetros:
    - processo: protocolo formatado ou IdProcedimento
    - marcador: ID do marcador (use sei_pesquisar_marcadores para listar)
    - texto: texto/comentário associado ao marcador (opcional)

    Para remover, use marcador vazio ou marque com outro marcador.
    """
    try:
        client = _get_client(ctx)
        id_proc = await _resolver_processo(client, processo)
        result = await client.marcar_processo(id_proc, marcador, texto)
        return _json(result)
    except Exception as e:
        return _error(str(e))


@mcp.tool()
async def sei_pesquisar_marcadores(
    filtro: str = "",
    limit: int = 50,
    ctx: Context = None,
) -> str:
    """Lista marcadores disponíveis na unidade atual.

    Use o 'id' retornado em sei_marcar_processo.
    """
    try:
        client = _get_client(ctx)
        result = await client.pesquisar_marcadores(filtro=filtro, limit=limit)
        return _json(result)
    except Exception as e:
        return _error(str(e))


@mcp.tool()
async def sei_consultar_marcador_processo(
    processo: str,
    ctx: Context = None,
) -> str:
    """Consulta os marcadores ativos de um processo."""
    try:
        client = _get_client(ctx)
        id_proc = await _resolver_processo(client, processo)
        result = await client.consultar_marcador_processo(id_proc)
        return _json(result)
    except Exception as e:
        return _error(str(e))


# ---------------------------------------------------------------------------
# Tools de acompanhamento especial
# ---------------------------------------------------------------------------


@mcp.tool()
async def sei_acompanhar_processo(
    processo: str,
    grupo: str = "",
    observacao: str = "",
    ctx: Context = None,
) -> str:
    """Adiciona acompanhamento especial em um processo.

    Parâmetros:
    - processo: protocolo formatado ou IdProcedimento
    - grupo: ID do grupo de acompanhamento (use sei_listar_grupos_acompanhamento)
    - observacao: observação/anotação do acompanhamento
    """
    try:
        client = _get_client(ctx)
        id_proc = await _resolver_processo(client, processo)
        result = await client.acompanhar_processo(id_proc, grupo, observacao)
        return _json(result)
    except Exception as e:
        return _error(str(e))


@mcp.tool()
async def sei_remover_acompanhamento(
    processo: str,
    ctx: Context = None,
) -> str:
    """Remove acompanhamento especial de um processo.

    Consulta o acompanhamento ativo e remove.
    """
    try:
        client = _get_client(ctx)
        id_proc = await _resolver_processo(client, processo)
        acomp = await client.consultar_acompanhamento(id_proc)
        if not acomp:
            return _json({"mensagem": "Nenhum acompanhamento ativo neste processo."})
        id_acomp = str(acomp.get("idAcompanhamento", acomp.get("id", "")))
        if not id_acomp:
            return _error("Não foi possível identificar o acompanhamento.")
        result = await client.excluir_acompanhamento(id_acomp)
        return _json(result)
    except Exception as e:
        return _error(str(e))


@mcp.tool()
async def sei_criar_grupo_acompanhamento(
    nome: str,
    ctx: Context = None,
) -> str:
    """Cria um grupo de acompanhamento especial no SEI."""
    try:
        client = _get_client(ctx)
        result = await client.criar_grupo_acompanhamento(nome)
        return _json(result)
    except Exception as e:
        return _error(str(e))


@mcp.tool()
async def sei_excluir_grupo_acompanhamento(
    ids_grupos: str,
    ctx: Context = None,
) -> str:
    """Exclui grupo(s) de acompanhamento especial. IDs separados por vírgula."""
    try:
        client = _get_client(ctx)
        result = await client.excluir_grupo_acompanhamento(ids_grupos)
        return _json(result)
    except Exception as e:
        return _error(str(e))


@mcp.tool()
async def sei_listar_grupos_acompanhamento(
    filtro: str = "",
    ctx: Context = None,
) -> str:
    """Lista grupos de acompanhamento disponíveis."""
    try:
        client = _get_client(ctx)
        result = await client.listar_grupos_acompanhamento(filtro=filtro)
        return _json(result)
    except Exception as e:
        return _error(str(e))


# ---------------------------------------------------------------------------
# Tools de bloco interno
# ---------------------------------------------------------------------------


@mcp.tool()
async def sei_criar_bloco_interno(
    descricao: str,
    ctx: Context = None,
) -> str:
    """Cria um bloco interno no SEI.

    Blocos internos são usados para organizar processos em lotes.
    """
    try:
        client = _get_client(ctx)
        result = await client.criar_bloco_interno(descricao)
        return _json(result)
    except Exception as e:
        return _error(str(e))


@mcp.tool()
async def sei_incluir_processo_bloco_interno(
    id_bloco: str,
    processos: str,
    ctx: Context = None,
) -> str:
    """Inclui processo(s) em um bloco interno.

    - id_bloco: ID do bloco
    - processos: IdProcedimento(s) separados por vírgula
    """
    try:
        client = _get_client(ctx)
        result = await client.incluir_processo_bloco_interno(id_bloco, processos)
        return _json(result)
    except Exception as e:
        return _error(str(e))


@mcp.tool()
async def sei_retirar_processo_bloco_interno(
    id_bloco: str,
    processos: str,
    ctx: Context = None,
) -> str:
    """Remove processo(s) de um bloco interno.

    - id_bloco: ID do bloco
    - processos: IdProcedimento(s) separados por vírgula
    """
    try:
        client = _get_client(ctx)
        result = await client.retirar_processo_bloco_interno(id_bloco, processos)
        return _json(result)
    except Exception as e:
        return _error(str(e))


# ---------------------------------------------------------------------------
# Tools de bloco de assinatura
# ---------------------------------------------------------------------------


@mcp.tool()
async def sei_criar_bloco_assinatura(
    descricao: str,
    unidades: str = "",
    ctx: Context = None,
) -> str:
    """Cria um bloco de assinatura no SEI.

    Parâmetros:
    - descricao: descrição do bloco
    - unidades: sigla(s) ou ID(s) das unidades para disponibilizar
      (separados por vírgula). Se informar sigla, resolve automaticamente.
    """
    try:
        client = _get_client(ctx)

        # Resolver siglas de unidades para IDs
        if unidades:
            destinos = [u.strip() for u in unidades.split(",")]
            ids = []
            for d in destinos:
                if d.isdigit():
                    ids.append(d)
                else:
                    result = await client.pesquisar_unidades(filtro=d, limit=5)
                    found = False
                    for u in result.get("unidades", []):
                        if u.get("sigla", "").upper() == d.upper():
                            ids.append(str(u.get("id", "")))
                            found = True
                            break
                    if not found and result.get("unidades"):
                        ids.append(str(result["unidades"][0].get("id", "")))
            unidades = ",".join(ids)

        result = await client.criar_bloco_assinatura(descricao, unidades)
        return _json(result)
    except Exception as e:
        return _error(str(e))


@mcp.tool()
async def sei_incluir_documento_bloco_assinatura(
    id_bloco: str,
    documentos: str,
    ctx: Context = None,
) -> str:
    """Inclui documento(s) em um bloco de assinatura.

    - id_bloco: ID do bloco de assinatura
    - documentos: ID(s) de documento(s) separados por vírgula
    """
    try:
        client = _get_client(ctx)
        result = await client.incluir_documento_bloco_assinatura(id_bloco, documentos)
        return _json(result)
    except Exception as e:
        return _error(str(e))


@mcp.tool()
async def sei_disponibilizar_bloco_assinatura(
    id_bloco: str,
    ctx: Context = None,
) -> str:
    """Disponibiliza um bloco de assinatura para as unidades configuradas.

    Após disponibilizar, os usuários das unidades podem assinar os documentos.
    """
    try:
        client = _get_client(ctx)
        result = await client.disponibilizar_bloco_assinatura(id_bloco)
        return _json(result)
    except Exception as e:
        return _error(str(e))


@mcp.tool()
async def sei_cancelar_disponibilizacao_bloco(
    id_bloco: str,
    ctx: Context = None,
) -> str:
    """Cancela a disponibilização de um bloco de assinatura.

    O bloco volta ao estado aberto e pode ser editado novamente.
    """
    try:
        client = _get_client(ctx)
        result = await client.cancelar_disponibilizacao_bloco_assinatura(id_bloco)
        return _json(result)
    except Exception as e:
        return _error(str(e))


@mcp.tool()
async def sei_pesquisar_blocos_assinatura(
    filtro: str = "",
    limit: int = 50,
    ctx: Context = None,
) -> str:
    """Pesquisa blocos de assinatura existentes."""
    try:
        client = _get_client(ctx)
        result = await client.pesquisar_blocos_assinatura(filtro=filtro, limit=limit)
        return _json(result)
    except Exception as e:
        return _error(str(e))


@mcp.tool()
async def sei_criar_anotacao(
    processo: str,
    descricao: str,
    prioridade: str = "1",
    ctx: Context = None,
) -> str:
    """Cria uma anotação (post-it) em um processo no SEI.

    Parâmetros:
    - processo: protocolo formatado (ex: 50300.018905/2018-67) ou IdProcedimento
    - descricao: texto da anotação
    - prioridade: nível de prioridade (1=normal)
    """
    try:
        client = _get_client(ctx)
        id_proc = await _resolver_processo(client, processo)
        result = await client.criar_anotacao(
            protocolo=id_proc,
            descricao=descricao,
            prioridade=prioridade,
        )
        return _json(result)
    except Exception as e:
        return _error(str(e))


# ---------------------------------------------------------------------------
# Endpoints adicionais do mod-wssei v2
# Todos disponíveis desde mod-wssei 2.0.0 (SEI 4.0.x), exceto:
#   - sei_listar_relacionamentos → requer mod-wssei 3.0.2+ (SEI 5.0.x)
# Se um endpoint falhar, use sei_versao para verificar a versão instalada.
# Compatibilidade: SEI 4.0.x=mod-wssei 2.0.x | SEI 4.1.1=2.2.0 | SEI 5.0.x=3.0.x
# ---------------------------------------------------------------------------


# -- Sistema / Informações --


@mcp.tool()
async def sei_versao(ctx: Context) -> str:
    """Retorna a versão do SEI e do módulo wssei instalado.

    Útil para verificar compatibilidade de funcionalidades.
    Disponível desde mod-wssei 2.0.0 (SEI 4.0.x).
    Se falhar com erro inesperado, use sei_versao para verificar a versão instalada.
    """
    try:
        client = _get_client(ctx)
        result = await client.versao()
        return _json(result)
    except Exception as e:
        return _error(str(e))


@mcp.tool()
async def sei_listar_orgaos(ctx: Context) -> str:
    """Lista os órgãos cadastrados na instalação do SEI.

    Disponível desde mod-wssei 2.0.0 (SEI 4.0.x).
    Se falhar com erro inesperado, use sei_versao para verificar a versão instalada.
    """
    try:
        client = _get_client(ctx)
        result = await client.listar_orgaos()
        return _json(result)
    except Exception as e:
        return _error(str(e))


@mcp.tool()
async def sei_listar_contextos(id_orgao: str, ctx: Context) -> str:
    """Lista os contextos disponíveis para um órgão.

    Disponível desde mod-wssei 2.0.0 (SEI 4.0.x).
    Se falhar com erro inesperado, use sei_versao para verificar a versão instalada.
    """
    try:
        client = _get_client(ctx)
        result = await client.listar_contextos(id_orgao)
        return _json(result)
    except Exception as e:
        return _error(str(e))


# -- Usuários --


@mcp.tool()
async def sei_pesquisar_usuarios(
    filtro: str = "",
    id_orgao: str = "",
    limit: int = 50,
    pagina: int = 0,
    ctx: Context = None,
) -> str:
    """Pesquisa usuários por palavra-chave no órgão.

    Diferente de sei_listar_usuarios (que lista por unidade),
    este pesquisa no servidor por nome/sigla em todo o órgão.
    Disponível desde mod-wssei 2.0.0 (SEI 4.0.x).
    Se falhar com erro inesperado, use sei_versao para verificar a versão instalada.
    """
    try:
        client = _get_client(ctx)
        result = await client.pesquisar_usuarios(
            filtro=filtro, id_orgao=id_orgao, limit=limit, start=pagina
        )
        return _json(result)
    except Exception as e:
        return _error(str(e))


# -- Unidades --


@mcp.tool()
async def sei_pesquisar_outras_unidades(
    filtro: str = "",
    limit: int = 50,
    pagina: int = 0,
    ctx: Context = None,
) -> str:
    """Pesquisa unidades excluindo a unidade atual.

    Útil para tramitação — já filtra a unidade do usuário.
    Disponível desde mod-wssei 2.0.0 (SEI 4.0.x).
    Se falhar com erro inesperado, use sei_versao para verificar a versão instalada.
    """
    try:
        client = _get_client(ctx)
        result = await client.pesquisar_outras_unidades(
            filtro=filtro, limit=limit, start=pagina
        )
        return _json(result)
    except Exception as e:
        return _error(str(e))


@mcp.tool()
async def sei_pesquisar_textos_padrao(
    filtro: str = "",
    limit: int = 50,
    pagina: int = 0,
    ctx: Context = None,
) -> str:
    """Pesquisa textos padrão internos disponíveis na unidade.

    Textos padrão são modelos reutilizáveis para preencher documentos
    automaticamente ao criar um novo documento interno.
    Disponível desde mod-wssei 2.0.0 (SEI 4.0.x).
    Se falhar com erro inesperado, use sei_versao para verificar a versão instalada.
    """
    try:
        client = _get_client(ctx)
        result = await client.pesquisar_textos_padrao(
            filtro=filtro, limit=limit, start=pagina
        )
        return _json(result)
    except Exception as e:
        return _error(str(e))


# -- Documentos --


@mcp.tool()
async def sei_consultar_documento_externo(
    id_documento: str,
    ctx: Context = None,
) -> str:
    """Consulta metadados de um documento externo pelo ID.

    Retorna informações como tipo, data, nível de acesso, etc.
    Para baixar o conteúdo use sei_baixar_anexo ou sei_ler_documento.
    Disponível desde mod-wssei 2.0.0 (SEI 4.0.x).
    Se falhar com erro inesperado, use sei_versao para verificar a versão instalada.
    """
    try:
        client = _get_client(ctx)
        result = await client.consultar_documento_externo(id_documento)
        return _json(result)
    except Exception as e:
        return _error(str(e))


@mcp.tool()
async def sei_alterar_documento_interno(
    id_documento: str,
    descricao: str = "",
    nivel_acesso: str = "",
    hipotese_legal: str = "",
    ctx: Context = None,
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
    try:
        client = _get_client(ctx)
        result = await client.alterar_documento_interno(
            id_documento=id_documento,
            descricao=descricao,
            nivel_acesso=nivel_acesso,
            id_hipotese_legal=hipotese_legal,
        )
        return _json(result)
    except Exception as e:
        return _error(str(e))


@mcp.tool()
async def sei_alterar_documento_externo(
    id_documento: str,
    descricao: str = "",
    nivel_acesso: str = "",
    hipotese_legal: str = "",
    arquivo_path: str = "",
    ctx: Context = None,
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
    try:
        client = _get_client(ctx)
        result = await client.alterar_documento_externo(
            id_documento=id_documento,
            descricao=descricao,
            nivel_acesso=nivel_acesso,
            id_hipotese_legal=hipotese_legal,
            arquivo_path=arquivo_path,
        )
        return _json(result)
    except Exception as e:
        return _error(str(e))


@mcp.tool()
async def sei_pesquisar_tipos_conferencia(
    filtro: str = "",
    limit: int = 50,
    pagina: int = 0,
    ctx: Context = None,
) -> str:
    """Pesquisa tipos de conferência para documentos externos.

    Tipo de conferência indica se o documento externo é cópia autenticada,
    cópia simples, original, etc.
    Disponível desde mod-wssei 2.0.0 (SEI 4.0.x).
    Se falhar com erro inesperado, use sei_versao para verificar a versão instalada.
    """
    try:
        client = _get_client(ctx)
        result = await client.pesquisar_tipos_conferencia(
            filtro=filtro, limit=limit, start=pagina
        )
        return _json(result)
    except Exception as e:
        return _error(str(e))


@mcp.tool()
async def sei_sugestao_assuntos_documento(
    id_serie: str,
    ctx: Context = None,
) -> str:
    """Lista sugestões de assuntos para um tipo de documento (série).

    Use o id_serie obtido via sei_pesquisar_tipos_documento.
    Disponível desde mod-wssei 2.0.0 (SEI 4.0.x).
    Se falhar com erro inesperado, use sei_versao para verificar a versão instalada.
    """
    try:
        client = _get_client(ctx)
        result = await client.sugestao_assuntos_documento(id_serie)
        return _json(result)
    except Exception as e:
        return _error(str(e))


@mcp.tool()
async def sei_listar_blocos_documento(
    id_documento: str,
    ctx: Context = None,
) -> str:
    """Lista blocos de assinatura em que um documento está incluído.

    Disponível desde mod-wssei 2.0.0 (SEI 4.0.x).
    Se falhar com erro inesperado, use sei_versao para verificar a versão instalada.
    """
    try:
        client = _get_client(ctx)
        result = await client.listar_blocos_documento(id_documento)
        return _json(result)
    except Exception as e:
        return _error(str(e))


@mcp.tool()
async def sei_pesquisar_tipos_documento_externo(
    filtro: str = "",
    limit: int = 50,
    pagina: int = 0,
    ctx: Context = None,
) -> str:
    """Pesquisa tipos de documento para documentos externos (séries externas).

    Diferente de sei_pesquisar_tipos_documento que lista todos os tipos,
    este retorna apenas os tipos aplicáveis a documentos externos.
    Disponível desde mod-wssei 2.0.0 (SEI 4.0.x).
    Se falhar com erro inesperado, use sei_versao para verificar a versão instalada.
    """
    try:
        client = _get_client(ctx)
        result = await client.pesquisar_tipos_documento_externo(
            filtro=filtro, limit=limit, start=pagina
        )
        return _json(result)
    except Exception as e:
        return _error(str(e))


@mcp.tool()
async def sei_parametros_upload(ctx: Context) -> str:
    """Retorna parâmetros de upload do SEI (extensões permitidas, tamanhos máximos).

    Útil antes de criar documentos externos para saber os limites.
    Disponível desde mod-wssei 2.0.0 (SEI 4.0.x).
    Se falhar com erro inesperado, use sei_versao para verificar a versão instalada.
    """
    try:
        client = _get_client(ctx)
        result = await client.parametros_upload()
        return _json(result)
    except Exception as e:
        return _error(str(e))


# -- Processos: assuntos, atribuição, acesso, relacionamentos --


@mcp.tool()
async def sei_pesquisar_assuntos(
    filtro: str = "",
    limit: int = 50,
    pagina: int = 0,
    ctx: Context = None,
) -> str:
    """Pesquisa assuntos disponíveis para processos.

    Use o ID retornado no campo 'assuntos' ao criar processos.
    Disponível desde mod-wssei 2.0.0 (SEI 4.0.x).
    Se falhar com erro inesperado, use sei_versao para verificar a versão instalada.
    """
    try:
        client = _get_client(ctx)
        result = await client.pesquisar_assuntos(
            filtro=filtro, limit=limit, start=pagina
        )
        return _json(result)
    except Exception as e:
        return _error(str(e))


@mcp.tool()
async def sei_sugestao_assuntos_processo(
    id_tipo_processo: str,
    ctx: Context = None,
) -> str:
    """Lista sugestões de assuntos para um tipo de processo.

    Use o id do tipo obtido via sei_pesquisar_tipos_processo.
    Disponível desde mod-wssei 2.0.0 (SEI 4.0.x).
    Se falhar com erro inesperado, use sei_versao para verificar a versão instalada.
    """
    try:
        client = _get_client(ctx)
        result = await client.sugestao_assuntos_processo(id_tipo_processo)
        return _json(result)
    except Exception as e:
        return _error(str(e))


@mcp.tool()
async def sei_consultar_atribuicao(
    processo: str,
    ctx: Context = None,
) -> str:
    """Consulta a atribuição atual de um processo (quem está responsável).

    Disponível desde mod-wssei 2.0.0 (SEI 4.0.x).
    Se falhar com erro inesperado, use sei_versao para verificar a versão instalada.
    """
    try:
        client = _get_client(ctx)
        id_proc = await _resolver_processo(client, processo)
        result = await client.consultar_atribuicao(id_proc)
        return _json(result)
    except Exception as e:
        return _error(str(e))


@mcp.tool()
async def sei_verificar_acesso(
    processo: str,
    ctx: Context = None,
) -> str:
    """Verifica se o usuário tem acesso a um processo.

    Útil para checar permissão antes de operações em processos restritos.
    Disponível desde mod-wssei 2.0.0 (SEI 4.0.x).
    Se falhar com erro inesperado, use sei_versao para verificar a versão instalada.
    """
    try:
        client = _get_client(ctx)
        id_proc = await _resolver_processo(client, processo)
        result = await client.verificar_acesso(id_proc)
        return _json(result)
    except Exception as e:
        return _error(str(e))


@mcp.tool()
async def sei_listar_relacionamentos(
    processo: str,
    ctx: Context = None,
) -> str:
    """Lista processos relacionados a um processo.

    REQUER mod-wssei 3.0.2+ (SEI 5.0.x). Não disponível em versões anteriores.
    Se falhar, use sei_versao para verificar. Precisa ser >= 3.0.2.
    """
    try:
        client = _get_client(ctx)
        id_proc = await _resolver_processo(client, processo)
        result = await client.listar_relacionamentos(id_proc)
        return _json(result)
    except Exception as e:
        return _error(str(e))


@mcp.tool()
async def sei_listar_atividades(
    processo: str,
    ctx: Context = None,
) -> str:
    """Lista o histórico de atividades/andamentos de um processo.

    Implementação via scraper web (procedimento_consultar_historico.php).
    Retorna todas as ações registradas (tramitações, assinaturas, edições, etc.)
    com data/hora, unidade, usuário e descrição.

    Aceita protocolo formatado (ex: 50300.000123/2025-00).
    """
    try:
        web = _get_web_client(ctx)
        if web._inbox_url is None:
            await web.login()
        result = await web.listar_atividades(processo)
        return _json(result)
    except Exception as e:
        return _error(str(e))


# -- Acompanhamento: meus, da unidade, alterar --


@mcp.tool()
async def sei_listar_meus_acompanhamentos(
    limit: int = 50,
    pagina: int = 0,
    ctx: Context = None,
) -> str:
    """Lista processos que o usuário está acompanhando (acompanhamento especial).

    Disponível desde mod-wssei 2.0.0 (SEI 4.0.x).
    Se falhar com erro inesperado, use sei_versao para verificar a versão instalada.
    """
    try:
        client = _get_client(ctx)
        result = await client.listar_meus_acompanhamentos(limit=limit, start=pagina)
        return _json(result)
    except Exception as e:
        return _error(str(e))


@mcp.tool()
async def sei_listar_acompanhamentos_unidade(
    limit: int = 50,
    pagina: int = 0,
    ctx: Context = None,
) -> str:
    """Lista processos com acompanhamento especial na unidade atual.

    Disponível desde mod-wssei 2.0.0 (SEI 4.0.x).
    Se falhar com erro inesperado, use sei_versao para verificar a versão instalada.
    """
    try:
        client = _get_client(ctx)
        result = await client.listar_acompanhamentos_unidade(limit=limit, start=pagina)
        return _json(result)
    except Exception as e:
        return _error(str(e))


@mcp.tool()
async def sei_alterar_acompanhamento(
    processo: str,
    grupo: str = "",
    observacao: str = "",
    ctx: Context = None,
) -> str:
    """Altera acompanhamento especial de um processo.

    - processo: protocolo formatado ou IdProcedimento
    - grupo: novo grupo de acompanhamento
    - observacao: nova observação

    Disponível desde mod-wssei 2.0.0 (SEI 4.0.x).
    Se falhar com erro inesperado, use sei_versao para verificar a versão instalada.
    """
    try:
        client = _get_client(ctx)
        id_proc = await _resolver_processo(client, processo)
        result = await client.alterar_acompanhamento(id_proc, grupo, observacao)
        return _json(result)
    except Exception as e:
        return _error(str(e))


# -- Credenciamento (processos sigilosos) --


@mcp.tool()
async def sei_listar_credenciamentos(
    processo: str,
    ctx: Context = None,
) -> str:
    """Lista credenciamentos de acesso a um processo sigiloso.

    Disponível desde mod-wssei 2.0.0 (SEI 4.0.x).
    Se falhar com erro inesperado, use sei_versao para verificar a versão instalada.
    """
    try:
        client = _get_client(ctx)
        id_proc = await _resolver_processo(client, processo)
        result = await client.listar_credenciamentos(id_proc)
        return _json(result)
    except Exception as e:
        return _error(str(e))


@mcp.tool()
async def sei_conceder_credenciamento(
    processo: str,
    id_usuario: str,
    ctx: Context = None,
) -> str:
    """Concede credenciamento de acesso a um processo sigiloso para um usuário.

    Disponível desde mod-wssei 2.0.0 (SEI 4.0.x).
    Se falhar com erro inesperado, use sei_versao para verificar a versão instalada.
    """
    try:
        client = _get_client(ctx)
        id_proc = await _resolver_processo(client, processo)
        result = await client.conceder_credenciamento(id_proc, id_usuario)
        return _json(result)
    except Exception as e:
        return _error(str(e))


@mcp.tool()
async def sei_renunciar_credenciamento(
    processo: str,
    ctx: Context = None,
) -> str:
    """Renuncia ao credenciamento de acesso a um processo sigiloso.

    O próprio usuário perde o acesso ao processo.
    Disponível desde mod-wssei 2.0.0 (SEI 4.0.x).
    Se falhar com erro inesperado, use sei_versao para verificar a versão instalada.
    """
    try:
        client = _get_client(ctx)
        id_proc = await _resolver_processo(client, processo)
        result = await client.renunciar_credenciamento(id_proc)
        return _json(result)
    except Exception as e:
        return _error(str(e))


@mcp.tool()
async def sei_cassar_credenciamento(
    processo: str,
    id_usuario: str,
    ctx: Context = None,
) -> str:
    """Cassa (revoga) credenciamento de acesso de um usuário a processo sigiloso.

    Disponível desde mod-wssei 2.0.0 (SEI 4.0.x).
    Se falhar com erro inesperado, use sei_versao para verificar a versão instalada.
    """
    try:
        client = _get_client(ctx)
        id_proc = await _resolver_processo(client, processo)
        result = await client.cassar_credenciamento(id_proc, id_usuario)
        return _json(result)
    except Exception as e:
        return _error(str(e))


# -- Assinantes e Observação --


@mcp.tool()
async def sei_listar_assinantes(ctx: Context) -> str:
    """Lista signatários (cargos/funções) disponíveis na unidade atual.

    Retorna os cargos que podem ser usados em sei_assinar_documento.
    Disponível desde mod-wssei 2.0.0 (SEI 4.0.x).
    Se falhar com erro inesperado, use sei_versao para verificar a versão instalada.
    """
    try:
        client = _get_client(ctx)
        result = await client.listar_assinantes()
        return _json(result)
    except Exception as e:
        return _error(str(e))


@mcp.tool()
async def sei_listar_orgaos_assinante(ctx: Context) -> str:
    """Lista órgãos disponíveis para assinatura.

    Disponível desde mod-wssei 2.0.0 (SEI 4.0.x).
    Se falhar com erro inesperado, use sei_versao para verificar a versão instalada.
    """
    try:
        client = _get_client(ctx)
        result = await client.listar_orgaos_assinante()
        return _json(result)
    except Exception as e:
        return _error(str(e))


@mcp.tool()
async def sei_criar_observacao(
    processo: str,
    descricao: str,
    ctx: Context = None,
) -> str:
    """Cria observação da unidade em um processo.

    Diferente da anotação (post-it individual), a observação é
    vinculada à unidade e visível por todos os usuários da unidade.
    Disponível desde mod-wssei 2.0.0 (SEI 4.0.x).
    Se falhar com erro inesperado, use sei_versao para verificar a versão instalada.
    """
    try:
        client = _get_client(ctx)
        id_proc = await _resolver_processo(client, processo)
        result = await client.criar_observacao(id_proc, descricao)
        return _json(result)
    except Exception as e:
        return _error(str(e))


@mcp.tool()
async def sei_criar_contato(
    nome: str,
    tipo: str = "",
    email: str = "",
    telefone: str = "",
    ctx: Context = None,
) -> str:
    """Cria novo contato no SEI.

    Disponível desde mod-wssei 2.0.0 (SEI 4.0.x).
    Se falhar com erro inesperado, use sei_versao para verificar a versão instalada.
    """
    try:
        client = _get_client(ctx)
        result = await client.criar_contato(
            nome=nome, tipo=tipo, email=email, telefone=telefone
        )
        return _json(result)
    except Exception as e:
        return _error(str(e))


# -- Modelos de documento --


@mcp.tool()
async def sei_listar_grupos_modelos(
    limit: int = 50,
    pagina: int = 0,
    ctx: Context = None,
) -> str:
    """Lista grupos de modelos de documento disponíveis.

    Disponível desde mod-wssei 2.0.0 (SEI 4.0.x).
    Se falhar com erro inesperado, use sei_versao para verificar a versão instalada.
    """
    try:
        client = _get_client(ctx)
        result = await client.listar_grupos_modelos(limit=limit, start=pagina)
        return _json(result)
    except Exception as e:
        return _error(str(e))


@mcp.tool()
async def sei_listar_modelos(
    id_grupo: str = "",
    filtro: str = "",
    limit: int = 50,
    pagina: int = 0,
    ctx: Context = None,
) -> str:
    """Lista modelos de documento disponíveis.

    - id_grupo: filtrar por grupo (use sei_listar_grupos_modelos)
    - filtro: texto para filtrar por nome

    Disponível desde mod-wssei 2.0.0 (SEI 4.0.x).
    Se falhar com erro inesperado, use sei_versao para verificar a versão instalada.
    """
    try:
        client = _get_client(ctx)
        result = await client.listar_modelos(
            id_grupo=id_grupo, filtro=filtro, limit=limit, start=pagina
        )
        return _json(result)
    except Exception as e:
        return _error(str(e))


# -- Marcador: desativar, reativar, histórico --


@mcp.tool()
async def sei_desativar_marcador(
    ids_marcadores: str,
    ctx: Context = None,
) -> str:
    """Desativa marcador(es) sem excluir. IDs separados por vírgula.

    Marcadores desativados deixam de aparecer nas pesquisas mas
    mantêm o histórico. Use sei_reativar_marcador para reativar.
    """
    try:
        client = _get_client(ctx)
        result = await client.desativar_marcadores(ids_marcadores)
        return _json(result)
    except Exception as e:
        return _error(str(e))


@mcp.tool()
async def sei_reativar_marcador(
    ids_marcadores: str,
    ctx: Context = None,
) -> str:
    """Reativa marcador(es) desativados. IDs separados por vírgula."""
    try:
        client = _get_client(ctx)
        result = await client.reativar_marcadores(ids_marcadores)
        return _json(result)
    except Exception as e:
        return _error(str(e))


@mcp.tool()
async def sei_historico_marcador_processo(
    processo: str,
    ctx: Context = None,
) -> str:
    """Lista histórico de marcadores de um processo.

    Mostra quais marcadores foram aplicados/removidos ao longo do tempo.
    Disponível desde mod-wssei 2.0.0 (SEI 4.0.x).
    Se falhar com erro inesperado, use sei_versao para verificar a versão instalada.
    """
    try:
        client = _get_client(ctx)
        id_proc = await _resolver_processo(client, processo)
        result = await client.historico_marcador_processo(id_proc)
        return _json(result)
    except Exception as e:
        return _error(str(e))


# -- Bloco Interno: operações adicionais --


@mcp.tool()
async def sei_listar_processos_bloco_interno(
    id_bloco: str,
    ctx: Context = None,
) -> str:
    """Lista processos de um bloco interno.

    Disponível desde mod-wssei 2.0.0 (SEI 4.0.x).
    Se falhar com erro inesperado, use sei_versao para verificar a versão instalada.
    """
    try:
        client = _get_client(ctx)
        result = await client.listar_processos_bloco_interno(id_bloco)
        return _json(result)
    except Exception as e:
        return _error(str(e))


@mcp.tool()
async def sei_alterar_bloco_interno(
    id_bloco: str,
    descricao: str,
    ctx: Context = None,
) -> str:
    """Altera descrição de um bloco interno.

    Disponível desde mod-wssei 2.0.0 (SEI 4.0.x).
    Se falhar com erro inesperado, use sei_versao para verificar a versão instalada.
    """
    try:
        client = _get_client(ctx)
        result = await client.alterar_bloco_interno(id_bloco, descricao)
        return _json(result)
    except Exception as e:
        return _error(str(e))


@mcp.tool()
async def sei_excluir_bloco_interno(
    ids_blocos: str,
    ctx: Context = None,
) -> str:
    """Exclui bloco(s) interno(s). IDs separados por vírgula.

    Disponível desde mod-wssei 2.0.0 (SEI 4.0.x).
    Se falhar com erro inesperado, use sei_versao para verificar a versão instalada.
    """
    try:
        client = _get_client(ctx)
        result = await client.excluir_blocos_internos(ids_blocos)
        return _json(result)
    except Exception as e:
        return _error(str(e))


@mcp.tool()
async def sei_concluir_bloco_interno(
    ids_blocos: str,
    ctx: Context = None,
) -> str:
    """Conclui bloco(s) interno(s). IDs separados por vírgula.

    Disponível desde mod-wssei 2.0.0 (SEI 4.0.x).
    Se falhar com erro inesperado, use sei_versao para verificar a versão instalada.
    """
    try:
        client = _get_client(ctx)
        result = await client.concluir_blocos_internos(ids_blocos)
        return _json(result)
    except Exception as e:
        return _error(str(e))


@mcp.tool()
async def sei_reabrir_bloco_interno(
    id_bloco: str,
    ctx: Context = None,
) -> str:
    """Reabre bloco interno concluído.

    Disponível desde mod-wssei 2.0.0 (SEI 4.0.x).
    Se falhar com erro inesperado, use sei_versao para verificar a versão instalada.
    """
    try:
        client = _get_client(ctx)
        result = await client.reabrir_bloco_interno(id_bloco)
        return _json(result)
    except Exception as e:
        return _error(str(e))


@mcp.tool()
async def sei_anotar_processo_bloco_interno(
    id_bloco: str,
    processo: str,
    descricao: str,
    ctx: Context = None,
) -> str:
    """Cria anotação em processo dentro de um bloco interno.

    Disponível desde mod-wssei 2.0.0 (SEI 4.0.x).
    Se falhar com erro inesperado, use sei_versao para verificar a versão instalada.
    """
    try:
        client = _get_client(ctx)
        id_proc = await _resolver_processo(client, processo)
        result = await client.anotar_processo_bloco_interno(id_bloco, id_proc, descricao)
        return _json(result)
    except Exception as e:
        return _error(str(e))


@mcp.tool()
async def sei_alterar_anotacao_bloco_interno(
    id_bloco: str,
    processo: str,
    descricao: str,
    ctx: Context = None,
) -> str:
    """Altera anotação de processo em um bloco interno.

    Disponível desde mod-wssei 2.0.0 (SEI 4.0.x).
    Se falhar com erro inesperado, use sei_versao para verificar a versão instalada.
    """
    try:
        client = _get_client(ctx)
        id_proc = await _resolver_processo(client, processo)
        result = await client.alterar_anotacao_bloco_interno(id_bloco, id_proc, descricao)
        return _json(result)
    except Exception as e:
        return _error(str(e))


# -- Bloco de Assinatura: operações adicionais --


@mcp.tool()
async def sei_listar_documentos_bloco_assinatura(
    id_bloco: str,
    ctx: Context = None,
) -> str:
    """Lista documentos de um bloco de assinatura."""
    try:
        client = _get_client(ctx)
        result = await client.listar_documentos_bloco_assinatura(id_bloco)
        return _json(result)
    except Exception as e:
        return _error(str(e))


@mcp.tool()
async def sei_retirar_documentos_bloco_assinatura(
    id_bloco: str,
    documentos: str,
    ctx: Context = None,
) -> str:
    """Retira documento(s) de um bloco de assinatura.

    - documentos: ID(s) de documento(s) separados por vírgula
    """
    try:
        client = _get_client(ctx)
        result = await client.retirar_documento_bloco_assinatura(id_bloco, documentos)
        return _json(result)
    except Exception as e:
        return _error(str(e))


@mcp.tool()
async def sei_alterar_bloco_assinatura(
    id_bloco: str,
    descricao: str,
    ctx: Context = None,
) -> str:
    """Altera descrição de um bloco de assinatura.

    Disponível desde mod-wssei 2.0.0 (SEI 4.0.x).
    Se falhar com erro inesperado, use sei_versao para verificar a versão instalada.
    """
    try:
        client = _get_client(ctx)
        result = await client.alterar_bloco_assinatura(id_bloco, descricao)
        return _json(result)
    except Exception as e:
        return _error(str(e))


@mcp.tool()
async def sei_excluir_bloco_assinatura(
    ids_blocos: str,
    ctx: Context = None,
) -> str:
    """Exclui bloco(s) de assinatura. IDs separados por vírgula.

    Disponível desde mod-wssei 2.0.0 (SEI 4.0.x).
    Se falhar com erro inesperado, use sei_versao para verificar a versão instalada.
    """
    try:
        client = _get_client(ctx)
        result = await client.excluir_blocos_assinatura(ids_blocos)
        return _json(result)
    except Exception as e:
        return _error(str(e))


@mcp.tool()
async def sei_concluir_bloco_assinatura(
    ids_blocos: str,
    ctx: Context = None,
) -> str:
    """Conclui bloco(s) de assinatura. IDs separados por vírgula.

    Disponível desde mod-wssei 2.0.0 (SEI 4.0.x).
    Se falhar com erro inesperado, use sei_versao para verificar a versão instalada.
    """
    try:
        client = _get_client(ctx)
        result = await client.concluir_blocos_assinatura(ids_blocos)
        return _json(result)
    except Exception as e:
        return _error(str(e))


@mcp.tool()
async def sei_reabrir_bloco_assinatura(
    id_bloco: str,
    ctx: Context = None,
) -> str:
    """Reabre bloco de assinatura concluído.

    Disponível desde mod-wssei 2.0.0 (SEI 4.0.x).
    Se falhar com erro inesperado, use sei_versao para verificar a versão instalada.
    """
    try:
        client = _get_client(ctx)
        result = await client.reabrir_bloco_assinatura(id_bloco)
        return _json(result)
    except Exception as e:
        return _error(str(e))


@mcp.tool()
async def sei_retornar_bloco_assinatura(
    id_bloco: str,
    ctx: Context = None,
) -> str:
    """Retorna bloco de assinatura para a unidade de origem.

    Disponível desde mod-wssei 2.0.0 (SEI 4.0.x).
    Se falhar com erro inesperado, use sei_versao para verificar a versão instalada.
    """
    try:
        client = _get_client(ctx)
        result = await client.retornar_bloco_assinatura(id_bloco)
        return _json(result)
    except Exception as e:
        return _error(str(e))


@mcp.tool()
async def sei_anotar_documento_bloco_assinatura(
    id_bloco: str,
    documento: str,
    descricao: str,
    ctx: Context = None,
) -> str:
    """Cria anotação em documento dentro de um bloco de assinatura.

    Disponível desde mod-wssei 2.0.0 (SEI 4.0.x).
    Se falhar com erro inesperado, use sei_versao para verificar a versão instalada.
    """
    try:
        client = _get_client(ctx)
        result = await client.anotar_documento_bloco_assinatura(id_bloco, documento, descricao)
        return _json(result)
    except Exception as e:
        return _error(str(e))


@mcp.tool()
async def sei_alterar_anotacao_bloco_assinatura(
    id_bloco: str,
    documento: str,
    descricao: str,
    ctx: Context = None,
) -> str:
    """Altera anotação de documento em um bloco de assinatura.

    Disponível desde mod-wssei 2.0.0 (SEI 4.0.x).
    Se falhar com erro inesperado, use sei_versao para verificar a versão instalada.
    """
    try:
        client = _get_client(ctx)
        result = await client.alterar_anotacao_bloco_assinatura(id_bloco, documento, descricao)
        return _json(result)
    except Exception as e:
        return _error(str(e))


def main():
    if _http_mode:
        import uvicorn
        from pathlib import Path
        from starlette.routing import Route
        from starlette.responses import Response

        from mcp_seipro.auth import login_page, login_submit

        # Favicon / ícone do SEI Pro — busca em vários locais possíveis
        _icon_bytes = b""
        for _candidate in [
            Path(__file__).resolve().parent.parent.parent / "icon.png",  # dev: repo root
            Path("/app/icon.png"),  # Docker
        ]:
            if _candidate.exists():
                _icon_bytes = _candidate.read_bytes()
                break

        from starlette.responses import HTMLResponse

        async def favicon(request):
            return Response(_icon_bytes, media_type="image/png",
                            headers={"Cache-Control": "public, max-age=86400"})

        _base = os.environ.get("BASE_URL", f"http://localhost:{_http_port}")
        _root_html = f"""<!DOCTYPE html>
<html><head>
<link rel="icon" type="image/png" href="{_base}/favicon.ico">
<link rel="icon" type="image/png" sizes="128x128" href="{_base}/icon.png">
<link rel="apple-touch-icon" href="{_base}/icon.png">
<title>SEI Pro MCP Server</title>
</head><body><h1>SEI Pro MCP Server</h1></body></html>"""

        async def root_page(request):
            return HTMLResponse(_root_html)

        app = mcp.streamable_http_app()
        # Adiciona rotas extras
        app.routes.insert(0, Route("/", root_page, methods=["GET"]))
        app.routes.insert(1, Route("/favicon.ico", favicon, methods=["GET"]))
        app.routes.insert(2, Route("/icon.png", favicon, methods=["GET"]))
        app.routes.insert(3, Route("/login", login_page, methods=["GET"]))
        app.routes.insert(4, Route("/login", login_submit, methods=["POST"]))

        config = uvicorn.Config(
            app,
            host="0.0.0.0",
            port=_http_port,
            log_level="info",
        )
        import anyio
        anyio.run(uvicorn.Server(config).serve)
    else:
        mcp.run(transport="stdio")
