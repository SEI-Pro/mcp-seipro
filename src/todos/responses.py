"""Modelos Pydantic de resposta compartilhados entre as tools do SEI.

Isolados aqui para não interferir na introspecção de type hints de entrada
do FastMCP (que requer anotações avaliadas em tempo de execução — sem
``from __future__ import annotations`` nos módulos de tools).

Uso: retorne o modelo diretamente (``return model``). FastMCP 3.4+ serializa
automaticamente em ``content[0].text`` (JSON, compatível com clientes antigos)
**e** ``structured_content`` (JSON tipado para clientes modernos), além de
publicar ``outputSchema`` no catálogo de tools.
"""

from pydantic import BaseModel, Field


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


class ResultadoHipoteses(Paginado):
    """Resposta de sei_pesquisar_hipoteses_legais."""

    hipoteses: list[dict[str, object]] = Field(default_factory=list)


class ResultadoTipos(Paginado):
    """Resposta de sei_pesquisar_tipos_processo / _documento / _externo / _conferencia."""

    tipos: list[dict[str, object]] = Field(default_factory=list)


class ResultadoAssuntos(Paginado):
    """Resposta de sei_pesquisar_assuntos."""

    assuntos: list[dict[str, object]] = Field(default_factory=list)


class ResultadoContatos(Paginado):
    """Resposta de sei_pesquisar_contatos."""

    contatos: list[dict[str, object]] = Field(default_factory=list)


class ResultadoTextos(Paginado):
    """Resposta de sei_pesquisar_textos_padrao."""

    textos: list[dict[str, object]] = Field(default_factory=list)


class ResultadoGruposModelos(Paginado):
    """Resposta de sei_listar_grupos_modelos."""

    grupos: list[dict[str, object]] = Field(default_factory=list)


class ResultadoModelos(Paginado):
    """Resposta de sei_listar_modelos."""

    modelos: list[dict[str, object]] = Field(default_factory=list)


class ResultadoUnidades(Paginado):
    """Resposta de sei_pesquisar_unidades / sei_pesquisar_outras_unidades."""

    unidades: list[dict[str, object]] = Field(default_factory=list)


class ResultadoUsuarios(Paginado):
    """Resposta de sei_pesquisar_usuarios."""

    usuarios: list[dict[str, object]] = Field(default_factory=list)


class ResultadoAcompanhamentos(Paginado):
    """Resposta de sei_listar_meus_acompanhamentos / sei_listar_acompanhamentos_unidade."""

    acompanhamentos: list[dict[str, object]] = Field(default_factory=list)


class ResultadoBlocos(Paginado):
    """Resposta de sei_pesquisar_blocos_assinatura."""

    blocos: list[dict[str, object]] = Field(default_factory=list)


class ResultadoDocumentosBloco(Paginado):
    """Resposta de sei_listar_documentos_bloco_assinatura."""

    documentos: list[dict[str, object]] = Field(default_factory=list)


class ResultadoListaProcessos(Paginado):
    """Resposta de sei_listar_processos."""

    processos: list[dict[str, object]] = Field(default_factory=list)


class ProcessosBloco(Paginado):
    """Resposta de sei_listar_processos_bloco_interno."""

    processos: list[dict[str, object]] = Field(default_factory=list)


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
