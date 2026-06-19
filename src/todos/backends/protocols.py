"""Domain Protocols for the SEI backend contract.

Each Protocol covers one domain section of ``SEIBackend``.  Backends declare
what they implement by inheriting from the relevant Protocol(s) — the type
checker then verifies completeness instead of relying on runtime
``NotImplementedError`` stubs.

``CompositeBackend`` only needs to declare the three operations it genuinely
implements itself (``consultar_processo``, ``trocar_unidade``,
``criar_documento_externo``); everything else is routed to REST or Web at
startup.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from todos.backends.models import (
        EnvioProcesso,
        FiltrosPesquisaProcessos,
        NovoDocumentoExterno,
        NovoDocumentoInterno,
        NovoProcesso,
    )


class UnidadesProtocol(Protocol):
    """Sessão, unidades e usuários (13 ops)."""

    async def unidade_atual(self) -> dict:
        """Retorna a unidade ativa do usuário autenticado."""
        ...

    async def listar_unidades(self) -> list[dict]:
        """Lista as unidades às quais o usuário tem acesso."""
        ...

    async def trocar_unidade(self, id_unidade: str) -> dict:
        """Troca a unidade ativa (aceita id interno ou sigla)."""
        ...

    async def pesquisar_unidades(self, filtro: str = "", limit: int = 50, pagina: int = 0) -> dict:
        """Pesquisa unidades por nome ou sigla."""
        ...

    async def pesquisar_outras_unidades(
        self, filtro: str = "", limit: int = 50, pagina: int = 0
    ) -> dict:
        """Pesquisa unidades excluindo a unidade atual."""
        ...

    async def listar_usuarios(self, filtro: str = "", *, apenas_unidade: bool = True) -> dict:
        """Lista usuários do órgão ou apenas da unidade atual."""
        ...

    async def pesquisar_usuarios(
        self,
        filtro: str = "",
        id_orgao: str = "",
        limit: int = 50,
        pagina: int = 0,
    ) -> dict:
        """Pesquisa usuários no órgão por nome ou sigla."""
        ...

    async def listar_orgaos(self) -> list[dict]:
        """Lista os órgãos cadastrados na instalação do SEI."""
        ...

    async def listar_contextos(self, id_orgao: str) -> list[dict]:
        """Lista os contextos disponíveis para um órgão."""
        ...

    async def versao(self) -> dict:
        """Retorna a versão do SEI e do mod-wssei instalado."""
        ...

    async def listar_assinantes(self) -> list[dict]:
        """Lista os cargos/funções de assinatura da unidade atual."""
        ...

    async def listar_orgaos_assinante(self) -> list[dict]:
        """Lista os órgãos disponíveis para assinatura."""
        ...

    async def parametros_upload(self) -> dict:
        """Retorna parâmetros de upload (extensões e tamanhos permitidos)."""
        ...


class ProcessosLeituraProtocol(Protocol):
    """Leitura de processos (15 ops)."""

    async def consultar_processo(self, processo: str) -> dict:
        """Consulta os dados completos de um processo pelo protocolo formatado."""
        ...

    async def consultar_processo_detalhe(self, processo: str) -> dict:
        """Retorna detalhes (unidades abertas, interessados, sobrestamentos)."""
        ...

    async def arvore_processo(self, processo: str) -> dict:
        """Retorna a árvore de documentos de um processo."""
        ...

    async def listar_documentos(self, processo: str) -> dict | list[dict]:
        """Lista os documentos de um processo."""
        ...

    async def listar_processos(
        self,
        pagina: int = 0,
        apenas_meus: str = "",
        tipo: str = "",
        filtro: str = "",
    ) -> dict:
        """Lista os processos abertos na unidade atual."""
        ...

    async def resumo_processos(
        self,
        agrupar_por: str = "tipo",
        agrupar_por_2: str = "",
        apenas_meus: str = "",
        filtro: str = "",
    ) -> dict:
        """Agrupa e resume os processos abertos na unidade."""
        ...

    async def pesquisar_processos(self, filtros: FiltrosPesquisaProcessos) -> dict:
        """Pesquisa processos por texto e filtros estruturados."""
        ...

    async def listar_atividades(self, processo: str) -> dict:
        """Lista o histórico de andamentos de um processo."""
        ...

    async def listar_unidades_processo(self, processo: str) -> list[dict]:
        """Lista as unidades onde o processo está aberto."""
        ...

    async def listar_interessados(self, processo: str) -> list[dict]:
        """Lista os interessados de um processo."""
        ...

    async def listar_sobrestamentos(self, processo: str) -> list[dict]:
        """Lista o histórico de sobrestamentos de um processo."""
        ...

    async def verificar_acesso(self, processo: str) -> dict:
        """Verifica se o usuário tem acesso a um processo."""
        ...

    async def listar_relacionamentos(self, processo: str) -> dict | list[dict]:
        """Lista os processos relacionados a um processo."""
        ...

    async def consultar_atribuicao(self, processo: str) -> dict:
        """Retorna o usuário atualmente atribuído ao processo."""
        ...

    async def listar_historico_atribuicoes(self, processo: str) -> dict:
        """Lista o histórico de atribuições do processo (anterior, atribuídos)."""
        ...


class ProcessosEscritaProtocol(Protocol):
    """Escrita / tramitação de processos (19 ops)."""

    async def criar_processo(self, dados: NovoProcesso) -> dict:
        """Cria um novo processo."""
        ...

    async def alterar_processo(
        self,
        processo: str,
        especificacao: str = "",
        nivel_acesso: str = "",
        hipotese_legal: str = "",
        observacao: str = "",
    ) -> dict:
        """Altera metadados de um processo."""
        ...

    async def enviar_processo(self, processo: str, dados: EnvioProcesso) -> dict:
        """Tramita um processo para uma ou mais unidades."""
        ...

    async def concluir_processo(self, processo: str) -> dict:
        """Conclui (encerra) um processo na unidade atual."""
        ...

    async def reabrir_processo(self, processo: str) -> dict:
        """Reabre um processo concluído na unidade atual."""
        ...

    async def atribuir_processo(self, processo: str, usuario: str) -> dict:
        """Atribui um processo a um usuário da unidade."""
        ...

    async def remover_atribuicao(self, processo: str) -> dict:
        """Remove a atribuição de um processo."""
        ...

    async def receber_processo(self, processo: str) -> dict:
        """Recebe (aceita) um processo tramitado para a unidade."""
        ...

    async def marcar_nao_lido(self, processo: str) -> dict:
        """Marca um processo como não lido."""
        ...

    async def registrar_andamento(self, processo: str, descricao: str) -> dict:
        """Registra um andamento manual no histórico do processo."""
        ...

    async def executar_acao(self, processo: str, acao: str) -> dict:
        """Executa uma ação genérica do toolbar do processo (web)."""
        ...

    async def sobrestar_processo(
        self, processo: str, motivo: str, processo_vinculado: str = ""
    ) -> dict:
        """Sobresta (suspende) um processo."""
        ...

    async def remover_sobrestamento(self, processo: str) -> dict:
        """Remove o sobrestamento de um processo."""
        ...

    async def criar_observacao(self, processo: str, descricao: str) -> dict:
        """Cria uma observação em um processo."""
        ...

    async def criar_anotacao(self, processo: str, descricao: str, prioridade: str = "1") -> dict:
        """Cria uma anotação (post-it) em um processo."""
        ...

    async def remover_anotacao(self, processo: str) -> dict:
        """Remove a anotação (post-it) de um processo."""
        ...

    async def gerar_pdf_processo(self, processo: str) -> bytes:
        """Gera o PDF consolidado de um processo."""
        ...

    async def gerar_zip_processo(self, processo: str) -> bytes:
        """Gera o ZIP com os documentos de um processo."""
        ...

    async def sugestao_assuntos_processo(self, id_tipo_processo: str) -> list[dict]:
        """Sugere assuntos para um tipo de processo."""
        ...


class DocumentosProtocol(Protocol):
    """Documentos — criação, consulta e assinatura (19 ops)."""

    async def buscar_documento(self, numero_sei: str, processo: str = "") -> dict:
        """Busca um documento pelo número SEI."""
        ...

    async def consultar_documento_interno(
        self, id_documento: str, processo: str | None = None
    ) -> dict:
        """Consulta metadados de um documento interno."""
        ...

    async def consultar_documento_externo(
        self, id_documento: str, processo: str | None = None
    ) -> dict:
        """Consulta metadados de um documento externo."""
        ...

    async def visualizar_documento_interno(
        self, id_documento: str, processo: str | None = None
    ) -> str:
        """Retorna o HTML de um documento interno."""
        ...

    async def baixar_anexo(self, id_documento: str, processo: str | None = None) -> bytes:
        """Baixa os bytes de um documento externo (anexo)."""
        ...

    async def criar_documento_interno(self, processo: str, dados: NovoDocumentoInterno) -> dict:
        """Cria um documento interno (editor HTML) em um processo."""
        ...

    async def criar_documento_externo(self, processo: str, dados: NovoDocumentoExterno) -> dict:
        """Cria um documento externo (upload de arquivo) em um processo."""
        ...

    async def alterar_documento_interno(
        self,
        id_documento: str,
        descricao: str = "",
        nivel_acesso: str = "",
        hipotese_legal: str = "",
        processo: str | None = None,
    ) -> dict:
        """Altera metadados de um documento interno."""
        ...

    async def alterar_documento_externo(
        self,
        id_documento: str,
        descricao: str = "",
        nivel_acesso: str = "",
        hipotese_legal: str = "",
        arquivo_path: str = "",
    ) -> dict:
        """Altera metadados (e opcionalmente o arquivo) de um documento externo."""
        ...

    async def listar_secoes(self, id_documento: str, processo: str | None = None) -> dict:
        """Lista as seções editáveis de um documento interno."""
        ...

    async def alterar_secoes(
        self, id_documento: str, secoes: list[dict], versao: str = "", processo: str | None = None
    ) -> dict:
        """Edita seções de um documento interno."""
        ...

    async def gerar_referencia(self, numero_sei: str, id_documento: str = "") -> dict:
        """Gera o HTML de referência (link dinâmico) para um documento."""
        ...

    async def sugestao_assuntos_documento(self, id_serie: str) -> list[dict]:
        """Sugere assuntos para um tipo de documento."""
        ...

    async def listar_blocos_documento(self, id_documento: str) -> list[dict]:
        """Lista os blocos que contêm um documento."""
        ...

    async def assinar_documento(self, id_documento: str, cargo: str = "", orgao: str = "") -> dict:
        """Assina um documento com o cargo informado."""
        ...

    async def cancelar_assinatura(self, id_documento: str) -> dict:
        """Cancela a assinatura de um documento."""
        ...

    async def listar_assinaturas(
        self, id_documento: str, processo: str | None = None
    ) -> list[dict]:
        """Lista as assinaturas de um documento."""
        ...

    async def dar_ciencia(self, referencia: str, tipo: str = "documento") -> dict:
        """Registra ciência em um documento ou processo."""
        ...

    async def listar_ciencias(
        self,
        referencia: str,
        tipo: str = "documento",
        processo: str | None = None,
    ) -> list[dict]:
        """Lista as ciências de um documento ou processo."""
        ...


class CatalogosProtocol(Protocol):
    """Catálogos de tipos, assuntos, contatos e modelos (11 ops)."""

    async def pesquisar_tipos_processo(
        self,
        filtro: str = "",
        favoritos: str = "",
        limit: int = 50,
        pagina: int = 0,
    ) -> dict:
        """Pesquisa tipos de processo."""
        ...

    async def pesquisar_tipos_documento(
        self,
        filtro: str = "",
        favoritos: str = "",
        aplicabilidade: str = "",
        limit: int = 50,
        pagina: int = 0,
    ) -> dict:
        """Pesquisa tipos de documento (séries)."""
        ...

    async def pesquisar_tipos_documento_externo(
        self, filtro: str = "", limit: int = 50, pagina: int = 0
    ) -> dict:
        """Pesquisa tipos de documento externo."""
        ...

    async def pesquisar_tipos_conferencia(
        self, filtro: str = "", limit: int = 50, pagina: int = 0
    ) -> dict:
        """Pesquisa tipos de conferência de documentos externos."""
        ...

    async def pesquisar_hipoteses_legais(
        self, filtro: str = "", limit: int = 50, pagina: int = 0
    ) -> dict:
        """Pesquisa hipóteses legais para restrição/sigilo."""
        ...

    async def pesquisar_assuntos(self, filtro: str = "", limit: int = 50, pagina: int = 0) -> dict:
        """Pesquisa assuntos para classificação de processos."""
        ...

    async def pesquisar_contatos(self, filtro: str = "", limit: int = 50, pagina: int = 0) -> dict:
        """Pesquisa contatos cadastrados."""
        ...

    async def criar_contato(
        self,
        nome: str,
        tipo: str = "",
        email: str = "",
        telefone: str = "",
    ) -> dict:
        """Cria um contato."""
        ...

    async def pesquisar_textos_padrao(
        self, filtro: str = "", limit: int = 50, pagina: int = 0
    ) -> dict:
        """Pesquisa textos padrão da unidade."""
        ...

    async def listar_grupos_modelos(self, limit: int = 50, pagina: int = 0) -> dict:
        """Lista grupos de modelos de documento."""
        ...

    async def listar_modelos(
        self,
        id_grupo: str = "",
        filtro: str = "",
        limit: int = 50,
        pagina: int = 0,
    ) -> dict:
        """Lista modelos de documento."""
        ...


class MarcadoresProtocol(Protocol):
    """Marcadores de processos (10 ops)."""

    async def criar_marcador(self, nome: str, id_cor: str = "") -> dict:
        """Cria um marcador na unidade."""
        ...

    async def excluir_marcadores(self, ids_marcadores: str) -> dict:
        """Exclui marcadores (lista de ids separada por vírgula)."""
        ...

    async def desativar_marcadores(self, ids_marcadores: str) -> dict:
        """Desativa marcadores."""
        ...

    async def reativar_marcadores(self, ids_marcadores: str) -> dict:
        """Reativa marcadores."""
        ...

    async def marcar_processo(self, processo: str, marcador: str, texto: str = "") -> dict:
        """Aplica um marcador a um processo."""
        ...

    async def desmarcar_processo(self, processo: str, marcador: str = "") -> dict:
        """Remove marcador(es) de um processo (vazio remove todos)."""
        ...

    async def pesquisar_marcadores(self, filtro: str = "", limit: int = 50) -> dict:
        """Lista marcadores disponíveis na unidade."""
        ...

    async def consultar_marcador_processo(self, processo: str) -> dict | list[dict]:
        """Consulta os marcadores ativos de um processo."""
        ...

    async def historico_marcador_processo(self, processo: str) -> list[dict]:
        """Lista o histórico de marcadores de um processo."""
        ...

    async def listar_cores_marcador(self) -> list[dict]:
        """Lista as cores disponíveis para marcadores."""
        ...


class AcompanhamentoProtocol(Protocol):
    """Acompanhamento especial (8 ops)."""

    async def acompanhar_processo(
        self, processo: str, grupo: str = "", observacao: str = ""
    ) -> dict:
        """Adiciona acompanhamento especial a um processo."""
        ...

    async def remover_acompanhamento(self, processo: str) -> dict:
        """Remove o acompanhamento especial de um processo."""
        ...

    async def alterar_acompanhamento(
        self, processo: str, grupo: str = "", observacao: str = ""
    ) -> dict:
        """Altera o grupo/observação do acompanhamento de um processo."""
        ...

    async def listar_meus_acompanhamentos(self, limit: int = 50, pagina: int = 0) -> dict:
        """Lista os acompanhamentos especiais do usuário."""
        ...

    async def listar_acompanhamentos_unidade(self, limit: int = 50, pagina: int = 0) -> dict:
        """Lista os acompanhamentos especiais da unidade."""
        ...

    async def criar_grupo_acompanhamento(self, nome: str) -> dict:
        """Cria um grupo de acompanhamento."""
        ...

    async def excluir_grupo_acompanhamento(self, ids_grupos: str) -> dict:
        """Exclui grupos de acompanhamento."""
        ...

    async def listar_grupos_acompanhamento(self, filtro: str = "") -> dict:
        """Lista grupos de acompanhamento disponíveis."""
        ...


class BlocoInternoProtocol(Protocol):
    """Bloco interno (10 ops)."""

    async def criar_bloco_interno(self, descricao: str) -> dict:
        """Cria um bloco interno."""
        ...

    async def incluir_processo_bloco_interno(self, id_bloco: str, processos: str) -> dict:
        """Inclui processos em um bloco interno."""
        ...

    async def retirar_processo_bloco_interno(self, id_bloco: str, processos: str) -> dict:
        """Retira processos de um bloco interno."""
        ...

    async def listar_processos_bloco_interno(self, id_bloco: str) -> list[dict]:
        """Lista os processos de um bloco interno."""
        ...

    async def alterar_bloco_interno(self, id_bloco: str, descricao: str) -> dict:
        """Altera a descrição de um bloco interno."""
        ...

    async def excluir_blocos_internos(self, ids_blocos: str) -> dict:
        """Exclui blocos internos."""
        ...

    async def concluir_blocos_internos(self, ids_blocos: str) -> dict:
        """Conclui blocos internos."""
        ...

    async def reabrir_bloco_interno(self, id_bloco: str) -> dict:
        """Reabre um bloco interno concluído."""
        ...

    async def anotar_processo_bloco_interno(
        self, id_bloco: str, processo: str, descricao: str
    ) -> dict:
        """Anota um processo dentro de um bloco interno."""
        ...

    async def alterar_anotacao_bloco_interno(
        self, id_bloco: str, processo: str, descricao: str
    ) -> dict:
        """Altera a anotação de um processo em um bloco interno."""
        ...


class BlocoAssinaturaProtocol(Protocol):
    """Bloco de assinatura (16 ops)."""

    async def criar_bloco_assinatura(self, descricao: str, unidades: str = "") -> dict:
        """Cria um bloco de assinatura."""
        ...

    async def incluir_documento_bloco_assinatura(self, id_bloco: str, documentos: str) -> dict:
        """Inclui documentos em um bloco de assinatura."""
        ...

    async def disponibilizar_bloco_assinatura(self, id_bloco: str) -> dict:
        """Disponibiliza um bloco de assinatura para os assinantes."""
        ...

    async def cancelar_disponibilizacao_bloco_assinatura(self, id_bloco: str) -> dict:
        """Cancela a disponibilização de um bloco de assinatura."""
        ...

    async def pesquisar_blocos_assinatura(
        self, filtro: str = "", limit: int = 50, pagina: int = 0
    ) -> dict:
        """Pesquisa blocos de assinatura existentes."""
        ...

    async def listar_documentos_bloco_assinatura(self, id_bloco: str) -> list[dict]:
        """Lista os documentos de um bloco de assinatura."""
        ...

    async def retirar_documentos_bloco_assinatura(
        self, id_bloco: str, documentos: str
    ) -> list[dict]:
        """Retira documentos de um bloco de assinatura."""
        ...

    async def alterar_bloco_assinatura(self, id_bloco: str, descricao: str) -> dict:
        """Altera a descrição de um bloco de assinatura."""
        ...

    async def excluir_blocos_assinatura(self, ids_blocos: str) -> list[dict]:
        """Exclui blocos de assinatura."""
        ...

    async def concluir_blocos_assinatura(self, ids_blocos: str) -> list[dict]:
        """Conclui blocos de assinatura."""
        ...

    async def reabrir_bloco_assinatura(self, id_bloco: str) -> dict:
        """Reabre um bloco de assinatura concluído."""
        ...

    async def retornar_bloco_assinatura(self, id_bloco: str) -> dict:
        """Retorna um bloco de assinatura à unidade de origem."""
        ...

    async def anotar_documento_bloco_assinatura(
        self, id_bloco: str, documento: str, descricao: str
    ) -> dict:
        """Anota um documento dentro de um bloco de assinatura."""
        ...

    async def alterar_anotacao_bloco_assinatura(
        self, id_bloco: str, documento: str, descricao: str
    ) -> dict:
        """Altera a anotação de um documento em um bloco de assinatura."""
        ...

    async def assinar_bloco(self, id_bloco: str, cargo: str = "") -> dict:
        """Assina todos os documentos de um bloco de assinatura."""
        ...

    async def assinar_documentos_bloco(self, documentos: str, cargo: str = "") -> dict:
        """Assina documentos específicos de um bloco de assinatura."""
        ...


class CredenciamentoProtocol(Protocol):
    """Credenciamento de processos sigilosos (4 ops)."""

    async def listar_credenciamentos(self, processo: str) -> list[dict]:
        """Lista os credenciamentos de um processo sigiloso."""
        ...

    async def conceder_credenciamento(self, processo: str, id_usuario: str) -> dict:
        """Concede credenciamento de acesso a um processo sigiloso."""
        ...

    async def renunciar_credenciamento(self, processo: str) -> dict:
        """Renuncia ao próprio credenciamento em um processo sigiloso."""
        ...

    async def cassar_credenciamento(self, processo: str, id_usuario: str) -> dict:
        """Cassa o credenciamento de um usuário em um processo sigiloso."""
        ...


__all__ = [
    "AcompanhamentoProtocol",
    "BlocoAssinaturaProtocol",
    "BlocoInternoProtocol",
    "CatalogosProtocol",
    "CredenciamentoProtocol",
    "DocumentosProtocol",
    "MarcadoresProtocol",
    "ProcessosEscritaProtocol",
    "ProcessosLeituraProtocol",
    "UnidadesProtocol",
]
