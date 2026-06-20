"""Mixin web: sessão, unidades e usuários."""

from __future__ import annotations

import logging

from todos.backends.web._session import _WebMixin
from todos.exceptions import SEINotImplementedError

logger = logging.getLogger(__name__)


class UnidadesWeb(_WebMixin):
    """Operações web de sessão, unidades e usuários."""

    async def unidade_atual(self) -> dict:
        """Retorna a unidade ativa do usuário autenticado."""
        return await self._web.unidade_atual()

    async def listar_unidades(self) -> list[dict]:
        """Lista as unidades às quais o usuário tem acesso."""
        return await self._web.listar_unidades()

    async def trocar_unidade(self, id_unidade: str) -> dict:
        """Troca a unidade ativa (aceita id interno ou sigla)."""
        return await self._web.trocar_unidade(id_unidade)

    async def pesquisar_unidades(self, filtro: str = "", limit: int = 50, pagina: int = 0) -> dict:
        """Pesquisa unidades por nome ou sigla (inclui a unidade atual)."""
        del pagina  # contrato exige o parâmetro; a pesquisa web não pagina via offset
        result = await self._web.pesquisar_outras_unidades_web(filtro=filtro, limit=limit)
        # O autocomplete web exclui a unidade atual; ao contrário de
        # pesquisar_outras_unidades, esta op deve incluí-la. Reinsere-a quando
        # casa o filtro (mesma forma {id, sigla, nome} dos demais itens).
        atual = await self._web.unidade_atual()
        if "sigla" not in atual:
            logger.warning(
                "pesquisar_unidades: unidade atual sem campo 'sigla' — excluindo de deduplicação"
            )
        sigla = atual.get("sigla", "")
        nome = atual.get("nome", "")
        f = (filtro or "").lower()
        if sigla and (not f or f in sigla.lower() or f in nome.lower()):
            unidades = result.get("unidades", [])
            id_atual = atual.get("id_unidade", "")
            if not any(u.get("sigla") == sigla or str(u.get("id")) == id_atual for u in unidades):
                unidades.insert(0, {"id": id_atual, "sigla": sigla, "nome": nome})
                result["unidades"] = unidades
                result["total_itens"] = len(unidades)
        return result

    async def pesquisar_outras_unidades(
        self, filtro: str = "", limit: int = 50, pagina: int = 0
    ) -> dict:
        """Pesquisa unidades excluindo a unidade atual."""
        del pagina  # contrato exige o parâmetro; a pesquisa web não pagina via offset
        return await self._web.pesquisar_outras_unidades_web(filtro=filtro, limit=limit)

    async def listar_usuarios(self, filtro: str = "", *, apenas_unidade: bool = True) -> dict:
        """Lista usuários do órgão ou apenas da unidade atual."""
        return await self._web.listar_usuarios_web(filtro=filtro, apenas_unidade=apenas_unidade)

    async def pesquisar_usuarios(
        self, filtro: str = "", id_orgao: str = "", limit: int = 50, pagina: int = 0
    ) -> dict:
        """Pesquisa usuários no órgão por nome ou sigla."""
        del id_orgao, pagina  # contrato exige os parâmetros; o autocomplete web os ignora
        return await self._web.pesquisar_usuarios_web(filtro=filtro, limit=limit)

    async def parametros_upload(self) -> dict:
        """Retorna parâmetros de upload; requer backend REST (mod-wssei).

        O scraping web não expõe as configurações de upload da instância.
        Use uma instância com mod-wssei para obter os valores reais.
        """
        msg = (
            "parametros_upload requer o backend REST (mod-wssei). "
            "Configurações de upload não estão disponíveis via scraping web — "
            "os valores variam por instância e não podem ser inferidos com segurança."
        )
        raise SEINotImplementedError(msg)
