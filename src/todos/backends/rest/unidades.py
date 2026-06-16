"""Mixin REST — sessão, unidades e usuários."""

from __future__ import annotations

import sys

if sys.version_info >= (3, 12):
    from typing import override
else:
    from typing_extensions import override

from todos.backends.rest._session import _RestMixin


class UnidadesRest(_RestMixin):
    """Operações REST de sessão, unidades e usuários."""

    async def trocar_unidade(self, id_unidade: str) -> dict:
        """Troca a unidade ativa no cliente REST."""
        return await self._rest.trocar_unidade(id_unidade)

    async def pesquisar_unidades(self, filtro: str = "", limit: int = 50, pagina: int = 0) -> dict:
        """Pesquisa unidades por nome ou sigla."""
        return await self._rest.pesquisar_unidades(filtro=filtro, limit=limit, start=pagina)

    async def pesquisar_outras_unidades(
        self, filtro: str = "", limit: int = 50, pagina: int = 0
    ) -> dict:
        """Pesquisa unidades excluindo a unidade atual."""
        return await self._rest.pesquisar_outras_unidades(filtro=filtro, limit=limit, start=pagina)

    async def listar_usuarios(self, filtro: str = "", *, apenas_unidade: bool = True) -> dict:
        """Lista usuários do órgão ou apenas da unidade atual."""
        return await self._rest.listar_usuarios(filtro=filtro, apenas_unidade=apenas_unidade)

    async def pesquisar_usuarios(
        self, filtro: str = "", id_orgao: str = "", limit: int = 50, pagina: int = 0
    ) -> dict:
        """Pesquisa usuários no órgão por nome ou sigla."""
        return await self._rest.pesquisar_usuarios(
            filtro=filtro, id_orgao=id_orgao, limit=limit, start=pagina
        )

    @override
    async def listar_orgaos(self) -> list[dict]:
        """Lista os órgãos cadastrados na instalação do SEI."""
        return await self._rest.listar_orgaos()

    @override
    async def listar_contextos(self, id_orgao: str) -> list[dict]:
        """Lista os contextos disponíveis para um órgão."""
        return await self._rest.listar_contextos(id_orgao)

    async def versao(self) -> dict:
        """Retorna a versão do SEI e do mod-wssei instalado."""
        return await self._rest.versao()

    @override
    async def listar_assinantes(self) -> list[dict]:
        """Lista os cargos/funções de assinatura da unidade atual."""
        return await self._rest.listar_assinantes()

    @override
    async def listar_orgaos_assinante(self) -> list[dict]:
        """Lista os órgãos disponíveis para assinatura."""
        return await self._rest.listar_orgaos_assinante()

    async def parametros_upload(self) -> dict:
        """Retorna parâmetros de upload (extensões e tamanhos permitidos)."""
        return await self._rest.parametros_upload()
