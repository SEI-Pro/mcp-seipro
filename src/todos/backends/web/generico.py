"""Mixin web: inspeção e submissão genérica de formulário (RFC 0020)."""

from __future__ import annotations

from todos.backends.web._session import _WebMixin


class GenericoWeb(_WebMixin):
    """Inspeção e submissão de formulário arbitrário — sem equivalente REST."""

    async def inspecionar_pagina(self, url: str, *, incluir_raw: bool = False) -> dict:
        """Busca uma URL e devolve forms + ações descobertas na página."""
        return await self._web.inspecionar_pagina_web(url, incluir_raw=incluir_raw)

    async def submeter_form(
        self,
        url_pagina: str,
        form_id: str,
        overrides: dict[str, str],
        url_destino: str | None = None,
        *,
        incluir_raw: bool = False,
    ) -> dict:
        """Submete um form arbitrário, com overrides de campo e URL de destino opcional."""
        return await self._web.submeter_form_web(
            url_pagina, form_id, overrides, url_destino, incluir_raw=incluir_raw
        )
