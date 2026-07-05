"""Backend web do SEI — wrapper fino sobre `SEIWebClient` (scraper HTTP).

`SEIWebBackend` implementa as operações do contrato `SEIBackend` que o scraper
do frontend web do SEI suporta, delegando cada uma ao método correspondente do
`SEIWebClient`.

Convenções:
- O contrato usa `processo` (protocolo formatado); o scraper usa o protocolo
  direto, sem resolução de id interno — basta repassá-lo.
- Os retornos são repassados crus (dict/list/bytes); a formatação, o envelope
  JSON e o controle de acesso permanecem na camada de tools. Onde a tool web
  pós-processa o resultado (ex: extrair `sobrestamentos` de
  `consultar_processo_detalhe`), o backend replica essa extração.

Operações sem equivalente web (assinatura PKI, credenciamento, histórico de
marcador, excluir/desativar/reativar marcador, blocos internos, `versao`,
`resumo_processos`, etc.) NÃO são sobrescritas e herdam o stub
`NotImplementedError` da base. `criar_marcador`/`listar_cores_marcador` TÊM
equivalente web (`MarcadoresWeb`) desde RFC 0026 — funcionam mesmo sem
mod-wssei.

A implementação é dividida em mixins de domínio (espelhando `todos.tools`),
compostos pela classe concreta `SEIWebBackend`.
"""

from __future__ import annotations

from todos.backends.base import SEIBackend
from todos.backends.web._session import _WebBase
from todos.backends.web.acompanhamento import AcompanhamentoWeb
from todos.backends.web.blocos import BlocosWeb
from todos.backends.web.catalogos import CatalogosWeb
from todos.backends.web.documentos import DocumentosWeb
from todos.backends.web.generico import GenericoWeb
from todos.backends.web.marcadores import MarcadoresWeb
from todos.backends.web.processos import ProcessosWeb
from todos.backends.web.unidades import UnidadesWeb

__all__ = ["SEIWebBackend"]


class SEIWebBackend(
    _WebBase,
    UnidadesWeb,
    ProcessosWeb,
    DocumentosWeb,
    CatalogosWeb,
    MarcadoresWeb,
    AcompanhamentoWeb,
    BlocosWeb,
    GenericoWeb,
    SEIBackend,
):
    """Backend que atende ao contrato `SEIBackend` via scraper do frontend web."""

    name = "web"
