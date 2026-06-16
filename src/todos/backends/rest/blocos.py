"""Mixin REST — blocos internos e blocos de assinatura."""

from __future__ import annotations

from todos.backends.rest._session import _RestMixin


class BlocosRest(_RestMixin):
    """Operações REST de blocos internos e blocos de assinatura."""

    # ------------------------------------------------------------------
    # Bloco interno
    # ------------------------------------------------------------------

    async def criar_bloco_interno(self, descricao: str) -> dict:
        """Cria um bloco interno."""
        return await self._rest.criar_bloco_interno(descricao)

    async def incluir_processo_bloco_interno(self, id_bloco: str, processos: str) -> dict:
        """Inclui processos em um bloco interno."""
        return await self._rest.incluir_processo_bloco_interno(id_bloco, processos)

    async def retirar_processo_bloco_interno(self, id_bloco: str, processos: str) -> dict:
        """Retira processos de um bloco interno."""
        return await self._rest.retirar_processo_bloco_interno(id_bloco, processos)

    async def listar_processos_bloco_interno(self, id_bloco: str) -> list[dict]:
        """Lista os processos de um bloco interno."""
        return await self._rest.listar_processos_bloco_interno(id_bloco)

    async def alterar_bloco_interno(self, id_bloco: str, descricao: str) -> dict:
        """Altera a descrição de um bloco interno."""
        return await self._rest.alterar_bloco_interno(id_bloco, descricao)

    async def excluir_blocos_internos(self, ids_blocos: str) -> dict:
        """Exclui blocos internos."""
        return await self._rest.excluir_blocos_internos(ids_blocos)

    async def concluir_blocos_internos(self, ids_blocos: str) -> dict:
        """Conclui blocos internos."""
        return await self._rest.concluir_blocos_internos(ids_blocos)

    async def reabrir_bloco_interno(self, id_bloco: str) -> dict:
        """Reabre um bloco interno concluído."""
        return await self._rest.reabrir_bloco_interno(id_bloco)

    async def anotar_processo_bloco_interno(
        self, id_bloco: str, processo: str, descricao: str
    ) -> dict:
        """Anota um processo dentro de um bloco interno.

        Note: calls ``_resolver_processo`` to translate the formatted protocol
        number to an internal id.  If the caller needs to invoke both
        ``anotar_processo_bloco_interno`` and ``alterar_anotacao_bloco_interno``
        for the same process, two separate REST resolution calls will be made.
        """
        id_proc = await self._resolver_processo(processo)
        return await self._rest.anotar_processo_bloco_interno(id_bloco, id_proc, descricao)

    async def alterar_anotacao_bloco_interno(
        self, id_bloco: str, processo: str, descricao: str
    ) -> dict:
        """Altera a anotação de um processo em um bloco interno.

        Note: calls ``_resolver_processo`` to translate the formatted protocol
        number to an internal id.  If the caller needs to invoke both
        ``anotar_processo_bloco_interno`` and ``alterar_anotacao_bloco_interno``
        for the same process, two separate REST resolution calls will be made.
        """
        id_proc = await self._resolver_processo(processo)
        return await self._rest.alterar_anotacao_bloco_interno(id_bloco, id_proc, descricao)

    # ------------------------------------------------------------------
    # Bloco de assinatura
    # ------------------------------------------------------------------

    async def criar_bloco_assinatura(self, descricao: str, unidades: str = "") -> dict:
        """Cria um bloco de assinatura."""
        return await self._rest.criar_bloco_assinatura(descricao, unidades)

    async def incluir_documento_bloco_assinatura(self, id_bloco: str, documentos: str) -> dict:
        """Inclui documentos em um bloco de assinatura."""
        return await self._rest.incluir_documento_bloco_assinatura(id_bloco, documentos)

    async def disponibilizar_bloco_assinatura(self, id_bloco: str) -> dict:
        """Disponibiliza um bloco de assinatura para os assinantes."""
        return await self._rest.disponibilizar_bloco_assinatura(id_bloco)

    async def cancelar_disponibilizacao_bloco_assinatura(self, id_bloco: str) -> dict:
        """Cancela a disponibilização de um bloco de assinatura."""
        return await self._rest.cancelar_disponibilizacao_bloco_assinatura(id_bloco)

    async def pesquisar_blocos_assinatura(self, filtro: str = "", limit: int = 50) -> dict:
        """Pesquisa blocos de assinatura existentes."""
        return await self._rest.pesquisar_blocos_assinatura(filtro=filtro, limit=limit)

    async def listar_documentos_bloco_assinatura(self, id_bloco: str) -> list[dict]:
        """Lista os documentos de um bloco de assinatura."""
        return await self._rest.listar_documentos_bloco_assinatura(id_bloco)

    async def retirar_documentos_bloco_assinatura(self, id_bloco: str, documentos: str) -> dict:
        """Retira documentos de um bloco de assinatura."""
        return await self._rest.retirar_documento_bloco_assinatura(id_bloco, documentos)

    async def alterar_bloco_assinatura(self, id_bloco: str, descricao: str) -> dict:
        """Altera a descrição de um bloco de assinatura."""
        return await self._rest.alterar_bloco_assinatura(id_bloco, descricao)

    async def excluir_blocos_assinatura(self, ids_blocos: str) -> dict:
        """Exclui blocos de assinatura."""
        return await self._rest.excluir_blocos_assinatura(ids_blocos)

    async def concluir_blocos_assinatura(self, ids_blocos: str) -> dict:
        """Conclui blocos de assinatura."""
        return await self._rest.concluir_blocos_assinatura(ids_blocos)

    async def reabrir_bloco_assinatura(self, id_bloco: str) -> dict:
        """Reabre um bloco de assinatura concluído."""
        return await self._rest.reabrir_bloco_assinatura(id_bloco)

    async def retornar_bloco_assinatura(self, id_bloco: str) -> dict:
        """Retorna um bloco de assinatura à unidade de origem."""
        return await self._rest.retornar_bloco_assinatura(id_bloco)

    async def anotar_documento_bloco_assinatura(
        self, id_bloco: str, documento: str, descricao: str
    ) -> dict:
        """Anota um documento dentro de um bloco de assinatura."""
        return await self._rest.anotar_documento_bloco_assinatura(id_bloco, documento, descricao)

    async def alterar_anotacao_bloco_assinatura(
        self, id_bloco: str, documento: str, descricao: str
    ) -> dict:
        """Altera a anotação de um documento em um bloco de assinatura."""
        return await self._rest.alterar_anotacao_bloco_assinatura(id_bloco, documento, descricao)

    async def assinar_bloco(self, id_bloco: str, cargo: str = "") -> dict:
        """Assina todos os documentos de um bloco de assinatura."""
        id_usuario = await self._rest.garantir_autenticacao()
        return await self._rest.assinar_bloco(
            id_bloco=id_bloco,
            login=self._rest.usuario,
            senha=self._rest.senha,
            cargo=cargo,
            id_usuario=id_usuario,
        )

    async def assinar_documentos_bloco(self, documentos: str, cargo: str = "") -> dict:
        """Assina documentos específicos de um bloco de assinatura."""
        id_usuario = await self._rest.garantir_autenticacao()
        return await self._rest.assinar_documentos_bloco(
            login=self._rest.usuario,
            senha=self._rest.senha,
            cargo=cargo,
            documentos=documentos,
            id_usuario=id_usuario,
        )
