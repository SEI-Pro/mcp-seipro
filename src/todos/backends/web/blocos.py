"""Mixin web: bloco de assinatura."""

from __future__ import annotations

import asyncio
import logging

from todos.backends.web._session import _WebMixin
from todos.exceptions import SEIError, SEIValidationError

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

    async def incluir_documento_bloco_assinatura(
        self, id_bloco: str, documentos: str, processo: str | None = None
    ) -> dict:
        """Inclui um documento num bloco de assinatura.

        Um bloco de assinatura agrupa documentos de PROCESSOS DIFERENTES —
        um único `processo` não serve pra resolver múltiplos `documentos`
        de processos distintos (diferente do REST, que resolve cada
        documento via Solr sem precisar do protocolo). Por isso, no backend
        web esta operação é deliberadamente unitária: um documento por
        chamada. Para incluir vários documentos (do mesmo processo ou de
        processos diferentes), chame esta tool uma vez por documento.
        """
        if processo is None:
            msg = (
                "Em instâncias sem mod-wssei, forneça o parâmetro 'processo' "
                "para incluir documento em bloco de assinatura."
            )
            raise SEIValidationError(msg)
        ids = [d.strip() for d in documentos.split(",") if d.strip()]
        if not ids:
            msg = "Lista vazia — forneça pelo menos um id."
            raise SEIValidationError(msg)
        if len(ids) > 1:
            msg = (
                f"Backend web só aceita 1 documento por chamada (recebeu {len(ids)}) "
                "— um bloco agrupa documentos de processos diferentes, e um único "
                "'processo' não serve pra resolver vários documentos que podem "
                "pertencer a processos distintos. Chame esta tool uma vez por "
                "documento, cada uma com o 'processo' correto."
            )
            raise SEIValidationError(msg)
        coros = [
            self._web.incluir_documento_bloco_assinatura_web(processo, id_doc, id_bloco)
            for id_doc in ids
        ]
        raw = await asyncio.gather(*coros, return_exceptions=True)
        erros: list[str] = []
        resultados: list[dict] = []
        for id_doc, outcome in zip(ids, raw, strict=True):
            if not isinstance(outcome, Exception) and isinstance(outcome, BaseException):
                raise outcome
            if isinstance(outcome, Exception):
                logger.warning(
                    "Falha ao incluir documento %s no bloco %s: %s", id_doc, id_bloco, outcome
                )
                erros.append(f"{id_doc}: {outcome}")
            else:
                resultados.append(outcome)
        if erros and not resultados:
            msg = f"Falha ao incluir todos os documentos: {'; '.join(erros)}"
            raise SEIError(msg)
        return {"ok": True, "idBloco": id_bloco, "resultados": resultados, "erros": erros}

    async def cancelar_disponibilizacao_bloco_assinatura(self, id_bloco: str) -> dict:
        """Cancela a disponibilização de um bloco de assinatura."""
        return await self._web.cancelar_disponibilizacao_bloco_assinatura_web(id_bloco)

    async def pesquisar_blocos_assinatura(
        self, filtro: str = "", limit: int = 50, pagina: int = 0
    ) -> dict:
        """Pesquisa blocos de assinatura existentes."""
        del pagina  # web backend não suporta paginação por offset
        return await self._web.pesquisar_blocos_assinatura_web(filtro=filtro, limit=limit)

    async def listar_documentos_bloco_assinatura(self, id_bloco: str) -> list[dict]:
        """Lista os documentos de um bloco de assinatura."""
        return await self._web.listar_documentos_bloco_assinatura_web(id_bloco)

    async def retirar_documentos_bloco_assinatura(
        self, id_bloco: str, documentos: str
    ) -> list[dict]:
        """Retira documentos de um bloco de assinatura.

        Executa os documentos em paralelo via asyncio.gather; falhas individuais
        são registradas como aviso e excluídas do resultado. Se todos falharem,
        levanta SEIError.
        """
        ids = [d.strip() for d in documentos.split(",") if d.strip()]
        if not ids:
            msg = "Lista vazia — forneça pelo menos um id."
            raise SEIValidationError(msg)
        coros = [
            self._web.retirar_documento_bloco_assinatura_web(id_bloco, id_doc) for id_doc in ids
        ]
        raw = await asyncio.gather(*coros, return_exceptions=True)
        erros: list[str] = []
        resultados: list[dict] = []
        for id_doc, outcome in zip(ids, raw, strict=True):
            if not isinstance(outcome, Exception) and isinstance(outcome, BaseException):
                raise outcome
            if isinstance(outcome, Exception):
                logger.warning(
                    "Falha ao retirar documento %s do bloco %s: %s", id_doc, id_bloco, outcome
                )
                erros.append(f"{id_doc}: {outcome}")
            else:
                resultados.append(outcome)
        if erros and not resultados:
            msg = f"Falha ao retirar todos os documentos: {'; '.join(erros)}"
            raise SEIError(msg)
        return resultados

    async def alterar_bloco_assinatura(self, id_bloco: str, descricao: str) -> dict:
        """Altera a descrição de um bloco de assinatura."""
        return await self._web.alterar_bloco_assinatura_web(id_bloco, descricao)

    async def excluir_blocos_assinatura(self, ids_blocos: str) -> list[dict]:
        """Exclui blocos de assinatura.

        Executa os blocos em paralelo via asyncio.gather; falhas individuais
        são registradas como aviso e excluídas do resultado. Se todos falharem,
        levanta SEIError.
        """
        ids = [b.strip() for b in ids_blocos.split(",") if b.strip()]
        if not ids:
            msg = "Lista vazia — forneça pelo menos um id."
            raise SEIValidationError(msg)
        coros = [self._web.excluir_bloco_assinatura_web(id_bloco) for id_bloco in ids]
        raw = await asyncio.gather(*coros, return_exceptions=True)
        erros: list[str] = []
        resultados: list[dict] = []
        for id_bloco, outcome in zip(ids, raw, strict=True):
            if not isinstance(outcome, Exception) and isinstance(outcome, BaseException):
                raise outcome
            if isinstance(outcome, Exception):
                logger.warning("Falha ao excluir bloco %s: %s", id_bloco, outcome)
                erros.append(f"{id_bloco}: {outcome}")
            else:
                resultados.append(outcome)
        if erros and not resultados:
            msg = f"Falha ao excluir todos os blocos: {'; '.join(erros)}"
            raise SEIError(msg)
        return resultados

    async def concluir_blocos_assinatura(self, ids_blocos: str) -> list[dict]:
        """Conclui blocos de assinatura.

        Executa os blocos em paralelo via asyncio.gather; falhas individuais
        são registradas como aviso e excluídas do resultado. Se todos falharem,
        levanta SEIError.
        """
        ids = [b.strip() for b in ids_blocos.split(",") if b.strip()]
        if not ids:
            msg = "Lista vazia — forneça pelo menos um id."
            raise SEIValidationError(msg)
        coros = [self._web.concluir_bloco_assinatura_web(id_bloco) for id_bloco in ids]
        raw = await asyncio.gather(*coros, return_exceptions=True)
        erros: list[str] = []
        resultados: list[dict] = []
        for id_bloco, outcome in zip(ids, raw, strict=True):
            if not isinstance(outcome, Exception) and isinstance(outcome, BaseException):
                raise outcome
            if isinstance(outcome, Exception):
                logger.warning("Falha ao concluir bloco %s: %s", id_bloco, outcome)
                erros.append(f"{id_bloco}: {outcome}")
            else:
                resultados.append(outcome)
        if erros and not resultados:
            msg = f"Falha ao concluir todos os blocos: {'; '.join(erros)}"
            raise SEIError(msg)
        return resultados

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
        """Altera a anotação de um documento em um bloco de assinatura.

        Alias de `anotar_documento_bloco_assinatura`: o SEI usa o mesmo endpoint
        para criar e alterar anotações de bloco (idempotente via POST).
        """
        return await self._web.anotar_documento_bloco_assinatura_web(id_bloco, documento, descricao)
