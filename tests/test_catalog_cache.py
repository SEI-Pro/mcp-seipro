"""Tests for catalog_cache — the on-disk SQLite TTL cache for SEI catalogs.

Exercises key derivation, get/set round-trip, TTL reporting, expiry (including
the lazy delete on read), and the probabilistic expired-row sweep. Uses tmp_path
for isolation and asyncio.run to drive the async API (matching test_backends).
"""

from __future__ import annotations

import asyncio
import sqlite3
import time
from typing import TYPE_CHECKING

from todos import catalog_cache as cc
from todos.catalog_cache import CatalogCache

if TYPE_CHECKING:
    from pathlib import Path

_NS = {"url": "https://sei.example.gov.br", "usuario": "fulano"}


def _cache(tmp_path: Path) -> CatalogCache:
    return CatalogCache(tmp_path / "cache")


# ---------------------------------------------------------------------------
# make_key
# ---------------------------------------------------------------------------


class TestMakeKey:
    def test_deterministic(self) -> None:
        assert CatalogCache.make_key(_NS, "tipos") == CatalogCache.make_key(_NS, "tipos")

    def test_namespace_order_independent(self) -> None:
        a = CatalogCache.make_key({"a": "1", "b": "2"}, "k")
        b = CatalogCache.make_key({"b": "2", "a": "1"}, "k")
        assert a == b

    def test_distinct_inputs_distinct_keys(self) -> None:
        assert CatalogCache.make_key(_NS, "tipos") != CatalogCache.make_key(_NS, "unidades")
        assert CatalogCache.make_key(_NS, "k") != CatalogCache.make_key({"url": "other"}, "k")

    def test_is_sha256_hex(self) -> None:
        key = CatalogCache.make_key(_NS, "tipos")
        assert len(key) == 64
        assert all(c in "0123456789abcdef" for c in key)


# ---------------------------------------------------------------------------
# get / set round-trip
# ---------------------------------------------------------------------------


class TestRoundTrip:
    def test_set_then_get(self, tmp_path: Path) -> None:
        cache = _cache(tmp_path)
        value = [{"id": "1", "nome": "Despacho"}, {"id": "2", "nome": "Ofício"}]
        asyncio.run(cache.set(_NS, "tipos", value))
        assert asyncio.run(cache.get(_NS, "tipos")) == value

    def test_get_miss_returns_none(self, tmp_path: Path) -> None:
        cache = _cache(tmp_path)
        assert asyncio.run(cache.get(_NS, "inexistente")) is None

    def test_set_overwrites(self, tmp_path: Path) -> None:
        cache = _cache(tmp_path)
        asyncio.run(cache.set(_NS, "k", {"v": 1}))
        asyncio.run(cache.set(_NS, "k", {"v": 2}))
        assert asyncio.run(cache.get(_NS, "k")) == {"v": 2}

    def test_preserves_non_ascii(self, tmp_path: Path) -> None:
        cache = _cache(tmp_path)
        asyncio.run(cache.set(_NS, "k", {"nome": "Coordenação de Ação"}))
        assert asyncio.run(cache.get(_NS, "k")) == {"nome": "Coordenação de Ação"}


# ---------------------------------------------------------------------------
# ttl
# ---------------------------------------------------------------------------


class TestTtl:
    def test_ttl_reports_remaining_time(self, tmp_path: Path) -> None:
        cache = _cache(tmp_path)
        asyncio.run(cache.set(_NS, "k", {"v": 1}))
        remaining = asyncio.run(cache.ttl(_NS, "k"))
        assert remaining is not None
        # Just-written entry: TTL is close to the full window, never above it.
        assert 0 < remaining <= cc.CATALOG_CACHE_TTL

    def test_ttl_missing_entry_is_none(self, tmp_path: Path) -> None:
        cache = _cache(tmp_path)
        assert asyncio.run(cache.ttl(_NS, "nope")) is None


# ---------------------------------------------------------------------------
# expiry + sweep
# ---------------------------------------------------------------------------


class TestExpiry:
    def test_expired_entry_returns_none_and_is_deleted(self, tmp_path: Path, monkeypatch) -> None:
        cache = _cache(tmp_path)
        monkeypatch.setattr(cc, "CATALOG_CACHE_TTL", -1)  # write already-expired
        asyncio.run(cache.set(_NS, "k", {"v": 1}))
        assert asyncio.run(cache.get(_NS, "k")) is None
        # The lazy delete on read must have removed the row.
        db_key = CatalogCache.make_key(_NS, "k")
        with sqlite3.connect(cache.db_path) as conn:
            row = conn.execute("SELECT 1 FROM catalogs WHERE key = ?", (db_key,)).fetchone()
        assert row is None

    def test_sweep_purges_expired_rows_on_write(self, tmp_path: Path, monkeypatch) -> None:
        cache = _cache(tmp_path)
        # Insert one already-expired row directly.
        stale_key = CatalogCache.make_key(_NS, "stale")
        with sqlite3.connect(cache.db_path) as conn:
            conn.execute(
                "INSERT INTO catalogs (key, value, expires_at) VALUES (?, ?, ?)",
                (stale_key, "{}", time.time() - 100),
            )
        # Force the probabilistic sweep to run on the next write.
        monkeypatch.setattr(cc.random, "random", lambda: 0.0)
        asyncio.run(cache.set(_NS, "fresh", {"v": 1}))
        with sqlite3.connect(cache.db_path) as conn:
            stale = conn.execute("SELECT 1 FROM catalogs WHERE key = ?", (stale_key,)).fetchone()
        assert stale is None


# ---------------------------------------------------------------------------
# get_catalog_cache (process-wide singleton)
# ---------------------------------------------------------------------------


class TestGetCatalogCache:
    def test_honors_env_dir_and_is_cached(self, tmp_path: Path, monkeypatch) -> None:
        target = tmp_path / "custom-cache"
        monkeypatch.setenv("TODOS_CACHE_DIR", str(target))
        cc.get_catalog_cache.cache_clear()
        try:
            cache = cc.get_catalog_cache()
            assert cache.directory == target
            # lru_cache(maxsize=1): same instance on a second call.
            assert cc.get_catalog_cache() is cache
        finally:
            cc.get_catalog_cache.cache_clear()
