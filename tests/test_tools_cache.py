"""Testes para `sei_cache_status`/`sei_cache_clear` (RFC 0019 §2.2).

As tools são finas: delegam para `CatalogCache.stats()`/`clear()`, já cobertos
a fundo em `test_catalog_cache.py`. Aqui só confirmamos que a tool resolve o
cache compartilhado (`get_catalog_cache()`) e serializa o resultado como JSON.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from todos.catalog_cache import CatalogCache
from todos.tools import configuracao


@pytest.fixture
def cache(tmp_path, monkeypatch: pytest.MonkeyPatch) -> CatalogCache:
    instance = CatalogCache(tmp_path / "cache")
    configuracao.get_catalog_cache.cache_clear()
    monkeypatch.setenv("TODOS_CACHE_DIR", str(tmp_path / "cache"))
    try:
        yield instance
    finally:
        configuracao.get_catalog_cache.cache_clear()


def test_sei_cache_status_reports_shared_cache_stats(cache: CatalogCache) -> None:
    asyncio.run(cache.set({"ns": "x"}, "k", {"v": 1}))

    result = json.loads(asyncio.run(configuracao.sei_cache_status()))

    assert result["total"] == 1
    assert result["fresh"] == 1
    assert result["bytes"] > 0


def test_sei_cache_clear_all_when_no_arg(cache: CatalogCache) -> None:
    asyncio.run(cache.set({"ns": "x"}, "a", {"v": 1}))
    asyncio.run(cache.set({"ns": "x"}, "b", {"v": 2}))

    result = json.loads(asyncio.run(configuracao.sei_cache_clear()))

    assert result["removidas"] == 2
    assert asyncio.run(cache.stats())["total"] == 0


def test_sei_cache_clear_older_than_seconds_is_forwarded(cache: CatalogCache) -> None:
    asyncio.run(cache.set({"ns": "x"}, "recent", {"v": 1}))

    result = json.loads(asyncio.run(configuracao.sei_cache_clear(older_than_seconds=10_000)))

    # entrada recém-gravada tem idade ~0s, bem menor que 10_000 — não é removida
    assert result["removidas"] == 0
    assert asyncio.run(cache.stats())["total"] == 1
