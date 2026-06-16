"""Cliente REST genérico para o mod-wssei v2 do SEI."""

import asyncio
import base64
import json
import logging
import os
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import cast
from urllib.parse import urlparse

import httpx

from todos.catalog_cache import get_catalog_cache
from todos.exceptions import (
    SEIAuthError,
    SEIConnectionError,
    SEIError,
    SEINotFoundError,
    erro_do_sei,
)

logger = logging.getLogger(__name__)

_keyring: ModuleType | None = None
try:
    import keyring as _keyring
except ImportError:
    logger.debug("keyring not available; password will be read from SEI_SENHA env var")


def _safe_int(val: str | int | None, default: int = 0) -> int:
    """Convert val to int, returning default on ValueError (e.g. server returns 'N/A')."""
    if val is None:
        return default
    try:
        return int(val)
    except (TypeError, ValueError):
        return default


class SEIClient:
    """Cliente REST assíncrono para qualquer instância do SEI com mod-wssei v2."""

    def __init__(
        self,
        *,
        sei_url: str = "",
        sei_web_url: str = "",
        sei_usuario: str = "",
        sei_senha: str = "",
        sei_orgao: str = "",
        sei_contexto: str = "",
        sei_verify_ssl: str | bool | None = None,
    ) -> None:
        """Initialise from keyword args (sei_url, sei_usuario, sei_senha, …) or env vars."""
        self.base_url = (sei_url or os.environ.get("SEI_URL", "")).rstrip("/")
        self._usuario = sei_usuario or os.environ.get("SEI_USUARIO", "")

        # Resolve o sei_root de forma consistente com SEIWebClient para namespace do keyring.
        # §1.1: usa urlparse para extrair scheme://host sem assumir componentes do path.
        _sei_web_url = sei_web_url or os.environ.get("SEI_WEB_URL", "")
        if _sei_web_url:
            self.sei_root = _sei_web_url.rstrip("/")
        elif self.base_url:
            _parsed = urlparse(self.base_url)
            if _parsed.scheme and _parsed.netloc:
                self.sei_root = f"{_parsed.scheme}://{_parsed.netloc}"
            else:
                # URL sem scheme/netloc reconhecível — avisa e usa heurística de fallback
                logger.warning(
                    "SEI_URL não possui scheme/netloc reconhecível (%r); "
                    "sei_root pode ser calculado incorretamente.",
                    self.base_url,
                )
                self.sei_root = self.base_url.rstrip("/")
        else:
            self.sei_root = self.base_url.rstrip("/")

        self._senha = sei_senha or os.environ.get("SEI_SENHA", "")
        # Pre-compute keyring key so autenticar() can do the actual lookup in a thread.
        # §1.5: usa apenas netloc (host+port) para chave estável, nunca a URL completa.
        self._keyring_user: str | None = None
        if not self._senha and self._usuario:
            _root_parsed = urlparse(self.sei_root)
            instance_host = (_root_parsed.netloc or "").strip().rstrip("/").lower()
            self._keyring_user = (
                f"{self._usuario}@{instance_host}" if instance_host else self._usuario
            )

        self._orgao = sei_orgao or os.environ.get("SEI_ORGAO", "0")
        self._contexto = sei_contexto or os.environ.get("SEI_CONTEXTO", "")
        self._token: str | None = None
        self._unidade_ativa: str | None = None
        self._id_usuario: str | None = None
        self._id_orgao_usuario: str | None = None
        self._catalog_cache = get_catalog_cache()
        self._cache_namespace = {
            "base_url": self.base_url,
            "usuario": self._usuario,
            "orgao": self._orgao,
            "contexto": self._contexto,
        }

        _raw_verify: str | bool = (
            sei_verify_ssl
            if sei_verify_ssl is not None
            else os.environ.get("SEI_VERIFY_SSL", "true")
        )
        _verify: bool = (
            _raw_verify.lower() != "false" if isinstance(_raw_verify, str) else _raw_verify
        )
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(120.0, connect=10.0, read=90.0),
            verify=_verify,
        )

    @property
    def http_client(self) -> httpx.AsyncClient:
        """Expose the underlying HTTP client for direct use (e.g., benchmarks)."""
        return self._client

    @property
    def auth_token(self) -> str | None:
        """Expose the current auth token for direct HTTP calls."""
        return self._token

    @property
    def usuario(self) -> str:
        """Login do usuário configurado."""
        return self._usuario

    @property
    def senha(self) -> str:
        """Senha do usuário (necessária para assinar documentos)."""
        return self._senha

    @property
    def unidade_ativa(self) -> str | None:
        """Sigla da unidade atualmente ativa, preenchida após autenticar()."""
        return self._unidade_ativa

    @property
    def id_usuario(self) -> str | None:
        """ID interno do usuário no SEI, preenchido após autenticar()."""
        return self._id_usuario

    async def garantir_autenticacao(self) -> str:
        """Garante que a autenticação rodou e retorna o id_usuario (pode ser vazio)."""
        await self._get_headers()
        return self._id_usuario or ""

    async def _cache_get(self, key: str) -> object | None:
        """Retorna um catálogo persistido ou None."""
        return await self._catalog_cache.get(self._cache_namespace, key)

    async def _cache_set(self, key: str, val: object) -> None:
        """Persista um catálogo retornado com sucesso pelo SEI."""
        await self._catalog_cache.set(self._cache_namespace, key, val)

    async def _get_headers(self) -> dict[str, str]:
        if not self._token:
            await self.autenticar()
        return {"token": self._token or ""}

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str | int] | None = None,
        data: dict[str, str] | None = None,
    ) -> httpx.Response:
        """Faz request com re-autenticação automática em caso de 401/403."""
        if not self.base_url:
            msg = (
                "mod-wssei não configurado. Configure a variável de ambiente SEI_URL "
                "com o endpoint REST da sua instância SEI."
            )
            raise SEIConnectionError(msg)
        headers = await self._get_headers()
        try:
            resp = await self._client.request(
                method,
                f"{self.base_url}{path}",
                params=params,
                data=data,
                headers=headers,
            )
            if resp.status_code in (401, 403):
                logger.info("Token expirado, re-autenticando...")
                await self.autenticar()
                headers = await self._get_headers()
                resp = await self._client.request(
                    method,
                    f"{self.base_url}{path}",
                    params=params,
                    data=data,
                    headers=headers,
                )
            if resp.status_code in (401, 403):
                msg = "Sessão SEI expirada ou inválida após re-autenticação."
                raise SEIAuthError(msg)
            if resp.status_code == httpx.codes.NOT_FOUND:
                msg = f"Recurso não encontrado: {method} {path}"
                raise SEINotFoundError(msg)
            resp.raise_for_status()
        except (httpx.TimeoutException, httpx.ConnectError) as e:
            msg = f"SEI inacessível: {e}"
            raise SEIConnectionError(msg) from e
        except SEIError:
            raise
        except httpx.HTTPStatusError as e:
            raise SEIError(str(e)) from e
        return resp

    async def autenticar(self) -> str:
        """Autentica no SEI e obtém token."""
        if not self._senha and self._keyring_user:
            keyring_user = self._keyring_user
            self._keyring_user = None  # prevent concurrent / empty-string repeated lookups
            if _keyring is not None:
                try:
                    senha = await asyncio.wait_for(
                        asyncio.to_thread(_keyring.get_password, "todos-mcp", keyring_user),
                        timeout=5.0,
                    )
                    if senha:
                        self._senha = senha
                    # _keyring_user stays None: keyring answered definitively (found or not found)
                except TimeoutError:
                    self._keyring_user = keyring_user  # restore: transient timeout, allow retry
                    logger.warning(
                        "Timeout ao buscar senha do keyring (>5s); use SEI_SENHA como fallback"
                    )
                except (OSError, RuntimeError, ValueError) as e:
                    self._keyring_user = keyring_user  # restore: transient error, allow retry
                    logger.warning("Não foi possível obter a senha do keyring: %s", e)

        resp = await self._client.post(
            f"{self.base_url}/autenticar",
            data={
                "usuario": self._usuario,
                "senha": self._senha,
                "orgao": self._orgao,
                "contexto": self._contexto,
            },
        )
        resp.raise_for_status()
        data = resp.json()
        if not data.get("sucesso"):
            msg = f"Falha na autenticação SEI: {data.get('mensagem')}"
            raise SEIAuthError(msg)
        if "data" not in data:
            logger.error("Resposta de autenticação sem campo 'data': %r", data)
            msg = "Resposta de autenticação inesperada do SEI (sem campo 'data')"
            raise SEIAuthError(msg)
        payload = data["data"]
        if "token" not in payload:
            logger.error("Payload de autenticação sem campo 'token': %r", payload)
            msg = "Resposta de autenticação inesperada do SEI (sem campo 'token')"
            raise SEIAuthError(msg)
        self._token = payload["token"]
        login_data = payload.get("loginData") or {}
        id_usuario = login_data.get("IdUsuario") or login_data.get("idUsuario")
        if id_usuario is not None:
            self._id_usuario = str(id_usuario)
        id_orgao_usuario = login_data.get("IdOrgao") or login_data.get("idOrgao")
        if id_orgao_usuario is not None:
            self._id_orgao_usuario = str(id_orgao_usuario)
        logger.info("Autenticação SEI bem-sucedida")
        return self._token

    # ------------------------------------------------------------------
    # Sistema — versão, órgãos, contextos
    # Disponível desde mod-wssei 2.0.0 (SEI 4.0.x).
    # Podem não existir em instalações mais antigas.
    # ------------------------------------------------------------------

    async def versao(self) -> dict:
        """Retorna versão do SEI e do módulo wssei."""
        resp = await self._request("GET", "/versao")
        data = resp.json()
        if not data.get("sucesso"):
            msg = f"Erro ao obter versão: {data.get('mensagem')}"
            raise SEIError(msg)
        return data.get("data", {})

    async def listar_orgaos(self) -> list[dict]:
        """Lista órgãos da instalação do SEI."""
        resp = await self._request("GET", "/orgao/listar")
        data = resp.json()
        if not data.get("sucesso"):
            msg = f"Erro ao listar órgãos: {data.get('mensagem')}"
            raise SEIError(msg)
        return data.get("data", [])

    async def listar_contextos(self, id_orgao: str) -> list[dict]:
        """Lista contextos disponíveis para um órgão."""
        resp = await self._request("GET", f"/contexto/listar/{id_orgao}")
        data = resp.json()
        if not data.get("sucesso"):
            msg = f"Erro ao listar contextos: {data.get('mensagem')}"
            raise SEIError(msg)
        return data.get("data", [])

    async def consultar_processo(self, protocolo_formatado: str) -> dict:
        """Consulta processo pelo número formatado (versão minimalista).

        Endpoint mod-wssei: GET /processo/consultar?protocoloFormatado=
        Backend: ProcedimentoINT::pesquisarDigitadoRI1023()

        Retorna apenas: {IdProcedimento, ProtocoloProcedimentoFormatado, NomeTipoProcedimento}.
        Para dados completos (especificacao, assuntos, interessados, etc.) use
        `consultar_processo_completo()`.
        """
        resp = await self._request(
            "GET",
            "/processo/consultar",
            params={"protocoloFormatado": protocolo_formatado},
        )
        data = resp.json()
        if not data.get("sucesso"):
            msg = f"Erro ao consultar processo: {data.get('mensagem')}"
            raise SEIError(msg)
        return data["data"]

    async def consultar_processo_completo(self, protocolo_formatado: str) -> dict:
        """Consulta processo com TODOS os campos disponíveis na REST.

        Faz a sequência de 2 chamadas que o mod-wssei expõe:

        1. GET /processo/consultar?protocoloFormatado=  → id + nome do tipo
           (apiConsultarProcessoDigitado, ~3 campos)
        2. GET /processo/consultar/{id}                  → especificacao,
           assuntos, interessados, observacoes, nivelAcesso, hipoteseLegal,
           grauSigilo (consultarProcessoConectado, ~8 campos)

        Combina ambos em um único dict. Use quando precisar de dados ricos
        do processo (a chamada minimalista `consultar_processo` retorna
        apenas 3 campos).
        """
        # call 1
        resp1 = await self._request(
            "GET",
            "/processo/consultar",
            params={"protocoloFormatado": protocolo_formatado},
        )
        j1 = resp1.json()
        if not j1.get("sucesso"):
            msg = f"Erro ao consultar processo: {j1.get('mensagem')}"
            raise SEIError(msg)
        d1 = j1["data"]

        id_proc = d1.get("IdProcedimento")
        if not id_proc:
            return d1

        # call 2 — endpoint /consultar/{id} retorna o conjunto rico
        resp2 = await self._request("GET", f"/processo/consultar/{id_proc}")
        j2 = resp2.json()
        if not j2.get("sucesso"):
            # se a segunda call falhar, retorna pelo menos a primeira
            return d1
        rich = j2.get("data", {}) or {}

        # merge: rich tem campos como especificacao, assuntos, interessados, ...
        # d1 tem IdProcedimento, ProtocoloProcedimentoFormatado, NomeTipoProcedimento
        return {**d1, **rich}

    async def listar_documentos(
        self, id_procedimento: str, limit: int = 200, start: int = 0
    ) -> list[dict]:
        """Lista documentos de um processo.

        Retorna array de: {id, atributos: {tipoDocumento, tipo, protocoloFormatado, ...}}
        """
        resp = await self._request(
            "GET",
            f"/documento/listar/{id_procedimento}",
            params={"limit": limit, "start": start},
        )
        data = resp.json()
        if not data.get("sucesso"):
            msg = f"Erro ao listar documentos: {data.get('mensagem')}"
            raise SEIError(msg)
        return data.get("data", [])

    async def consultar_documento_interno(self, id_documento: str) -> dict:
        """Consulta metadados de um documento interno pelo id.

        Retorna: id, tipo, unidade geradora, assinaturas, etc.
        """
        resp = await self._request("GET", f"/documento/interno/consultar/{id_documento}")
        data = resp.json()
        if not data.get("sucesso"):
            msg = f"Erro ao consultar documento {id_documento}"
            raise erro_do_sei(msg, data.get("mensagem"))
        return data["data"]

    async def consultar_documento_externo(self, id_documento: str) -> dict:
        """Consulta metadados de um documento externo pelo id.

        Disponível desde mod-wssei 2.0.0 (SEI 4.0.x).
        """
        resp = await self._request("GET", f"/documento/externo/consultar/{id_documento}")
        data = resp.json()
        if not data.get("sucesso"):
            msg = f"Erro ao consultar documento externo {id_documento}"
            raise erro_do_sei(msg, data.get("mensagem"))
        return data["data"]

    async def alterar_documento_interno(
        self,
        id_documento: str,
        descricao: str = "",
        nivel_acesso: str = "",
        id_hipotese_legal: str = "",
    ) -> dict:
        """Altera metadados de um documento interno (não o conteúdo).

        Disponível desde mod-wssei 2.0.0 (SEI 4.0.x).
        """
        payload: dict = {}
        if descricao:
            payload["descricao"] = descricao
        if nivel_acesso:
            payload["nivelAcesso"] = nivel_acesso
        if id_hipotese_legal:
            payload["idHipoteseLegal"] = id_hipotese_legal
        resp = await self._request(
            "POST", f"/documento/interno/{id_documento}/alterar", data=payload
        )
        data = resp.json()
        if not data.get("sucesso"):
            msg = "Erro ao alterar documento interno"
            raise erro_do_sei(msg, data.get("mensagem"))
        return data.get("data", {"mensagem": data.get("mensagem")})

    async def alterar_documento_externo(
        self,
        id_documento: str,
        descricao: str = "",
        nivel_acesso: str = "",
        id_hipotese_legal: str = "",
        arquivo_path: str = "",
    ) -> dict:
        """Altera metadados de um documento externo (e opcionalmente substitui o arquivo).

        Disponível desde mod-wssei 2.0.0 (SEI 4.0.x).
        """
        payload: dict = {}
        if descricao:
            payload["descricao"] = descricao
        if nivel_acesso:
            payload["nivelAcesso"] = nivel_acesso
        if id_hipotese_legal:
            payload["idHipoteseLegal"] = id_hipotese_legal

        if arquivo_path:
            headers = await self._get_headers()
            _file_bytes = await asyncio.to_thread(Path(arquivo_path).read_bytes)
            resp = await self._client.post(
                f"{self.base_url}/documento/externo/{id_documento}/alterar",
                headers=headers,
                data=payload,
                files={"anexo": (Path(arquivo_path).name, _file_bytes)},
            )
        else:
            resp = await self._request(
                "POST", f"/documento/externo/{id_documento}/alterar", data=payload
            )
        data = resp.json()
        if not data.get("sucesso"):
            msg = "Erro ao alterar documento externo"
            raise erro_do_sei(msg, data.get("mensagem"))
        return data.get("data", {"mensagem": data.get("mensagem")})

    async def pesquisar_tipos_conferencia(
        self, filtro: str = "", limit: int = 50, start: int = 0
    ) -> dict:
        """Pesquisa tipos de conferência para documentos externos.

        Disponível desde mod-wssei 2.0.0 (SEI 4.0.x).
        """
        params: dict = {"limit": limit, "start": start}
        if filtro:
            params["filter"] = filtro
        resp = await self._request("GET", "/documento/tipoconferencia/pesquisar", params=params)
        data = resp.json()
        if not data.get("sucesso"):
            msg = f"Erro ao pesquisar tipos de conferência: {data.get('mensagem')}"
            raise SEIError(msg)
        return self._paginated(data, "tipos", data.get("data", []), start, limit)

    async def sugestao_assuntos_documento(self, id_serie: str) -> list[dict]:
        """Lista sugestões de assuntos para um tipo de documento (série).

        Disponível desde mod-wssei 2.0.0 (SEI 4.0.x).
        """
        resp = await self._request("GET", f"/documento/assunto/sugestao/{id_serie}/listar")
        data = resp.json()
        if not data.get("sucesso"):
            msg = f"Erro ao listar sugestões de assunto: {data.get('mensagem')}"
            raise SEIError(msg)
        return data.get("data", [])

    async def listar_blocos_documento(self, id_documento: str) -> list[dict]:
        """Lista blocos de assinatura em que um documento está incluído.

        Disponível desde mod-wssei 2.0.0 (SEI 4.0.x).
        """
        resp = await self._request("GET", f"/documento/{id_documento}/bloco/assinatura/listar")
        data = resp.json()
        if not data.get("sucesso"):
            msg = f"Erro ao listar blocos do documento: {data.get('mensagem')}"
            raise SEIError(msg)
        return data.get("data", [])

    async def pesquisar_tipos_documento_externo(
        self, filtro: str = "", limit: int = 50, start: int = 0
    ) -> dict:
        """Pesquisa tipos de documento para documentos externos (séries externas).

        Disponível desde mod-wssei 2.0.0 (SEI 4.0.x).
        """
        params: dict = {"limit": limit, "start": start}
        if filtro:
            params["filter"] = filtro
        resp = await self._request("GET", "/serie/externo/pesquisar", params=params)
        data = resp.json()
        if not data.get("sucesso"):
            msg = f"Erro ao pesquisar tipos de doc externo: {data.get('mensagem')}"
            raise SEIError(msg)
        return self._paginated(data, "tipos", data.get("data", []), start, limit)

    async def parametros_upload(self) -> dict:
        """Retorna parâmetros de upload (extensões permitidas, tamanhos máximos).

        Disponível desde mod-wssei 2.0.0 (SEI 4.0.x).
        """
        resp = await self._request("GET", "/upload/parametros")
        data = resp.json()
        if not data.get("sucesso"):
            msg = f"Erro ao obter parâmetros de upload: {data.get('mensagem')}"
            raise SEIError(msg)
        return data.get("data", {})

    async def listar_assinaturas(self, id_documento: str) -> list[dict]:
        """Lista assinaturas de um documento."""
        resp = await self._request("GET", f"/documento/listar/assinaturas/{id_documento}")
        data = resp.json()
        if not data.get("sucesso"):
            msg = f"Erro ao listar assinaturas: {data.get('mensagem')}"
            raise SEIError(msg)
        return data.get("data", [])

    async def visualizar_documento_interno(self, id_documento: str) -> str:
        """Visualiza conteúdo HTML de documento interno (tipoDocumento=I)."""
        resp = await self._request("GET", f"/documento/{id_documento}/interno/visualizar")
        data = resp.json()
        if not data.get("sucesso"):
            msg = f"Erro ao visualizar documento {id_documento}"
            raise erro_do_sei(msg, data.get("mensagem"))
        return data["data"]

    async def baixar_anexo(self, id_documento: str) -> bytes:
        """Baixa anexo de documento externo (tipoDocumento=X). Retorna bytes."""
        resp = await self._request("GET", f"/documento/baixar/anexo/{id_documento}")
        content_type = resp.headers.get("content-type", "")
        if "json" in content_type:
            data = resp.json()
            if not data.get("sucesso"):
                msg = f"Erro ao baixar anexo {id_documento}"
                raise erro_do_sei(msg, data.get("mensagem"))
            return base64.b64decode(data["data"])
        return resp.content

    async def criar_documento_interno(
        self,
        id_procedimento: str,
        id_serie: str,
        descricao: str = "",
        nivel_acesso: str = "0",
        hipotese_legal: str = "",
        id_unidade: str = "",
    ) -> dict:
        """Cria documento interno (nativo) em um processo SEI.

        Retorna: {idDocumento, protocoloDocumentoFormatado}
        """
        resp = await self._request(
            "POST",
            f"/documento/{id_procedimento}/interno/criar",
            data={
                "idSerie": id_serie,
                "numero": "",
                "descricao": descricao,
                "dataElaboracao": "",
                "nivelAcesso": nivel_acesso,
                "idHipoteseLegal": hipotese_legal,
                "grauSigilo": "",
                "idUnidadeGeradoraProtocolo": id_unidade,
                "assuntos": "",
                "interessados": "",
                "remetente": "",
                "destinatarios": "",
                "observacao": "",
                "idTextoPadraoInterno": "",
                "idTipoConferencia": "",
                "protocoloDocumentoModelo": "",
            },
        )
        data = resp.json()
        if not data.get("sucesso"):
            msg = f"Erro ao criar documento interno: {data.get('mensagem')}"
            raise SEIError(msg)
        return data["data"]

    async def listar_secao_documento(self, id_documento: str) -> dict:
        """Lista seções de um documento interno.

        Retorna: {secoes: [{id, idSecaoModelo, conteudo, ...}], ultimaVersaoDocumento: N}
        """
        resp = await self._request(
            "GET",
            "/documento/secao/listar",
            params={"id": id_documento},
        )
        data = resp.json()
        if not data.get("sucesso"):
            msg = f"Erro ao listar seções do documento {id_documento}: {data.get('mensagem')}"
            raise SEIError(msg)
        return data.get("data", {})

    async def alterar_secao_documento(
        self,
        id_documento: str,
        secoes: list[dict],
        versao: str = "1",
    ) -> dict:
        """Altera conteúdo HTML das seções de um documento interno.

        secoes: [{id, idSecaoModelo, conteudo}, ...]
        """
        secoes_json = json.dumps(secoes)
        resp = await self._request(
            "POST",
            "/documento/secao/alterar",
            data={
                "documento": id_documento,
                "secoes": secoes_json,
                "versao": versao,
            },
        )
        data = resp.json()
        if not data.get("sucesso"):
            msg = f"Erro ao alterar seção do documento {id_documento}"
            raise erro_do_sei(msg, data.get("mensagem"))
        return data.get("data", {})

    # ------------------------------------------------------------------
    # Usuário e unidades
    # ------------------------------------------------------------------

    async def listar_unidades_usuario(self) -> list[dict]:
        """Lista unidades às quais o usuário autenticado tem acesso.

        Resultado cacheado por 24 horas em disco (raramente muda).
        """
        cached = await self._cache_get("unidades_usuario")
        if cached is not None:
            return cast("list[dict]", cached)
        resp = await self._request("GET", "/usuario/unidades")
        data = resp.json()
        if not data.get("sucesso"):
            msg = f"Erro ao listar unidades: {data.get('mensagem')}"
            raise SEIError(msg)
        result = data.get("data", [])
        await self._cache_set("unidades_usuario", result)
        return result

    async def pesquisar_usuarios(
        self, filtro: str = "", id_orgao: str = "", limit: int = 50, start: int = 0
    ) -> dict:
        """Pesquisa usuários por palavra-chave no órgão.

        Diferente de listar_usuarios que filtra client-side — este usa
        o endpoint /usuario/pesquisar que busca no servidor.
        Disponível desde mod-wssei 2.0.0 (SEI 4.0.x).
        """
        params: dict = {"limit": limit, "start": start}
        if filtro:
            params["filter"] = filtro
        if id_orgao:
            params["orgao"] = id_orgao
        resp = await self._request("GET", "/usuario/pesquisar", params=params)
        data = resp.json()
        if not data.get("sucesso"):
            msg = f"Erro ao pesquisar usuários: {data.get('mensagem')}"
            raise SEIError(msg)
        return self._paginated(data, "usuarios", data.get("data", []), start, limit)

    async def trocar_unidade(self, id_unidade: str) -> dict:
        """Troca a unidade ativa do usuário."""
        resp = await self._request("POST", "/usuario/alterar/unidade", data={"unidade": id_unidade})
        data = resp.json()
        if not data.get("sucesso"):
            msg = f"Erro ao trocar unidade: {data.get('mensagem')}"
            raise SEIError(msg)
        self._unidade_ativa = id_unidade
        return {"mensagem": data.get("mensagem")}

    async def pesquisar_unidades(self, filtro: str = "", limit: int = 50, start: int = 0) -> dict:
        """Pesquisa unidades disponíveis no SEI."""
        params: dict = {"limit": limit, "start": start}
        if filtro:
            params["filter"] = filtro
        resp = await self._request("GET", "/unidade/pesquisar", params=params)
        data = resp.json()
        if not data.get("sucesso"):
            msg = f"Erro ao pesquisar unidades: {data.get('mensagem')}"
            raise SEIError(msg)
        return self._paginated(data, "unidades", data.get("data", []), start, limit)

    async def pesquisar_outras_unidades(
        self, filtro: str = "", limit: int = 50, start: int = 0
    ) -> dict:
        """Pesquisa unidades excluindo a unidade atual.

        Disponível desde mod-wssei 2.0.0 (SEI 4.0.x).
        """
        params: dict = {"limit": limit, "start": start}
        if filtro:
            params["filter"] = filtro
        resp = await self._request("GET", "/unidade/outras/pesquisar", params=params)
        data = resp.json()
        if not data.get("sucesso"):
            msg = f"Erro ao pesquisar outras unidades: {data.get('mensagem')}"
            raise SEIError(msg)
        return self._paginated(data, "unidades", data.get("data", []), start, limit)

    async def pesquisar_textos_padrao(
        self, filtro: str = "", limit: int = 50, start: int = 0
    ) -> dict:
        """Pesquisa textos padrão internos disponíveis na unidade.

        Textos padrão são modelos reutilizáveis para preencher documentos.
        Disponível desde mod-wssei 2.0.0 (SEI 4.0.x).
        """
        params: dict = {"limit": limit, "start": start}
        if filtro:
            params["filter"] = filtro
        resp = await self._request("GET", "/unidade/textopadrao/interno/pesquisar", params=params)
        data = resp.json()
        if not data.get("sucesso"):
            msg = f"Erro ao pesquisar textos padrão: {data.get('mensagem')}"
            raise SEIError(msg)
        return self._paginated(data, "textos", data.get("data", []), start, limit)

    async def listar_usuarios(
        self,
        filtro: str = "",
        id_unidade: str = "",
        *,
        apenas_unidade: bool = True,
    ) -> dict:
        """Lista usuários.

        - apenas_unidade=True (padrão): só usuários com permissão na unidade
          informada (ou na unidade ativa se id_unidade for vazio).
          Usa o parâmetro 'unidade' da API.
        - apenas_unidade=False: todos os usuários do órgão.
        O filtro por nome/sigla é aplicado client-side.
        """
        params: dict = {"limit": 1000, "start": 0}
        if apenas_unidade:
            unid = id_unidade or self._unidade_ativa
            if unid:
                params["unidade"] = unid
        resp = await self._request("GET", "/usuario/listar", params=params)
        data = resp.json()
        if not data.get("sucesso"):
            msg = f"Erro ao listar usuários: {data.get('mensagem')}"
            raise SEIError(msg)

        usuarios = data.get("data", [])

        if filtro:
            filtro_lower = filtro.lower()
            usuarios = [
                u
                for u in usuarios
                if filtro_lower in u.get("nome", "").lower()
                or filtro_lower in u.get("sigla", "").lower()
            ]

        return {
            "usuarios": usuarios,
            "total_itens": len(usuarios),
        }

    # ------------------------------------------------------------------
    # Processos — listar, pesquisar, criar, enviar, concluir, reabrir, atribuir
    # ------------------------------------------------------------------

    async def listar_processos(
        self,
        limit: int = 50,
        start: int = 0,
        tipo: str = "",
        usuario: str = "",
        apenas_meus: str = "",
        filtro: str = "",
    ) -> dict:
        """Lista processos da caixa da unidade atual.

        Retorna: {data: [...], total: N}
        """
        params: dict = {"limit": limit, "start": start}
        if tipo:
            params["tipo"] = tipo
        if usuario:
            params["usuario"] = usuario
        if apenas_meus:
            params["apenasMeus"] = apenas_meus
        if filtro:
            params["filter"] = filtro
        resp = await self._request("GET", "/processo/listar", params=params)
        data = resp.json()
        if not data.get("sucesso"):
            msg = f"Erro ao listar processos: {data.get('mensagem')}"
            raise SEIError(msg)
        return self._paginated(data, "processos", data.get("data", []), start, limit)

    async def pesquisar_processos(
        self,
        palavras_chave: str = "",
        descricao: str = "",
        busca_rapida: str = "",
        data_inicio: str = "",
        data_fim: str = "",
        sta_tipo_data: str = "",
        id_unidade_geradora: str = "",
        id_assunto: str = "",
        grupo: str = "",
        limit: int = 50,
        start: int = 0,
    ) -> dict:
        """Pesquisa processos via busca textual (Solr).

        Retorna: {data: [...], total: N}
        """
        params: dict = {"limit": limit, "start": start}
        if palavras_chave:
            params["palavrasChave"] = palavras_chave
        if descricao:
            params["descricao"] = descricao
        if busca_rapida:
            params["buscaRapida"] = busca_rapida
        if data_inicio:
            params["dataInicio"] = data_inicio
        if data_fim:
            params["dataFim"] = data_fim
        if sta_tipo_data:
            params["staTipoData"] = sta_tipo_data
        if id_unidade_geradora:
            params["idUnidadeGeradora"] = id_unidade_geradora
        if id_assunto:
            params["idAssunto"] = id_assunto
        if grupo:
            params["grupo"] = grupo
        resp = await self._request("GET", "/processo/pesquisar", params=params)
        data = resp.json()
        if not data.get("sucesso"):
            msg = f"Erro ao pesquisar processos: {data.get('mensagem')}"
            raise SEIError(msg)
        return self._paginated(data, "processos", data.get("data", []), start, limit)

    async def alterar_processo(
        self,
        id_procedimento: str,
        especificacao: str | None = None,
        nivel_acesso: str | None = None,
        hipotese_legal: str | None = None,
        observacao: str | None = None,
    ) -> dict:
        """Altera metadados de um processo (especificação, nível de acesso, etc.).

        A API exige todos os campos — busca os atuais e sobrescreve os alterados.
        """
        # Buscar dados atuais do processo
        proc = await self.consultar_processo_por_id(id_procedimento)

        # Montar payload com dados atuais + alterações
        na = nivel_acesso if nivel_acesso is not None else proc.get("nivelAcesso", "0")

        # Se mudando para público, limpar hipótese legal
        if na == "0":
            hl = ""
        else:
            hl = hipotese_legal if hipotese_legal is not None else proc.get("hipoteseLegal", "")

        # Assuntos: reenviar os existentes (a API exige)
        assuntos_atuais = proc.get("assuntos", [])
        assuntos_ids = ",".join(str(a.get("id", "")) for a in assuntos_atuais)

        payload = {
            "idTipoProcesso": proc.get("tipoProcesso", ""),
            "especificacao": especificacao
            if especificacao is not None
            else proc.get("especificacao", ""),
            "nivelAcesso": na,
            "idHipoteseLegal": hl,
            "grauSigilo": proc.get("grauSigilo", ""),
            "observacao": observacao if observacao is not None else "",
            "assuntos": assuntos_ids,
        }

        resp = await self._request("POST", f"/processo/{id_procedimento}/alterar", data=payload)
        data = resp.json()
        if not data.get("sucesso"):
            msg = "Erro ao alterar processo"
            raise erro_do_sei(msg, data.get("mensagem"))
        return data.get("data", {"mensagem": data.get("mensagem")})

    async def consultar_processo_por_id(self, id_procedimento: str) -> dict:
        """Consulta processo pelo IdProcedimento (id interno)."""
        resp = await self._request("GET", f"/processo/consultar/{id_procedimento}")
        data = resp.json()
        if not data.get("sucesso"):
            msg = f"Erro ao consultar processo: {data.get('mensagem')}"
            raise SEIError(msg)
        return data["data"]

    async def pesquisar_hipoteses_legais(
        self, filtro: str = "", limit: int = 50, start: int = 0
    ) -> dict:
        """Pesquisa hipóteses legais para processos/documentos restritos ou sigilosos."""
        params: dict = {"limit": limit, "start": start}
        if filtro:
            params["filter"] = filtro
        resp = await self._request("GET", "/hipoteseLegal/pesquisar", params=params)
        data = resp.json()
        if not data.get("sucesso"):
            msg = f"Erro ao pesquisar hipóteses legais: {data.get('mensagem')}"
            raise SEIError(msg)
        return self._paginated(data, "hipoteses", data.get("data", []), start, limit)

    async def pesquisar_tipos_processo(
        self, filtro: str = "", favoritos: str = "", limit: int = 50, start: int = 0
    ) -> dict:
        """Pesquisa tipos de processo disponíveis.

        Resultado cacheado por 24 horas em disco quando chamado sem filtros.
        """
        cache_key = f"tipos_processo:{filtro}:{favoritos}:{limit}:{start}"
        if not filtro and not favoritos:
            cached = await self._cache_get(cache_key)
            if cached is not None:
                return cast("dict", cached)
        params: dict = {"limit": limit, "start": start}
        if filtro:
            params["filter"] = filtro
        if favoritos:
            params["favoritos"] = favoritos
        resp = await self._request("GET", "/processo/tipo/listar", params=params)
        data = resp.json()
        if not data.get("sucesso"):
            msg = f"Erro ao pesquisar tipos de processo: {data.get('mensagem')}"
            raise SEIError(msg)
        result = self._paginated(data, "tipos", data.get("data", []), start, limit)
        if not filtro and not favoritos:
            await self._cache_set(cache_key, result)
        return result

    async def criar_processo(
        self,
        tipo_processo: str,
        especificacao: str = "",
        assuntos: str = "",
        interessados: str = "",
        observacoes: str = "",
        nivel_acesso: str = "0",
        hipotese_legal: str = "",
    ) -> dict:
        """Cria novo processo no SEI.

        assuntos e interessados devem ser JSON arrays de objetos com campo "id".
        Ex: '[{"id":"876"}]'
        Retorna: {IdProcedimento, ProtocoloFormatado}
        """
        # Converter IDs simples para formato JSON esperado pela API
        if assuntos and not assuntos.startswith("["):
            ids = [a.strip() for a in assuntos.split(",")]
            assuntos = json.dumps([{"id": i} for i in ids])
        if interessados and not interessados.startswith("["):
            ids = [i.strip() for i in interessados.split(",")]
            interessados = json.dumps([{"id": i} for i in ids])

        resp = await self._request(
            "POST",
            "/processo/criar",
            data={
                "tipoProcesso": tipo_processo,
                "especificacao": especificacao,
                "assuntos": assuntos,
                "interessados": interessados,
                "observacoes": observacoes,
                "nivelAcesso": nivel_acesso,
                "hipoteseLegal": hipotese_legal,
                "grauSigilo": "",
            },
        )
        data = resp.json()
        if not data.get("sucesso"):
            msg = f"Erro ao criar processo: {data.get('mensagem')}"
            raise SEIError(msg)
        return data["data"]

    async def enviar_processo(
        self,
        numero_processo: str,
        unidades_destino: str,
        manter_aberto: str = "N",
        remover_anotacao: str = "N",
        enviar_email: str = "N",
        data_retorno: str = "",
        dias_retorno: str = "",
        dias_uteis_retorno: str = "S",
        reabrir: str = "N",
    ) -> dict:
        """Envia (tramita) processo para outra(s) unidade(s)."""
        payload: dict = {
            "numeroProcesso": numero_processo,
            "unidadesDestino": unidades_destino,
            "sinManterAbertoUnidade": manter_aberto,
            "sinRemoverAnotacao": remover_anotacao,
            "sinEnviarEmailNotificacao": enviar_email,
            "sinReabrir": reabrir,
        }
        if data_retorno:
            payload["dataRetornoProgramado"] = data_retorno
        if dias_retorno:
            payload["diasRetornoProgramado"] = dias_retorno
            payload["sinDiasUteisRetornoProgramado"] = dias_uteis_retorno
        resp = await self._request("POST", "/processo/enviar", data=payload)
        data = resp.json()
        if not data.get("sucesso"):
            msg = f"Erro ao enviar processo: {data.get('mensagem')}"
            raise SEIError(msg)
        return data.get("data", {"mensagem": data.get("mensagem")})

    async def concluir_processo(self, numero_processo: str) -> dict:
        """Conclui processo na unidade atual."""
        resp = await self._request(
            "POST", "/processo/concluir", data={"numeroProcesso": numero_processo}
        )
        data = resp.json()
        if not data.get("sucesso"):
            msg = "Erro ao concluir processo"
            raise erro_do_sei(msg, data.get("mensagem"))
        return data.get("data", {"mensagem": data.get("mensagem")})

    async def reabrir_processo(self, id_procedimento: str) -> dict:
        """Reabre processo concluído na unidade."""
        resp = await self._request("POST", f"/processo/reabrir/{id_procedimento}")
        data = resp.json()
        if not data.get("sucesso"):
            msg = f"Erro ao reabrir processo: {data.get('mensagem')}"
            raise SEIError(msg)
        return data.get("data", {"mensagem": data.get("mensagem")})

    async def atribuir_processo(self, numero_processo: str, id_usuario: str) -> dict:
        """Atribui processo a um usuário da unidade."""
        resp = await self._request(
            "POST",
            "/processo/atribuir",
            data={"numeroProcesso": numero_processo, "usuario": id_usuario},
        )
        data = resp.json()
        if not data.get("sucesso"):
            msg = f"Erro ao atribuir processo: {data.get('mensagem')}"
            raise SEIError(msg)
        return data.get("data", {"mensagem": data.get("mensagem")})

    # ------------------------------------------------------------------
    # Documentos — assinar, pesquisar tipos
    # ------------------------------------------------------------------

    async def assinar_documento(
        self,
        id_documento: str,
        login: str,
        senha: str,
        cargo: str,
        orgao: str = "",
        id_usuario: str = "",
    ) -> dict:
        """Assina documento eletronicamente."""
        payload = {
            "documento": id_documento,
            "login": login,
            "senha": senha,
            "cargo": cargo,
            "orgao": orgao or self._orgao,
        }
        if id_usuario:
            payload["usuario"] = id_usuario
        resp = await self._request(
            "POST",
            "/documento/assinar",
            data=payload,
        )
        data = resp.json()
        if not data.get("sucesso"):
            msg = f"Erro ao assinar documento: {data.get('mensagem')}"
            raise SEIError(msg)
        return data.get("data", {"mensagem": data.get("mensagem")})

    async def pesquisar_tipos_documento(
        self,
        filtro: str = "",
        favoritos: str = "",
        aplicabilidade: str = "",
        limit: int = 50,
        start: int = 0,
    ) -> dict:
        """Pesquisa tipos de documento (séries) disponíveis.

        Retorna: {data: [{id, nome}, ...], total: N}
        """
        cache_key = f"tipos_documento:{filtro}:{favoritos}:{aplicabilidade}:{limit}:{start}"
        if not filtro and not favoritos:
            cached = await self._cache_get(cache_key)
            if cached is not None:
                return cast("dict", cached)
        params: dict = {"limit": limit, "start": start}
        if filtro:
            params["filter"] = filtro
        if favoritos:
            params["favoritos"] = favoritos
        if aplicabilidade:
            params["aplicabilidade"] = aplicabilidade
        resp = await self._request("GET", "/documento/tipo/pesquisar", params=params)
        data = resp.json()
        if not data.get("sucesso"):
            msg = f"Erro ao pesquisar tipos de documento: {data.get('mensagem')}"
            raise SEIError(msg)
        result = self._paginated(data, "tipos", data.get("data", []), start, limit)
        if not filtro and not favoritos:
            await self._cache_set(cache_key, result)
        return result

    # ------------------------------------------------------------------
    # Ciência
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Sobrestamento
    # ------------------------------------------------------------------

    async def sobrestar_processo(
        self, id_procedimento: str, motivo: str, protocolo_vinculado: str = ""
    ) -> dict:
        """Sobresta um processo. Motivo é obrigatório."""
        payload = {"motivo": motivo}
        if protocolo_vinculado:
            payload["protocoloDestino"] = protocolo_vinculado
        resp = await self._request(
            "POST", f"/processo/{id_procedimento}/sobrestar/processo", data=payload
        )
        data = resp.json()
        if not data.get("sucesso"):
            msg = "Erro ao sobrestar processo"
            raise erro_do_sei(msg, data.get("mensagem"))
        return data.get("data", {"mensagem": data.get("mensagem")})

    async def remover_sobrestamento(self, id_procedimento: str) -> dict:
        """Remove sobrestamento de um processo."""
        resp = await self._request("POST", f"/processo/{id_procedimento}/cancelar/sobrestamento")
        data = resp.json()
        if not data.get("sucesso"):
            msg = f"Erro ao remover sobrestamento: {data.get('mensagem')}"
            raise SEIError(msg)
        return data.get("data", {"mensagem": data.get("mensagem")})

    # ------------------------------------------------------------------
    # Ciência
    # ------------------------------------------------------------------

    async def dar_ciencia_documento(self, id_documento: str) -> dict:
        """Dá ciência em um documento."""
        resp = await self._request("POST", "/documento/ciencia", data={"documento": id_documento})
        data = resp.json()
        if not data.get("sucesso"):
            msg = f"Erro ao dar ciência no documento: {data.get('mensagem')}"
            raise SEIError(msg)
        return data.get("data", {"mensagem": data.get("mensagem")})

    async def dar_ciencia_processo(self, id_procedimento: str) -> dict:
        """Dá ciência em um processo."""
        resp = await self._request("POST", f"/processo/{id_procedimento}/ciencia")
        data = resp.json()
        if not data.get("sucesso"):
            msg = f"Erro ao dar ciência no processo: {data.get('mensagem')}"
            raise SEIError(msg)
        return data.get("data", {"mensagem": data.get("mensagem")})

    async def listar_ciencias_documento(self, id_documento: str) -> list[dict]:
        """Lista ciências registradas em um documento."""
        resp = await self._request("GET", f"/documento/listar/ciencia/{id_documento}")
        data = resp.json()
        if not data.get("sucesso"):
            msg = f"Erro ao listar ciências do documento: {data.get('mensagem')}"
            raise SEIError(msg)
        return data.get("data", [])

    async def listar_ciencias_processo(self, id_procedimento: str) -> list[dict]:
        """Lista ciências registradas em um processo."""
        resp = await self._request("GET", f"/processo/{id_procedimento}/ciencia/listar")
        data = resp.json()
        if not data.get("sucesso"):
            msg = f"Erro ao listar ciências do processo: {data.get('mensagem')}"
            raise SEIError(msg)
        return data.get("data", [])

    # ------------------------------------------------------------------
    # Anotação
    # ------------------------------------------------------------------

    async def criar_anotacao(
        self,
        protocolo: str,
        descricao: str,
        prioridade: str = "1",
    ) -> dict:
        """Cria anotação (post-it) em um processo."""
        resp = await self._request(
            "POST",
            "/anotacao/",
            data={
                "protocolo": protocolo,
                "descricao": descricao,
                "prioridade": prioridade,
            },
        )
        data = resp.json()
        if not data.get("sucesso"):
            msg = f"Erro ao criar anotação: {data.get('mensagem')}"
            raise SEIError(msg)
        return data.get("data", {"mensagem": data.get("mensagem")})

    @staticmethod
    def _paginated(data: dict, items_key: str, itens: list, pagina: int, limit: int) -> dict:
        """Monta resposta paginada com metadados."""
        total = _safe_int(data.get("total"), default=len(itens))
        return {
            items_key: itens,
            "pagina_atual": pagina,
            "itens_pagina": len(itens),
            "total_itens": total,
            "tem_proxima": len(itens) >= limit,
        }

    # ------------------------------------------------------------------
    # Marcador
    # ------------------------------------------------------------------

    async def pesquisar_marcadores(self, filtro: str = "", limit: int = 50, start: int = 0) -> dict:
        """Pesquisa marcadores disponíveis na unidade."""
        params: dict = {"limit": limit, "start": start}
        if filtro:
            params["filter"] = filtro
        resp = await self._request("GET", "/marcador/pesquisar", params=params)
        data = resp.json()
        if not data.get("sucesso"):
            msg = f"Erro ao pesquisar marcadores: {data.get('mensagem')}"
            raise SEIError(msg)
        return self._paginated(data, "marcadores", data.get("data", []), start, limit)

    async def listar_cores_marcador(self) -> list[dict]:
        """Lista cores disponíveis para marcadores."""
        resp = await self._request("GET", "/marcador/cores/listar")
        data = resp.json()
        if not data.get("sucesso"):
            msg = f"Erro ao listar cores: {data.get('mensagem')}"
            raise SEIError(msg)
        return data.get("data", [])

    async def criar_marcador(self, nome: str, id_cor: str) -> dict:
        """Cria marcador na unidade atual."""
        resp = await self._request("POST", "/marcador/criar", data={"nome": nome, "idCor": id_cor})
        data = resp.json()
        if not data.get("sucesso"):
            msg = f"Erro ao criar marcador: {data.get('mensagem')}"
            raise SEIError(msg)
        return data.get("data", {"mensagem": data.get("mensagem")})

    async def alterar_marcador(self, id_marcador: str, nome: str, id_cor: str) -> dict:
        """Altera nome e/ou cor de um marcador."""
        resp = await self._request(
            "POST",
            f"/marcador/{id_marcador}/alterar",
            data={"nome": nome, "idCor": id_cor},
        )
        data = resp.json()
        if not data.get("sucesso"):
            msg = f"Erro ao alterar marcador: {data.get('mensagem')}"
            raise SEIError(msg)
        return data.get("data", {"mensagem": data.get("mensagem")})

    async def excluir_marcadores(self, ids: str) -> dict:
        """Exclui marcador(es). IDs separados por vírgula."""
        resp = await self._request("POST", "/marcador/excluir", data={"marcadores": ids})
        data = resp.json()
        if not data.get("sucesso"):
            msg = f"Erro ao excluir marcadores: {data.get('mensagem')}"
            raise SEIError(msg)
        return data.get("data", {"mensagem": data.get("mensagem")})

    async def desativar_marcadores(self, ids: str) -> dict:
        """Desativa marcador(es) sem excluir. IDs separados por vírgula."""
        resp = await self._request("POST", "/marcador/desativar", data={"marcadores": ids})
        data = resp.json()
        if not data.get("sucesso"):
            msg = f"Erro ao desativar marcadores: {data.get('mensagem')}"
            raise SEIError(msg)
        return data.get("data", {"mensagem": data.get("mensagem")})

    async def reativar_marcadores(self, ids: str) -> dict:
        """Reativa marcador(es) desativados. IDs separados por vírgula."""
        resp = await self._request("POST", "/marcador/reativar", data={"marcadores": ids})
        data = resp.json()
        if not data.get("sucesso"):
            msg = f"Erro ao reativar marcadores: {data.get('mensagem')}"
            raise SEIError(msg)
        return data.get("data", {"mensagem": data.get("mensagem")})

    async def marcar_processo(
        self, id_procedimento: str, id_marcador: str, texto: str = ""
    ) -> dict:
        """Adiciona marcador a um processo."""
        resp = await self._request(
            "POST",
            f"/marcador/processo/{id_procedimento}/marcar",
            data={"marcador": id_marcador, "texto": texto},
        )
        data = resp.json()
        if not data.get("sucesso"):
            msg = f"Erro ao marcar processo: {data.get('mensagem')}"
            raise SEIError(msg)
        return data.get("data", {"mensagem": data.get("mensagem")})

    async def consultar_marcador_processo(self, id_procedimento: str) -> list[dict]:
        """Consulta marcadores de um processo."""
        resp = await self._request("GET", f"/marcador/processo/{id_procedimento}/consultar")
        data = resp.json()
        if not data.get("sucesso"):
            msg = f"Erro ao consultar marcador: {data.get('mensagem')}"
            raise SEIError(msg)
        return data.get("data", [])

    # ------------------------------------------------------------------
    # Acompanhamento Especial
    # ------------------------------------------------------------------

    async def acompanhar_processo(
        self, id_procedimento: str, id_grupo: str = "", observacao: str = ""
    ) -> dict:
        """Adiciona acompanhamento especial em um processo."""
        payload: dict = {"protocolo": id_procedimento}
        if id_grupo:
            payload["grupo"] = id_grupo
        if observacao:
            payload["observacao"] = observacao
        resp = await self._request("POST", "/processo/acompanhar", data=payload)
        data = resp.json()
        if not data.get("sucesso"):
            msg = f"Erro ao acompanhar processo: {data.get('mensagem')}"
            raise SEIError(msg)
        return data.get("data", {"mensagem": data.get("mensagem")})

    async def consultar_acompanhamento(self, id_procedimento: str) -> dict:
        """Consulta acompanhamento de um processo."""
        resp = await self._request(
            "GET",
            "/processo/acompanhamento/consultar",
            params={"protocolo": id_procedimento},
        )
        data = resp.json()
        if not data.get("sucesso"):
            msg = f"Erro ao consultar acompanhamento: {data.get('mensagem')}"
            raise SEIError(msg)
        return data.get("data", {})

    async def excluir_acompanhamento(self, id_acompanhamento: str) -> dict:
        """Remove acompanhamento especial."""
        resp = await self._request("POST", f"/processo/acompanhamento/{id_acompanhamento}/excluir")
        data = resp.json()
        if not data.get("sucesso"):
            msg = f"Erro ao excluir acompanhamento: {data.get('mensagem')}"
            raise SEIError(msg)
        return data.get("data", {"mensagem": data.get("mensagem")})

    async def criar_grupo_acompanhamento(self, nome: str) -> dict:
        """Cria grupo de acompanhamento especial."""
        resp = await self._request("POST", "/grupoacompanhamento/cadastrar", data={"nome": nome})
        data = resp.json()
        if not data.get("sucesso"):
            msg = f"Erro ao criar grupo: {data.get('mensagem')}"
            raise SEIError(msg)
        return data.get("data", {"mensagem": data.get("mensagem")})

    async def alterar_grupo_acompanhamento(self, id_grupo: str, nome: str) -> dict:
        """Altera nome de um grupo de acompanhamento."""
        resp = await self._request(
            "POST", f"/grupoacompanhamento/{id_grupo}/alterar", data={"nome": nome}
        )
        data = resp.json()
        if not data.get("sucesso"):
            msg = f"Erro ao alterar grupo: {data.get('mensagem')}"
            raise SEIError(msg)
        return data.get("data", {"mensagem": data.get("mensagem")})

    async def excluir_grupo_acompanhamento(self, ids_grupos: str) -> dict:
        """Exclui grupo(s) de acompanhamento. IDs separados por vírgula."""
        resp = await self._request(
            "POST", "/grupoacompanhamento/excluir", data={"grupos": ids_grupos}
        )
        data = resp.json()
        if not data.get("sucesso"):
            msg = f"Erro ao excluir grupo: {data.get('mensagem')}"
            raise SEIError(msg)
        return data.get("data", {"mensagem": data.get("mensagem")})

    async def listar_grupos_acompanhamento(self, filtro: str = "", limit: int = 50) -> dict:
        """Lista grupos de acompanhamento disponíveis."""
        params: dict = {"limit": limit}
        if filtro:
            params["filter"] = filtro
        resp = await self._request("GET", "/grupoacompanhamento/listar", params=params)
        data = resp.json()
        if not data.get("sucesso"):
            msg = f"Erro ao listar grupos: {data.get('mensagem')}"
            raise SEIError(msg)
        return {"grupos": data.get("data", []), "total": data.get("total")}

    # ------------------------------------------------------------------
    # Bloco Interno
    # ------------------------------------------------------------------

    async def criar_bloco_interno(self, descricao: str) -> dict:
        """Cria bloco interno."""
        resp = await self._request("POST", "/bloco/interno/criar", data={"descricao": descricao})
        data = resp.json()
        if not data.get("sucesso"):
            msg = f"Erro ao criar bloco interno: {data.get('mensagem')}"
            raise SEIError(msg)
        return data.get("data", {})

    async def incluir_processo_bloco_interno(self, id_bloco: str, protocolos: str) -> dict:
        """Inclui processo(s) em bloco interno."""
        resp = await self._request(
            "POST",
            f"/bloco/interno/{id_bloco}/processos/incluir",
            data={"protocolos": protocolos},
        )
        data = resp.json()
        if not data.get("sucesso"):
            msg = f"Erro ao incluir no bloco: {data.get('mensagem')}"
            raise SEIError(msg)
        return data.get("data", {"mensagem": data.get("mensagem")})

    async def retirar_processo_bloco_interno(self, id_bloco: str, protocolos: str) -> dict:
        """Remove processo(s) de bloco interno."""
        resp = await self._request(
            "POST",
            f"/bloco/interno/{id_bloco}/processos/retirar",
            data={"protocolos": protocolos},
        )
        data = resp.json()
        if not data.get("sucesso"):
            msg = f"Erro ao retirar do bloco: {data.get('mensagem')}"
            raise SEIError(msg)
        return data.get("data", {"mensagem": data.get("mensagem")})

    async def listar_processos_bloco_interno(self, id_bloco: str, limit: int = 200) -> list[dict]:
        """Lista processos de um bloco interno.

        Disponível desde mod-wssei 2.0.0 (SEI 4.0.x).
        """
        resp = await self._request(
            "GET",
            f"/bloco/interno/{id_bloco}/processos/listar",
            params={"limit": limit},
        )
        data = resp.json()
        if not data.get("sucesso"):
            msg = f"Erro ao listar processos do bloco: {data.get('mensagem')}"
            raise SEIError(msg)
        return data.get("data", [])

    async def alterar_bloco_interno(self, id_bloco: str, descricao: str) -> dict:
        """Altera descrição de um bloco interno.

        Disponível desde mod-wssei 2.0.0 (SEI 4.0.x).
        """
        resp = await self._request(
            "POST",
            f"/bloco/interno/{id_bloco}/alterar",
            data={"descricao": descricao},
        )
        data = resp.json()
        if not data.get("sucesso"):
            msg = f"Erro ao alterar bloco interno: {data.get('mensagem')}"
            raise SEIError(msg)
        return data.get("data", {"mensagem": data.get("mensagem")})

    async def excluir_blocos_internos(self, ids: str) -> dict:
        """Exclui bloco(s) interno(s). IDs separados por vírgula.

        Disponível desde mod-wssei 2.0.0 (SEI 4.0.x).
        """
        resp = await self._request("POST", "/bloco/interno/excluir", data={"blocos": ids})
        data = resp.json()
        if not data.get("sucesso"):
            msg = f"Erro ao excluir blocos internos: {data.get('mensagem')}"
            raise SEIError(msg)
        return data.get("data", {"mensagem": data.get("mensagem")})

    async def concluir_blocos_internos(self, ids: str) -> dict:
        """Conclui bloco(s) interno(s). IDs separados por vírgula.

        Disponível desde mod-wssei 2.0.0 (SEI 4.0.x).
        """
        resp = await self._request("POST", "/bloco/interno/concluir", data={"blocos": ids})
        data = resp.json()
        if not data.get("sucesso"):
            msg = f"Erro ao concluir blocos internos: {data.get('mensagem')}"
            raise SEIError(msg)
        return data.get("data", {"mensagem": data.get("mensagem")})

    async def reabrir_bloco_interno(self, id_bloco: str) -> dict:
        """Reabre bloco interno concluído.

        Disponível desde mod-wssei 2.0.0 (SEI 4.0.x).
        """
        resp = await self._request("POST", f"/bloco/interno/{id_bloco}/reabrir")
        data = resp.json()
        if not data.get("sucesso"):
            msg = f"Erro ao reabrir bloco interno: {data.get('mensagem')}"
            raise SEIError(msg)
        return data.get("data", {"mensagem": data.get("mensagem")})

    async def anotar_processo_bloco_interno(
        self, id_bloco: str, protocolo: str, descricao: str
    ) -> dict:
        """Cria anotação em processo dentro de um bloco interno.

        Disponível desde mod-wssei 2.0.0 (SEI 4.0.x).
        """
        resp = await self._request(
            "POST",
            "/bloco/interno/anotacao/cadastrar",
            data={"bloco": id_bloco, "protocolo": protocolo, "descricao": descricao},
        )
        data = resp.json()
        if not data.get("sucesso"):
            msg = f"Erro ao anotar no bloco interno: {data.get('mensagem')}"
            raise SEIError(msg)
        return data.get("data", {"mensagem": data.get("mensagem")})

    async def alterar_anotacao_bloco_interno(
        self, id_bloco: str, protocolo: str, descricao: str
    ) -> dict:
        """Altera anotação de processo em um bloco interno.

        Disponível desde mod-wssei 2.0.0 (SEI 4.0.x).
        """
        resp = await self._request(
            "POST",
            "/bloco/interno/anotacao/alterar",
            data={"bloco": id_bloco, "protocolo": protocolo, "descricao": descricao},
        )
        data = resp.json()
        if not data.get("sucesso"):
            msg = f"Erro ao alterar anotação do bloco interno: {data.get('mensagem')}"
            raise SEIError(msg)
        return data.get("data", {"mensagem": data.get("mensagem")})

    async def pesquisar_blocos_internos(
        self, filtro: str = "", limit: int = 50, start: int = 0
    ) -> dict:
        """Pesquisa blocos internos."""
        params: dict = {"limit": limit, "start": start}
        if filtro:
            params["filter"] = filtro
        resp = await self._request("GET", "/bloco/interno/pesquisar", params=params)
        data = resp.json()
        if not data.get("sucesso"):
            msg = f"Erro ao pesquisar blocos: {data.get('mensagem')}"
            raise SEIError(msg)
        return self._paginated(data, "blocos", data.get("data", []), start, limit)

    # ------------------------------------------------------------------
    # Bloco de Assinatura
    # ------------------------------------------------------------------

    async def criar_bloco_assinatura(self, descricao: str, unidades: str = "") -> dict:
        """Cria bloco de assinatura."""
        payload: dict = {"descricao": descricao}
        if unidades:
            payload["unidades"] = unidades
        resp = await self._request("POST", "/bloco/assinatura/criar", data=payload)
        data = resp.json()
        if not data.get("sucesso"):
            msg = f"Erro ao criar bloco de assinatura: {data.get('mensagem')}"
            raise SEIError(msg)
        return data.get("data", {})

    async def incluir_documento_bloco_assinatura(self, id_bloco: str, documentos: str) -> dict:
        """Inclui documento(s) em bloco de assinatura."""
        resp = await self._request(
            "POST",
            f"/bloco/assinatura/{id_bloco}/documentos/incluir",
            data={"documentos": documentos},
        )
        data = resp.json()
        if not data.get("sucesso"):
            msg = f"Erro ao incluir no bloco: {data.get('mensagem')}"
            raise SEIError(msg)
        return data.get("data", {"mensagem": data.get("mensagem")})

    async def disponibilizar_bloco_assinatura(self, id_bloco: str) -> dict:
        """Disponibiliza bloco de assinatura para as unidades."""
        resp = await self._request("POST", f"/bloco/assinatura/{id_bloco}/disponibilizar")
        data = resp.json()
        if not data.get("sucesso"):
            msg = f"Erro ao disponibilizar bloco: {data.get('mensagem')}"
            raise SEIError(msg)
        return data.get("data", {"mensagem": data.get("mensagem")})

    async def cancelar_disponibilizacao_bloco_assinatura(self, id_bloco: str) -> dict:
        """Cancela disponibilização de bloco de assinatura."""
        resp = await self._request(
            "POST", f"/bloco/assinatura/{id_bloco}/disponibilizacao/cancelar"
        )
        data = resp.json()
        if not data.get("sucesso"):
            msg = f"Erro ao cancelar disponibilização: {data.get('mensagem')}"
            raise SEIError(msg)
        return data.get("data", {"mensagem": data.get("mensagem")})

    async def pesquisar_blocos_assinatura(
        self, filtro: str = "", limit: int = 50, start: int = 0
    ) -> dict:
        """Pesquisa blocos de assinatura."""
        params: dict = {"limit": limit, "start": start}
        if filtro:
            params["filter"] = filtro
        resp = await self._request("GET", "/bloco/assinatura/pesquisar", params=params)
        data = resp.json()
        if not data.get("sucesso"):
            msg = f"Erro ao pesquisar blocos: {data.get('mensagem')}"
            raise SEIError(msg)
        return self._paginated(data, "blocos", data.get("data", []), start, limit)

    async def listar_documentos_bloco_assinatura(
        self, id_bloco: str, limit: int = 200
    ) -> list[dict]:
        """Lista documentos de um bloco de assinatura."""
        resp = await self._request(
            "GET",
            f"/bloco/assinatura/{id_bloco}/documentos/listar",
            params={"limit": limit},
        )
        data = resp.json()
        if not data.get("sucesso"):
            msg = f"Erro ao listar documentos do bloco: {data.get('mensagem')}"
            raise SEIError(msg)
        return data.get("data", [])

    async def retirar_documento_bloco_assinatura(self, id_bloco: str, documentos: str) -> dict:
        """Retira documento(s) de bloco de assinatura."""
        resp = await self._request(
            "POST",
            f"/bloco/assinatura/{id_bloco}/documentos/retirar",
            data={"documentos": documentos},
        )
        data = resp.json()
        if not data.get("sucesso"):
            msg = f"Erro ao retirar do bloco: {data.get('mensagem')}"
            raise SEIError(msg)
        return data.get("data", {"mensagem": data.get("mensagem")})

    async def alterar_bloco_assinatura(self, id_bloco: str, descricao: str) -> dict:
        """Altera descrição de um bloco de assinatura.

        Disponível desde mod-wssei 2.0.0 (SEI 4.0.x).
        """
        resp = await self._request(
            "POST",
            f"/bloco/assinatura/{id_bloco}/alterar",
            data={"descricao": descricao},
        )
        data = resp.json()
        if not data.get("sucesso"):
            msg = f"Erro ao alterar bloco de assinatura: {data.get('mensagem')}"
            raise SEIError(msg)
        return data.get("data", {"mensagem": data.get("mensagem")})

    async def excluir_blocos_assinatura(self, ids: str) -> dict:
        """Exclui bloco(s) de assinatura. IDs separados por vírgula.

        Disponível desde mod-wssei 2.0.0 (SEI 4.0.x).
        """
        resp = await self._request("POST", "/bloco/assinatura/excluir", data={"blocos": ids})
        data = resp.json()
        if not data.get("sucesso"):
            msg = f"Erro ao excluir blocos de assinatura: {data.get('mensagem')}"
            raise SEIError(msg)
        return data.get("data", {"mensagem": data.get("mensagem")})

    async def concluir_blocos_assinatura(self, ids: str) -> dict:
        """Conclui bloco(s) de assinatura. IDs separados por vírgula.

        Disponível desde mod-wssei 2.0.0 (SEI 4.0.x).
        """
        resp = await self._request("POST", "/bloco/assinatura/concluir", data={"blocos": ids})
        data = resp.json()
        if not data.get("sucesso"):
            msg = f"Erro ao concluir blocos de assinatura: {data.get('mensagem')}"
            raise SEIError(msg)
        return data.get("data", {"mensagem": data.get("mensagem")})

    async def reabrir_bloco_assinatura(self, id_bloco: str) -> dict:
        """Reabre bloco de assinatura concluído.

        Disponível desde mod-wssei 2.0.0 (SEI 4.0.x).
        """
        resp = await self._request("POST", f"/bloco/assinatura/{id_bloco}/reabrir")
        data = resp.json()
        if not data.get("sucesso"):
            msg = f"Erro ao reabrir bloco de assinatura: {data.get('mensagem')}"
            raise SEIError(msg)
        return data.get("data", {"mensagem": data.get("mensagem")})

    async def retornar_bloco_assinatura(self, id_bloco: str) -> dict:
        """Retorna bloco de assinatura para a unidade de origem.

        Disponível desde mod-wssei 2.0.0 (SEI 4.0.x).
        """
        resp = await self._request("POST", f"/bloco/assinatura/{id_bloco}/retornar")
        data = resp.json()
        if not data.get("sucesso"):
            msg = f"Erro ao retornar bloco de assinatura: {data.get('mensagem')}"
            raise SEIError(msg)
        return data.get("data", {"mensagem": data.get("mensagem")})

    async def anotar_documento_bloco_assinatura(
        self, id_bloco: str, documento: str, descricao: str
    ) -> dict:
        """Cria anotação em documento dentro de um bloco de assinatura.

        Disponível desde mod-wssei 2.0.0 (SEI 4.0.x).
        """
        resp = await self._request(
            "POST",
            "/bloco/assinatura/anotacao/cadastrar",
            data={"bloco": id_bloco, "documento": documento, "descricao": descricao},
        )
        data = resp.json()
        if not data.get("sucesso"):
            msg = f"Erro ao anotar no bloco de assinatura: {data.get('mensagem')}"
            raise SEIError(msg)
        return data.get("data", {"mensagem": data.get("mensagem")})

    async def alterar_anotacao_bloco_assinatura(
        self, id_bloco: str, documento: str, descricao: str
    ) -> dict:
        """Altera anotação de documento em um bloco de assinatura.

        Disponível desde mod-wssei 2.0.0 (SEI 4.0.x).
        """
        resp = await self._request(
            "POST",
            "/bloco/assinatura/anotacao/alterar",
            data={"bloco": id_bloco, "documento": documento, "descricao": descricao},
        )
        data = resp.json()
        if not data.get("sucesso"):
            msg = f"Erro ao alterar anotação do bloco: {data.get('mensagem')}"
            raise SEIError(msg)
        return data.get("data", {"mensagem": data.get("mensagem")})

    # ------------------------------------------------------------------
    # Atribuição — remover
    # ------------------------------------------------------------------

    async def remover_atribuicao(self, id_procedimento: str) -> dict:
        """Remove atribuição de um processo (desatribui). Usa IdProcedimento."""
        resp = await self._request("POST", f"/processo/{id_procedimento}/remover/atribuicao")
        data = resp.json()
        if not data.get("sucesso"):
            msg = f"Erro ao remover atribuição: {data.get('mensagem')}"
            raise SEIError(msg)
        return data.get("data", {"mensagem": data.get("mensagem")})

    # ------------------------------------------------------------------
    # Receber processo
    # ------------------------------------------------------------------

    async def receber_processo(self, id_procedimento: str) -> dict:
        """Recebe processo na unidade atual (confirma recebimento)."""
        resp = await self._request(
            "POST", "/processo/receber", data={"procedimento": id_procedimento}
        )
        data = resp.json()
        if not data.get("sucesso"):
            msg = f"Erro ao receber processo: {data.get('mensagem')}"
            raise SEIError(msg)
        return data.get("data", {"mensagem": data.get("mensagem")})

    # ------------------------------------------------------------------
    # Documento externo (upload)
    # ------------------------------------------------------------------

    async def _post_with_file_reopen(
        self,
        url: str,
        arquivo_path: str,
        nome_arquivo: str,
        data: dict[str, str],
    ) -> httpx.Response:
        """POST multipart com arquivo; reabre o arquivo e repete em caso de 401/403.

        Extrai o padrão open→POST→reauth→open→POST comum em uploads, evitando
        duplicação e garantindo que o arquivo seja lido do início nas duas tentativas.
        """
        headers = await self._get_headers()
        file_bytes = await asyncio.to_thread(Path(arquivo_path).read_bytes)
        resp = await self._client.post(
            url,
            headers=headers,
            data=data,
            files={"anexo": (nome_arquivo, file_bytes)},
        )
        if resp.status_code in (401, 403):
            logger.info("Token expirado durante upload, re-autenticando...")
            await self.autenticar()
            headers = {"token": self._token or ""}
            file_bytes = await asyncio.to_thread(Path(arquivo_path).read_bytes)
            resp = await self._client.post(
                url,
                headers=headers,
                data=data,
                files={"anexo": (nome_arquivo, file_bytes)},
            )
        return resp

    async def criar_documento_externo(
        self,
        id_procedimento: str,
        id_serie: str,
        arquivo_path: str,
        descricao: str = "",
        nivel_acesso: str = "0",
        id_unidade: str = "",
    ) -> dict:
        """Cria documento externo com upload de arquivo em um processo SEI.

        arquivo_path: caminho local do arquivo (PDF, imagem, etc.)
        Retorna: {idDocumento, protocoloDocumentoFormatado}
        """
        if not await asyncio.to_thread(Path(arquivo_path).exists):
            msg = f"Arquivo não encontrado: {arquivo_path}"
            raise SEIError(msg)

        nome_arquivo = Path(arquivo_path).name
        data_hoje = datetime.now(tz=UTC).astimezone().strftime("%d/%m/%Y")

        resp = await self._post_with_file_reopen(
            url=f"{self.base_url}/documento/{id_procedimento}/externo/criar",
            arquivo_path=arquivo_path,
            nome_arquivo=nome_arquivo,
            data={
                "idSerie": id_serie,
                "numero": "",
                "descricao": descricao,
                "dataElaboracao": data_hoje,
                "nivelAcesso": nivel_acesso,
                "idHipoteseLegal": "",
                "grauSigilo": "",
                "idUnidadeGeradoraProtocolo": id_unidade,
                "assuntos": "",
                "interessados": "",
                "remetente": "",
                "destinatarios": "",
                "observacao": "",
                "idTextoPadraoInterno": "",
                "idTipoConferencia": "",
                "protocoloDocumentoModelo": "",
            },
        )

        resp.raise_for_status()
        data = resp.json()
        if not data.get("sucesso"):
            msg = f"Erro ao criar documento externo: {data.get('mensagem')}"
            raise SEIError(msg)
        return data["data"]

    # ------------------------------------------------------------------
    # Retorno programado
    # ------------------------------------------------------------------

    async def agendar_retorno_programado(
        self,
        numero_processo: str,
        data_retorno: str = "",
        dias_retorno: str = "",
        dias_uteis: str = "S",
    ) -> dict:
        """Agenda retorno programado para um processo."""
        payload: dict = {"numeroProcesso": numero_processo}
        if data_retorno:
            payload["dataRetornoProgramado"] = data_retorno
        if dias_retorno:
            payload["diasRetornoProgramado"] = dias_retorno
            payload["sinDiasUteisRetornoProgramado"] = dias_uteis
        resp = await self._request("POST", "/processo/agendar/retorno/programado", data=payload)
        data = resp.json()
        if not data.get("sucesso"):
            msg = f"Erro ao agendar retorno: {data.get('mensagem')}"
            raise SEIError(msg)
        return data.get("data", {"mensagem": data.get("mensagem")})

    # ------------------------------------------------------------------
    # Consultas adicionais de processo
    # ------------------------------------------------------------------

    async def listar_sobrestamentos(self, id_procedimento: str) -> list[dict]:
        """Lista histórico de sobrestamentos de um processo."""
        resp = await self._request("GET", f"/processo/listar/sobrestamento/{id_procedimento}")
        data = resp.json()
        if not data.get("sucesso"):
            msg = f"Erro ao listar sobrestamentos: {data.get('mensagem')}"
            raise SEIError(msg)
        return data.get("data", [])

    async def listar_unidades_processo(self, id_procedimento: str) -> list[dict]:
        """Lista unidades onde o processo está aberto."""
        resp = await self._request("GET", f"/processo/listar/unidades/{id_procedimento}")
        data = resp.json()
        if not data.get("sucesso"):
            msg = f"Erro ao listar unidades: {data.get('mensagem')}"
            raise SEIError(msg)
        return data.get("data", [])

    async def listar_interessados(self, id_procedimento: str) -> list[dict]:
        """Lista interessados de um processo."""
        resp = await self._request("GET", f"/processo/{id_procedimento}/interessados/listar")
        data = resp.json()
        if not data.get("sucesso"):
            msg = f"Erro ao listar interessados: {data.get('mensagem')}"
            raise SEIError(msg)
        return data.get("data", [])

    async def pesquisar_assuntos(self, filtro: str = "", limit: int = 50, start: int = 0) -> dict:
        """Pesquisa assuntos disponíveis para processos.

        Disponível desde mod-wssei 2.0.0 (SEI 4.0.x).
        """
        params: dict = {"limit": limit, "start": start}
        if filtro:
            params["filter"] = filtro
        resp = await self._request("GET", "/processo/assunto/pesquisar", params=params)
        data = resp.json()
        if not data.get("sucesso"):
            msg = f"Erro ao pesquisar assuntos: {data.get('mensagem')}"
            raise SEIError(msg)
        return self._paginated(data, "assuntos", data.get("data", []), start, limit)

    async def sugestao_assuntos_processo(self, id_tipo_processo: str) -> list[dict]:
        """Lista sugestões de assuntos para um tipo de processo.

        Disponível desde mod-wssei 2.0.0 (SEI 4.0.x).
        """
        resp = await self._request("GET", f"/processo/assunto/sugestao/{id_tipo_processo}/listar")
        data = resp.json()
        if not data.get("sucesso"):
            msg = f"Erro ao listar sugestões de assunto: {data.get('mensagem')}"
            raise SEIError(msg)
        return data.get("data", [])

    async def consultar_atribuicao(self, id_procedimento: str) -> dict:
        """Consulta atribuição atual de um processo.

        Disponível desde mod-wssei 2.0.0 (SEI 4.0.x).
        """
        resp = await self._request("GET", f"/processo/{id_procedimento}/consultar/atribuicao")
        data = resp.json()
        if not data.get("sucesso"):
            msg = f"Erro ao consultar atribuição: {data.get('mensagem')}"
            raise SEIError(msg)
        return data.get("data", {})

    async def verificar_acesso(self, id_procedimento: str) -> dict:
        """Verifica se o usuário tem acesso a um processo.

        Disponível desde mod-wssei 2.0.0 (SEI 4.0.x).
        """
        resp = await self._request("GET", f"/processo/verifica/acesso/{id_procedimento}")
        data = resp.json()
        if not data.get("sucesso"):
            msg = f"Erro ao verificar acesso: {data.get('mensagem')}"
            raise SEIError(msg)
        return data.get("data", {})

    async def listar_relacionamentos(self, id_procedimento: str) -> list[dict]:
        """Lista processos relacionados.

        REQUER mod-wssei 3.0.2+ (SEI 5.0.x). Não disponível em versões anteriores.
        """
        resp = await self._request("GET", f"/processo/{id_procedimento}/relacionamentos")
        data = resp.json()
        if not data.get("sucesso"):
            msg = f"Erro ao listar relacionamentos: {data.get('mensagem')}"
            raise SEIError(msg)
        return data.get("data", [])

    async def listar_meus_acompanhamentos(self, limit: int = 50, start: int = 0) -> dict:
        """Lista processos acompanhados pelo usuário.

        Disponível desde mod-wssei 2.0.0 (SEI 4.0.x).
        """
        params: dict = {"limit": limit, "start": start}
        resp = await self._request("GET", "/processo/listar/meus/acompanhamentos", params=params)
        data = resp.json()
        if not data.get("sucesso"):
            msg = f"Erro ao listar acompanhamentos: {data.get('mensagem')}"
            raise SEIError(msg)
        return self._paginated(data, "acompanhamentos", data.get("data", []), start, limit)

    async def listar_acompanhamentos_unidade(self, limit: int = 50, start: int = 0) -> dict:
        """Lista processos acompanhados na unidade atual.

        Disponível desde mod-wssei 2.0.0 (SEI 4.0.x).
        """
        params: dict = {"limit": limit, "start": start}
        resp = await self._request("GET", "/processo/listar/acompanhamentos", params=params)
        data = resp.json()
        if not data.get("sucesso"):
            msg = f"Erro ao listar acompanhamentos da unidade: {data.get('mensagem')}"
            raise SEIError(msg)
        return self._paginated(data, "acompanhamentos", data.get("data", []), start, limit)

    async def alterar_acompanhamento(
        self, id_procedimento: str, id_grupo: str = "", observacao: str = ""
    ) -> dict:
        """Altera acompanhamento especial de um processo.

        Disponível desde mod-wssei 2.0.0 (SEI 4.0.x).
        """
        payload: dict = {"protocolo": id_procedimento}
        if id_grupo:
            payload["grupo"] = id_grupo
        if observacao:
            payload["observacao"] = observacao
        resp = await self._request("POST", "/processo/acompanhamento/alterar", data=payload)
        data = resp.json()
        if not data.get("sucesso"):
            msg = f"Erro ao alterar acompanhamento: {data.get('mensagem')}"
            raise SEIError(msg)
        return data.get("data", {"mensagem": data.get("mensagem")})

    # ------------------------------------------------------------------
    # Credenciamento (processos sigilosos)
    # Disponível desde mod-wssei 2.0.0 (SEI 4.0.x).
    # ------------------------------------------------------------------

    async def listar_credenciamentos(self, id_procedimento: str) -> list[dict]:
        """Lista credenciamentos de acesso a um processo sigiloso."""
        resp = await self._request("GET", f"/processo/{id_procedimento}/credenciamento/listar")
        data = resp.json()
        if not data.get("sucesso"):
            msg = f"Erro ao listar credenciamentos: {data.get('mensagem')}"
            raise SEIError(msg)
        return data.get("data", [])

    async def conceder_credenciamento(self, id_procedimento: str, id_usuario: str) -> dict:
        """Concede credenciamento de acesso a um processo sigiloso."""
        resp = await self._request(
            "POST",
            f"/processo/{id_procedimento}/credenciamento/conceder",
            data={"usuario": id_usuario},
        )
        data = resp.json()
        if not data.get("sucesso"):
            msg = f"Erro ao conceder credenciamento: {data.get('mensagem')}"
            raise SEIError(msg)
        return data.get("data", {"mensagem": data.get("mensagem")})

    async def renunciar_credenciamento(self, id_procedimento: str) -> dict:
        """Renuncia ao credenciamento de acesso a um processo sigiloso."""
        resp = await self._request("POST", f"/processo/{id_procedimento}/credenciamento/renunciar")
        data = resp.json()
        if not data.get("sucesso"):
            msg = f"Erro ao renunciar credenciamento: {data.get('mensagem')}"
            raise SEIError(msg)
        return data.get("data", {"mensagem": data.get("mensagem")})

    async def cassar_credenciamento(self, id_procedimento: str, id_usuario: str) -> dict:
        """Cassa credenciamento de acesso de um usuário a processo sigiloso."""
        resp = await self._request(
            "POST",
            f"/processo/{id_procedimento}/credenciamento/cassar",
            data={"usuario": id_usuario},
        )
        data = resp.json()
        if not data.get("sucesso"):
            msg = f"Erro ao cassar credenciamento: {data.get('mensagem')}"
            raise SEIError(msg)
        return data.get("data", {"mensagem": data.get("mensagem")})

    # ------------------------------------------------------------------
    # Andamento
    # ------------------------------------------------------------------

    async def listar_atividades(
        self, id_procedimento: str, limit: int = 50, start: int = 0
    ) -> dict:
        """Lista histórico de atividades/andamentos de um processo.

        Disponível desde mod-wssei 2.0.0 (SEI 4.0.x).
        """
        params: dict = {"protocolo": id_procedimento, "limit": limit, "start": start}
        resp = await self._request("GET", "/atividade/listar", params=params)
        data = resp.json()
        if not data.get("sucesso"):
            msg = f"Erro ao listar atividades: {data.get('mensagem')}"
            raise SEIError(msg)
        return self._paginated(data, "atividades", data.get("data", []), start, limit)

    async def registrar_andamento(self, id_procedimento: str, descricao: str) -> dict:
        """Registra andamento (atividade) em um processo."""
        resp = await self._request(
            "POST",
            "/atividade/lancar/andamento/processo",
            data={"protocolo": id_procedimento, "descricao": descricao},
        )
        data = resp.json()
        if not data.get("sucesso"):
            msg = f"Erro ao registrar andamento: {data.get('mensagem')}"
            raise SEIError(msg)
        return data.get("data", {"mensagem": data.get("mensagem")})

    # ------------------------------------------------------------------
    # Contato
    # ------------------------------------------------------------------

    async def pesquisar_contatos(self, filtro: str = "", limit: int = 50) -> dict:
        """Pesquisa contatos no SEI."""
        params: dict = {"limit": limit}
        if filtro:
            params["filter"] = filtro
        resp = await self._request("GET", "/contato/pesquisar", params=params)
        data = resp.json()
        if not data.get("sucesso"):
            msg = f"Erro ao pesquisar contatos: {data.get('mensagem')}"
            raise SEIError(msg)
        return {"contatos": data.get("data", []), "total": data.get("total")}

    async def criar_contato(
        self, nome: str, tipo: str = "", email: str = "", telefone: str = ""
    ) -> dict:
        """Cria novo contato no SEI.

        Disponível desde mod-wssei 2.0.0 (SEI 4.0.x).
        """
        payload: dict = {"nome": nome}
        if tipo:
            payload["tipo"] = tipo
        if email:
            payload["email"] = email
        if telefone:
            payload["telefone"] = telefone
        resp = await self._request("POST", "/contato/criar", data=payload)
        data = resp.json()
        if not data.get("sucesso"):
            msg = f"Erro ao criar contato: {data.get('mensagem')}"
            raise SEIError(msg)
        return data.get("data", {"mensagem": data.get("mensagem")})

    # ------------------------------------------------------------------
    # Assinante — listar signatários e órgãos
    # Disponível desde mod-wssei 2.0.0 (SEI 4.0.x).
    # ------------------------------------------------------------------

    async def listar_assinantes(self) -> list[dict]:
        """Lista signatários (cargos/funções) disponíveis na unidade atual."""
        resp = await self._request("GET", "/assinante/listar")
        data = resp.json()
        if not data.get("sucesso"):
            msg = f"Erro ao listar assinantes: {data.get('mensagem')}"
            raise SEIError(msg)
        return data.get("data", [])

    async def listar_orgaos_assinante(self) -> list[dict]:
        """Lista órgãos disponíveis para assinatura."""
        resp = await self._request("GET", "/assinante/orgao")
        data = resp.json()
        if not data.get("sucesso"):
            msg = f"Erro ao listar órgãos para assinatura: {data.get('mensagem')}"
            raise SEIError(msg)
        return data.get("data", [])

    # ------------------------------------------------------------------
    # Observação de unidade
    # Disponível desde mod-wssei 2.0.0 (SEI 4.0.x).
    # ------------------------------------------------------------------

    async def criar_observacao(self, id_procedimento: str, descricao: str) -> dict:
        """Cria observação da unidade em um processo (diferente de anotação).

        Observação é visível apenas para a unidade, anotação é post-it individual.
        """
        resp = await self._request(
            "POST",
            "/observacao/",
            data={"protocolo": id_procedimento, "descricao": descricao},
        )
        data = resp.json()
        if not data.get("sucesso"):
            msg = f"Erro ao criar observação: {data.get('mensagem')}"
            raise SEIError(msg)
        return data.get("data", {"mensagem": data.get("mensagem")})

    # ------------------------------------------------------------------
    # Modelos de documento
    # Disponível desde mod-wssei 2.0.0 (SEI 4.0.x).
    # ------------------------------------------------------------------

    async def listar_grupos_modelos(self, limit: int = 50, start: int = 0) -> dict:
        """Lista grupos de modelos de documento disponíveis."""
        params: dict = {"limit": limit, "start": start}
        resp = await self._request("GET", "/protocolomodelo/grupo/listar", params=params)
        data = resp.json()
        if not data.get("sucesso"):
            msg = f"Erro ao listar grupos de modelos: {data.get('mensagem')}"
            raise SEIError(msg)
        return self._paginated(data, "grupos", data.get("data", []), start, limit)

    async def listar_modelos(
        self, id_grupo: str = "", filtro: str = "", limit: int = 50, start: int = 0
    ) -> dict:
        """Lista modelos de documento disponíveis."""
        params: dict = {"limit": limit, "start": start}
        if id_grupo:
            params["grupo"] = id_grupo
        if filtro:
            params["filter"] = filtro
        resp = await self._request("GET", "/protocolomodelo/listar", params=params)
        data = resp.json()
        if not data.get("sucesso"):
            msg = f"Erro ao listar modelos: {data.get('mensagem')}"
            raise SEIError(msg)
        return self._paginated(data, "modelos", data.get("data", []), start, limit)

    # ------------------------------------------------------------------
    # Marcador — histórico
    # Disponível desde mod-wssei 2.0.0 (SEI 4.0.x).
    # ------------------------------------------------------------------

    async def historico_marcador_processo(self, id_procedimento: str) -> list[dict]:
        """Lista histórico de marcadores de um processo."""
        resp = await self._request("GET", f"/marcador/processo/{id_procedimento}/historico/listar")
        data = resp.json()
        if not data.get("sucesso"):
            msg = f"Erro ao listar histórico de marcadores: {data.get('mensagem')}"
            raise SEIError(msg)
        return data.get("data", [])

    # ------------------------------------------------------------------
    # Bloco de assinatura — assinar
    # ------------------------------------------------------------------

    async def assinar_bloco(
        self,
        id_bloco: str,
        login: str,
        senha: str,
        cargo: str,
        orgao: str = "",
        id_usuario: str = "",
    ) -> dict:
        """Assina todos os documentos de um bloco de assinatura."""
        payload = {
            "orgao": orgao or self._orgao,
            "cargo": cargo,
            "login": login,
            "senha": senha,
        }
        if id_usuario:
            payload["usuario"] = id_usuario
        resp = await self._request("POST", f"/bloco/assinatura/{id_bloco}/assinar", data=payload)
        data = resp.json()
        if not data.get("sucesso"):
            msg = f"Erro ao assinar bloco: {data.get('mensagem')}"
            raise SEIError(msg)
        return data.get("data", {"mensagem": data.get("mensagem")})

    async def assinar_documentos_bloco(
        self,
        login: str,
        senha: str,
        cargo: str,
        documentos: str,
        orgao: str = "",
        id_usuario: str = "",
    ) -> dict:
        """Assina documentos específicos (de um ou mais blocos)."""
        payload = {
            "orgao": orgao or self._orgao,
            "cargo": cargo,
            "login": login,
            "senha": senha,
            "documentos": documentos,
        }
        if id_usuario:
            payload["usuario"] = id_usuario
        resp = await self._request("POST", "/bloco/assinatura/assinar/documentos", data=payload)
        data = resp.json()
        if not data.get("sucesso"):
            msg = f"Erro ao assinar documentos: {data.get('mensagem')}"
            raise SEIError(msg)
        return data.get("data", {"mensagem": data.get("mensagem")})

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self._client.aclose()
