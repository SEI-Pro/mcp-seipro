"""Shared pytest fixtures for the todos test suite."""

from __future__ import annotations

import pytest

from todos.catalog_cache import get_catalog_cache
from todos.settings import get_settings


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> None:
    """Clear LRU caches before and after each test.

    Any test that sets env vars via monkeypatch must see fresh instances;
    without this, cached instances from a previous test leak through.
    """
    get_settings.cache_clear()
    get_catalog_cache.cache_clear()
    yield
    get_settings.cache_clear()
    get_catalog_cache.cache_clear()
