"""Mixin REST — blocos internos e blocos de assinatura."""

from __future__ import annotations

import logging

import httpx

from todos.backends.rest._session import _RestMixin
from todos.backends.rest.catalogos import _validar_pagina
from todos.exceptions import SEIError, SEIValidationError

logger = logging.getLogger(__name__)


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

    async def incluir_documento_bloco_assinatura(
        self,
        id_bloco: str,
        documentos: str,
        _processo: str | None = None,
    ) -> dict:
        """Inclui documentos em um bloco de assinatura.

        `_processo` é ignorado neste backend: o REST resolve cada documento
        via Solr sem precisar do protocolo do processo (diferente do backend
        web, que exige um único processo por chamada — ver `BlocosWeb`).
        """
        return await self._rest.incluir_documento_bloco_assinatura(id_bloco, documentos)

    async def disponibilizar_bloco_assinatura(self, id_bloco: str) -> dict:
        """Disponibiliza um bloco de assinatura para os assinantes."""
        return await self._rest.disponibilizar_bloco_assinatura(id_bloco)

    async def cancelar_disponibilizacao_bloco_assinatura(self, id_bloco: str) -> dict:
        """Cancela a disponibilização de um bloco de assinatura."""
        return await self._rest.cancelar_disponibilizacao_bloco_assinatura(id_bloco)

    async def pesquisar_blocos_assinatura(
        self, filtro: str = "", limit: int = 50, pagina: int = 0
    ) -> dict:
        """Pesquisa blocos de assinatura existentes."""
        _validar_pagina(pagina)
        return await self._rest.pesquisar_blocos_assinatura(
            filtro=filtro, limit=limit, start=pagina
        )

    async def listar_documentos_bloco_assinatura(self, id_bloco: str) -> list[dict]:
        """Lista os documentos de um bloco de assinatura."""
        return await self._rest.listar_documentos_bloco_assinatura(id_bloco)

    async def retirar_documentos_bloco_assinatura(
        self, id_bloco: str, documentos: str
    ) -> list[dict]:
        """Retira documentos de um bloco de assinatura, um por chamada REST."""
        ids = [d.strip() for d in documentos.split(",") if d.strip()]
        return [
            await self._rest.retirar_documento_bloco_assinatura(id_bloco, id_doc) for id_doc in ids
        ]

    async def alterar_bloco_assinatura(self, id_bloco: str, descricao: str) -> dict:
        """Altera a descrição de um bloco de assinatura."""
        return await self._rest.alterar_bloco_assinatura(id_bloco, descricao)

    async def excluir_blocos_assinatura(self, ids_blocos: str) -> list[dict]:
        """Exclui blocos de assinatura, um por chamada REST."""
        ids = [b.strip() for b in ids_blocos.split(",") if b.strip()]
        return [await self._rest.excluir_blocos_assinatura(b) for b in ids]

    async def concluir_blocos_assinatura(self, ids_blocos: str) -> list[dict]:
        """Conclui blocos de assinatura, um por chamada REST."""
        ids = [b.strip() for b in ids_blocos.split(",") if b.strip()]
        return [await self._rest.concluir_blocos_assinatura(b) for b in ids]

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

    async def _resolver_id_usuario(self) -> str:
        """Resolve o id do usuário autenticado; levanta SEIValidationError se não encontrar."""
        login = self._rest.usuario
        id_usuario = await self._rest.garantir_autenticacao()
        if not id_usuario:
            try:
                res = await self._rest.listar_usuarios(filtro=login, apenas_unidade=False)
                for u in res.get("usuarios", []):
                    if u.get("sigla", "").lower() == login.lower():
                        raw = u.get("id_usuario")
                        if raw is not None:
                            id_usuario = str(raw)
                        break
            except (SEIError, httpx.HTTPError) as exc:
                logger.warning(
                    "Falha ao resolver id do usuário '%s' via listar_usuarios: %s", login, exc
                )
        if not id_usuario:
            msg = (
                f"Não foi possível resolver o id do usuário para o login '{login}'. "
                "Verifique se o login está correto e se o usuário pertence à unidade ativa."
            )
            raise SEIValidationError(msg)
        return id_usuario

    async def assinar_bloco(self, id_bloco: str, cargo: str = "") -> dict:
        """Assina todos os documentos de um bloco de assinatura."""
        id_usuario = await self._resolver_id_usuario()
        cred = self._rest.build_credenciais_assinatura(
            login=self._rest.usuario,
            cargo=cargo,
            id_usuario=id_usuario,
        )
        return await self._rest.assinar_bloco(id_bloco, cred)

    async def assinar_documentos_bloco(self, documentos: str, cargo: str = "") -> dict:
        """Assina documentos específicos de um bloco de assinatura."""
        id_usuario = await self._resolver_id_usuario()
        cred = self._rest.build_credenciais_assinatura(
            login=self._rest.usuario,
            cargo=cargo,
            id_usuario=id_usuario,
        )
        return await self._rest.assinar_documentos_bloco(cred, documentos)
