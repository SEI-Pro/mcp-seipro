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
        assert 0 < remaining <= cc._DEFAULT_CATALOG_CACHE_TTL

    def test_ttl_missing_entry_is_none(self, tmp_path: Path) -> None:
        cache = _cache(tmp_path)
        assert asyncio.run(cache.ttl(_NS, "nope")) is None


# ---------------------------------------------------------------------------
# expiry + sweep
# ---------------------------------------------------------------------------


class TestExpiry:
    def test_expired_entry_returns_none_and_is_deleted(self, tmp_path: Path, monkeypatch) -> None:
        cache = _cache(tmp_path)
        monkeypatch.setattr(cc, "_DEFAULT_CATALOG_CACHE_TTL", -1)  # write already-expired
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
        monkeypatch.setattr(cc._rng, "random", lambda: 0.0)
        asyncio.run(cache.set(_NS, "fresh", {"v": 1}))
        with sqlite3.connect(cache.db_path) as conn:
            stale = conn.execute("SELECT 1 FROM catalogs WHERE key = ?", (stale_key,)).fetchone()
        assert stale is None


# ---------------------------------------------------------------------------
# stats + clear (RFC 0019 §2.2 — sei_cache_status/sei_cache_clear)
# ---------------------------------------------------------------------------


class TestCreatedAtMigration:
    def test_pre_existing_db_without_created_at_column_gets_migrated(self, tmp_path: Path) -> None:
        """Simula um banco criado antes de `created_at` existir — reabrir não deve quebrar."""
        directory = tmp_path / "cache"
        directory.mkdir()
        db_path = directory / "catalogs.db"
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                "CREATE TABLE catalogs (key TEXT PRIMARY KEY, value TEXT, expires_at REAL)"
            )
            conn.execute(
                "INSERT INTO catalogs (key, value, expires_at) VALUES (?, ?, ?)",
                ("legacy-key", "{}", time.time() + 1000),
            )

        cache = CatalogCache(directory)  # reabre — deve rodar a migração ALTER TABLE

        with sqlite3.connect(db_path) as conn:
            cols = {row[1] for row in conn.execute("PRAGMA table_info(catalogs)")}
        assert "created_at" in cols
        stats = asyncio.run(cache.stats())
        assert stats["total"] == 1
        assert stats["fresh"] == 1
        assert stats["bytes"] > 0
        # A entrada pré-existente não tem created_at — clear(older_than_seconds=...)
        # deve tratá-la como "idade desconhecida" e removê-la.
        assert asyncio.run(cache.clear(older_than_seconds=1)) == 1

    def test_reopening_migrated_db_is_a_no_op(self, tmp_path: Path) -> None:
        directory = tmp_path / "cache"
        CatalogCache(directory)
        CatalogCache(directory)  # segunda abertura não deve falhar (coluna já existe)


class TestStats:
    def test_empty_cache_reports_zeroes(self, tmp_path: Path) -> None:
        cache = _cache(tmp_path)
        stats = asyncio.run(cache.stats())
        assert stats == {"total": 0, "fresh": 0, "bytes": stats["bytes"]}
        assert stats["bytes"] >= 0

    def test_counts_total_and_fresh_separately(self, tmp_path: Path) -> None:
        cache = _cache(tmp_path)
        asyncio.run(cache.set(_NS, "fresh-1", {"v": 1}))
        asyncio.run(cache.set(_NS, "fresh-2", {"v": 2}))
        stale_key = CatalogCache.make_key(_NS, "stale")
        with sqlite3.connect(cache.db_path) as conn:
            conn.execute(
                "INSERT INTO catalogs (key, value, expires_at, created_at) VALUES (?, ?, ?, ?)",
                (stale_key, "{}", time.time() - 100, time.time() - 200),
            )
        stats = asyncio.run(cache.stats())
        assert stats["total"] == 3
        assert stats["fresh"] == 2

    def test_bytes_reflects_disk_usage_after_write(self, tmp_path: Path) -> None:
        cache = _cache(tmp_path)
        before = asyncio.run(cache.stats())["bytes"]
        asyncio.run(cache.set(_NS, "k", {"v": "x" * 1000}))
        after = asyncio.run(cache.stats())["bytes"]
        assert after > before


class TestClear:
    def test_clear_all_removes_every_row(self, tmp_path: Path) -> None:
        cache = _cache(tmp_path)
        asyncio.run(cache.set(_NS, "a", {"v": 1}))
        asyncio.run(cache.set(_NS, "b", {"v": 2}))
        removed = asyncio.run(cache.clear())
        assert removed == 2
        assert asyncio.run(cache.stats())["total"] == 0

    def test_clear_older_than_seconds_keeps_recent_entries(self, tmp_path: Path) -> None:
        cache = _cache(tmp_path)
        old_key = CatalogCache.make_key(_NS, "old")
        with sqlite3.connect(cache.db_path) as conn:
            conn.execute(
                "INSERT INTO catalogs (key, value, expires_at, created_at) VALUES (?, ?, ?, ?)",
                (old_key, "{}", time.time() + 1000, time.time() - 1000),
            )
        asyncio.run(cache.set(_NS, "recent", {"v": 1}))

        removed = asyncio.run(cache.clear(older_than_seconds=500))

        assert removed == 1
        remaining = asyncio.run(cache.stats())["total"]
        assert remaining == 1

    def test_clear_older_than_seconds_treats_null_created_at_as_old(self, tmp_path: Path) -> None:
        """Linhas gravadas antes da migração de `created_at` (NULL) contam como antigas."""
        cache = _cache(tmp_path)
        legacy_key = CatalogCache.make_key(_NS, "legacy")
        with sqlite3.connect(cache.db_path) as conn:
            conn.execute(
                "INSERT INTO catalogs (key, value, expires_at, created_at) VALUES (?, ?, ?, NULL)",
                (legacy_key, "{}", time.time() + 1000),
            )
        removed = asyncio.run(cache.clear(older_than_seconds=1))
        assert removed == 1


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
