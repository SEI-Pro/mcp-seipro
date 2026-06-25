"""Dataclasses de parâmetros das operações de alta aridade do contrato.

Operações como `criar_processo`/`pesquisar_processos`/`enviar_processo`/
`criar_documento_*` recebem um objeto de parâmetros congelado em vez de muitos
kwargs planos — mantém `PLR0913` limpo no backend sem per-file-ignore (a objeção
"dataclasses escondem o schema MCP" vale só na camada de tool, não aqui).
"""

from __future__ import annotations

from dataclasses import dataclass, field

NIVEL_ACESSO_PUBLICO = "0"
NIVEL_ACESSO_RESTRITO = "1"
NIVEL_ACESSO_SIGILOSO = "2"


@dataclass(frozen=True)
class FiltrosPesquisaProcessos:
    """Filtros de uma pesquisa de processos."""

    palavras_chave: str = ""
    descricao: str = ""
    busca_rapida: str = ""
    data_inicio: str = ""
    data_fim: str = ""
    sta_tipo_data: str = ""
    id_unidade_geradora: str = ""
    id_assunto: str = ""
    grupo: str = ""
    limit: int = 50
    pagina: int = 0

    def __post_init__(self) -> None:
        """Valida os campos numéricos."""
        if self.limit < 1:
            msg = f"limit deve ser >= 1, recebido: {self.limit}"
            raise ValueError(msg)
        if self.pagina < 0:
            msg = f"pagina deve ser >= 0, recebido: {self.pagina}"
            raise ValueError(msg)


@dataclass(frozen=True)
class NovoProcesso:
    """Dados para criação de um processo."""

    tipo_processo: str
    especificacao: str = ""
    assuntos: str = ""
    interessados: str = ""
    observacoes: str = ""
    nivel_acesso: str = NIVEL_ACESSO_PUBLICO
    hipotese_legal: str = ""


@dataclass(frozen=True)
class EnvioProcesso:
    """Opções de tramitação de um processo."""

    unidades_destino: str
    manter_aberto: str = "N"
    remover_anotacao: str = "N"
    enviar_email: str = "N"
    data_retorno: str = ""
    dias_retorno: str = ""
    dias_uteis_retorno: str = "S"
    reabrir: str = "N"


@dataclass(frozen=True)
class NovoDocumentoInterno:
    """Dados para criação de um documento interno."""

    id_serie: str = ""
    descricao: str = ""
    nivel_acesso: str = NIVEL_ACESSO_PUBLICO
    hipotese_legal: str = ""
    id_unidade: str = ""


@dataclass(frozen=True)
class NovoDocumentoExterno:
    """Dados para criação/inclusão de um documento externo."""

    id_serie: str
    arquivo_path: str = ""
    arquivo_base64: str = ""
    nome_arquivo: str = ""
    descricao: str = ""
    data_elaboracao: str = ""
    nivel_acesso: str = NIVEL_ACESSO_PUBLICO
    hipotese_legal: str = ""
    id_unidade: str = ""


@dataclass(frozen=True)
class FiltroListagemProcessos:
    """Filtros para listagem de processos da unidade."""

    pagina: int = 0
    limit: int = 50
    tipo: str = ""
    usuario: str = ""
    apenas_meus: str = ""
    filtro: str = ""

    def __post_init__(self) -> None:
        """Valida os campos numéricos."""
        if self.limit < 1:
            msg = f"limit deve ser >= 1, recebido: {self.limit}"
            raise ValueError(msg)
        if self.pagina < 0:
            msg = f"pagina deve ser >= 0, recebido: {self.pagina}"
            raise ValueError(msg)


@dataclass(frozen=True)
class CredenciaisAssinatura:
    """Credenciais para assinatura eletrônica de documentos ou blocos."""

    login: str
    senha: str = field(repr=False)
    cargo: str = ""
    orgao: str = ""
    id_usuario: str = ""


@dataclass(frozen=True)
class SEIClientConfig:
    """Parâmetros de conexão do SEIClient REST."""

    sei_url: str = ""
    sei_web_url: str = ""
    sei_usuario: str = ""
    sei_senha: str = ""
    sei_orgao: str = ""
    sei_contexto: str = ""
    sei_verify_ssl: str | bool | None = None
    sei_ca_bundle: str = ""


@dataclass(frozen=True)
class SEIWebClientConfig:
    """Parâmetros de conexão do SEIWebClient (scraper)."""

    sei_url: str = ""
    sei_web_url: str = ""
    sei_usuario: str = ""
    sei_senha: str = ""
    sei_orgao: str = ""
    sei_sigla_orgao: str = ""
    sei_sigla_sistema: str = ""
    sei_sigla_orgao_sistema: str = ""
    sei_verify_ssl: str | bool | None = None
    sei_ca_bundle: str = ""


@dataclass(frozen=True)
class NovoProcessoWeb:
    """Dados para criação de processo via scraper web."""

    tipo_processo: str
    especificacao: str = ""
    assuntos_ids: list[str] | None = None
    interessados_ids: list[str] | None = None
    nivel_acesso: str = NIVEL_ACESSO_PUBLICO
    hipotese_legal: str = ""


@dataclass(frozen=True)
class OpcoesTramitacaoWeb:
    """Opções de tramitação de processo via scraper web."""

    manter_aberto: str = "N"
    remover_anotacao: str = "N"
    enviar_email: str = "N"
    data_retorno: str = ""
    dias_retorno: str = ""


@dataclass(frozen=True)
class DocumentoExternoInclusaoWeb:
    """Parâmetros de inclusão de documento externo via scraper web."""

    arquivo_path: str | None = None
    nome_arquivo: str | None = None
    id_serie: str | None = None
    data_elaboracao: str = ""
    nivel_acesso: str = NIVEL_ACESSO_PUBLICO
    hipotese_legal: str = ""
    conteudo: bytes | None = None
