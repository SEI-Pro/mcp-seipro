"""Configuração de processo centralizada (RFC 0016).

`TodosSettings` é a fonte única das variáveis de ambiente de **processo** —
conexão SEI, credenciais, limites operacionais, controle de acesso e
identificadores do frontend web. Substitui as chamadas `os.environ.get(...)`
espalhadas pelos módulos da aplicação.

Os campos têm o mesmo nome (em minúsculas) da variável de ambiente que leem,
espelhando os campos dos dataclasses `SEIClientConfig`/`SEIWebClientConfig`:
``sei_url`` ↔ ``SEI_URL``, ``sei_verify_ssl`` ↔ ``SEI_VERIFY_SSL`` etc.

Fora de escopo (ver RFC 0016 §2.2): credenciais OAuth por-request — no modo
HTTP elas vêm do token via `auth.get_sei_credentials_from_token`, não daqui.
"""

from __future__ import annotations

import logging
from functools import lru_cache

from pydantic import AliasChoices, Field, ValidationInfo, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class TodosSettings(BaseSettings):
    """Configuração de processo do todos, lida do ambiente.

    O nome de cada campo é a variável de ambiente correspondente em minúsculas
    (a leitura é case-insensitive).
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
        populate_by_name=True,
        env_ignore_empty=True,
    )

    # -- Conexão SEI (Phase 1) ------------------------------------------------
    sei_url: str = ""
    sei_web_url: str = ""
    sei_usuario: str = ""
    sei_senha: str = ""
    sei_orgao: str = "0"
    sei_contexto: str = ""

    # -- TLS (Phase 1) --------------------------------------------------------
    sei_verify_ssl: bool = True
    sei_ca_bundle: str = ""

    # -- Identificadores do frontend web (Phase 1) ----------------------------
    sei_sigla_orgao: str = "ANTAQ"
    sei_sigla_sistema: str = "SEI"
    sei_sigla_orgao_sistema: str = ""

    # -- Sessões e limites (Phase 2) ------------------------------------------
    sei_max_sessions: int = 100
    sei_max_ocr_pages: int = 20
    sei_elicit_timeout_s: float = 30.0
    # AliasChoices: SEI_CACHE_TTL_SECONDS takes priority; CATALOG_CACHE_TTL kept for compatibility.
    sei_cache_ttl_seconds: int | None = Field(
        None,
        validation_alias=AliasChoices("sei_cache_ttl_seconds", "catalog_cache_ttl"),
    )

    # -- Controle de acesso (Phase 2) -----------------------------------------
    # Pipe-separated extra risk strings; parsed to list by access_control.riscos_padrao().
    sei_riscos_extra: str = ""
    sei_permitir_restritos: bool = False

    # -- Caminhos e uploads (Phase 2) -----------------------------------------
    todos_cache_dir: str = ""
    sei_upload_dir: str = ""

    # -- Protocolo e OCR (Phase 2) --------------------------------------------
    sei_protocolo_pattern: str = ""
    sei_ocr_lang: str = "por"

    # -- Hints para agentes (Phase 2) -----------------------------------------
    # JSON array of strings; empty/invalid JSON → built-in defaults applied by hints.py.
    sei_hints: str = ""

    # -- Segurança HTTP (Phase 3) ---------------------------------------------
    jwt_secret: str = ""

    # -- Validators -----------------------------------------------------------

    @field_validator("sei_verify_ssl", mode="before")
    @classmethod
    def _verify_ssl_from_env(cls, value: object) -> object:
        """Interpreta ``SEI_VERIFY_SSL`` como string do ambiente: só ``false`` desabilita.

        Preserva a semântica histórica dos clientes (qualquer valor diferente de
        ``false`` mantém a verificação ligada) e evita um ``ValidationError`` quando
        o operador define ``SEI_VERIFY_SSL=`` (vazio) no ambiente ou no ``.env`` —
        nesse caso vale o default documentado (verificação ligada).
        """
        if isinstance(value, str):
            return value.strip().lower() != "false"
        return value

    @field_validator("sei_cache_ttl_seconds")
    @classmethod
    def _validate_ttl_positive(cls, value: int | None) -> int | None:
        """TTL must be positive when explicitly set."""
        if value is not None and value <= 0:
            msg = f"SEI_CACHE_TTL_SECONDS deve ser positivo; recebido: {value}"
            raise ValueError(msg)
        return value

    @field_validator("sei_max_sessions", "sei_max_ocr_pages")
    @classmethod
    def _validate_positive_limit(cls, value: int, info: ValidationInfo) -> int:
        """Limites de contagem devem ser positivos — falha rápido em vez de OCRar 0 páginas.

        ``min(len(images), 0)`` silenciosamente não OCRa nada; um valor negativo é
        ainda mais nonsensical. Rejeitar na carga da config dá um erro acionável.
        """
        if value <= 0:
            campo = (info.field_name or "valor").upper()
            msg = f"{campo} deve ser positivo; recebido: {value}"
            raise ValueError(msg)
        return value

    @field_validator("sei_permitir_restritos", mode="before")
    @classmethod
    def _parse_permitir_restritos(cls, value: object) -> object:
        """Aceita '1', 'true', 'yes', 'sim' (preserva suporte a pt-BR)."""
        if isinstance(value, str):
            return value.strip().lower() in ("1", "true", "yes", "sim")
        return value


@lru_cache(maxsize=1)
def get_settings() -> TodosSettings:
    """Retorna a instância única de ``TodosSettings`` (lida do ambiente uma vez).

    Cacheada por processo. Em testes que alteram o ambiente após a primeira
    leitura, chame ``get_settings.cache_clear()`` para forçar releitura.
    """
    return TodosSettings()
