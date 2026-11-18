"""Modelos de saída tipada das tools (MCP `outputSchema` + `structuredContent`).

Por que existe: sem anotação de retorno, toda tool devolvia `{"result": string}`
com JSON escapado dentro da string. O cliente (Claude.ai inclusive) entrega ao
modelo o `structuredContent`, então o modelo lia `\\n` e `\\"` à toa; e quem
gera tipos a partir do schema (ex.: Code Mode) não sabia a forma da resposta.

Regras de desenho, todas impostas pelo SDK MCP (FastMCP 1.30):

- O SDK valida o retorno contra o modelo (`model_validate`) e depois contra o
  JSON Schema. Retorno que não casa vira "Output validation error". Por isso
  falha de tool é EXCEÇÃO (`isError`), nunca um dict `{"error": ...}`.
- Pydantic descarta em silêncio o que o modelo não declara. Todos os modelos
  aceitam campos extras (`extra="allow"`): campo novo da wssei passa adiante
  em vez de sumir do `structuredContent`.
- A wssei devolve ids ora como string, ora como número, conforme a versão;
  `coerce_numbers_to_str` normaliza para string sem rejeitar a resposta.
- Campos com `_` inicial (`_nota`, `_aviso_acesso`) são alias: Pydantic não
  aceita nome de campo começando por sublinhado.
"""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class _Saida(BaseModel):
    model_config = ConfigDict(
        extra="allow",
        coerce_numbers_to_str=True,
        populate_by_name=True,
    )


# ---------------------------------------------------------------------------
# sei_listar_processos
# ---------------------------------------------------------------------------


class Atribuicao(_Saida):
    id_usuario: Optional[str] = Field(
        None, description="id do usuário; null quando a wssei só informa o nome")
    nome: str


class Marcador(_Saida):
    nome: str
    cor: Optional[str] = Field(None, description="nome da cor no SEI, minúsculo")


class ProcessoResumido(_Saida):
    id_procedimento: str = Field(description="id interno do processo (IdProcedimento)")
    protocolo: str = Field(description="número formatado, ex.: 50300.000123/2025-00")
    tipo: str = Field(description="tipo processual")
    descricao: str = Field(description="especificação, sem entidades HTML")
    acesso: Literal["publico", "restrito", "sigiloso", "desconhecido"]
    atribuido_unidade_atual: Optional[Atribuicao] = Field(
        None,
        description="atribuição na unidade da sessão; null sem atribuição nela "
                    "ou sem sei_trocar_unidade antes")
    gerado_ou_recebido: Literal["gerado", "recebido"]
    em_tramitacao: bool
    sobrestado: bool
    bloqueado: bool
    tem_documento_novo: bool
    tem_anotacao: bool
    tem_ciencia: bool
    marcador: Optional[Marcador] = None
    prazo: Optional[str] = Field(None, description="data ISO aaaa-mm-dd")
    aberto_em_unidades: list[str] = Field(
        default_factory=list, description="unidades onde o processo está aberto")
    ciencias: Optional[list[dict[str, Any]]] = Field(
        None, description="só com incluir_detalhe=true (payload da wssei)")
    anotacoes: Optional[list[dict[str, Any]]] = Field(
        None, description="só com incluir_detalhe=true (payload da wssei)")


class ListaProcessos(_Saida):
    processos: list[ProcessoResumido] = Field(
        default_factory=list, description="vazio com apenas_contar=true")
    pagina_atual: Optional[int] = None
    itens_pagina: Optional[int] = Field(None, description="itens nesta página, após o filtro `tipo`")
    total_itens: Optional[int] = Field(None, description="total na caixa (servidor, antes do filtro `tipo`)")
    tem_proxima: Optional[bool] = None
    itens_por_pagina: Optional[int] = Field(None, description="só com apenas_contar=true")
    paginas: Optional[int] = Field(None, description="só com apenas_contar=true")
    nota: Optional[str] = Field(None, alias="_nota")


# ---------------------------------------------------------------------------
# sei_listar_documentos
# ---------------------------------------------------------------------------


class DocumentoResumido(_Saida):
    id: str = Field(description="id interno — o que sei_ler_documento aceita")
    protocoloFormatado: str = Field(description="número SEI que o usuário vê")
    tipo: str = Field(description="tipo do documento, ex.: Despacho")
    tipo_documento: str = Field(
        description="staDocumento: I=editor interno, X=externo; formulário e "
                    "e-mail usam outras letras")
    unidade: str = Field(description="sigla da unidade geradora")
    nome: str
    assinado: bool
    cancelado: bool
    acesso: Literal["publico", "restrito", "sigiloso"]
    bruto: Optional[dict[str, Any]] = Field(
        None, description="item completo da wssei; só com resumido=false")


class ListaDocumentos(_Saida):
    id_procedimento: str
    total: Optional[int] = Field(None, description="total de documentos no processo (servidor)")
    retornados: int
    offset: int
    ordem: Literal["asc", "desc"]
    truncado: bool = Field(description="true se o processo passou do teto de páginas lidas")
    documentos: list[DocumentoResumido]


# ---------------------------------------------------------------------------
# sei_consultar_processo
# ---------------------------------------------------------------------------


class Assunto(_Saida):
    id: str
    codigo: Optional[str] = None
    descricao: Optional[str] = None


class Interessado(_Saida):
    id: str
    nome: Optional[str] = None


class Observacao(_Saida):
    unidade: Optional[str] = Field(None, description="id da unidade autora")
    observacao: Optional[str] = None


class ConsultaProcesso(_Saida):
    """Nomes de campo da wssei preservados (IdProcedimento etc.)."""

    IdProcedimento: str
    ProtocoloProcedimentoFormatado: Optional[str] = None
    NomeTipoProcedimento: Optional[str] = None
    especificacao: Optional[str] = None
    tipoProcesso: Optional[str] = Field(None, description="id do tipo processual")
    assuntos: list[Assunto] = Field(default_factory=list)
    interessados: list[Interessado] = Field(default_factory=list)
    observacoes: list[Observacao] = Field(default_factory=list)
    nivelAcesso: Optional[str] = Field(None, description="0=público, 1=restrito, 2=sigiloso")
    hipoteseLegal: Optional[str] = Field(None, description="id da hipótese legal")
    grauSigilo: Optional[str] = None
    aviso_acesso: Optional[dict[str, Any]] = Field(
        None, alias="_aviso_acesso",
        description="aviso informativo de classificação (restrito/sigiloso); "
                    "não é erro de permissão")
    warnings: Optional[list[str]] = Field(None, alias="_warnings")


# ---------------------------------------------------------------------------
# sei_ler_documento
# ---------------------------------------------------------------------------


class LeituraDocumento(_Saida):
    id_documento: str = Field(description="id interno efetivamente lido")
    tipo_documento: str = Field(description="I=interno, X=externo")
    formato: Literal["markdown", "texto", "html"]
    liberado: bool = Field(
        description="false quando o gate de acesso restrito reteve o conteúdo")
    conteudo: Optional[str] = Field(
        None,
        description="texto do documento; em restrito autorizado vem precedido "
                    "do aviso de classificação. null quando liberado=false")
    aviso_acesso: Optional[dict[str, Any]] = Field(
        None, description="aviso de classificação que acompanha conteúdo restrito")
    bloqueio: Optional[dict[str, Any]] = Field(
        None,
        description="quando liberado=false: motivo e orientação do gate "
                    "(consentimento pendente ou recusado)")
