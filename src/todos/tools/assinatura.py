"""Tools de assinatura eletrônica e ciência de documentos/processos.

Reúne o domínio de assinatura: assinar documento, assinar bloco (todos ou
documentos específicos), cancelar assinatura e listar assinaturas; mais ciência
(dar ciência e listar ciências) em documentos ou processos.

Sem `from __future__ import annotations`: o FastMCP introspecta os type hints em
tempo de execução para montar o schema de cada tool, então as anotações precisam
ser objetos reais (não strings adiadas).
"""

import html
import logging
from typing import TYPE_CHECKING, Literal

import httpx
from fastmcp import Context

from todos.backends.choice import requires_backend
from todos.exceptions import SEIConnectionError, SEIError, SEIValidationError
from todos.html_utils import sanitize_iso8859
from todos.mcp_app import (
    _DEST,
    _IDEM,
    _READ,
    _WRITE,
    _backend,
    _json,
    _rest_backend,
    mcp,
)
from todos.responses import AssinaturaSEI, NextAction, PaginadoGenerico

if TYPE_CHECKING:
    from todos.backends.base import SEIBackend

logger = logging.getLogger(__name__)


def _exigir_cargo(cargos: object) -> SEIValidationError:
    """Erro de cargo obrigatório, com as opções disponíveis embutidas na mensagem."""
    itens = cargos if isinstance(cargos, list) else []
    nomes = ", ".join(str(c) for c in itens) if itens else "(nenhum retornado)"
    return SEIValidationError(
        "Cargo/Função não informado — obrigatório para assinar. "
        f"Cargos/funções disponíveis: {nomes}. Pergunte ao usuário qual usar e "
        "reutilize a escolha nas próximas assinaturas desta conversa."
    )


async def _validar_cargo(backend: "SEIBackend", cargo: str) -> None:
    """Valida que cargo não está vazio; levanta SEIValidationError com lista de opções.

    No backend web, ``listar_assinantes`` não está implementado (não existe
    endpoint equivalente ao REST) e o form de assinatura já vem com um cargo
    padrão pré-selecionado pelo próprio SEI — então cargo vazio ali não é
    erro, é "use o padrão do form". Só o REST exige cargo explícito.
    """
    if not cargo:
        try:
            cargos = await backend.listar_assinantes()
        except NotImplementedError:
            logger.debug(
                "_validar_cargo: listar_assinantes não implementado neste backend — pulando validação"
            )
            return
        except SEIConnectionError:
            raise
        except (SEIError, httpx.HTTPError) as exc:
            logger.warning(
                "_validar_cargo: listar_assinantes falhou — cargo obrigatório mas lista indisponível: %s",
                exc,
            )
            cargos = []
        raise _exigir_cargo(cargos)


@mcp.tool(annotations=_WRITE)
@requires_backend
async def sei_cancelar_assinatura(
    id_documento: str,
    processo: str | None = None,
    ctx: Context | None = None,
) -> str:
    """Tenta cancelar (derrubar) a assinatura de um documento no SEI.

    Aceita id interno ou número SEI (protocoloFormatado).

    A API do SEI não possui endpoint direto para cancelar assinatura.
    Esta tool tenta forçar uma edição mínima no documento para que o
    SEI remova a assinatura automaticamente (comportamento padrão ao editar).

    LIMITAÇÃO IMPORTANTE: só é possível enquanto o processo está exclusivamente
    na unidade geradora e ainda NÃO foi lido nem enviado para outra unidade.
    Uma vez lido ou tramitado, o documento fica travado e a assinatura NÃO pode
    mais ser cancelada — por nenhum meio, nem pela interface web do SEI.

    - processo: protocolo do processo (necessário em instâncias sem mod-wssei)

    Orquestração: o SEI não expõe "cancelar assinatura" como op; a tool força uma
    edição mínima (derruba a assinatura) compondo listar_secoes + alterar_secoes
    pelo backend composto. Se o documento estiver travado (processo já lido/
    enviado), o SEI rejeita a edição e o erro original propaga ao agente — é um
    ToolError com a mensagem do próprio SEI.
    """
    backend = await _backend(ctx)

    # Resolver número SEI → id interno via backend composto (best-effort)
    doc_id = id_documento.strip()
    try:
        doc_id, _ = await backend.resolver_documento(doc_id)
    except (SEIError, httpx.HTTPError) as exc:
        logger.warning(
            "Resolução do documento falhou (%s) — usando referência original: %s",
            exc,
            doc_id,
        )

    # Verificar se está assinado e capturar a versão atual
    secoes_data = await backend.listar_secoes(doc_id, processo=processo)
    versao = str(secoes_data.get("ultimaVersaoDocumento", "1"))

    # Montar payload com todas as seções (mesmo conteúdo)
    secoes_enviar = []
    for s in secoes_data.get("secoes", []):
        if not isinstance(s, dict):
            continue
        conteudo = html.unescape(s.get("conteudo", "") or "")
        secoes_enviar.append(
            {
                "id": str(s.get("id")),
                "idSecaoModelo": str(s.get("idSecaoModelo")),
                "conteudo": sanitize_iso8859(conteudo),
            }
        )

    # Editar derruba a assinatura se o documento ainda puder ser editado. Se
    # estiver travado (processo lido/enviado), o SEI rejeita e o erro propaga.
    await backend.alterar_secoes(doc_id, secoes_enviar, versao, processo=processo)
    return _json(
        {"mensagem": "Assinatura cancelada com sucesso. O documento foi editado (nova versão)."}
    )


@mcp.tool(annotations=_DEST)
@requires_backend
async def sei_assinar_documento(
    id_documento: str,
    cargo: str = "",
    orgao: str = "",
    processo: str | None = None,
    ctx: Context | None = None,
) -> str:
    """Assina eletronicamente um documento no SEI.

    A autenticação é automática — basta informar o documento e o cargo.

    No backend REST, o parâmetro `cargo` é OBRIGATÓRIO — sem ele a assinatura
    falha. Se não souber o cargo, chame sem cargo para obter a lista de
    opções, pergunte ao usuário qual usar e chame de novo com o cargo
    escolhido. Grave o cargo escolhido para reutilizar nas próximas
    assinaturas.

    No backend web (instâncias sem mod-wssei), `cargo`/`orgao` são
    OPCIONAIS — o próprio formulário de assinatura do SEI já vem com um
    cargo e órgão padrão pré-selecionados para o usuário logado; omitir
    esses parâmetros usa esse padrão.

    IMPORTANTE (backend web): esta implementação foi escrita e revisada
    (lint/type-check) mas NUNCA foi executada contra uma instância real —
    a submissão exige reenviar a senha do usuário no próprio POST do form
    do SEI, e nenhuma sessão de desenvolvimento chegou a testá-la de fato.
    Trate o primeiro uso real com cautela e confirme o resultado no SEI.

    Parâmetros:
    - id_documento: ID interno do documento ou número SEI (protocoloFormatado).
      Se for número SEI, resolve automaticamente via pesquisa Solr (REST).
    - cargo: cargo/função para assinatura (ex: "Agente Público").
      Obrigatório no REST; opcional no web.
    - orgao: código do órgão (usa o padrão se omitido)
    - processo: protocolo do processo (necessário em instâncias sem mod-wssei)
    """
    backend = await _backend(ctx)
    await _validar_cargo(backend, cargo)
    result = await backend.assinar_documento(id_documento, cargo=cargo, orgao=orgao, processo=processo)
    return _json(result)


@mcp.tool(annotations=_READ)
@requires_backend
async def sei_listar_assinaturas(
    id_documento: str,
    processo: str | None = None,
    ctx: Context | None = None,
) -> PaginadoGenerico[AssinaturaSEI]:
    """Lista as assinaturas de um documento.

    - id_documento: id interno do documento
    - processo: protocolo do processo (necessário em instâncias sem mod-wssei)

    """
    backend = await _backend(ctx)
    raw = await backend.listar_assinaturas(id_documento, processo=processo)
    itens = [AssinaturaSEI.model_validate(a) for a in raw]
    tem_nao_assinado = any(not a.get("assinado", True) for a in raw if isinstance(a, dict))
    actions = (
        [
            NextAction(
                tool="sei_assinar_documento",
                args={"id_documento": id_documento},
                reason="Há assinaturas pendentes neste documento.",
            )
        ]
        if tem_nao_assinado
        else []
    )
    return PaginadoGenerico[AssinaturaSEI](
        total_itens=len(itens),
        proximo_cursor=None,
        itens=itens,
        next_actions=actions,
    )


@mcp.tool(annotations=_DEST)
async def sei_assinar_bloco(
    id_bloco: str,
    cargo: str = "",
    ctx: Context | None = None,
) -> str:
    """Assina TODOS os documentos de um bloco de assinatura.

    A autenticação é automática — basta informar o bloco e o cargo.

    IMPORTANTE: o parâmetro `cargo` é OBRIGATÓRIO. Sem ele a assinatura falha.
    Se não souber o cargo, chame sem cargo para ver a lista de opções.
    Pergunte ao usuário e grave o cargo para reutilizar na mesma conversa.

    - id_bloco: ID do bloco
    - cargo: cargo/função — OBRIGATÓRIO (se omitido, lista opções disponíveis)
    """
    backend = await _rest_backend(ctx)
    await _validar_cargo(backend, cargo)
    result = await backend.assinar_bloco(id_bloco, cargo=cargo)
    return _json(result)


@mcp.tool(annotations=_DEST)
async def sei_assinar_documentos_bloco(
    documentos: str,
    cargo: str = "",
    ctx: Context | None = None,
) -> str:
    """Assina documentos específicos de um bloco de assinatura.

    A autenticação é automática — basta informar os documentos e o cargo.

    IMPORTANTE: o parâmetro `cargo` é OBRIGATÓRIO. Sem ele a assinatura falha.
    Se não souber o cargo, chame sem cargo para ver a lista de opções.
    Pergunte ao usuário e grave o cargo para reutilizar na mesma conversa.

    - documentos: ID(s) de documento(s) separados por vírgula
    - cargo: cargo/função — OBRIGATÓRIO (se omitido, lista opções disponíveis)
    """
    backend = await _rest_backend(ctx)
    await _validar_cargo(backend, cargo)
    result = await backend.assinar_documentos_bloco(documentos, cargo=cargo)
    return _json(result)


@mcp.tool(annotations=_IDEM)
@requires_backend
async def sei_dar_ciencia(
    referencia: str,
    tipo: Literal["documento", "processo"] = "documento",
    ctx: Context | None = None,
) -> str:
    """Dá ciência em um documento ou processo no SEI.

    Parâmetros:
    - referencia: número SEI do documento OU protocolo/IdProcedimento do processo
    - tipo: "documento" (padrão) ou "processo"

    Exemplos:
    - sei_dar_ciencia("1482875", tipo="documento")  → ciência na NT 16
    - sei_dar_ciencia("50300.018905/2018-67", tipo="processo")  → ciência no processo

    QUANDO NÃO USAR: tipo "documento" exige mod-wssei (REST). Em instâncias
    sem mod-wssei, use tipo="processo" (disponível via web). Retorna {"ok": True}.
    """
    backend = await _backend(ctx)
    result = await backend.dar_ciencia(referencia, tipo=tipo)
    return _json(result)


@mcp.tool(annotations=_READ)
@requires_backend
async def sei_listar_ciencias(
    referencia: str,
    tipo: Literal["documento", "processo"] = "documento",
    processo: str | None = None,
    ctx: Context | None = None,
) -> str:
    """Lista as ciências registradas em um documento ou processo.

    Parâmetros:
    - referencia: número SEI do documento OU protocolo/IdProcedimento do processo
    - tipo: "documento" (padrão) ou "processo"
    - processo: protocolo do processo (necessário em instâncias sem mod-wssei quando tipo="documento")

    """
    backend = await _backend(ctx)
    ciencias = await backend.listar_ciencias(referencia, tipo=tipo, processo=processo)
    return _json(
        {
            "ciencias": ciencias,
            "_next": [
                {"tool": "sei_dar_ciencia", "args": {"referencia": referencia, "tipo": tipo}}
            ],
        }
    )
