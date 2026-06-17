"""Modelos Pydantic de resposta compartilhados entre as tools do SEI.

Isolados aqui para não interferir na introspecção de type hints de entrada
do FastMCP (que requer anotações avaliadas em tempo de execução — sem
``from __future__ import annotations`` nos módulos de tools).

Uso: ``return _json(model.model_dump())`` — JSON compacto via ``_json`` enquanto
o FastMCP/cliente não suportar ``structuredContent`` de forma estável. Quando
suportado, trocar por ``return model`` é só fiação.
"""

from pydantic import BaseModel, Field


class NextAction(BaseModel):
    """Próxima ação sugerida ao agente para continuar (paginação ou recuperação)."""

    tool: str = Field(description="Nome da tool a chamar em seguida, ex: 'sei_arvore_processo'")
    args: dict = Field(description="Argumentos para a tool, ex: {'cursor': 'eyJwIjoxfQ'}")
    reason: str = Field(description="Por que esta ação, em uma linha")


class DocumentoResumo(BaseModel):
    """Documento na árvore de um processo (campos essenciais para chaining)."""

    id: str = Field(description="idDocumento interno, ex: '2843449'")
    numero_sei: str = Field(default="", description="Número visível, ex: '2843449'")
    tipo_documento: str = Field(default="", description="ex: 'Despacho'")
    nome_composto: str = Field(default="", description="ex: 'Despacho GPF 2874369'")
    sigla_unidade: str = Field(default="", description="unidade geradora, ex: 'GPF'")
    assinado: bool | None = None
    cancelado: bool | None = None
    volume: int | None = None


class ListaDocumentos(BaseModel):
    """Resposta de sei_arvore_processo / sei_listar_documentos."""

    processo: str
    total_documentos: int = Field(description="total real no servidor")
    documentos: list[DocumentoResumo]
    next_actions: list[NextAction] = Field(default_factory=list)


class RespostaEscrita(BaseModel):
    """Resposta enxuta de tools de criação/alteração."""

    acao: str
    status: str = "ok"
    id_procedimento: str | None = None
    protocolo: str | None = None
    id_documento: str | None = None
    numero_sei: str | None = None
    mensagem: str | None = None


class Paginado(BaseModel):
    """Envelope de paginação por cursor opaco."""

    total_itens: int | None = Field(
        default=None,
        description="total no servidor, None se desconhecido",
    )
    proximo_cursor: str | None = Field(
        default=None,
        description="passe em `cursor`; None = fim",
    )
    tem_proxima_inferida: bool = Field(
        default=False,
        description="True se inferido de len(items)>=limit, não de contagem exata",
    )
    next_actions: list[NextAction] = Field(default_factory=list)


class ProcessoDetalhe(BaseModel):
    """Resposta shaped de sei_consultar_processo."""

    id_procedimento: str = ""
    protocolo: str
    tipo: str = ""
    especificacao: str = ""
    situacao: str = ""
    nivel_acesso: str = ""
    interessados: list[str] = Field(default_factory=list)
    unidades_abertas: list[str] = Field(default_factory=list)
    total_documentos: int = 0
    next_actions: list[NextAction] = Field(default_factory=list)
