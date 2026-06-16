"""Mixin web: marcadores."""

from __future__ import annotations

from todos.backends.web._session import _WebMixin


class MarcadoresWeb(_WebMixin):
    """Operações web de marcadores."""

    async def marcar_processo(self, processo: str, marcador: str, texto: str = "") -> dict:
        """Aplica um marcador a um processo."""
        # O servidor lê o id do hidden hdnIdMarcador (sincronizado do selMarcador
        # por JS); a observação vai em txaTexto. Ação correta: gerenciar.
        campos: dict[str, str] = {"selMarcador": marcador, "hdnIdMarcador": marcador}
        if texto:
            campos["txaTexto"] = texto
        return await self._web.executar_acao_processo(
            processo, "andamento_marcador_gerenciar", campos
        )

    async def desmarcar_processo(self, processo: str, marcador: str = "") -> dict:
        """Remove marcador(es) de um processo (vazio = todos)."""
        return await self._web.desmarcar_processo_web(processo, marcador)

    async def consultar_marcador_processo(self, processo: str) -> dict:
        """Lista os marcadores atualmente aplicados a um processo."""
        return await self._web.consultar_marcador_processo_web(processo)

    async def pesquisar_marcadores(self, filtro: str = "", limit: int = 50) -> dict:
        """Lista marcadores disponíveis na unidade.

        A interface web do SEI não suporta limitação server-side; o limite é
        aplicado no cliente após obter a lista completa.
        """
        result = await self._web.pesquisar_marcadores_web(filtro=filtro)
        if limit > 0 and len(result.get("marcadores", [])) > limit:
            marcadores = result["marcadores"][:limit]
            return {"marcadores": marcadores, "total_itens": len(marcadores)}
        return result
