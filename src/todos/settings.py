"""Configuração de processo centralizada (RFC 0016).

`TodosSettings` é a fonte única das variáveis de ambiente de **processo** —
conexão SEI, credenciais lidas do ambiente e identificadores do frontend web.
Substitui as chamadas `os.environ.get(...)` espalhadas pelos construtores de
`SEIClient`/`SEIWebClient`.

Os campos têm o mesmo nome (em minúsculas) da variável de ambiente que leem,
espelhando os campos dos dataclasses `SEIClientConfig`/`SEIWebClientConfig`:
``sei_url`` ↔ ``SEI_URL``, ``sei_verify_ssl`` ↔ ``SEI_VERIFY_SSL`` etc.

Fora de escopo (ver RFC 0016 §2.2): credenciais OAuth por-request — no modo
HTTP elas vêm do token via `auth.get_sei_credentials_from_token`, não daqui.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


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
    )

    # -- Conexão SEI --------------------------------------------------------
    sei_url: str = ""
    sei_web_url: str = ""
    sei_usuario: str = ""
    sei_senha: str = ""
    sei_orgao: str = "0"
    sei_contexto: str = ""

    # -- TLS ----------------------------------------------------------------
    sei_verify_ssl: bool = True
    sei_ca_bundle: str = ""

    # -- Identificadores do frontend web ------------------------------------
    sei_sigla_orgao: str = "ANTAQ"
    sei_sigla_sistema: str = "SEI"
    sei_sigla_orgao_sistema: str = ""


@lru_cache(maxsize=1)
def get_settings() -> TodosSettings:
    """Retorna a instância única de ``TodosSettings`` (lida do ambiente uma vez).

    Cacheada por processo. Em testes que alteram o ambiente após a primeira
    leitura, chame ``get_settings.cache_clear()`` para forçar releitura.
    """
    return TodosSettings()
