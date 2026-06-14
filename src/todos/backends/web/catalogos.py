"""Mixin web: catálogos de tipos, assuntos, contatos e modelos."""

from __future__ import annotations

from todos.backends.web._session import _WebMixin


class CatalogosWeb(_WebMixin):
    """Operações web de catálogos de tipos, assuntos, contatos e modelos."""

    async def pesquisar_tipos_processo(
        self, filtro: str = "", favoritos: str = "", limit: int = 50, pagina: int = 0
    ) -> dict:
        """Pesquisa tipos de processo."""
        del favoritos, limit, pagina  # contrato exige os parâmetros; o scraper só filtra por texto
        return await self._web.pesquisar_tipos_processo_web(filtro=filtro)

    async def pesquisar_tipos_documento(
        self,
        filtro: str = "",
        favoritos: str = "",
        aplicabilidade: str = "",
        limit: int = 50,
        pagina: int = 0,
    ) -> dict:
        """Pesquisa tipos de documento (séries)."""
        del favoritos, aplicabilidade, limit, pagina  # contrato exige; scraper só filtra por texto
        return await self._web.pesquisar_tipos_documento_web(filtro=filtro)

    async def pesquisar_tipos_documento_externo(
        self, filtro: str = "", limit: int = 50, pagina: int = 0
    ) -> dict:
        """Pesquisa tipos de documento externo."""
        del limit, pagina  # contrato exige os parâmetros; o scraper só filtra por texto
        return await self._web.pesquisar_tipos_documento_externo_web(filtro=filtro)

    async def pesquisar_tipos_conferencia(
        self, filtro: str = "", limit: int = 50, pagina: int = 0
    ) -> dict:
        """Pesquisa tipos de conferência de documentos externos."""
        del limit, pagina  # contrato exige os parâmetros; o scraper só filtra por texto
        return await self._web.pesquisar_tipos_conferencia_web(filtro=filtro)

    async def pesquisar_hipoteses_legais(
        self, filtro: str = "", limit: int = 50, pagina: int = 0
    ) -> dict:
        """Pesquisa hipóteses legais para restrição/sigilo."""
        del limit, pagina  # contrato exige os parâmetros; o scraper só filtra por texto
        return await self._web.pesquisar_hipoteses_legais_web(filtro=filtro)

    async def pesquisar_assuntos(self, filtro: str = "", limit: int = 50, pagina: int = 0) -> dict:
        """Pesquisa assuntos para classificação de processos."""
        del pagina  # contrato exige o parâmetro; o autocomplete web não pagina via offset
        return await self._web.pesquisar_assuntos_web(filtro=filtro, limit=limit)

    async def pesquisar_contatos(self, filtro: str = "", limit: int = 50) -> dict:
        """Pesquisa contatos cadastrados."""
        return await self._web.pesquisar_contatos_web(filtro=filtro, limit=limit)

    async def pesquisar_textos_padrao(
        self, filtro: str = "", limit: int = 50, pagina: int = 0
    ) -> dict:
        """Pesquisa textos padrão da unidade."""
        del pagina  # contrato exige o parâmetro; o autocomplete web não pagina via offset
        return await self._web.pesquisar_textos_padrao_web(filtro=filtro, limit=limit)

    async def listar_grupos_modelos(self, limit: int = 50, pagina: int = 0) -> dict:
        """Lista grupos de modelos de documento."""
        del limit, pagina  # contrato exige os parâmetros; o scraper retorna a lista completa
        return await self._web.listar_grupos_modelos_web()

    async def listar_modelos(
        self, id_grupo: str = "", filtro: str = "", limit: int = 50, pagina: int = 0
    ) -> dict:
        """Lista modelos de documento."""
        del limit, pagina  # contrato exige os parâmetros; o scraper retorna a lista completa
        return await self._web.listar_modelos_web(filtro=filtro, id_grupo=id_grupo)
