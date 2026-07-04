"""Mixin REST — documentos internos, externos, seções, assinaturas e ciências.

Sem tradução de erros: cada método delega ao SEIClient, que já levanta o SEIError
adequado (com a mensagem do SEI). O erro original propaga sem reembrulho.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import httpx

from todos.backends.models import FiltrosPesquisaProcessos
from todos.backends.rest._session import _RestMixin
from todos.exceptions import SEIError, SEIValidationError

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from todos.backends.models import NovoDocumentoExterno, NovoDocumentoInterno


class DocumentosRest(_RestMixin):
    """Operações REST de documentos."""

    async def buscar_documento(self, numero_sei: str, processo: str = "") -> dict:
        """Busca um documento pelo número SEI via pesquisa textual REST (Solr)."""
        numero_sei = numero_sei.strip()

        def _match(proto: str) -> bool:
            return proto == numero_sei or proto.lstrip("0") == numero_sei.lstrip("0")

        if processo:
            id_procedimento = await self._resolver_processo(processo)
            docs = await self._rest.listar_documentos(id_procedimento, limit=200)
            for d in docs:
                if _match(d.get("atributos", {}).get("protocoloFormatado", "")):
                    return {"encontrado": True, "id_procedimento": id_procedimento, "documento": d}
            return {
                "encontrado": False,
                "mensagem": f"SEI {numero_sei} não encontrado no processo {id_procedimento}",
            }

        result = await self._rest.pesquisar_processos(
            FiltrosPesquisaProcessos(palavras_chave=numero_sei, limit=20)
        )
        candidatos = result.get("processos", [])
        for p in candidatos:
            id_proc = str(p.get("idProcedimento", ""))
            if not id_proc:
                continue
            try:
                docs = await self._rest.listar_documentos(id_proc, limit=200)
            except (SEIError, httpx.HTTPError) as exc:
                logger.warning("Processo inacessível ao buscar documentos (%s): %s", id_proc, exc)
                continue
            for d in docs:
                if _match(d.get("atributos", {}).get("protocoloFormatado", "")):
                    return {
                        "encontrado": True,
                        "processo": p.get("protocoloFormatadoProcedimento", ""),
                        "id_procedimento": id_proc,
                        "documento": d,
                    }
        logger.debug(
            "buscar_documento: %s não encontrado (%d candidatos)", numero_sei, len(candidatos)
        )
        return {
            "encontrado": False,
            "processos_pesquisados": len(candidatos),
            "mensagem": f"SEI {numero_sei} não encontrado via pesquisa textual",
            "dica": "A pesquisa Solr pode não indexar esse documento. Informe o número "
            "do processo (parâmetro processo=) para busca direta, ou use sei_arvore_processo.",
        }

    async def consultar_documento_interno(
        self, id_documento: str, processo: str | None = None
    ) -> dict:
        """Consulta metadados de um documento interno."""
        del processo  # REST localiza o documento pelo id; protocolo não é necessário
        return await self._rest.consultar_documento_interno(id_documento)

    async def consultar_documento_externo(
        self, id_documento: str, processo: str | None = None
    ) -> dict:
        """Consulta metadados de um documento externo."""
        del processo  # contrato exige o parâmetro; a REST resolve só pelo id
        return await self._rest.consultar_documento_externo(id_documento)

    async def visualizar_documento_interno(
        self, id_documento: str, processo: str | None = None
    ) -> str:
        """Retorna o HTML de um documento interno."""
        del processo  # REST localiza o documento pelo id; protocolo não é necessário
        return await self._rest.visualizar_documento_interno(id_documento)

    async def baixar_anexo(self, id_documento: str, processo: str | None = None) -> bytes:
        """Baixa os bytes de um documento externo (anexo)."""
        del processo  # contrato exige o parâmetro; a REST resolve só pelo id
        return await self._rest.baixar_anexo(id_documento)

    async def criar_documento_interno(self, processo: str, dados: NovoDocumentoInterno) -> dict:
        """Cria um documento interno (editor HTML) em um processo."""
        id_proc = await self._resolver_processo(processo)
        return await self._rest.criar_documento_interno(id_proc, dados)

    async def criar_documento_externo(self, processo: str, dados: NovoDocumentoExterno) -> dict:
        """Cria um documento externo (upload de arquivo) em um processo."""
        id_proc = await self._resolver_processo(processo)
        return await self._rest.criar_documento_externo(id_proc, dados)

    async def alterar_documento_interno(
        self,
        id_documento: str,
        descricao: str = "",
        nivel_acesso: str = "",
        hipotese_legal: str = "",
        processo: str | None = None,
    ) -> dict:
        """Altera metadados de um documento interno."""
        del processo  # REST localiza o documento pelo id; protocolo não é necessário
        return await self._rest.alterar_documento_interno(
            id_documento=id_documento,
            descricao=descricao,
            nivel_acesso=nivel_acesso,
            id_hipotese_legal=hipotese_legal,
        )

    async def alterar_documento_externo(
        self,
        id_documento: str,
        descricao: str = "",
        nivel_acesso: str = "",
        hipotese_legal: str = "",
        arquivo_path: str = "",
    ) -> dict:
        """Altera metadados (e opcionalmente o arquivo) de um documento externo."""
        return await self._rest.alterar_documento_externo(
            id_documento=id_documento,
            descricao=descricao,
            nivel_acesso=nivel_acesso,
            id_hipotese_legal=hipotese_legal,
            arquivo_path=arquivo_path,
        )

    async def listar_secoes(self, id_documento: str, processo: str | None = None) -> dict:
        """Lista as seções editáveis de um documento interno."""
        del processo  # REST localiza o documento pelo id; protocolo não é necessário
        return await self._rest.listar_secao_documento(id_documento)

    async def alterar_secoes(
        self, id_documento: str, secoes: list[dict], versao: str = "", processo: str | None = None
    ) -> dict:
        """Edita seções de um documento interno."""
        del processo  # REST localiza o documento pelo id; protocolo não é necessário
        return await self._rest.alterar_secao_documento(id_documento, secoes, versao or "1")

    async def resolver_documento(self, referencia: str) -> tuple[str, str]:
        """Resolve referência de documento via pesquisa Solr (REST)."""
        return await self._resolver_documento(referencia)

    async def sugestao_assuntos_documento(self, id_serie: str) -> list[dict]:
        """Sugere assuntos para um tipo de documento."""
        return await self._rest.sugestao_assuntos_documento(id_serie)

    async def listar_blocos_documento(self, id_documento: str) -> list[dict]:
        """Lista os blocos que contêm um documento."""
        return await self._rest.listar_blocos_documento(id_documento)

    async def assinar_documento(
        self,
        id_documento: str,
        cargo: str = "",
        orgao: str = "",
        processo: str | None = None,  # noqa: ARG002 — REST resolve via Solr, não precisa do protocolo
    ) -> dict:
        """Assina um documento com o cargo informado.

        Resolve número SEI → id interno (como `dar_ciencia`), evitando assinar o
        documento errado quando recebe um `protocoloFormatado`. Se a sessão não
        trouxer o `IdUsuario`, busca-o por login via `listar_usuarios`.
        """
        doc_id, _ = await self._resolver_documento(id_documento)
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
        cred = self._rest.build_credenciais_assinatura(
            login=login,
            cargo=cargo,
            orgao=orgao,
            id_usuario=id_usuario,
        )
        return await self._rest.assinar_documento(doc_id, cred)

    async def listar_assinaturas(
        self, id_documento: str, processo: str | None = None
    ) -> list[dict]:
        """Lista as assinaturas de um documento."""
        del processo  # contrato exige o parâmetro; a REST resolve só pelo id
        return await self._rest.listar_assinaturas(id_documento)

    async def dar_ciencia(self, referencia: str, tipo: str = "documento") -> dict:
        """Registra ciência em um documento ou processo."""
        if tipo == "documento":
            doc_id, _ = await self._resolver_documento(referencia)
            return await self._rest.dar_ciencia_documento(doc_id)
        id_proc = await self._resolver_processo(referencia)
        return await self._rest.dar_ciencia_processo(id_proc)

    async def listar_ciencias(
        self, referencia: str, tipo: str = "documento", processo: str | None = None
    ) -> list[dict]:
        """Lista as ciências de um documento ou processo."""
        del processo  # só usado no fallback web; a REST resolve pela referência
        if tipo == "documento":
            doc_id, _ = await self._resolver_documento(referencia)
            return await self._rest.listar_ciencias_documento(doc_id)
        id_proc = await self._resolver_processo(referencia)
        return await self._rest.listar_ciencias_processo(id_proc)
