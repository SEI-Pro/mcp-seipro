"""Regression tests for `SEIWebClient.fetch_inbox` pagination.

Bug: `fetch_inbox(pagina=N>0)` set a hidden field named `hdnInfraPaginaAtual`,
which does not exist in the `procedimento_controlar.php` (Controle de
Processos) form — that name comes from a different SEI screen
(`procedimento_consultar_historico.php`). The real field is
`hdn{Layout}PaginaAtual` (e.g. `hdnDetalhadoPaginaAtual` in the Detalhada
view). Posting an unknown field name is a silent no-op: the server ignores it
and returns page 0 again, byte-identical to the previous call, while
`sei_listar_processos` still reports an incremented `pagina_atual` and a fresh
`proximo_cursor` — i.e. paginação parece avançar mas nunca avança de fato.

Confirmed live against a real SEI instance (TJRO/PGE-IPERON) before/after the
fix: `hdnDetalhadoPaginaAtual` was the only hidden field in the form whose
name ends in `PaginaAtual`; the buggy code's `hdnInfraPaginaAtual` was simply
extra, ignored POST data.

No pytest-asyncio plugin is installed in this repo (see test_keyring_reread.py
for the established pattern) — each test wraps its async body in
``asyncio.run()``.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock

import httpx

from todos.backends.models import SEIWebClientConfig
from todos.sei_web_client import SEIWebClient, parse_inbox

_INBOX_URL = "http://sei.test/sei/controlador.php?acao=procedimento_controlar"
_FORM_ACTION = "controlador.php?acao=procedimento_controlar&infra_hash=abc123"
_POST_URL = "http://sei.test/sei/controlador.php?acao=procedimento_controlar&infra_hash=abc123"


def make_client() -> SEIWebClient:
    return SEIWebClient(
        SEIWebClientConfig(sei_web_url="http://sei.test", sei_usuario="u", sei_senha="p")
    )


def _client_with_form(*, extra_hidden: dict[str, str] | None = None) -> SEIWebClient:
    """A SEIWebClient with an already-authenticated session and cached form.

    Mirrors the state fetch_inbox expects on any call after the first: an
    `_inbox_url` (so `ensure_authenticated` is a no-op) and a cached
    `_form_action`/`_form_hidden` (so no seed GET is needed).
    """
    c = make_client()
    c._inbox_url = httpx.URL(_INBOX_URL)
    c._form_action = _FORM_ACTION
    c._form_hidden = {
        "hdnDetalhadoPaginaAtual": "0",
        "hdnDetalhadoNroItens": "500",
        "hdnTipoVisualizacao": "D",
        "hdnMeusProcessos": "T",
        **(extra_hidden or {}),
    }
    return c


def _inbox_html(*, pagina_atual: int, ids: list[str]) -> str:
    """Build a minimal tblProcessosDetalhado page for the given ids."""
    rows = "\n".join(
        f'<tr id="P{pid}"><td></td><td></td>'
        f'<td><a href="controlador.php?acao=procedimento_trabalhar&id={pid}">'
        f"{pid}.000000/2024-00</a></td><td></td></tr>"
        for pid in ids
    )
    return f"""
    <html><body>
    <form action="{_FORM_ACTION}">
        <input type="hidden" name="hdnDetalhadoPaginaAtual" value="{pagina_atual}">
        <input type="hidden" name="hdnDetalhadoNroItens" value="500">
        <input type="hidden" name="hdnTipoVisualizacao" value="D">
        <input type="hidden" name="hdnMeusProcessos" value="T">
        <table id="tblProcessosDetalhado">
            <tr><th></th><th></th><th></th><th></th></tr>
            {rows}
        </table>
    </form>
    </body></html>
    """


def _resp(url: str, text: str) -> httpx.Response:
    return httpx.Response(200, text=text, request=httpx.Request("GET", url))


def _fake_sei_server(pages: dict[str, str]) -> Any:
    """Simulate the real SEI server: honours ONLY hdn{Layout}PaginaAtual.

    Any other posted field (e.g. the buggy `hdnInfraPaginaAtual`) is simply
    ignored, exactly like a real, unrecognized POST field would be — so a
    regression back to the wrong field name makes every call resolve to the
    "0" page below, and the pagination test fails.
    """

    async def fake_post(
        url: str, *, data: dict[str, str] | None = None, **_kw: Any
    ) -> httpx.Response:
        assert url == _POST_URL
        posted = data or {}
        pagina = posted.get("hdnDetalhadoPaginaAtual", "0")
        return _resp(_POST_URL, pages.get(pagina, pages["0"]))

    return fake_post


class TestFetchInboxPaginationAdvances:
    def test_pagina_1_posts_the_real_hidden_field_name(self) -> None:
        """The POST for pagina=1 must set hdnDetalhadoPaginaAtual, not a made-up name."""
        client = _client_with_form()
        captured: dict[str, str] = {}

        async def fake_post(
            url: str, *, data: dict[str, str] | None = None, **_kw: Any
        ) -> httpx.Response:
            captured.update(data or {})
            return _resp(url, _inbox_html(pagina_atual=1, ids=["200", "201"]))

        client._http.post = AsyncMock(side_effect=fake_post)  # type: ignore[method-assign]

        asyncio.run(client.fetch_inbox(detalhada=True, pagina=1))

        assert captured.get("hdnDetalhadoPaginaAtual") == "1"
        # Regression guard: the old (wrong) field name must never be sent —
        # a real SEI form has no such hidden input, so posting it is a no-op.
        assert "hdnInfraPaginaAtual" not in captured

    def test_second_page_returns_different_processos_than_first(self) -> None:
        """End-to-end: pagina=0 then pagina=1 must yield disjoint process sets.

        This is the exact scenario from the bug report: two consecutive
        sei_listar_processos calls (page 0, then page 1 via the returned
        cursor) came back byte-identical. A fixed client must advance.
        """
        client = _client_with_form()
        pages = {
            "0": _inbox_html(pagina_atual=0, ids=["100", "101"]),
            "1": _inbox_html(pagina_atual=1, ids=["200", "201"]),
        }
        client._http.post = AsyncMock(side_effect=_fake_sei_server(pages))  # type: ignore[method-assign]

        _, html0 = asyncio.run(client.fetch_inbox(detalhada=True, pagina=0))
        layout0, rows0 = parse_inbox(html0)
        assert client._form_hidden["hdnDetalhadoPaginaAtual"] == "0"

        _, html1 = asyncio.run(client.fetch_inbox(detalhada=True, pagina=1))
        layout1, rows1 = parse_inbox(html1)

        assert layout0 == layout1 == "detalhada"
        ids0 = [r["id_procedimento"] for r in rows0]
        ids1 = [r["id_procedimento"] for r in rows1]
        assert ids0 == ["100", "101"]
        assert ids1 == ["200", "201"]
        assert ids0 != ids1
        assert client._form_hidden["hdnDetalhadoPaginaAtual"] == "1"

    def test_unknown_field_name_would_have_looked_stuck(self) -> None:
        """Sanity check on the fake server itself: proves it reproduces the bug.

        If fetch_inbox posted the wrong field name (as the old code did),
        the fake server would not recognise the page request and would keep
        answering with page 0 — this test drives that path directly (by
        posting the wrong field name ourselves) to confirm the harness
        actually detects a stuck pagination, not just happens to pass.
        """
        pages = {
            "0": _inbox_html(pagina_atual=0, ids=["100", "101"]),
            "1": _inbox_html(pagina_atual=1, ids=["200", "201"]),
        }
        fake_post = _fake_sei_server(pages)

        async def _drive() -> tuple[list[str], list[str]]:
            r0 = await fake_post(_POST_URL, data={"hdnDetalhadoPaginaAtual": "0"})
            _, rows0 = parse_inbox(r0.text)
            # Simulates the OLD buggy behaviour: posting the wrong field name.
            r1 = await fake_post(_POST_URL, data={"hdnInfraPaginaAtual": "1"})
            _, rows1 = parse_inbox(r1.text)
            return (
                [r["id_procedimento"] for r in rows0],
                [r["id_procedimento"] for r in rows1],
            )

        ids0, ids1 = asyncio.run(_drive())
        assert ids0 == ids1  # confirms the fake server reproduces the old bug
