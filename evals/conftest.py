"""Pytest fixtures for eval tests.

Sets up the FastMCP server with a FixtureBackend that returns pre-defined
responses for fixture process 50300.001234/2025-00 — no live SEI, no network.
"""

from __future__ import annotations

import importlib

import pytest

from evals.fixture_backend import FakeRESTClient, FixtureBackend
from todos.mcp_app import mcp

_TOOL_MODULES = [
    "todos.server",
    "todos.tools.acompanhamento",
    "todos.tools.assinatura",
    "todos.tools.blocos_assinatura",
    "todos.tools.blocos_internos",
    "todos.tools.catalogos",
    "todos.tools.credenciamento",
    "todos.tools.documentos",
    "todos.tools.marcadores",
    "todos.tools.processos",
    "todos.tools.unidades",
]


def pytest_configure(config: pytest.Config) -> None:
    """Register eval marker."""
    config.addinivalue_line(
        "markers",
        "eval: marks eval tests that require ANTHROPIC_API_KEY",
    )


@pytest.fixture(scope="session", autouse=True)
def _import_all_tool_modules() -> None:
    """Import all tool modules so @mcp.tool decorators register tools."""
    for mod_name in _TOOL_MODULES:
        importlib.import_module(mod_name)


@pytest.fixture
def fixture_backend() -> FixtureBackend:
    """Return a FixtureBackend instance with pre-defined SEI responses."""
    return FixtureBackend()


@pytest.fixture
def fake_rest_client() -> FakeRESTClient:
    """Return a FakeRESTClient for REST-only tools (sei_resumo_processos)."""
    return FakeRESTClient()


@pytest.fixture
def patched_mcp(fixture_backend: FixtureBackend, fake_rest_client: FakeRESTClient, monkeypatch):
    """Return the FastMCP server with all backends patched to use fixture data."""

    async def _fake_backend(_ctx: object) -> FixtureBackend:
        return fixture_backend

    async def _fake_get_client(_ctx: object) -> FakeRESTClient:
        return fake_rest_client

    for mod_name in _TOOL_MODULES:
        mod = importlib.import_module(mod_name)
        if hasattr(mod, "_backend"):
            monkeypatch.setattr(mod, "_backend", _fake_backend)
        if hasattr(mod, "_get_client"):
            monkeypatch.setattr(mod, "_get_client", _fake_get_client)

    return mcp
