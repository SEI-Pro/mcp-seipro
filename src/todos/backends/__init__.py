"""Backends do SEI: contrato abstrato + implementações REST e web.

- `SEIBackend` (base.py): contrato abstrato com todas as operações. Cada método
  levanta `NotImplementedError` por padrão, então as subclasses só precisam
  sobrescrever as operações que de fato suportam.
- `SEIRestBackend` (rest.py): operações via mod-wssei REST.
- `SEIWebBackend` (web.py): operações via scraper do frontend web.

O roteamento REST-first-com-fallback-web vive no factory/composite, não em cada
tool — graças ao contrato compartilhado, a composição é genérica.
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
from todos.backends.composite import CompositeBackend, build_backend
from todos.backends.rest import SEIRestBackend
from todos.backends.web import SEIWebBackend

__all__ = [
    "CompositeBackend",
    "EnvioProcesso",
    "FiltrosPesquisaProcessos",
    "NovoDocumentoExterno",
    "NovoDocumentoInterno",
    "NovoProcesso",
    "SEIBackend",
    "SEIRestBackend",
    "SEIWebBackend",
    "build_backend",
]
