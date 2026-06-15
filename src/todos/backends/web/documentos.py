"""Mixin web: documentos.

Erros específicos deste domínio são definidos aqui e levantados pelos métodos que
conhecem o contexto, via `try/except … raise XxxError from e`.
"""

from __future__ import annotations

import base64
from typing import TYPE_CHECKING, TypeVar

from todos.backends.web._session import _WebMixin
from todos.exceptions import DocumentoNaoAutorizadoError, SEIError, SEINotImplementedError

if TYPE_CHECKING:
    from collections.abc import Awaitable

    from todos.backends.base import NovoDocumentoExterno, NovoDocumentoInterno

_T = TypeVar("_T")


def _traduzir_erro_documento(e: SEIError) -> SEIError | None:
    """Mapeia um erro web de documento numa exceção específica, ou None se desconhecido."""
    low = str(e).lower()
    if "não autorizado" in low or "nao autorizado" in low or "acesso negado" in low:
        return DocumentoNaoAutorizadoError(
            "Acesso ao documento negado. Confirme que o processo está aberto na sua "
            "unidade (sei_trocar_unidade) e que você tem acesso ao documento."
        )
    return None


class DocumentosWeb(_WebMixin):
    """Operações web de documentos."""

    async def _doc(self, coro: Awaitable[_T]) -> _T:
        """Executa uma chamada de documento, traduzindo erros conhecidos do SEI."""
        try:
            return await coro
        except SEIError as e:
            especifico = _traduzir_erro_documento(e)
            if especifico is not None:
                raise especifico from e
            raise

    async def buscar_documento(self, numero_sei: str, processo: str = "") -> dict:
        """Busca um documento pelo número SEI."""
        numero_sei = numero_sei.strip()

        def _match(proto: str) -> bool:
            return proto == numero_sei or proto.lstrip("0") == numero_sei.lstrip("0")

        if processo:
            result_web = await self._web.listar_documentos(processo)
            for d in result_web.get("documentos", []):
                if _match(d.get("numero_sei", "")):
                    return {"encontrado": True, "processo": processo, "documento": d}
            return {
                "encontrado": False,
                "mensagem": (f"SEI {numero_sei} não encontrado na árvore do processo {processo}"),
            }

        result_pesq = await self._web.pesquisar_processos_web(q=numero_sei)
        candidatos = result_pesq.get("processos", [])
        for p in candidatos:
            proto_proc = p.get("protocolo", "")
            if not proto_proc:
                continue
            result_web = await self._web.listar_documentos(proto_proc)
            for d in result_web.get("documentos", []):
                if _match(d.get("numero_sei", "")):
                    return {"encontrado": True, "processo": proto_proc, "documento": d}
        return {
            "encontrado": False,
            "processos_pesquisados": len(candidatos),
            "mensagem": f"SEI {numero_sei} não encontrado via pesquisa web",
            "dica": "Informe o protocolo do processo (parâmetro processo=) para busca direta.",
        }

    async def consultar_documento_externo(
        self, id_documento: str, processo: str | None = None
    ) -> dict:
        """Consulta metadados de um documento externo."""
        if processo is None:
            msg = (
                "Em instâncias sem mod-wssei, forneça o parâmetro 'processo' "
                "para consultar metadados de documento."
            )
            raise SEINotImplementedError(msg)
        return await self._doc(self._web.consultar_documento_web(processo, id_documento))

    async def consultar_documento_interno(
        self, id_documento: str, processo: str | None = None
    ) -> dict:
        """Consulta metadados de um documento interno (mesma tela genérica do web)."""
        if processo is None:
            msg = (
                "Em instâncias sem mod-wssei, forneça o parâmetro 'processo' "
                "para consultar metadados de documento."
            )
            raise SEINotImplementedError(msg)
        return await self._doc(self._web.consultar_documento_web(processo, id_documento))

    async def baixar_anexo(self, id_documento: str, processo: str | None = None) -> bytes:
        """Baixa os bytes de um documento externo (anexo)."""
        if processo is None:
            msg = "Em instâncias sem mod-wssei, forneça o parâmetro 'processo' para baixar anexos."
            raise SEINotImplementedError(msg)
        return await self._doc(self._web.baixar_documento_externo_web(processo, id_documento))

    async def visualizar_documento_interno(
        self, id_documento: str, processo: str | None = None
    ) -> str:
        """Retorna o HTML de um documento interno."""
        if processo is None:
            msg = (
                "Em instâncias sem mod-wssei, forneça o parâmetro 'processo' "
                "para ler um documento interno."
            )
            raise SEINotImplementedError(msg)
        return await self._doc(self._web.visualizar_documento_interno_web(processo, id_documento))

    async def criar_documento_interno(self, processo: str, dados: NovoDocumentoInterno) -> dict:
        """Cria um documento interno (editor HTML) em um processo."""
        return await self._web.criar_documento_interno_web(
            protocolo=processo,
            id_serie=dados.id_serie,
            descricao=dados.descricao,
            nivel_acesso=dados.nivel_acesso,
            hipotese_legal=dados.hipotese_legal,
        )

    async def criar_documento_externo(self, processo: str, dados: NovoDocumentoExterno) -> dict:
        """Cria um documento externo (upload de arquivo) em um processo."""
        conteudo: bytes | None = None
        if dados.arquivo_base64:
            conteudo = base64.b64decode(dados.arquivo_base64, validate=True)
        return await self._web.incluir_documento_externo(
            protocolo_formatado=processo,
            arquivo_path=dados.arquivo_path or None,
            nome_arquivo=dados.nome_arquivo or None,
            id_serie=dados.id_serie or None,
            data_elaboracao=dados.data_elaboracao,
            nivel_acesso=dados.nivel_acesso,
            hipotese_legal=dados.hipotese_legal,
            conteudo=conteudo,
        )

    async def listar_assinaturas(
        self, id_documento: str, processo: str | None = None
    ) -> list[dict]:
        """Lista as assinaturas de um documento."""
        if processo is None:
            msg = (
                "Em instâncias sem mod-wssei, forneça o parâmetro 'processo' "
                "para listar assinaturas."
            )
            raise SEINotImplementedError(msg)
        return await self._web.listar_assinaturas_web(processo, id_documento)

    async def dar_ciencia(self, referencia: str, tipo: str = "documento") -> dict:
        """Registra ciência em um documento ou processo."""
        if tipo != "processo":
            msg = (
                "Dar ciência em documento requer mod-wssei (REST). "
                "Configure SEI_URL ou use tipo='processo'."
            )
            raise SEINotImplementedError(msg)
        return await self._web.executar_acao_processo(referencia, "processo_dar_ciencia")

    async def listar_ciencias(
        self, referencia: str, tipo: str = "documento", processo: str | None = None
    ) -> list[dict]:
        """Lista as ciências de um documento ou processo."""
        if tipo == "processo":
            msg = (
                "Listar ciências de processo requer mod-wssei (REST). "
                "Configure SEI_URL para habilitar esta funcionalidade."
            )
            raise SEINotImplementedError(msg)
        if processo is None:
            msg = (
                "Em instâncias sem mod-wssei, forneça 'processo' para listar ciências de documento."
            )
            raise SEINotImplementedError(msg)
        return await self._web.listar_ciencias_web(processo, referencia)
