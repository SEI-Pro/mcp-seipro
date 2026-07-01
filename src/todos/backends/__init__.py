"""Backends do SEI: contrato abstrato + implementações REST e web.

- `SEIBackend` (base.py): contrato abstrato com todas as operações. Cada método
  levanta `NotImplementedError` por padrão, então as subclasses só precisam
  sobrescrever as operações que de fato suportam.
- `SEIRestBackend` (rest.py): operações via mod-wssei REST.
- `SEIWebBackend` (web.py): operações via scraper do frontend web.

Não há roteamento automático REST/web: cada tool MCP recebe o backend cru
que o chamador escolheu explicitamente (ver `todos.backends.choice` e
`mcp_app._backend`/`_rest_backend`/`_web_backend`). Nenhum backend tenta o
outro silenciosamente em caso de falha.
"""

from __future__ import annotations

from todos.backends.base import (
    EnvioProcesso,
    FiltrosPesquisaProcessos,
    NovoDocumentoExterno,
    NovoDocumentoInterno,
    NovoProcesso,
    SEIBackend,
)
from todos.backends.rest import SEIRestBackend
from todos.backends.web import SEIWebBackend

__all__ = [
    "EnvioProcesso",
    "FiltrosPesquisaProcessos",
    "NovoDocumentoExterno",
    "NovoDocumentoInterno",
    "NovoProcesso",
    "SEIBackend",
    "SEIRestBackend",
    "SEIWebBackend",
]
