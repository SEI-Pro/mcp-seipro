"""Cache persistente para catálogos estáveis do SEI."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import secrets
import sqlite3
import time
from functools import lru_cache
from pathlib import Path

from todos.settings import get_settings

logger = logging.getLogger(__name__)

_DEFAULT_CATALOG_CACHE_TTL: int = 24 * 60 * 60  # 24 hours
_SWEEP_PROBABILITY = 0.05  # probabilistic expired-row sweep: run on ~5% of writes
# secrets.SystemRandom used here to satisfy ruff S311; this is NOT a security use —
# just a probabilistic sweep trigger that avoids importing the bare random module.
_rng = secrets.SystemRandom()


class CatalogCache:
    """Armazena respostas JSON em disco com TTL usando SQLite (sem dependências externas)."""

    def __init__(self, directory: Path) -> None:
        """Inicialize o armazenamento no diretório informado."""
        self.directory = directory
        self.directory.mkdir(parents=True, exist_ok=True)
        self.db_path = self.directory / "catalogs.db"
        self._init_db()

    def _init_db(self) -> None:
        """Inicializa a tabela SQLite se ela não existir (e migra `created_at` se faltar)."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS catalogs (
                    key TEXT PRIMARY KEY,
                    value TEXT,
                    expires_at REAL,
                    created_at REAL
                )
                """
            )
            # Migração para bancos criados antes de `created_at` existir (usado só
            # por `clear(older_than_seconds=...)`) — linhas antigas ficam com
            # created_at NULL, tratado como "idade desconhecida" em `_clear_sync`.
            existing_cols = {row[1] for row in conn.execute("PRAGMA table_info(catalogs)")}
            if "created_at" not in existing_cols:
                conn.execute("ALTER TABLE catalogs ADD COLUMN created_at REAL")

    @staticmethod
    def make_key(namespace: dict[str, str], key: str) -> str:
        """Gera chave estável sem expor usuário ou URLs no banco."""
        payload = json.dumps(
            {"namespace": namespace, "key": key},
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode()).hexdigest()

    async def get(self, namespace: dict[str, str], key: str) -> object | None:
        """Retorna um valor válido ou None em miss/falha do cache (executado em thread worker)."""
        try:
            return await asyncio.to_thread(self._get_sync, namespace, key)
        except (sqlite3.Error, json.JSONDecodeError):
            logger.warning("Falha ao ler cache de catalogos", exc_info=True)
        return None

    def _get_sync(self, namespace: dict[str, str], key: str) -> object | None:
        db_key = self.make_key(namespace, key)
        now = time.time()
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "SELECT value, expires_at FROM catalogs WHERE key = ?",
                (db_key,),
            )
            row = cursor.fetchone()
            if row:
                val_str, expires_at = row
                if expires_at > now:
                    return json.loads(val_str)
                # Limpa entrada expirada
                logger.debug("cache entry expired: %s/%s", namespace, key)
                conn.execute("DELETE FROM catalogs WHERE key = ?", (db_key,))
        return None

    async def set(self, namespace: dict[str, str], key: str, value: object) -> None:
        """Persista uma resposta bem-sucedida pelo TTL padrão (executado em thread worker)."""
        try:
            await asyncio.to_thread(self._set_sync, namespace, key, value)
        except (sqlite3.Error, json.JSONDecodeError):
            logger.warning("Falha ao gravar cache de catalogos", exc_info=True)

    def _set_sync(self, namespace: dict[str, str], key: str, value: object) -> None:
        db_key = self.make_key(namespace, key)
        val_str = json.dumps(value, ensure_ascii=False)
        now = time.time()
        expires_at = now + (get_settings().sei_cache_ttl_seconds or _DEFAULT_CATALOG_CACHE_TTL)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO catalogs (key, value, expires_at, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (db_key, val_str, expires_at, now),
            )
            # Probabilistic sweep (5%) — full cleanup available via cleanup()
            if _rng.random() < _SWEEP_PROBABILITY:
                conn.execute("DELETE FROM catalogs WHERE expires_at < ?", (now,))

    async def delete(self, namespace: dict[str, str], key: str) -> None:
        """Remove uma entrada do cache imediatamente (executado em thread worker)."""
        try:
            await asyncio.to_thread(self._delete_sync, namespace, key)
        except sqlite3.Error:
            logger.warning("Falha ao remover entrada do cache de catalogos", exc_info=True)

    def _delete_sync(self, namespace: dict[str, str], key: str) -> None:
        db_key = self.make_key(namespace, key)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM catalogs WHERE key = ?", (db_key,))

    async def ttl(self, namespace: dict[str, str], key: str) -> float | None:
        """Retorne o TTL restante de uma entrada (executado em thread worker)."""
        try:
            return await asyncio.to_thread(self._ttl_sync, namespace, key)
        except sqlite3.Error:
            logger.warning("Falha ao consultar TTL do cache de catalogos", exc_info=True)
        return None

    def _ttl_sync(self, namespace: dict[str, str], key: str) -> float | None:
        db_key = self.make_key(namespace, key)
        now = time.time()
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "SELECT expires_at FROM catalogs WHERE key = ?",
                (db_key,),
            )
            row = cursor.fetchone()
            if row:
                expires_at = row[0]
                return max(0.0, expires_at - now)
        return None

    async def cleanup(self) -> int:
        """Remove expired entries. Returns count of deleted rows."""
        return await asyncio.to_thread(self._cleanup_sync)

    def _cleanup_sync(self) -> int:
        now = time.time()
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("DELETE FROM catalogs WHERE expires_at < ?", (now,))
            return cursor.rowcount

    async def stats(self) -> dict[str, int]:
        """Estatísticas agregadas: total de entradas, entradas ainda válidas (fresh) e bytes em disco."""
        return await asyncio.to_thread(self._stats_sync)

    def _stats_sync(self) -> dict[str, int]:
        now = time.time()
        with sqlite3.connect(self.db_path) as conn:
            total = conn.execute("SELECT COUNT(*) FROM catalogs").fetchone()[0]
            fresh = conn.execute(
                "SELECT COUNT(*) FROM catalogs WHERE expires_at > ?", (now,)
            ).fetchone()[0]
        # Soma o arquivo principal + WAL/SHM (modo WAL pode manter dados ainda não
        # "checkpointed" de volta ao arquivo principal) — sem isso, `bytes` subestima
        # o uso real de disco logo após escritas recentes.
        bytes_used = sum(
            path.stat().st_size
            for suffix in ("", "-wal", "-shm")
            if (path := self.db_path.with_name(self.db_path.name + suffix)).exists()
        )
        return {"total": total, "fresh": fresh, "bytes": bytes_used}

    async def clear(self, *, older_than_seconds: float | None = None) -> int:
        """Remove entradas do cache. Retorna a quantidade removida.

        Sem `older_than_seconds`: limpa tudo. Com `older_than_seconds`: remove só
        entradas gravadas há mais tempo que isso — linhas de bancos anteriores à
        migração de `created_at` (NULL) contam como "idade desconhecida" e são
        tratadas como antigas (elegíveis pra remoção), já que não há como saber
        quando foram escritas.
        """
        return await asyncio.to_thread(self._clear_sync, older_than_seconds)

    def _clear_sync(self, older_than_seconds: float | None) -> int:
        with sqlite3.connect(self.db_path) as conn:
            if older_than_seconds is None:
                cursor = conn.execute("DELETE FROM catalogs")
            else:
                cutoff = time.time() - older_than_seconds
                cursor = conn.execute(
                    "DELETE FROM catalogs WHERE created_at IS NULL OR created_at < ?", (cutoff,)
                )
            return cursor.rowcount

    async def close(self) -> None:
        """Feche o armazenamento em disco.

        # SQLite connections are opened/closed per-call — nothing persistent to close here
        """


@lru_cache(maxsize=1)
def get_catalog_cache() -> CatalogCache:
    """Retorna o cache compartilhado pelo processo."""
    configured = get_settings().todos_cache_dir
    directory = Path(configured).expanduser() if configured else Path.home() / ".cache" / "todos"
    return CatalogCache(directory)
