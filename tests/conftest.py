"""Shared pytest fixtures for the todos test suite."""

from __future__ import annotations

import pytest

from todos.settings import get_settings


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> None:
    """Clear the get_settings() LRU cache before and after each test.

    Any test that sets env vars via monkeypatch must see a fresh TodosSettings
    instance; without this, the cached instance from a previous test leaks through.
    """
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
