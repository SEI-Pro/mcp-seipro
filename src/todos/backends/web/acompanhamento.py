"""Mixin web: acompanhamento especial."""

from __future__ import annotations

from todos.backends.web._session import _WebMixin


class AcompanhamentoWeb(_WebMixin):
    """Operações web de acompanhamento especial."""

    async def acompanhar_processo(
        self, processo: str, grupo: str = "", observacao: str = ""
    ) -> dict:
        """Adiciona acompanhamento especial a um processo."""
        # Ação correta: acompanhamento_gerenciar (form frmAcompanhamentoCadastro);
        # campo selGrupoAcompanhamento (não selGrupo). As ações
        # acompanhamento_especial_incluir/_excluir não existem na árvore.
        campos: dict[str, str] = {}
        if grupo:
            campos["selGrupoAcompanhamento"] = grupo
        if observacao:
            campos["txaObservacao"] = observacao
        return await self._web.executar_acao_processo(processo, "acompanhamento_gerenciar", campos)

    async def remover_acompanhamento(self, processo: str) -> dict:
        """Remove o acompanhamento especial de um processo."""
        return await self._web.remover_acompanhamento_web(processo)

    async def alterar_acompanhamento(
        self, processo: str, grupo: str = "", observacao: str = ""
    ) -> dict:
        """Altera o grupo/observação do acompanhamento de um processo."""
        return await self._web.alterar_acompanhamento_web(processo, grupo, observacao)

    async def listar_meus_acompanhamentos(self, limit: int = 50, pagina: int = 0) -> dict:
        """Lista os acompanhamentos especiais do usuário."""
        del pagina  # contrato exige o parâmetro; o scraper aplica o limite sem paginar por offset
        return await self._web.listar_meus_acompanhamentos_web(limit=limit)

    async def listar_acompanhamentos_unidade(self, limit: int = 50, pagina: int = 0) -> dict:
        """Lista os acompanhamentos especiais da unidade."""
        del pagina  # contrato exige o parâmetro; o scraper aplica o limite sem paginar por offset
        return await self._web.listar_acompanhamentos_unidade_web(limit=limit)

    async def listar_grupos_acompanhamento(self, filtro: str = "") -> dict:
        """Lista grupos de acompanhamento disponíveis."""
        return await self._web.listar_grupos_acompanhamento_web(filtro=filtro)
