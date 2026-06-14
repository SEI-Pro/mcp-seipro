"""Mixin REST — catálogos de tipos, assuntos, contatos e modelos."""

from __future__ import annotations

from todos.backends.rest._session import _RestMixin


class CatalogosRest(_RestMixin):
    """Operações REST de catálogos."""

    async def pesquisar_tipos_processo(
        self, filtro: str = "", favoritos: str = "", limit: int = 50, pagina: int = 0
    ) -> dict:
        """Pesquisa tipos de processo."""
        return await self._rest.pesquisar_tipos_processo(
            filtro=filtro, favoritos=favoritos, limit=limit, start=pagina
        )

    async def pesquisar_tipos_documento(
        self,
        filtro: str = "",
        favoritos: str = "",
        aplicabilidade: str = "",
        limit: int = 50,
        pagina: int = 0,
    ) -> dict:
        """Pesquisa tipos de documento (séries)."""
        return await self._rest.pesquisar_tipos_documento(
            filtro=filtro,
            favoritos=favoritos,
            aplicabilidade=aplicabilidade,
            limit=limit,
            start=pagina,
        )

    async def pesquisar_tipos_documento_externo(
        self, filtro: str = "", limit: int = 50, pagina: int = 0
    ) -> dict:
        """Pesquisa tipos de documento externo."""
        return await self._rest.pesquisar_tipos_documento_externo(
            filtro=filtro, limit=limit, start=pagina
        )

    async def pesquisar_tipos_conferencia(
        self, filtro: str = "", limit: int = 50, pagina: int = 0
    ) -> dict:
        """Pesquisa tipos de conferência de documentos externos."""
        return await self._rest.pesquisar_tipos_conferencia(
            filtro=filtro, limit=limit, start=pagina
        )

    async def pesquisar_hipoteses_legais(
        self, filtro: str = "", limit: int = 50, pagina: int = 0
    ) -> dict:
        """Pesquisa hipóteses legais para restrição/sigilo."""
        return await self._rest.pesquisar_hipoteses_legais(filtro=filtro, limit=limit, start=pagina)

    async def pesquisar_assuntos(self, filtro: str = "", limit: int = 50, pagina: int = 0) -> dict:
        """Pesquisa assuntos para classificação de processos."""
        return await self._rest.pesquisar_assuntos(filtro=filtro, limit=limit, start=pagina)

    async def pesquisar_contatos(self, filtro: str = "", limit: int = 50) -> dict:
        """Pesquisa contatos cadastrados."""
        return await self._rest.pesquisar_contatos(filtro=filtro, limit=limit)

    async def criar_contato(
        self, nome: str, tipo: str = "", email: str = "", telefone: str = ""
    ) -> dict:
        """Cria um contato."""
        return await self._rest.criar_contato(nome=nome, tipo=tipo, email=email, telefone=telefone)

    async def pesquisar_textos_padrao(
        self, filtro: str = "", limit: int = 50, pagina: int = 0
    ) -> dict:
        """Pesquisa textos padrão da unidade."""
        return await self._rest.pesquisar_textos_padrao(filtro=filtro, limit=limit, start=pagina)

    async def listar_grupos_modelos(self, limit: int = 50, pagina: int = 0) -> dict:
        """Lista grupos de modelos de documento."""
        return await self._rest.listar_grupos_modelos(limit=limit, start=pagina)

    async def listar_modelos(
        self, id_grupo: str = "", filtro: str = "", limit: int = 50, pagina: int = 0
    ) -> dict:
        """Lista modelos de documento."""
        return await self._rest.listar_modelos(
            id_grupo=id_grupo, filtro=filtro, limit=limit, start=pagina
        )
