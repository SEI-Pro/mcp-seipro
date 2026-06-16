"""Mixin REST — marcadores."""

from __future__ import annotations

import sys

if sys.version_info >= (3, 12):
    from typing import override
else:
    from typing_extensions import override

from todos.backends.rest._session import _RestMixin


class MarcadoresRest(_RestMixin):
    """Operações REST de marcadores."""

    async def criar_marcador(self, nome: str, id_cor: str = "") -> dict:
        """Cria um marcador na unidade."""
        return await self._rest.criar_marcador(nome, id_cor)

    async def excluir_marcadores(self, ids_marcadores: str) -> dict:
        """Exclui marcadores (lista de ids separada por vírgula)."""
        return await self._rest.excluir_marcadores(ids_marcadores)

    async def desativar_marcadores(self, ids_marcadores: str) -> dict:
        """Desativa marcadores."""
        return await self._rest.desativar_marcadores(ids_marcadores)

    async def reativar_marcadores(self, ids_marcadores: str) -> dict:
        """Reativa marcadores."""
        return await self._rest.reativar_marcadores(ids_marcadores)

    async def marcar_processo(self, processo: str, marcador: str, texto: str = "") -> dict:
        """Aplica um marcador a um processo."""
        id_proc = await self._resolver_processo(processo)
        return await self._rest.marcar_processo(id_proc, marcador, texto)

    async def pesquisar_marcadores(self, filtro: str = "", limit: int = 50) -> dict:
        """Lista marcadores disponíveis na unidade."""
        return await self._rest.pesquisar_marcadores(filtro=filtro, limit=limit)

    @override
    async def consultar_marcador_processo(self, processo: str) -> list[dict]:
        """Consulta os marcadores ativos de um processo.

        Narrows the base-class return type ``dict | list[dict]`` to ``list[dict]``
        because the REST backend always returns a list for this endpoint.
        """
        id_proc = await self._resolver_processo(processo)
        return await self._rest.consultar_marcador_processo(id_proc)

    @override
    async def historico_marcador_processo(self, processo: str) -> list[dict]:
        """Lista o histórico de marcadores de um processo."""
        id_proc = await self._resolver_processo(processo)
        return await self._rest.historico_marcador_processo(id_proc)

    @override
    async def listar_cores_marcador(self) -> list[dict]:
        """Lista as cores disponíveis para marcadores."""
        return await self._rest.listar_cores_marcador()
