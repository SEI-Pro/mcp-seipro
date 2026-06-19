"""Modelos Pydantic de resposta compartilhados entre as tools do SEI.

Isolados aqui para não interferir na introspecção de type hints de entrada
do FastMCP (que requer anotações avaliadas em tempo de execução — sem
``from __future__ import annotations`` nos módulos de tools).

Uso: retorne o modelo diretamente (``return model``). FastMCP 3.4+ serializa
automaticamente em ``content[0].text`` (JSON, compatível com clientes antigos)
**e** ``structured_content`` (JSON tipado para clientes modernos), além de
publicar ``outputSchema`` no catálogo de tools.
"""

from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field, model_validator

T = TypeVar("T")


class NextAction(BaseModel):
    """Próxima ação sugerida ao agente para continuar (paginação ou recuperação)."""

    tool: str = Field(description="Nome da tool a chamar em seguida, ex: 'sei_arvore_processo'")
    args: dict[str, object] = Field(
        description="Argumentos para a tool, ex: {'cursor': 'eyJwIjoxfQ'}"
    )
    reason: str = Field(description="Por que esta ação, em uma linha")


class DocumentoResumo(BaseModel):
    """Documento na árvore de um processo (campos essenciais para chaining)."""

    id: str = Field(description="idDocumento interno (opaco), ex: '2843449'")
    numero_sei: str = Field(
        default="", description="Número visível formatado, ex: '0050769-51.2024.4.02.8000'"
    )
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


class Andamento(BaseModel):
    """Entrada no histórico de andamentos de um processo."""

    data_hora: str = Field(description="Data e hora, ex: '19/06/2026 10:30:00'")
    unidade: str = Field(default="", description="Unidade que registrou o andamento")
    usuario: str = Field(default="", description="Usuário que registrou o andamento")
    descricao: str = Field(default="", description="Descrição da ação realizada")


class ProcessoInfo(BaseModel):
    """Identificação do processo retornada por sei_listar_atividades."""

    protocolo: str = Field(
        default="", description="Protocolo formatado, ex: '50300.000123/2025-00'"
    )
    id_procedimento: str = Field(default="", description="Id interno do SEI")


class ListaAtividades(BaseModel):
    """Resposta de sei_listar_atividades."""

    processo: ProcessoInfo = Field(description="Identificação do processo")
    total_andamentos: int = Field(description="Total de andamentos no histórico")
    andamentos: list[Andamento]
    truncado: bool = Field(
        default=False,
        description="True se há mais andamentos além do limite exibido (50)",
    )


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


class PaginadoGenerico(Paginado, Generic[T]):
    """Envelope de paginação tipado com campo `itens: list[T]`.

    Usado por tools de catálogo SEI. FastMCP resolve o tipo concreto em tempo
    de decoração e publica outputSchema com propriedades por campo de item.
    """

    itens: list[T] = Field(default_factory=list, description="Itens da página atual")


# ---------------------------------------------------------------------------
# Modelos de item de catálogo SEI
# ---------------------------------------------------------------------------


class ItemSEI(BaseModel):
    """Item de catálogo SEI com id e nome (base)."""

    model_config = ConfigDict(extra="allow")

    id: str = Field(default="", description="Identificador interno do SEI")
    nome: str = Field(default="", description="Nome legível")


class HipoteseLegal(ItemSEI):
    """Hipótese legal para nível de acesso restrito ou sigiloso."""


class TipoCatalogo(ItemSEI):
    """Tipo de processo, documento, documento externo ou conferência."""


class AssuntoSEI(ItemSEI):
    """Assunto para classificação de processos."""

    codigo: str = Field(default="", description="Código de classificação, ex: '021.1'")


class ContatoSEI(ItemSEI):
    """Contato (pessoa física, jurídica ou órgão) cadastrado no SEI."""

    sigla: str = Field(default="", description="Sigla ou abreviatura do contato")


class TextoPadrao(ItemSEI):
    """Texto padrão para preenchimento automático de documentos internos."""


class GrupoModelos(ItemSEI):
    """Grupo de modelos de documento."""


class ModeloDocumento(ItemSEI):
    """Modelo de documento para criação de documentos internos."""


class UnidadeSEI(BaseModel):
    """Unidade organizacional do SEI."""

    model_config = ConfigDict(extra="allow")

    id: str = Field(default="", description="Identificador interno da unidade")
    sigla: str = Field(default="", description="Sigla da unidade, ex: 'CGTI'")
    nome: str = Field(default="", description="Nome completo da unidade")

    @model_validator(mode="before")
    @classmethod
    def _normalizar_id(cls, data: object) -> object:
        if isinstance(data, dict) and not data.get("id") and "id_unidade" in data:
            data["id"] = data["id_unidade"]
        return data


class UsuarioSEI(BaseModel):
    """Usuário do SEI."""

    model_config = ConfigDict(extra="allow")

    id: str = Field(default="", description="Identificador interno do usuário")
    nome: str = Field(default="", description="Nome completo do usuário")
    sigla: str = Field(default="", description="Login/sigla do usuário")

    @model_validator(mode="before")
    @classmethod
    def _normalizar_id(cls, data: object) -> object:
        if isinstance(data, dict) and not data.get("id") and "id_usuario" in data:
            data["id"] = data["id_usuario"]
        return data


class AcompanhamentoSEI(BaseModel):
    """Processo em acompanhamento especial."""

    model_config = ConfigDict(extra="allow")

    id: str = Field(default="", description="Identificador do acompanhamento")
    protocolo: str = Field(default="", description="Número do processo acompanhado")

    @model_validator(mode="before")
    @classmethod
    def _normalizar_campos(cls, data: object) -> object:
        if isinstance(data, dict):
            if not data.get("id") and "idProcedimento" in data:
                data["id"] = data["idProcedimento"]
            if not data.get("protocolo") and "protocoloFormatado" in data:
                data["protocolo"] = data["protocoloFormatado"]
        return data


class BlocoAssinatura(BaseModel):
    """Bloco de assinatura para agrupamento de documentos."""

    model_config = ConfigDict(extra="allow")

    id: str = Field(default="", description="Identificador do bloco")
    descricao: str = Field(default="", description="Descrição do bloco")
    situacao: str = Field(
        default="", description="Estado do bloco, ex: 'Aberto', 'Disponibilizado'"
    )

    @model_validator(mode="before")
    @classmethod
    def _normalizar_campos(cls, data: object) -> object:
        if isinstance(data, dict):
            if not data.get("id") and "idBloco" in data:
                data["id"] = data["idBloco"]
            if not data.get("situacao") and "estado" in data:
                data["situacao"] = data["estado"]
        return data


class DocumentoBloco(BaseModel):
    """Documento incluído em um bloco de assinatura."""

    model_config = ConfigDict(extra="allow")

    id: str = Field(default="", description="Identificador interno do documento")
    protocolo: str = Field(default="", description="Número do processo do documento")

    @model_validator(mode="before")
    @classmethod
    def _normalizar_campos(cls, data: object) -> object:
        if isinstance(data, dict):
            if not data.get("id") and "idDocumento" in data:
                data["id"] = data["idDocumento"]
            if not data.get("protocolo") and "numero" in data:
                data["protocolo"] = data["numero"]
        return data


# ---------------------------------------------------------------------------
# Respostas paginadas especializadas (mantêm campos extras além de `itens`)
# ---------------------------------------------------------------------------


class ResultadoPesquisaProcessos(Paginado):
    """Resposta de sei_pesquisar_processos (envelope Paginado + lista de processos)."""

    processos: list[dict[str, object]] = Field(
        default_factory=list,
        description="Lista de processos encontrados (campos variam entre REST e web)",
    )
    fonte: str = Field(default="rest", description="'rest' ou 'web'")
    aviso: str | None = Field(
        default=None,
        description="Avisos de filtros ignorados no caminho web",
    )


class ResultadoListaProcessos(Paginado):
    """Resposta de sei_listar_processos e sei_listar_processos_bloco_interno."""

    processos: list[dict[str, object]] = Field(default_factory=list)
    total_filtrados: int | None = Field(
        default=None,
        description="total após filtros client-side (tipo/filtro); None quando não aplicável",
    )
    pagina_atual: int | None = Field(default=None, description="página corrente (0-indexed)")
    layout: str | None = Field(
        default=None, description="'detalhada' ou 'resumida' — layout da caixa SEI (web only)"
    )
    hints: list[str] = Field(default_factory=list, description="dicas de workflow")


class ProcessoDetalhe(BaseModel):
    """Resposta shaped de sei_consultar_processo."""

    id_procedimento: str = ""
    protocolo: str
    tipo: str = ""
    especificacao: str = ""
    nivel_acesso: str = ""
    interessados: list[str] = Field(default_factory=list)
    total_documentos: int = 0
    next_actions: list[NextAction] = Field(default_factory=list)
    aviso_acesso: dict | None = Field(
        default=None, description="Aviso informativo de classificação de acesso"
    )
    warnings: list[str] | None = Field(
        default=None, description="Avisos de backend (ex: falha de uma fonte)"
    )
