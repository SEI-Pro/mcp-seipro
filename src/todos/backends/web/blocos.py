"""Mixin web: bloco de assinatura."""

from __future__ import annotations

from todos.backends.web._session import _WebMixin


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
        resultados = [
            await self._web.retirar_documento_bloco_assinatura_web(id_bloco, id_doc)
            for id_doc in (d.strip() for d in documentos.split(","))
            if id_doc
        ]
        if len(resultados) == 1:
            return resultados[0]
        return {"ok": True, "resultados": resultados}

    async def alterar_bloco_assinatura(self, id_bloco: str, descricao: str) -> dict:
        """Altera a descrição de um bloco de assinatura."""
        return await self._web.alterar_bloco_assinatura_web(id_bloco, descricao)

    async def excluir_blocos_assinatura(self, ids_blocos: str) -> dict:
        """Exclui blocos de assinatura."""
        resultados = [
            await self._web.excluir_bloco_assinatura_web(id_bloco)
            for id_bloco in (b.strip() for b in ids_blocos.split(","))
            if id_bloco
        ]
        if len(resultados) == 1:
            return resultados[0]
        return {"ok": True, "resultados": resultados}

    async def concluir_blocos_assinatura(self, ids_blocos: str) -> dict:
        """Conclui blocos de assinatura."""
        resultados = [
            await self._web.concluir_bloco_assinatura_web(id_bloco)
            for id_bloco in (b.strip() for b in ids_blocos.split(","))
            if id_bloco
        ]
        if len(resultados) == 1:
            return resultados[0]
        return {"ok": True, "resultados": resultados}

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
