"""Mixin web: sessão, unidades e usuários."""

from __future__ import annotations

from todos.backends.web._session import _WebMixin


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
        """Retorna parâmetros de upload (extensões e tamanhos permitidos)."""
        return {
            "extensoes_permitidas": [
                "pdf",
                "doc",
                "docx",
                "xls",
                "xlsx",
                "ppt",
                "pptx",
                "odt",
                "ods",
                "odp",
                "txt",
                "rtf",
                "csv",
                "jpg",
                "jpeg",
                "png",
                "gif",
                "bmp",
                "tif",
                "tiff",
                "zip",
                "rar",
                "7z",
                "mp3",
                "mp4",
                "avi",
                "mov",
            ],
            "tamanho_maximo_mb": 50,
            "_aviso": (
                "Valores padrão do SEI 4.x — configure SEI_URL para valores reais desta instância."
            ),
        }
