"""Mixin web: inspeção/submissão genérica de formulário (RFC 0020) e captura de tela (RFC 0021)."""

from __future__ import annotations

from todos.backends.web._session import _WebMixin


class GenericoWeb(_WebMixin):
    """Inspeção/submissão de formulário e captura de tela — sem equivalente REST."""

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

    async def capturar_tela(
        self,
        url: str,
        *,
        selector: str | None = None,
        aguardar_segundos: float = 1.0,
    ) -> dict:
        """Captura um screenshot PNG real (browser Playwright) de uma URL do SEI (RFC 0021).

        Exceção deliberada à arquitetura pure-HTTP deste backend — ver
        `todos.browser_capture` para a justificativa e o escopo dessa exceção.
        """
        caminho = await self._web.capturar_tela_web(
            url, selector=selector, aguardar_segundos=aguardar_segundos
        )
        return {
            "arquivo": str(caminho),
            "tamanho_bytes": caminho.stat().st_size,
            "selector": selector,
        }
