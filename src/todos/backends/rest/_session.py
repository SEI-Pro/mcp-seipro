"""Base de sessão do backend REST — cliente encapsulado e helpers de resolução.

`_RestBase` guarda o `SEIClient` (`self._rest`) e oferece os dois helpers de
resolução de referência compartilhados por todos os mixins: `_resolver_processo`
(protocolo → IdProcedimento) e `_resolver_documento` (número SEI → id interno).
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

import httpx

from todos.backends.models import FiltrosPesquisaProcessos
from todos.exceptions import SEIError, SEINotFoundError

if TYPE_CHECKING:
    from todos.sei_client import SEIClient

logger = logging.getLogger(__name__)

# Limiar mínimo de bytes para considerar um documento interno como não-vazio.
# O SEI retorna `"<br>"` (5 bytes) ou `"&nbsp;"` (6 bytes) para documentos sem
# conteúdo real. Qualquer conteúdo genuíno será mais longo que esses shells;
# 10 bytes é deliberadamente conservador para não descartar conteúdos finos mas válidos.
_MIN_DOC_CONTENT_LENGTH = 10


def _normalizar_protocolo(p: str) -> str:
    """Remove zeros à esquerda de segmentos numéricos do protocolo formatado.

    Aplica apenas após início da string ou separadores `.` `/` ` ` — não toca
    no sufixo de verificação `-XX` (ex: `2024-01` permanece `2024-01`).
    """
    return re.sub(r"(^|[./ ])0+(\d)", r"\1\2", p)


class _RestMixin:
    """Atributos/helpers compartilhados pelos mixins REST.

    Declarados apenas para o type-checker — em runtime são providos por
    `_RestBase` na classe composta `SEIRestBackend` (os mixins de domínio não
    os definem, apenas os usam via `self`).
    """

    if TYPE_CHECKING:
        _rest: SEIClient

        async def _resolver_processo(self, referencia: str) -> str: ...

        async def _resolver_documento(self, referencia: str) -> tuple[str, str]: ...


class _RestBase(_RestMixin):
    """Guarda o cliente REST e expõe os helpers de resolução de referência."""

    def __init__(self, client: SEIClient) -> None:
        """Armazena o cliente REST a ser encapsulado."""
        self._rest = client

    # ------------------------------------------------------------------
    # Helpers de resolução de referência
    # ------------------------------------------------------------------

    async def _resolver_processo(self, referencia: str) -> str:
        """Resolve uma referência de processo para o IdProcedimento."""
        referencia = referencia.strip()
        if "." in referencia or "/" in referencia:
            proc = await self._rest.consultar_processo(referencia)
            id_proc = str(proc.get("IdProcedimento", ""))
            if not id_proc:
                msg = f"Processo '{referencia}' não retornou IdProcedimento."
                raise SEINotFoundError(msg)
            return id_proc
        return referencia

    async def _resolver_documento(self, referencia: str) -> tuple[str, str]:
        """Resolve uma referência de documento para (id_interno, tipo_documento)."""
        referencia = referencia.strip()

        try:
            result = await self._rest.pesquisar_processos(
                FiltrosPesquisaProcessos(palavras_chave=referencia, limit=20)
            )
            processos = result.get("processos", [])
            for p in processos:
                id_proc = str(p.get("idProcedimento", ""))
                if not id_proc:
                    continue
                try:
                    docs = await self._rest.listar_documentos(id_proc, limit=200)
                except (SEIError, httpx.RequestError) as exc:
                    logger.warning(
                        "Falha ao listar documentos do processo %s ao resolver '%s': %s",
                        id_proc,
                        referencia,
                        exc,
                    )
                    continue
                for d in docs:
                    proto = d.get("atributos", {}).get("protocoloFormatado", "")
                    if proto == referencia or _normalizar_protocolo(proto) == _normalizar_protocolo(
                        referencia
                    ):
                        doc_id = str(d.get("id", ""))
                        if not doc_id:
                            continue
                        tipo = d.get("atributos", {}).get("tipoDocumento", "I")
                        return doc_id, tipo
        except (SEIError, httpx.RequestError) as exc:
            logger.warning(
                "Estratégia de pesquisa Solr falhou ao resolver documento '%s': %s",
                referencia,
                exc,
            )

        try:
            raw = await self._rest.visualizar_documento_interno(referencia)
            if raw and len(raw) > _MIN_DOC_CONTENT_LENGTH:
                return referencia, "I"
        except (SEIError, httpx.HTTPError) as exc:
            logger.warning(
                "Estratégia de visualização direta falhou ao resolver documento '%s': %s",
                referencia,
                exc,
            )

        msg = (
            f"Documento '{referencia}' não encontrado via pesquisa. "
            "Se é um documento recém-criado, o Solr pode não ter indexado ainda. "
            "Use sei_arvore_processo com o protocolo do processo para encontrá-lo."
        )
        raise SEINotFoundError(msg)
