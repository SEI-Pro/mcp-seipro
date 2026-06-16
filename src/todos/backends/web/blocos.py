"""Mixin web: bloco de assinatura."""

from __future__ import annotations

import logging

import httpx

from todos.backends.web._session import _WebMixin
from todos.exceptions import SEIError

logger = logging.getLogger(__name__)


class BlocosWeb(_WebMixin):
    """Operações web de bloco de assinatura."""

    async def criar_bloco_assinatura(self, descricao: str, unidades: str = "") -> dict:
        """Cria um bloco de assinatura."""
        del (
            unidades
        )  # contrato exige o parâmetro; o bloco web é criado sem unidades pré-configuradas
        return await self._web.criar_bloco_assinatura_web(descricao=descricao)

    async def disponibilizar_bloco_assinatura(self, id_bloco: str) -> dict:
        """Disponibiliza um bloco de assinatura para os assinantes."""
        return await self._web.disponibilizar_bloco_assinatura_web(id_bloco)

    async def cancelar_disponibilizacao_bloco_assinatura(self, id_bloco: str) -> dict:
        """Cancela a disponibilização de um bloco de assinatura."""
        return await self._web.cancelar_disponibilizacao_bloco_assinatura_web(id_bloco)

    async def pesquisar_blocos_assinatura(self, filtro: str = "", limit: int = 50) -> dict:
        """Pesquisa blocos de assinatura existentes."""
        return await self._web.pesquisar_blocos_assinatura_web(filtro=filtro, limit=limit)

    async def listar_documentos_bloco_assinatura(self, id_bloco: str) -> list[dict]:
        """Lista os documentos de um bloco de assinatura."""
        return await self._web.listar_documentos_bloco_assinatura_web(id_bloco)

    async def retirar_documentos_bloco_assinatura(self, id_bloco: str, documentos: str) -> dict:
        """Retira documentos de um bloco de assinatura."""
        ids = [d.strip() for d in documentos.split(",") if d.strip()]
        erros: list[str] = []
        resultados: list[dict] = []
        for id_doc in ids:
            try:
                r = await self._web.retirar_documento_bloco_assinatura_web(id_bloco, id_doc)
                resultados.append(r)
            except (SEIError, httpx.HTTPError) as _e:
                logger.warning(
                    "Falha ao retirar documento %s do bloco %s: %s", id_doc, id_bloco, _e
                )
                erros.append(f"{id_doc}: {_e}")
        if erros and not resultados:
            msg = f"Falha ao retirar todos os documentos: {'; '.join(erros)}"
            raise SEIError(msg)
        if erros:
            logger.warning("Falha parcial ao retirar documentos do bloco: %s", "; ".join(erros))
        return resultados[0] if len(resultados) == 1 else {"ok": True, "resultados": resultados}

    async def alterar_bloco_assinatura(self, id_bloco: str, descricao: str) -> dict:
        """Altera a descrição de um bloco de assinatura."""
        return await self._web.alterar_bloco_assinatura_web(id_bloco, descricao)

    async def excluir_blocos_assinatura(self, ids_blocos: str) -> dict:
        """Exclui blocos de assinatura."""
        ids = [b.strip() for b in ids_blocos.split(",") if b.strip()]
        erros: list[str] = []
        resultados: list[dict] = []
        for id_bloco in ids:
            try:
                r = await self._web.excluir_bloco_assinatura_web(id_bloco)
                resultados.append(r)
            except (SEIError, httpx.HTTPError) as _e:
                logger.warning("Falha ao excluir bloco %s: %s", id_bloco, _e)
                erros.append(f"{id_bloco}: {_e}")
        if erros and not resultados:
            msg = f"Falha ao excluir todos os blocos: {'; '.join(erros)}"
            raise SEIError(msg)
        if erros:
            logger.warning("Falha parcial ao excluir blocos de assinatura: %s", "; ".join(erros))
        return resultados[0] if len(resultados) == 1 else {"ok": True, "resultados": resultados}

    async def concluir_blocos_assinatura(self, ids_blocos: str) -> dict:
        """Conclui blocos de assinatura."""
        ids = [b.strip() for b in ids_blocos.split(",") if b.strip()]
        erros: list[str] = []
        resultados: list[dict] = []
        for id_bloco in ids:
            try:
                r = await self._web.concluir_bloco_assinatura_web(id_bloco)
                resultados.append(r)
            except (SEIError, httpx.HTTPError) as _e:
                logger.warning("Falha ao concluir bloco %s: %s", id_bloco, _e)
                erros.append(f"{id_bloco}: {_e}")
        if erros and not resultados:
            msg = f"Falha ao concluir todos os blocos: {'; '.join(erros)}"
            raise SEIError(msg)
        if erros:
            logger.warning("Falha parcial ao concluir blocos de assinatura: %s", "; ".join(erros))
        return resultados[0] if len(resultados) == 1 else {"ok": True, "resultados": resultados}

    async def reabrir_bloco_assinatura(self, id_bloco: str) -> dict:
        """Reabre um bloco de assinatura concluído."""
        return await self._web.reabrir_bloco_assinatura_web(id_bloco)

    async def retornar_bloco_assinatura(self, id_bloco: str) -> dict:
        """Retorna um bloco de assinatura à unidade de origem."""
        return await self._web.retornar_bloco_assinatura_web(id_bloco)

    async def anotar_documento_bloco_assinatura(
        self, id_bloco: str, documento: str, descricao: str
    ) -> dict:
        """Anota um documento dentro de um bloco de assinatura."""
        return await self._web.anotar_documento_bloco_assinatura_web(id_bloco, documento, descricao)

    async def alterar_anotacao_bloco_assinatura(
        self, id_bloco: str, documento: str, descricao: str
    ) -> dict:
        """Altera a anotação de um documento em um bloco de assinatura."""
        return await self._web.anotar_documento_bloco_assinatura_web(id_bloco, documento, descricao)
