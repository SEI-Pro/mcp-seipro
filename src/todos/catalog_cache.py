"""Cache persistente para catálogos estáveis do SEI."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import random
import sqlite3
import time
from functools import lru_cache
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_DEFAULT_CATALOG_CACHE_TTL: int = 24 * 60 * 60  # 24 hours

# §33.2 — Env-variable override: SEI_CACHE_TTL_SECONDS takes precedence; legacy
# CATALOG_CACHE_TTL is kept for backwards compatibility.
_raw_catalog_ttl = os.environ.get("SEI_CACHE_TTL_SECONDS") or os.environ.get(
    "CATALOG_CACHE_TTL", ""
)
try:
    CATALOG_CACHE_TTL: int = (
        int(_raw_catalog_ttl) if _raw_catalog_ttl else _DEFAULT_CATALOG_CACHE_TTL
    )
except ValueError as exc:
    _ttl_err = (
        f"SEI_CACHE_TTL_SECONDS / CATALOG_CACHE_TTL deve ser um inteiro em segundos; "
        f"recebido: {_raw_catalog_ttl!r}"
    )
    raise RuntimeError(_ttl_err) from exc
if CATALOG_CACHE_TTL <= 0:
    _ttl_zero_err = f"SEI_CACHE_TTL_SECONDS / CATALOG_CACHE_TTL deve ser positivo; recebido: {CATALOG_CACHE_TTL}"
    raise ValueError(_ttl_zero_err)
_SWEEP_PROBABILITY = 0.05  # probabilistic expired-row sweep: run on ~5% of writes


class CatalogCache:
    """Armazena respostas JSON em disco com TTL usando SQLite (sem dependências externas)."""

    def __init__(self, directory: Path) -> None:
        """Inicialize o armazenamento no diretório informado."""
        self.directory = directory
        self.directory.mkdir(parents=True, exist_ok=True)
        self.db_path = self.directory / "catalogs.db"
        self._init_db()

    def _init_db(self) -> None:
        """Inicializa a tabela SQLite se ela não existir."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS catalogs (
                    key TEXT PRIMARY KEY,
                    value TEXT,
                    expires_at REAL
                )
                """
            )

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

    async def get(self, namespace: dict[str, str], key: str) -> Any:
        """Retorna um valor válido ou None em miss/falha do cache (executado em thread worker)."""
        try:
            return await asyncio.to_thread(self._get_sync, namespace, key)
        except (sqlite3.Error, json.JSONDecodeError):
            logger.warning("Falha ao ler cache de catalogos", exc_info=True)
        return None

    def _get_sync(self, namespace: dict[str, str], key: str) -> Any:
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
                conn.execute("DELETE FROM catalogs WHERE key = ?", (db_key,))
        return None

    async def set(self, namespace: dict[str, str], key: str, value: Any) -> None:
        """Persista uma resposta bem-sucedida pelo TTL padrão (executado em thread worker)."""
        try:
            await asyncio.to_thread(self._set_sync, namespace, key, value)
        except (sqlite3.Error, json.JSONDecodeError):
            logger.warning("Falha ao gravar cache de catalogos", exc_info=True)

    def _set_sync(self, namespace: dict[str, str], key: str, value: Any) -> None:
        db_key = self.make_key(namespace, key)
        val_str = json.dumps(value, ensure_ascii=False)
        now = time.time()
        expires_at = now + CATALOG_CACHE_TTL
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO catalogs (key, value, expires_at)
                VALUES (?, ?, ?)
                """,
                (db_key, val_str, expires_at),
            )
            # Probabilistic sweep (5%) — full cleanup available via cleanup()
            if random.random() < _SWEEP_PROBABILITY:
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

    async def close(self) -> None:
        """Feche o armazenamento em disco."""


@lru_cache(maxsize=1)
def get_catalog_cache() -> CatalogCache:
    """Retorna o cache compartilhado pelo processo."""
    configured = os.environ.get("TODOS_CACHE_DIR")
    directory = Path(configured).expanduser() if configured else Path.home() / ".cache" / "todos"
    return CatalogCache(directory)
