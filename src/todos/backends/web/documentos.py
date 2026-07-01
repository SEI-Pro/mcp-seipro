"""Mixin web: documentos.

Sem tradução de erros: cada método delega ao scraper, cujo SEIError (com a
mensagem do SEI) propaga sem reembrulho. Onde o backend web simplesmente não
serve a operação (ex.: falta `processo`), levanta `SEINotImplementedError`.
"""

from __future__ import annotations

import base64
import logging
from typing import TYPE_CHECKING

from todos.backends.models import DocumentoExternoInclusaoWeb
from todos.backends.web._session import _WebMixin
from todos.exceptions import SEINotFoundError, SEINotImplementedError, SEIValidationError
from todos.html_utils import sanitize_iso8859

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from collections.abc import Callable

    from todos.backends.base import NovoDocumentoExterno, NovoDocumentoInterno


class DocumentosWeb(_WebMixin):
    """Operações web de documentos."""

    async def _encontrar_em_processo(
        self, proto_proc: str, _match: Callable[[str], bool]
    ) -> dict | None:
        """Busca um documento pelo número SEI dentro de um processo específico."""
        result_web = await self._web.listar_documentos(proto_proc)
        for d in result_web.get("documentos", []):
            _num = d.get("numero_sei")
            if _num is None:
                logger.warning("Documento sem chave 'numero_sei' no resultado do scraper: %r", d)
                continue
            if _match(_num):
                return {"encontrado": True, "processo": proto_proc, "documento": d}
        return None

    async def buscar_documento(self, numero_sei: str, processo: str = "") -> dict:
        """Busca um documento pelo número SEI."""
        numero_sei = numero_sei.strip()
        if not numero_sei:
            msg = "numero_sei não pode ser vazio"
            raise SEIValidationError(msg)

        def _match(proto: str) -> bool:
            return proto == numero_sei or proto.lstrip("0") == numero_sei.lstrip("0")

        if processo:
            encontrado = await self._encontrar_em_processo(processo, _match)
            if encontrado:
                return encontrado
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
            encontrado = await self._encontrar_em_processo(proto_proc, _match)
            if encontrado:
                return encontrado
        # processos_pesquisados: metadado de diagnóstico — quantos processos candidatos
        # foram inspecionados. Não é uma chave do schema SEI; existe apenas no
        # response de erro desta tool, para orientar o usuário a refinar a busca.
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
        return await self._web.consultar_documento_web(processo, id_documento)

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
        return await self._web.consultar_documento_web(processo, id_documento)

    async def baixar_anexo(self, id_documento: str, processo: str | None = None) -> bytes:
        """Baixa os bytes de um documento externo (anexo)."""
        if processo is None:
            msg = "Em instâncias sem mod-wssei, forneça o parâmetro 'processo' para baixar anexos."
            raise SEINotImplementedError(msg)
        return await self._web.baixar_documento_externo_web(processo, id_documento)

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
        return await self._web.visualizar_documento_interno_web(processo, id_documento)

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
            processo,
            DocumentoExternoInclusaoWeb(
                arquivo_path=dados.arquivo_path or None,
                nome_arquivo=dados.nome_arquivo or None,
                id_serie=dados.id_serie or None,
                data_elaboracao=dados.data_elaboracao,
                nivel_acesso=dados.nivel_acesso,
                hipotese_legal=dados.hipotese_legal,
                conteudo=conteudo,
            ),
        )

    async def listar_secoes(self, id_documento: str, processo: str | None = None) -> dict:
        """Lista as seções editáveis de um documento interno via editor_montar."""
        if processo is None:
            msg = (
                "Em instâncias sem mod-wssei, forneça o parâmetro 'processo' "
                "para listar seções de documento."
            )
            raise SEINotImplementedError(msg)
        return await self._web.listar_secoes_web(processo, id_documento)

    async def alterar_secoes(
        self, id_documento: str, secoes: list[dict], versao: str = "", processo: str | None = None
    ) -> dict:
        """Edita seções de um documento interno via editor_montar."""
        del versao  # web relê o form; versão não é enviada como parâmetro
        if processo is None:
            msg = (
                "Em instâncias sem mod-wssei, forneça o parâmetro 'processo' "
                "para editar seções de documento."
            )
            raise SEINotImplementedError(msg)
        # Sanitizar conteúdo para ISO-8859-1 antes de enviar ao SEI
        secoes_sanitizadas = [
            {**s, "conteudo": sanitize_iso8859(s.get("conteudo", ""))} for s in secoes
        ]
        return await self._web.alterar_secoes_web(processo, id_documento, secoes_sanitizadas)

    async def alterar_documento_interno(
        self,
        id_documento: str,
        descricao: str = "",
        nivel_acesso: str = "",
        hipotese_legal: str = "",
        processo: str | None = None,
    ) -> dict:
        """Altera metadados de um documento interno via documento_alterar."""
        if processo is None:
            msg = (
                "Em instâncias sem mod-wssei, forneça o parâmetro 'processo' "
                "para alterar um documento interno."
            )
            raise SEINotImplementedError(msg)
        return await self._web.alterar_documento_interno_web(
            processo,
            id_documento,
            descricao=descricao,
            nivel_acesso=nivel_acesso,
            hipotese_legal=hipotese_legal,
        )

    async def resolver_documento(self, referencia: str) -> tuple[str, str]:
        """Resolve referência de documento via pesquisa web.

        Tenta busca direta; se não encontrar, faz pesquisa web por número SEI.
        Retorna (id_interno, tipo) onde tipo é 'I', 'X' ou 'auto'.
        """
        result = await self.buscar_documento(referencia)
        if not result.get("encontrado"):
            msg = (
                f"Documento SEI '{referencia}' não encontrado via pesquisa web. "
                "Informe o parâmetro processo= para busca direta, "
                "ou use sei_arvore_processo para encontrar o id interno."
            )
            raise SEINotFoundError(msg)
        doc = result["documento"]
        # tipo_documento do scraper é label humano (ex: "Despacho") — não os códigos "I"/"X".
        # Normalizar para "auto" quando não for código canônico.
        raw_tipo = doc.get("tipo_documento", "")
        tipo = raw_tipo if raw_tipo in ("I", "X") else "auto"
        return doc["id"], tipo

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

    async def listar_blocos_documento(self, id_documento: str) -> list[dict]:
        """Lista os blocos de assinatura que contêm um documento (REST-only)."""
        del id_documento  # contrato exige o parâmetro; operação só disponível via REST
        msg = (
            "Listar blocos de um documento requer mod-wssei (REST). "
            "Configure SEI_URL para habilitar esta funcionalidade."
        )
        raise SEINotImplementedError(msg)

    async def sugestao_assuntos_documento(self, id_serie: str) -> list[dict]:
        """Sugere assuntos para um tipo de documento (REST-only)."""
        del id_serie  # contrato exige o parâmetro; operação só disponível via REST
        msg = (
            "Sugestão de assuntos por tipo requer mod-wssei (REST). "
            "Configure SEI_URL para habilitar esta funcionalidade."
        )
        raise SEINotImplementedError(msg)

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
