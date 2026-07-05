"""Mixin web: marcadores."""

from __future__ import annotations

import logging

from todos.backends.web._session import _WebMixin

logger = logging.getLogger(__name__)


class MarcadoresWeb(_WebMixin):
    """Operações web de marcadores."""

    async def criar_marcador(self, nome: str, id_cor: str = "") -> dict:
        """Cria um marcador na unidade atual.

        Delega a `SEIWebClient.criar_marcador_web` — funciona sem REST/
        mod-wssei (diferente do backend REST, que é a única implementação
        que existia antes). Se `id_cor` vier vazio, o próprio
        `criar_marcador_web` levanta erro com as cores disponíveis,
        extraídas ao vivo do form de cadastro (não depende de REST).
        """
        return await self._web.criar_marcador_web(nome, id_cor)

    async def listar_cores_marcador(self) -> list[dict]:
        """Lista as cores disponíveis para marcadores (extraídas do form de cadastro)."""
        return await self._web.listar_cores_marcador_web()

    async def marcar_processo(self, processo: str, marcador: str, texto: str = "") -> dict:
        """Aplica um marcador a um processo.

        Delega a `SEIWebClient.marcar_processo_web`, que usa a ação
        `andamento_marcador_cadastrar` e reconfirma o resultado lendo os
        marcadores aplicados de volta (ver docstring lá — a ação
        `andamento_marcador_gerenciar` usada antes aqui era a tela errada:
        aceitava o POST sem erro mas não aplicava nada de verdade).
        """
        return await self._web.marcar_processo_web(processo, marcador, texto)

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
        if "marcadores" not in result:
            logger.warning(
                "pesquisar_marcadores: chave 'marcadores' ausente na resposta do scraper"
            )
        marcadores = result.get("marcadores", [])
        if limit >= 0:
            marcadores = marcadores[:limit]
        return {"marcadores": marcadores, "total_itens": len(marcadores)}
