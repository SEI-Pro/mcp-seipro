"""Contrato abstrato de backend do SEI.

`SEIBackend` enumera **todas** as operações expostas pelas MCP tools, usando
parâmetros canônicos e independentes de backend (processos identificados pelo
protocolo formatado, não pelo id interno da REST).

Cada método aqui é um *stub* que levanta `NotImplementedError`. As subclasses
(`SEIRestBackend`, `SEIWebBackend`) sobrescrevem apenas as operações que
realmente suportam — uma operação não sobrescrita permanece "não implementada"
e o roteador (composite) trata a queda para o outro backend ou converte em
`SEINotImplementedError` (subclasse de `SEIError`, capturável pelas tools).

Convenções de assinatura:
- `processo`: protocolo formatado (ex: "0016.269301/2020-39"). A subclasse REST
  resolve para o id interno; a web usa o protocolo direto.
- `id_documento`: id interno do documento OU número SEI; a subclasse resolve.
- Listas codificadas como string separada por vírgula (ex: `documentos`,
  `ids_blocos`) seguem o mesmo contrato das tools.
- Retornos são `dict`/`list[dict]` prontos para serialização, ou `bytes` para
  downloads binários (PDF/ZIP/anexos).
"""

from __future__ import annotations

from todos.backends.models import (
    EnvioProcesso,
    FiltrosPesquisaProcessos,
    NovoDocumentoExterno,
    NovoDocumentoInterno,
    NovoProcesso,
)

__all__ = [
    "EnvioProcesso",
    "FiltrosPesquisaProcessos",
    "NovoDocumentoExterno",
    "NovoDocumentoInterno",
    "NovoProcesso",
    "SEIBackend",
]


class SEIBackend:
    """Contrato comum a todos os backends do SEI.

    Não usa `abc.ABC`/`@abstractmethod` de propósito: as subclasses sobrescrevem
    apenas as operações que suportam, e qualquer método não sobrescrito levanta
    `NotImplementedError` — sinalizando que o backend concreto não implementa a
    operação. O atributo `name` identifica o backend nas mensagens de erro.
    """

    name: str = "base"

    # ------------------------------------------------------------------
    # Sessão, unidades e usuários
    # ------------------------------------------------------------------

    async def unidade_atual(self) -> dict:
        """Retorna a unidade ativa do usuário autenticado."""
        raise NotImplementedError

    async def listar_unidades(self) -> list[dict]:
        """Lista as unidades às quais o usuário tem acesso."""
        raise NotImplementedError

    async def trocar_unidade(self, id_unidade: str) -> dict:
        """Troca a unidade ativa (aceita id interno ou sigla)."""
        raise NotImplementedError

    async def pesquisar_unidades(
        self, filtro: str = "", limit: int = 50, pagina: int = 0, protocolo: str = ""
    ) -> dict:
        """Pesquisa unidades por nome ou sigla.

        `protocolo` é ignorado pelo backend REST; alguns backends web exigem
        um processo de referência para abrir o contexto de busca (ver
        SEIWebClient.pesquisar_unidades_envio).
        """
        raise NotImplementedError

    async def pesquisar_outras_unidades(
        self, filtro: str = "", limit: int = 50, pagina: int = 0, protocolo: str = ""
    ) -> dict:
        """Pesquisa unidades excluindo a unidade atual.

        `protocolo` é ignorado pelo backend REST; alguns backends web exigem
        um processo de referência para abrir o contexto de busca (ver
        SEIWebClient.pesquisar_unidades_envio).
        """
        raise NotImplementedError

    async def listar_usuarios(self, filtro: str = "", *, apenas_unidade: bool = True) -> dict:
        """Lista usuários do órgão ou apenas da unidade atual."""
        raise NotImplementedError

    async def pesquisar_usuarios(
        self, filtro: str = "", id_orgao: str = "", limit: int = 50, pagina: int = 0
    ) -> dict:
        """Pesquisa usuários no órgão por nome ou sigla."""
        raise NotImplementedError

    async def listar_orgaos(self) -> list[dict]:
        """Lista os órgãos cadastrados na instalação do SEI."""
        raise NotImplementedError

    async def listar_contextos(self, id_orgao: str) -> list[dict]:
        """Lista os contextos disponíveis para um órgão."""
        raise NotImplementedError

    async def versao(self) -> dict:
        """Retorna a versão do SEI e do mod-wssei instalado."""
        raise NotImplementedError

    async def listar_assinantes(self) -> list[dict]:
        """Lista os cargos/funções de assinatura da unidade atual."""
        raise NotImplementedError

    async def listar_orgaos_assinante(self) -> list[dict]:
        """Lista os órgãos disponíveis para assinatura."""
        raise NotImplementedError

    async def parametros_upload(self) -> dict:
        """Retorna parâmetros de upload (extensões e tamanhos permitidos)."""
        raise NotImplementedError

    # ------------------------------------------------------------------
    # Operações de leitura de processos
    # ------------------------------------------------------------------

    async def consultar_processo(self, processo: str) -> dict:
        """Consulta os dados completos de um processo pelo protocolo formatado."""
        raise NotImplementedError

    async def consultar_processo_detalhe(self, processo: str) -> dict:
        """Retorna detalhes (unidades abertas, interessados, sobrestamentos)."""
        raise NotImplementedError

    async def arvore_processo(self, processo: str) -> dict:
        """Retorna a árvore de documentos de um processo."""
        raise NotImplementedError

    async def listar_documentos(self, processo: str) -> dict | list[dict]:
        """Lista os documentos de um processo.

        Retorna ``list[dict]`` no backend REST e ``dict`` no backend web.
        """
        raise NotImplementedError

    async def listar_processos(
        self, pagina: int = 0, apenas_meus: str = "", tipo: str = "", filtro: str = ""
    ) -> dict:
        """Lista os processos abertos na unidade atual."""
        raise NotImplementedError

    async def resumo_processos(
        self,
        agrupar_por: str = "tipo",
        agrupar_por_2: str = "",
        apenas_meus: str = "",
        filtro: str = "",
    ) -> dict:
        """Agrupa e resume os processos abertos na unidade."""
        raise NotImplementedError

    async def pesquisar_processos(self, filtros: FiltrosPesquisaProcessos) -> dict:
        """Pesquisa processos por texto e filtros estruturados."""
        raise NotImplementedError

    async def listar_atividades(self, processo: str, tipo_historico: str = "R") -> dict:
        """Lista o histórico de andamentos de um processo."""
        raise NotImplementedError

    async def listar_unidades_processo(self, processo: str) -> list[dict]:
        """Lista as unidades onde o processo está aberto."""
        raise NotImplementedError

    async def listar_interessados(self, processo: str) -> list[dict]:
        """Lista os interessados de um processo."""
        raise NotImplementedError

    async def listar_sobrestamentos(self, processo: str) -> list[dict]:
        """Lista o histórico de sobrestamentos de um processo."""
        raise NotImplementedError

    async def verificar_acesso(self, processo: str) -> dict:
        """Verifica se o usuário tem acesso a um processo."""
        raise NotImplementedError

    async def listar_relacionamentos(self, processo: str) -> dict | list[dict]:
        """Lista os processos relacionados a um processo.

        Retorna ``list[dict]`` no backend REST e ``dict`` no backend web.
        """
        raise NotImplementedError

    async def consultar_atribuicao(self, processo: str) -> dict:
        """Retorna o usuário atualmente atribuído ao processo."""
        raise NotImplementedError

    async def listar_historico_atribuicoes(self, processo: str) -> dict:
        """Lista o histórico de atribuições do processo (anterior, atribuídos)."""
        raise NotImplementedError

    # ------------------------------------------------------------------
    # Operações de escrita de processos
    # ------------------------------------------------------------------

    async def criar_processo(self, dados: NovoProcesso) -> dict:
        """Cria um novo processo."""
        raise NotImplementedError

    async def alterar_processo(
        self,
        processo: str,
        especificacao: str = "",
        nivel_acesso: str = "",
        hipotese_legal: str = "",
        observacao: str = "",
    ) -> dict:
        """Altera metadados de um processo."""
        raise NotImplementedError

    async def enviar_processo(self, processo: str, dados: EnvioProcesso) -> dict:
        """Tramita um processo para uma ou mais unidades."""
        raise NotImplementedError

    async def concluir_processo(self, processo: str) -> dict:
        """Conclui (encerra) um processo na unidade atual."""
        raise NotImplementedError

    async def reabrir_processo(self, processo: str) -> dict:
        """Reabre um processo concluído na unidade atual."""
        raise NotImplementedError

    async def atribuir_processo(self, processo: str, usuario: str) -> dict:
        """Atribui um processo a um usuário da unidade."""
        raise NotImplementedError

    async def remover_atribuicao(self, processo: str) -> dict:
        """Remove a atribuição de um processo."""
        raise NotImplementedError

    async def receber_processo(self, processo: str) -> dict:
        """Recebe (aceita) um processo tramitado para a unidade."""
        raise NotImplementedError

    async def marcar_nao_lido(self, processo: str) -> dict:
        """Marca um processo como não lido."""
        raise NotImplementedError

    async def registrar_andamento(self, processo: str, descricao: str) -> dict:
        """Registra um andamento manual no histórico do processo."""
        raise NotImplementedError

    async def executar_acao(self, processo: str, acao: str) -> dict:
        """Executa uma ação genérica do toolbar do processo (web)."""
        raise NotImplementedError

    async def sobrestar_processo(
        self, processo: str, motivo: str, processo_vinculado: str = ""
    ) -> dict:
        """Sobresta (suspende) um processo."""
        raise NotImplementedError

    async def remover_sobrestamento(self, processo: str) -> dict:
        """Remove o sobrestamento de um processo."""
        raise NotImplementedError

    async def criar_observacao(self, processo: str, descricao: str) -> dict:
        """Cria uma observação em um processo."""
        raise NotImplementedError

    async def criar_anotacao(self, processo: str, descricao: str, prioridade: str = "1") -> dict:
        """Cria uma anotação (post-it) em um processo."""
        raise NotImplementedError

    async def remover_anotacao(self, processo: str) -> dict:
        """Remove a anotação (post-it) de um processo."""
        raise NotImplementedError

    async def gerar_pdf_processo(self, processo: str) -> bytes:
        """Gera o PDF consolidado de um processo."""
        raise NotImplementedError

    async def gerar_zip_processo(self, processo: str) -> bytes:
        """Gera o ZIP com os documentos de um processo."""
        raise NotImplementedError

    async def sugestao_assuntos_processo(self, id_tipo_processo: str) -> list[dict]:
        """Sugere assuntos para um tipo de processo."""
        raise NotImplementedError

    # ------------------------------------------------------------------
    # Documentos
    # ------------------------------------------------------------------

    async def buscar_documento(self, numero_sei: str, processo: str = "") -> dict:
        """Busca um documento pelo número SEI."""
        raise NotImplementedError

    async def consultar_documento_interno(
        self, id_documento: str, processo: str | None = None
    ) -> dict:
        """Consulta metadados de um documento interno."""
        raise NotImplementedError

    async def consultar_documento_externo(
        self, id_documento: str, processo: str | None = None
    ) -> dict:
        """Consulta metadados de um documento externo."""
        raise NotImplementedError

    async def visualizar_documento_interno(
        self, id_documento: str, processo: str | None = None
    ) -> str:
        """Retorna o HTML de um documento interno."""
        raise NotImplementedError

    async def baixar_anexo(self, id_documento: str, processo: str | None = None) -> bytes:
        """Baixa os bytes de um documento externo (anexo)."""
        raise NotImplementedError

    async def criar_documento_interno(self, processo: str, dados: NovoDocumentoInterno) -> dict:
        """Cria um documento interno (editor HTML) em um processo."""
        raise NotImplementedError

    async def criar_documento_externo(self, processo: str, dados: NovoDocumentoExterno) -> dict:
        """Cria um documento externo (upload de arquivo) em um processo."""
        raise NotImplementedError

    async def alterar_documento_interno(
        self,
        id_documento: str,
        descricao: str = "",
        nivel_acesso: str = "",
        hipotese_legal: str = "",
        processo: str | None = None,
    ) -> dict:
        """Altera metadados de um documento interno."""
        raise NotImplementedError

    async def alterar_documento_externo(
        self,
        id_documento: str,
        descricao: str = "",
        nivel_acesso: str = "",
        hipotese_legal: str = "",
        arquivo_path: str = "",
    ) -> dict:
        """Altera metadados (e opcionalmente o arquivo) de um documento externo."""
        raise NotImplementedError

    async def listar_secoes(self, id_documento: str, processo: str | None = None) -> dict:
        """Lista as seções editáveis de um documento interno."""
        raise NotImplementedError

    async def alterar_secoes(
        self, id_documento: str, secoes: list[dict], versao: str = "", processo: str | None = None
    ) -> dict:
        """Edita seções de um documento interno."""
        raise NotImplementedError

    async def gerar_referencia(self, numero_sei: str, id_documento: str = "") -> dict:
        """Gera o HTML de referência (link dinâmico) para um documento."""
        raise NotImplementedError

    async def resolver_documento(self, referencia: str) -> tuple[str, str]:
        """Resolve referência de documento → (id_interno, tipo_documento).

        Aceita tanto o id interno quanto o número SEI (protocoloFormatado).
        Retorna (id_documento, tipo) onde tipo é 'I', 'X' ou 'auto'.
        """
        raise NotImplementedError

    async def sugestao_assuntos_documento(self, id_serie: str) -> list[dict]:
        """Sugere assuntos para um tipo de documento."""
        raise NotImplementedError

    async def listar_blocos_documento(self, id_documento: str) -> list[dict]:
        """Lista os blocos que contêm um documento."""
        raise NotImplementedError

    async def assinar_documento(
        self,
        id_documento: str,
        cargo: str = "",
        orgao: str = "",
        processo: str | None = None,
    ) -> dict:
        """Assina um documento com o cargo informado."""
        raise NotImplementedError

    async def excluir_documento(
        self,
        id_documento: str,
        processo: str | None = None,
        *,
        confirmar: bool = False,
    ) -> dict:
        """Exclui um documento — ação destrutiva e irreversível.

        `confirmar=True` é obrigatório para executar; sem ele a operação é
        recusada antes de qualquer chamada ao SEI.
        """
        raise NotImplementedError

    async def cancelar_assinatura(self, id_documento: str) -> dict:
        """Cancela a assinatura de um documento."""
        raise NotImplementedError

    async def listar_assinaturas(
        self, id_documento: str, processo: str | None = None
    ) -> list[dict]:
        """Lista as assinaturas de um documento."""
        raise NotImplementedError

    async def dar_ciencia(self, referencia: str, tipo: str = "documento") -> dict:
        """Registra ciência em um documento ou processo."""
        raise NotImplementedError

    async def listar_ciencias(
        self, referencia: str, tipo: str = "documento", processo: str | None = None
    ) -> list[dict]:
        """Lista as ciências de um documento ou processo."""
        raise NotImplementedError

    # ------------------------------------------------------------------
    # Catálogos de tipos, assuntos, contatos e modelos
    # ------------------------------------------------------------------

    async def pesquisar_tipos_processo(
        self, filtro: str = "", favoritos: str = "", limit: int = 50, pagina: int = 0
    ) -> dict:
        """Pesquisa tipos de processo."""
        raise NotImplementedError

    async def pesquisar_tipos_documento(
        self,
        filtro: str = "",
        favoritos: str = "",
        aplicabilidade: str = "",
        limit: int = 50,
        pagina: int = 0,
    ) -> dict:
        """Pesquisa tipos de documento (séries)."""
        raise NotImplementedError

    async def pesquisar_tipos_documento_externo(
        self, filtro: str = "", limit: int = 50, pagina: int = 0
    ) -> dict:
        """Pesquisa tipos de documento externo."""
        raise NotImplementedError

    async def pesquisar_tipos_conferencia(
        self, filtro: str = "", limit: int = 50, pagina: int = 0
    ) -> dict:
        """Pesquisa tipos de conferência de documentos externos."""
        raise NotImplementedError

    async def pesquisar_hipoteses_legais(
        self, filtro: str = "", limit: int = 50, pagina: int = 0
    ) -> dict:
        """Pesquisa hipóteses legais para restrição/sigilo."""
        raise NotImplementedError

    async def pesquisar_assuntos(self, filtro: str = "", limit: int = 50, pagina: int = 0) -> dict:
        """Pesquisa assuntos para classificação de processos."""
        raise NotImplementedError

    async def pesquisar_contatos(self, filtro: str = "", limit: int = 50, pagina: int = 0) -> dict:
        """Pesquisa contatos cadastrados."""
        raise NotImplementedError

    async def criar_contato(
        self, nome: str, tipo: str = "", email: str = "", telefone: str = ""
    ) -> dict:
        """Cria um contato."""
        raise NotImplementedError

    async def pesquisar_textos_padrao(
        self, filtro: str = "", limit: int = 50, pagina: int = 0
    ) -> dict:
        """Pesquisa textos padrão da unidade."""
        raise NotImplementedError

    async def listar_grupos_modelos(self, limit: int = 50, pagina: int = 0) -> dict:
        """Lista grupos de modelos de documento."""
        raise NotImplementedError

    async def listar_modelos(
        self, id_grupo: str = "", filtro: str = "", limit: int = 50, pagina: int = 0
    ) -> dict:
        """Lista modelos de documento."""
        raise NotImplementedError

    # ------------------------------------------------------------------
    # Marcadores
    # ------------------------------------------------------------------

    async def criar_marcador(self, nome: str, id_cor: str = "") -> dict:
        """Cria um marcador na unidade."""
        raise NotImplementedError

    async def excluir_marcadores(self, ids_marcadores: str) -> dict:
        """Exclui marcadores (lista de ids separada por vírgula)."""
        raise NotImplementedError

    async def desativar_marcadores(self, ids_marcadores: str) -> dict:
        """Desativa marcadores."""
        raise NotImplementedError

    async def reativar_marcadores(self, ids_marcadores: str) -> dict:
        """Reativa marcadores."""
        raise NotImplementedError

    async def marcar_processo(self, processo: str, marcador: str, texto: str = "") -> dict:
        """Aplica um marcador a um processo."""
        raise NotImplementedError

    async def desmarcar_processo(self, processo: str, marcador: str = "") -> dict:
        """Remove marcador(es) de um processo (vazio remove todos)."""
        raise NotImplementedError

    async def pesquisar_marcadores(self, filtro: str = "", limit: int = 50) -> dict:
        """Lista marcadores disponíveis na unidade."""
        raise NotImplementedError

    async def consultar_marcador_processo(self, processo: str) -> dict | list[dict]:
        """Consulta os marcadores ativos de um processo.

        Retorna ``list[dict]`` no backend REST e ``dict`` no backend web.
        """
        raise NotImplementedError

    async def historico_marcador_processo(self, processo: str) -> list[dict]:
        """Lista o histórico de marcadores de um processo."""
        raise NotImplementedError

    async def listar_cores_marcador(self) -> list[dict]:
        """Lista as cores disponíveis para marcadores."""
        raise NotImplementedError

    # ------------------------------------------------------------------
    # Acompanhamento especial
    # ------------------------------------------------------------------

    async def acompanhar_processo(
        self, processo: str, grupo: str = "", observacao: str = ""
    ) -> dict:
        """Adiciona acompanhamento especial a um processo."""
        raise NotImplementedError

    async def remover_acompanhamento(self, processo: str) -> dict:
        """Remove o acompanhamento especial de um processo."""
        raise NotImplementedError

    async def alterar_acompanhamento(
        self, processo: str, grupo: str = "", observacao: str = ""
    ) -> dict:
        """Altera o grupo/observação do acompanhamento de um processo."""
        raise NotImplementedError

    async def listar_meus_acompanhamentos(self, limit: int = 50, pagina: int = 0) -> dict:
        """Lista os acompanhamentos especiais do usuário."""
        raise NotImplementedError

    async def listar_acompanhamentos_unidade(self, limit: int = 50, pagina: int = 0) -> dict:
        """Lista os acompanhamentos especiais da unidade."""
        raise NotImplementedError

    async def criar_grupo_acompanhamento(self, nome: str) -> dict:
        """Cria um grupo de acompanhamento."""
        raise NotImplementedError

    async def excluir_grupo_acompanhamento(self, ids_grupos: str) -> dict:
        """Exclui grupos de acompanhamento."""
        raise NotImplementedError

    async def listar_grupos_acompanhamento(self, filtro: str = "") -> dict:
        """Lista grupos de acompanhamento disponíveis."""
        raise NotImplementedError

    # ------------------------------------------------------------------
    # Bloco interno
    # ------------------------------------------------------------------

    async def criar_bloco_interno(self, descricao: str) -> dict:
        """Cria um bloco interno."""
        raise NotImplementedError

    async def incluir_processo_bloco_interno(self, id_bloco: str, processos: str) -> dict:
        """Inclui processos em um bloco interno."""
        raise NotImplementedError

    async def retirar_processo_bloco_interno(self, id_bloco: str, processos: str) -> dict:
        """Retira processos de um bloco interno."""
        raise NotImplementedError

    async def listar_processos_bloco_interno(self, id_bloco: str) -> list[dict]:
        """Lista os processos de um bloco interno."""
        raise NotImplementedError

    async def alterar_bloco_interno(self, id_bloco: str, descricao: str) -> dict:
        """Altera a descrição de um bloco interno."""
        raise NotImplementedError

    async def excluir_blocos_internos(self, ids_blocos: str) -> dict:
        """Exclui blocos internos."""
        raise NotImplementedError

    async def concluir_blocos_internos(self, ids_blocos: str) -> dict:
        """Conclui blocos internos."""
        raise NotImplementedError

    async def reabrir_bloco_interno(self, id_bloco: str) -> dict:
        """Reabre um bloco interno concluído."""
        raise NotImplementedError

    async def anotar_processo_bloco_interno(
        self, id_bloco: str, processo: str, descricao: str
    ) -> dict:
        """Anota um processo dentro de um bloco interno."""
        raise NotImplementedError

    async def alterar_anotacao_bloco_interno(
        self, id_bloco: str, processo: str, descricao: str
    ) -> dict:
        """Altera a anotação de um processo em um bloco interno."""
        raise NotImplementedError

    # ------------------------------------------------------------------
    # Bloco de assinatura
    # ------------------------------------------------------------------

    async def criar_bloco_assinatura(self, descricao: str, unidades: str = "") -> dict:
        """Cria um bloco de assinatura."""
        raise NotImplementedError

    async def incluir_documento_bloco_assinatura(
        self, id_bloco: str, documentos: str, processo: str | None = None
    ) -> dict:
        """Inclui documentos em um bloco de assinatura."""
        raise NotImplementedError

    async def disponibilizar_bloco_assinatura(self, id_bloco: str) -> dict:
        """Disponibiliza um bloco de assinatura para os assinantes."""
        raise NotImplementedError

    async def cancelar_disponibilizacao_bloco_assinatura(self, id_bloco: str) -> dict:
        """Cancela a disponibilização de um bloco de assinatura."""
        raise NotImplementedError

    async def pesquisar_blocos_assinatura(
        self, filtro: str = "", limit: int = 50, pagina: int = 0
    ) -> dict:
        """Pesquisa blocos de assinatura existentes."""
        raise NotImplementedError

    async def listar_documentos_bloco_assinatura(self, id_bloco: str) -> list[dict]:
        """Lista os documentos de um bloco de assinatura."""
        raise NotImplementedError

    async def retirar_documentos_bloco_assinatura(
        self, id_bloco: str, documentos: str
    ) -> list[dict]:
        """Retira documentos de um bloco de assinatura."""
        raise NotImplementedError

    async def alterar_bloco_assinatura(self, id_bloco: str, descricao: str) -> dict:
        """Altera a descrição de um bloco de assinatura."""
        raise NotImplementedError

    async def excluir_blocos_assinatura(self, ids_blocos: str) -> list[dict]:
        """Exclui blocos de assinatura."""
        raise NotImplementedError

    async def concluir_blocos_assinatura(self, ids_blocos: str) -> list[dict]:
        """Conclui blocos de assinatura."""
        raise NotImplementedError

    async def reabrir_bloco_assinatura(self, id_bloco: str) -> dict:
        """Reabre um bloco de assinatura concluído."""
        raise NotImplementedError

    async def retornar_bloco_assinatura(self, id_bloco: str) -> dict:
        """Retorna um bloco de assinatura à unidade de origem."""
        raise NotImplementedError

    async def anotar_documento_bloco_assinatura(
        self, id_bloco: str, documento: str, descricao: str
    ) -> dict:
        """Anota um documento dentro de um bloco de assinatura."""
        raise NotImplementedError

    async def alterar_anotacao_bloco_assinatura(
        self, id_bloco: str, documento: str, descricao: str
    ) -> dict:
        """Altera a anotação de um documento em um bloco de assinatura."""
        raise NotImplementedError

    async def assinar_bloco(self, id_bloco: str, cargo: str = "") -> dict:
        """Assina todos os documentos de um bloco de assinatura."""
        raise NotImplementedError

    async def assinar_documentos_bloco(self, documentos: str, cargo: str = "") -> dict:
        """Assina documentos específicos de um bloco de assinatura."""
        raise NotImplementedError

    # ------------------------------------------------------------------
    # Credenciamento
    # ------------------------------------------------------------------

    async def listar_credenciamentos(self, processo: str) -> list[dict]:
        """Lista os credenciamentos de um processo sigiloso."""
        raise NotImplementedError

    async def conceder_credenciamento(self, processo: str, id_usuario: str) -> dict:
        """Concede credenciamento de acesso a um processo sigiloso."""
        raise NotImplementedError

    async def renunciar_credenciamento(self, processo: str) -> dict:
        """Renuncia ao próprio credenciamento em um processo sigiloso."""
        raise NotImplementedError

    async def cassar_credenciamento(self, processo: str, id_usuario: str) -> dict:
        """Cassa o credenciamento de um usuário em um processo sigiloso."""
        raise NotImplementedError

    # ------------------------------------------------------------------
    # Genérico (RFC 0020) — inspeção e submissão de formulário arbitrário.
    # Só o backend web implementa (não há equivalente REST — o mod-wssei
    # não expõe HTML pra inspecionar).
    # ------------------------------------------------------------------

    async def inspecionar_pagina(self, url: str, *, incluir_raw: bool = False) -> dict:
        """Busca uma URL e devolve forms + ações descobertas na página."""
        raise NotImplementedError

    async def submeter_form(
        self,
        url_pagina: str,
        form_id: str,
        overrides: dict[str, str],
        url_destino: str | None = None,
        *,
        incluir_raw: bool = False,
    ) -> dict:
        """Submete um form arbitrário, com overrides de campo e URL de destino opcional."""
        raise NotImplementedError

    # ------------------------------------------------------------------
    # Genérico (RFC 0021) — captura de screenshot via browser real. Exceção
    # deliberada à arquitetura pure-HTTP (ver todos.browser_capture); só o
    # backend web implementa (não há equivalente REST/mod-wssei).
    # ------------------------------------------------------------------

    async def capturar_tela(
        self,
        url: str,
        *,
        selector: str | None = None,
        aguardar_segundos: float = 1.0,
    ) -> dict:
        """Captura um screenshot PNG real (browser Playwright) de uma URL do SEI."""
        raise NotImplementedError
