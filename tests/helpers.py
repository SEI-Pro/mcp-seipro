"""Shared test helpers."""

from __future__ import annotations


def aconst(v: object):
    """Return an async callable that always resolves to v, for monkeypatching async functions."""

    async def _f(_ctx: object) -> object:
        return v

    return _f


class FakeCtx:
    """Minimal fake of `fastmcp.Context` for tools decorated with `@requires_backend`.

    The decorator's wrapper calls `await ctx.set_state(...)` to stash the
    caller's backend choice, and some tools read it back mid-body via
    `get_backend_choice(ctx)` (e.g. `sei_criar_documento`'s id_serie check) —
    so this fake genuinely stores state (request-scoped dict), not a no-op.
    """

    def __init__(self) -> None:
        self._state: dict[str, object] = {}

    async def set_state(self, key: str, value: object, *, serializable: bool = True) -> None:
        del serializable
        self._state[key] = value

    async def get_state(self, key: str) -> object:
        return self._state.get(key)
