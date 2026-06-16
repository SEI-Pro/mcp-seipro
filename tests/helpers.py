"""Shared test helpers."""

from __future__ import annotations


def aconst(v: object):
    """Return an async callable that always resolves to v, for monkeypatching async functions."""

    async def _f(_ctx: object) -> object:
        return v

    return _f
