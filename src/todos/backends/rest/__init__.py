"""Backend REST do SEI — wrapper fino sobre `SEIClient` (mod-wssei v2).

`SEIRestBackend` implementa as operações do contrato `SEIBackend` que a REST
mod-wssei suporta, delegando cada uma ao método correspondente do `SEIClient`.

Convenções:
- Onde o contrato usa `processo` (protocolo formatado), o backend resolve o
  `IdProcedimento` interno via `_resolver_processo` antes de chamar a REST.
- Onde o contrato usa uma referência de documento, `_resolver_documento`
  traduz número SEU/protocolo para o id interno.
- Os retornos são repassados crus (dict/list/bytes); a formatação, o envelope
  JSON e o controle de acesso permanecem na camada de tools.

Operações sem método REST equivalente (unidades web-only, geração de PDF/ZIP,
ações genéricas do toolbar, `marcar_nao_lido`, etc.) NÃO são sobrescritas e
herdam o stub `NotImplementedError` da base.

A implementação está dividida em mixins por domínio (mirror de `todos.tools`);
`SEIRestBackend` apenas os compõe via herança.
"""

from __future__ import annotations

from todos.backends.base import SEIBackend
from todos.backends.rest._session import _RestBase
from todos.backends.rest.acompanhamento import AcompanhamentoRest
from todos.backends.rest.blocos import BlocosRest
from todos.backends.rest.catalogos import CatalogosRest
from todos.backends.rest.credenciamento import CredenciamentoRest
from todos.backends.rest.documentos import DocumentosRest
from todos.backends.rest.marcadores import MarcadoresRest
from todos.backends.rest.processos import ProcessosRest
from todos.backends.rest.unidades import UnidadesRest

__all__ = ["SEIRestBackend"]


class SEIRestBackend(
    _RestBase,
    UnidadesRest,
    ProcessosRest,
    DocumentosRest,
    CatalogosRest,
    MarcadoresRest,
    AcompanhamentoRest,
    BlocosRest,
    CredenciamentoRest,
    SEIBackend,
):
    """Backend que atende ao contrato SEIBackend via REST mod-wssei v2."""

    name = "rest"
