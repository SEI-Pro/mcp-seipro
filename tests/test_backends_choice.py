"""Tests for `todos.backends.choice` — explicit per-call backend selection.

No automatic REST/web fallback exists anymore (see `feat/explicit-backend-choice`):
`requires_backend` exposes a `backend: Literal["rest", "web"]` parameter on the
MCP schema of a decorated tool, and `get_backend_choice` reads it back from the
call's `Context` (via `ctx.set_state`/`ctx.get_state`, FastMCP's own
request-scoped state — not a bespoke global). Nothing tries "the other side"
if the chosen backend fails.

Sem `from __future__ import annotations`: ``requires_backend`` resolve a
anotação de ``ctx`` em tempo de execução (``find_kwarg_by_type``), então
``Context`` precisa ser um objeto real no namespace do módulo, não uma string
adiada — mesma razão pela qual os módulos de tools reais evitam essa importação.
"""

import asyncio
import inspect
from typing import cast

import pytest
from fastmcp import Context

from todos.backends.choice import get_backend_choice, requires_backend
from todos.exceptions import SEIError


class _FakeContext:
    """Duck-types the slice of `fastmcp.Context` that `choice.py` touches.

    `find_kwarg_by_type` matches on the *declared* annotation of the wrapped
    function's parameter (`ctx: Context`), not on the runtime type of the
    value passed in — so a lightweight fake with the same request-scoped
    `set_state`/`get_state` semantics is enough here, without needing a full
    FastMCP session/lifespan.
    """

    def __init__(self) -> None:
        self._state: dict[str, object] = {}

    async def set_state(self, key: str, value: object, *, serializable: bool = True) -> None:
        del serializable
        self._state[key] = value

    async def get_state(self, key: str) -> object:
        return self._state.get(key)


async def _sample(id_documento: str, ctx: Context | None = None) -> str:
    """Stand-in tool body — returns the choice it observes via get_backend_choice."""
    return await get_backend_choice(ctx) + ":" + id_documento


class TestRequiresBackendSchema:
    def test_adds_a_required_backend_parameter(self) -> None:
        decorated = requires_backend(_sample)
        sig = inspect.signature(decorated)
        assert "backend" in sig.parameters
        param = sig.parameters["backend"]
        assert param.kind is inspect.Parameter.KEYWORD_ONLY
        assert param.default is inspect.Parameter.empty

    def test_original_parameters_are_preserved(self) -> None:
        decorated = requires_backend(_sample)
        sig = inspect.signature(decorated)
        assert list(sig.parameters)[:2] == ["id_documento", "ctx"]

    def test_raises_typeerror_when_function_has_no_context_param(self) -> None:
        async def sem_ctx(x: str) -> str:
            return x

        with pytest.raises(TypeError, match="Context"):
            requires_backend(sem_ctx)


class TestRequiresBackendRuntimeBehavior:
    def test_stashes_choice_on_ctx_and_delegates_to_wrapped_fn(self) -> None:
        decorated = requires_backend(_sample)
        ctx = _FakeContext()
        result = asyncio.run(decorated("DOC1", ctx=ctx, backend="web"))
        assert result == "web:DOC1"

    def test_different_calls_do_not_leak_into_each_other(self) -> None:
        """Each call gets its own ctx (request-scoped) — no shared state to leak."""
        decorated = requires_backend(_sample)
        ctx_a = _FakeContext()
        ctx_b = _FakeContext()
        result_a = asyncio.run(decorated("A", ctx=ctx_a, backend="rest"))
        result_b = asyncio.run(decorated("B", ctx=ctx_b, backend="web"))
        assert result_a == "rest:A"
        assert result_b == "web:B"

    def test_raises_sei_error_when_ctx_is_none(self) -> None:
        decorated = requires_backend(_sample)
        with pytest.raises(SEIError, match="contexto MCP"):
            asyncio.run(decorated("DOC1", ctx=None, backend="web"))


class TestGetBackendChoice:
    def test_raises_sei_error_when_ctx_is_none(self) -> None:
        with pytest.raises(SEIError, match="contexto MCP"):
            asyncio.run(get_backend_choice(None))

    def test_raises_sei_error_when_no_choice_was_ever_set(self) -> None:
        """Tools that forgot @requires_backend get a clear error, not a silent pick."""
        ctx = cast("Context", _FakeContext())
        with pytest.raises(SEIError, match="@requires_backend"):
            asyncio.run(get_backend_choice(ctx))

    def test_returns_the_stashed_choice(self) -> None:
        ctx = cast("Context", _FakeContext())
        asyncio.run(ctx.set_state("todos.backend_choice", "rest", serializable=False))
        assert asyncio.run(get_backend_choice(ctx)) == "rest"
