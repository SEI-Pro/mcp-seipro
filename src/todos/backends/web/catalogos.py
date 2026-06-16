"""Mixin web: catálogos de tipos, assuntos, contatos e modelos."""

from __future__ import annotations

from todos.backends.web._session import _WebMixin


class CatalogosWeb(_WebMixin):
    """Operações web de catálogos de tipos, assuntos, contatos e modelos.

    Parâmetros de paginação (``pagina``) e filtros REST-only (``favoritos``,
    ``aplicabilidade``) são aceitos no contrato público mas ignorados: o
    scraper retorna resultados completos sem paginação por offset.
    ``limit`` é aplicado no cliente após obter a lista completa, exceto para
    os endpoints que já suportam truncagem server-side (autocomplete de
    assuntos, contatos e textos padrão — passam ``limit`` direto).
    """

    async def pesquisar_tipos_processo(
        self, filtro: str = "", favoritos: str = "", limit: int = 50, pagina: int = 0
    ) -> dict:
        """Pesquisa tipos de processo.

        ``favoritos`` e ``pagina`` ignorados: o scraper só filtra por texto e
        retorna a lista completa.
        """
        del favoritos, pagina  # parâmetros do contrato não suportados pelo scraper
        result = await self._web.pesquisar_tipos_processo_web(filtro=filtro)
        if limit > 0:
            tipos = result.get("tipos", [])[:limit]
            return {"tipos": tipos, "total_itens": len(tipos)}
        return result

    async def pesquisar_tipos_documento(
        self,
        filtro: str = "",
        favoritos: str = "",
        aplicabilidade: str = "",
        limit: int = 50,
        pagina: int = 0,
    ) -> dict:
        """Pesquisa tipos de documento (séries).

        ``favoritos``, ``aplicabilidade`` e ``pagina`` ignorados: o scraper
        só filtra por texto e retorna a lista completa.
        """
        del favoritos, aplicabilidade, pagina  # parâmetros do contrato não suportados
        result = await self._web.pesquisar_tipos_documento_web(filtro=filtro)
        if limit > 0:
            tipos = result.get("tipos", [])[:limit]
            return {"tipos": tipos, "total_itens": len(tipos)}
        return result

    async def pesquisar_tipos_documento_externo(
        self, filtro: str = "", limit: int = 50, pagina: int = 0
    ) -> dict:
        """Pesquisa tipos de documento externo.

        ``pagina`` ignorado: o scraper só filtra por texto e retorna a lista completa.
        """
        del pagina  # parâmetro do contrato não suportado pelo scraper
        result = await self._web.pesquisar_tipos_documento_externo_web(filtro=filtro)
        if limit > 0:
            tipos = result.get("tipos", [])[:limit]
            return {"tipos": tipos, "total_itens": len(tipos)}
        return result

    async def pesquisar_tipos_conferencia(
        self, filtro: str = "", limit: int = 50, pagina: int = 0
    ) -> dict:
        """Pesquisa tipos de conferência de documentos externos.

        ``pagina`` ignorado: o scraper só filtra por texto e retorna a lista completa.
        """
        del pagina  # parâmetro do contrato não suportado pelo scraper
        result = await self._web.pesquisar_tipos_conferencia_web(filtro=filtro)
        if limit > 0:
            tipos = result.get("tipos", [])[:limit]
            return {"tipos": tipos, "total_itens": len(tipos)}
        return result

    async def pesquisar_hipoteses_legais(
        self, filtro: str = "", limit: int = 50, pagina: int = 0
    ) -> dict:
        """Pesquisa hipóteses legais para restrição/sigilo.

        ``pagina`` ignorado: o scraper só filtra por texto e retorna a lista completa.
        """
        del pagina  # parâmetro do contrato não suportado pelo scraper
        result = await self._web.pesquisar_hipoteses_legais_web(filtro=filtro)
        if limit > 0:
            hipoteses = result.get("hipoteses", [])[:limit]
            return {"hipoteses": hipoteses, "total_itens": len(hipoteses)}
        return result

    async def pesquisar_assuntos(self, filtro: str = "", limit: int = 50, pagina: int = 0) -> dict:
        """Pesquisa assuntos para classificação de processos.

        ``pagina`` ignorado: o autocomplete web não pagina via offset.
        """
        del pagina  # parâmetro do contrato não suportado pelo autocomplete web
        return await self._web.pesquisar_assuntos_web(filtro=filtro, limit=limit)

    async def pesquisar_contatos(self, filtro: str = "", limit: int = 50) -> dict:
        """Pesquisa contatos cadastrados."""
        return await self._web.pesquisar_contatos_web(filtro=filtro, limit=limit)

    async def pesquisar_textos_padrao(
        self, filtro: str = "", limit: int = 50, pagina: int = 0
    ) -> dict:
        """Pesquisa textos padrão da unidade.

        ``pagina`` ignorado: o autocomplete web não pagina via offset.
        """
        del pagina  # parâmetro do contrato não suportado pelo autocomplete web
        return await self._web.pesquisar_textos_padrao_web(filtro=filtro, limit=limit)

    async def listar_grupos_modelos(self, limit: int = 50, pagina: int = 0) -> dict:
        """Lista grupos de modelos de documento.

        ``pagina`` ignorado: o scraper retorna a lista completa.
        """
        del pagina  # parâmetro do contrato não suportado pelo scraper
        result = await self._web.listar_grupos_modelos_web()
        if limit > 0:
            grupos = result.get("grupos", [])[:limit]
            return {"grupos": grupos, "total_itens": len(grupos)}
        return result

    async def listar_modelos(
        self, id_grupo: str = "", filtro: str = "", limit: int = 50, pagina: int = 0
    ) -> dict:
        """Lista modelos de documento.

        ``pagina`` ignorado: o scraper retorna a lista completa.
        """
        del pagina  # parâmetro do contrato não suportado pelo scraper
        result = await self._web.listar_modelos_web(filtro=filtro, id_grupo=id_grupo)
        if limit > 0:
            modelos = result.get("modelos", [])[:limit]
            return {"modelos": modelos, "total_itens": len(modelos)}
        return result
