"""Base de sessão dos mixins do backend web.

Armazena o `SEIWebClient` encapsulado em `self._web`, compartilhado por todos os
mixins de domínio via MRO. Não implementa nenhuma operação de contrato.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from todos.sei_web_client import SEIWebClient


class _WebMixin:
    """Atributos compartilhados pelos mixins web, declarados só para o type-checker.

    Em runtime `self._web` é provido por `_WebBase` na classe composta
    `SEIWebBackend`; os mixins de domínio apenas o usam via `self`.
    """

    if TYPE_CHECKING:
        _web: SEIWebClient


class _WebBase(_WebMixin):
    """Mixin base que encapsula o `SEIWebClient` compartilhado pelos demais mixins."""

    def __init__(self, client: SEIWebClient) -> None:
        """Armazena o cliente web a ser encapsulado."""
        self._web = client
