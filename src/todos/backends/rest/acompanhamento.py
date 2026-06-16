"""Mixin REST — acompanhamento especial."""

from __future__ import annotations

from todos.backends.rest._session import _RestMixin
from todos.exceptions import SEINotFoundError


class AcompanhamentoRest(_RestMixin):
    """Operações REST de acompanhamento especial."""

    async def acompanhar_processo(
        self, processo: str, grupo: str = "", observacao: str = ""
    ) -> dict:
        """Adiciona acompanhamento especial a um processo."""
        id_proc = await self._resolver_processo(processo)
        return await self._rest.acompanhar_processo(id_proc, grupo, observacao)

    async def remover_acompanhamento(self, processo: str) -> dict:
        """Remove o acompanhamento especial de um processo."""
        id_proc = await self._resolver_processo(processo)
        acomp = await self._rest.consultar_acompanhamento(id_proc)
        # mod-wssei 2.0.x returns this field as "idAcompanhamento"; some older builds
        # used plain "id". Check both for compatibility.
        id_acomp = str(acomp.get("idAcompanhamento") or acomp.get("id", "")) if acomp else ""
        if not id_acomp:
            msg = f"Nenhum acompanhamento ativo no processo '{processo}'."
            raise SEINotFoundError(msg)
        return await self._rest.excluir_acompanhamento(id_acomp)

    async def alterar_acompanhamento(
        self, processo: str, grupo: str = "", observacao: str = ""
    ) -> dict:
        """Altera o grupo/observação do acompanhamento de um processo."""
        id_proc = await self._resolver_processo(processo)
        return await self._rest.alterar_acompanhamento(id_proc, grupo, observacao)

    async def listar_meus_acompanhamentos(self, limit: int = 50, pagina: int = 0) -> dict:
        """Lista os acompanhamentos especiais do usuário."""
        return await self._rest.listar_meus_acompanhamentos(limit=limit, start=pagina)

    async def listar_acompanhamentos_unidade(self, limit: int = 50, pagina: int = 0) -> dict:
        """Lista os acompanhamentos especiais da unidade."""
        return await self._rest.listar_acompanhamentos_unidade(limit=limit, start=pagina)

    async def criar_grupo_acompanhamento(self, nome: str) -> dict:
        """Cria um grupo de acompanhamento."""
        return await self._rest.criar_grupo_acompanhamento(nome)

    async def excluir_grupo_acompanhamento(self, ids_grupos: str) -> dict:
        """Exclui grupos de acompanhamento."""
        return await self._rest.excluir_grupo_acompanhamento(ids_grupos)

    async def listar_grupos_acompanhamento(self, filtro: str = "") -> dict:
        """Lista grupos de acompanhamento disponíveis."""
        return await self._rest.listar_grupos_acompanhamento(filtro=filtro)
