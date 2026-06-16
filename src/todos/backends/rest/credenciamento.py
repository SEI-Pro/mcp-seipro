"""Mixin REST — credenciamento em processos sigilosos.

Thin delegation layer: these methods validate inputs, resolve the process
identifier and delegate directly to the REST client.  Response schema validation
is the REST client's responsibility.
"""

from __future__ import annotations

from todos.backends.rest._session import _RestMixin
from todos.exceptions import SEIValidationError


def _exigir_processo(processo: str) -> None:
    """Valida que o protocolo de processo não está vazio."""
    if not processo or not processo.strip():
        msg = "O parâmetro 'processo' é obrigatório e não pode ser vazio."
        raise SEIValidationError(msg)


def _exigir_id_usuario(id_usuario: str) -> None:
    """Valida que o id_usuario não está vazio."""
    if not id_usuario or not id_usuario.strip():
        msg = "O parâmetro 'id_usuario' é obrigatório e não pode ser vazio."
        raise SEIValidationError(msg)


class CredenciamentoRest(_RestMixin):
    """Operações REST de credenciamento.

    Each method validates required inputs, resolves the process identifier via
    ``_resolver_processo`` and then delegates directly to ``self._rest``.
    """

    async def listar_credenciamentos(self, processo: str) -> list[dict]:
        """Lista os credenciamentos de um processo sigiloso."""
        _exigir_processo(processo)
        id_proc = await self._resolver_processo(processo)
        return await self._rest.listar_credenciamentos(id_proc)

    async def conceder_credenciamento(self, processo: str, id_usuario: str) -> dict:
        """Concede credenciamento de acesso a um processo sigiloso."""
        _exigir_processo(processo)
        _exigir_id_usuario(id_usuario)
        id_proc = await self._resolver_processo(processo)
        return await self._rest.conceder_credenciamento(id_proc, id_usuario)

    async def renunciar_credenciamento(self, processo: str) -> dict:
        """Renuncia ao próprio credenciamento em um processo sigiloso."""
        _exigir_processo(processo)
        id_proc = await self._resolver_processo(processo)
        return await self._rest.renunciar_credenciamento(id_proc)

    async def cassar_credenciamento(self, processo: str, id_usuario: str) -> dict:
        """Cassa o credenciamento de um usuário em um processo sigiloso."""
        _exigir_processo(processo)
        _exigir_id_usuario(id_usuario)
        id_proc = await self._resolver_processo(processo)
        return await self._rest.cassar_credenciamento(id_proc, id_usuario)
