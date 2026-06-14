"""Contract tests for the SEI backend abstraction.

These tests need no live server. They guard three invariants:
1. Base operations are NotImplementedError stubs.
2. REST/web subclass overrides match the base signatures exactly (no drift).
3. Every `self._rest.X` / `self._web.X` call in the subclasses resolves to a
   real method/attribute on the wrapped client (catches typos/renames that
   ruff cannot see).
"""

from __future__ import annotations

import asyncio
import inspect
import re
from pathlib import Path

import pytest

from todos.backends.base import SEIBackend
from todos.backends.composite import CompositeBackend, build_backend
from todos.backends.rest import SEIRestBackend
from todos.backends.web import SEIWebBackend
from todos.exceptions import (
    SEINotFoundError,
    SEINotImplementedError,
    SEIParseError,
    SEIPermissionError,
)
from todos.sei_client import SEIClient
from todos.sei_web_client import SEIWebClient

_SRC = Path(__file__).parent.parent / "src" / "todos" / "backends"
_TOOLS = Path(__file__).parent.parent / "src" / "todos" / "tools"


def _backend_source(name: str) -> str:
    """Combined source of a backend, whether it is a single file or a package."""
    single = _SRC / f"{name}.py"
    if single.exists():
        return single.read_text(encoding="utf-8")
    return "\n".join(p.read_text(encoding="utf-8") for p in sorted((_SRC / name).glob("*.py")))


def _async_defs(name: str) -> set[str]:
    return set(re.findall(r"async def (\w+)\(", _backend_source(name)))


def _backend_op_calls(path: Path) -> set[str]:
    # Only matches composite-style `backend.<op>(` calls. Legacy-facade files use
    # `backend.rest.X(` / `backend.web.X(`, which do not match this pattern.
    return set(re.findall(r"backend\.(\w+)\(", path.read_text(encoding="utf-8")))


def _public_ops(cls: type) -> dict[str, inspect.Signature]:
    return {
        n: inspect.signature(m)
        for n, m in inspect.getmembers(cls, inspect.isfunction)
        if not n.startswith("_")
    }


def _members(cls: type) -> set[str]:
    return {n for n, _ in inspect.getmembers(cls)}


# ---------------------------------------------------------------------------
# 1. Base stubs raise NotImplementedError
# ---------------------------------------------------------------------------


def test_base_operation_raises_not_implemented() -> None:
    backend = SEIBackend()
    with pytest.raises(NotImplementedError):
        asyncio.run(backend.consultar_processo("0001.000001/2024-01"))


def test_base_has_no_abstractmethods() -> None:
    # Intentionally not abc.ABC — subclasses may implement only a subset.
    assert getattr(SEIBackend, "__abstractmethods__", frozenset()) == frozenset()


# ---------------------------------------------------------------------------
# 2. Subclass overrides match base signatures exactly
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("cls", [SEIRestBackend, SEIWebBackend])
def test_overrides_match_base_signatures(cls: type) -> None:
    base = _public_ops(SEIBackend)
    drift = []
    extra = []
    for name, member in vars(cls).items():
        if not inspect.isfunction(member) or name.startswith("_"):
            continue
        if name not in base:
            extra.append(name)
            continue
        if inspect.signature(member) != base[name]:
            drift.append((name, str(inspect.signature(member)), str(base[name])))
    assert not extra, f"{cls.__name__} defines methods absent from base (typos?): {extra}"
    assert not drift, f"{cls.__name__} signature drift: {drift}"


# ---------------------------------------------------------------------------
# 3. Every wrapped-client call resolves to a real member
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "attr", "client"),
    [
        ("rest", "_rest", SEIClient),
        ("web", "_web", SEIWebClient),
    ],
)
def test_wrapped_client_calls_exist(name: str, attr: str, client: type) -> None:
    src = _backend_source(name)
    calls = set(re.findall(rf"self\.{attr}\.(\w+)", src))
    valid = _members(client)
    missing = sorted(c for c in calls if c not in valid)
    assert not missing, f"{name} calls non-existent {client.__name__} members: {missing}"


# ---------------------------------------------------------------------------
# 4. Tool modules call only real, IMPLEMENTED contract ops
# ---------------------------------------------------------------------------


def _always_raise_ops() -> set[str]:
    """Contract ops implemented by no backend (composite would always raise)."""
    base = {
        n for n, _ in inspect.getmembers(SEIBackend, inspect.isfunction) if not n.startswith("_")
    }
    impl = _async_defs("rest") | _async_defs("web") | _async_defs("composite")
    return base - impl


def test_tool_backend_calls_resolve_to_contract_ops() -> None:
    # Every composite-style backend.<op>() in a tool module must name a real op.
    ops = set(_public_ops(SEIBackend))
    bad = {}
    for path in sorted(_TOOLS.glob("*.py")):
        unknown = sorted(_backend_op_calls(path) - ops)
        if unknown:
            bad[path.name] = unknown
    assert not bad, f"tool modules call non-contract backend ops: {bad}"


def test_no_tool_delegates_to_always_raise_op() -> None:
    # A tool delegating to an op no backend implements would always raise at
    # runtime (e.g. cancelar_assinatura/resumo_processos are tool-layer only).
    always_raise = _always_raise_ops()
    bad = {}
    for path in sorted(_TOOLS.glob("*.py")):
        hit = sorted(_backend_op_calls(path) & always_raise)
        if hit:
            bad[path.name] = hit
    assert not bad, f"tool modules delegate to always-raise contract ops: {bad}"


# ---------------------------------------------------------------------------
# Construction smoke test (no network)
# ---------------------------------------------------------------------------


def test_backends_construct_without_network() -> None:
    rest = SEIRestBackend(SEIClient())
    web = SEIWebBackend(SEIWebClient())
    assert rest.name == "rest"
    assert web.name == "web"


# ---------------------------------------------------------------------------
# Composite routing
# ---------------------------------------------------------------------------


class _FakeRest(SEIBackend):
    name = "rest"

    async def verificar_acesso(self, processo: str) -> dict:
        return {"src": "rest", "processo": processo}

    async def listar_documentos(self, processo: str) -> dict:
        return {"src": "rest", "processo": processo}

    async def consultar_processo(self, processo: str) -> dict:
        return {"src": "rest", "id": processo, "tipo": "Administrativo"}


class _FakeWeb(SEIBackend):
    name = "web"

    async def verificar_acesso(self, processo: str) -> dict:
        return {"src": "web", "processo": processo}

    async def listar_documentos(self, processo: str) -> dict:
        return {"src": "web", "processo": processo}

    async def listar_processos(self, **_kwargs: object) -> dict:
        return {"src": "web"}

    async def consultar_processo(self, processo: str) -> dict:
        return {"src": "web", "processo": processo, "documentos": [1, 2], "tipo": "ignorado"}


def test_composite_prefers_rest_when_both_implement() -> None:
    c = CompositeBackend(_FakeRest(), _FakeWeb())
    out = asyncio.run(c.verificar_acesso("X"))
    assert out["src"] == "rest"


def test_composite_web_first_set_prefers_web() -> None:
    # listar_documentos is in _WEB_FIRST → web wins even though REST implements it
    c = CompositeBackend(_FakeRest(), _FakeWeb())
    out = asyncio.run(c.listar_documentos("X"))
    assert out["src"] == "web"


def test_composite_falls_back_to_web_on_rest_stub() -> None:
    # _FakeRest does not implement listar_processos → inherited stub → web
    c = CompositeBackend(_FakeRest(), _FakeWeb())
    out = asyncio.run(c.listar_processos())
    assert out["src"] == "web"


def test_composite_raises_when_neither_implements() -> None:
    c = CompositeBackend(_FakeRest(), _FakeWeb())
    with pytest.raises(SEINotImplementedError):
        asyncio.run(c.versao())


class _Rest404(SEIBackend):
    name = "rest"

    async def listar_credenciamentos(self, processo: str) -> dict:
        raise SEINotFoundError(processo)

    async def listar_relacionamentos(self, processo: str) -> dict:
        raise SEINotFoundError(processo)


class _WebRelac(SEIBackend):
    name = "web"

    async def listar_relacionamentos(self, processo: str) -> dict:
        return {"src": "web", "processo": processo, "relacionados": []}


def test_composite_rest_404_on_rest_only_op_surfaces_not_found() -> None:
    # REST-only op (sem mixin web): REST 404 real não deve virar
    # SEINotImplementedError por causa do NotImplementedError do web stub.
    c = CompositeBackend(_Rest404(), _WebRelac())
    with pytest.raises(SEINotFoundError):
        asyncio.run(c.listar_credenciamentos("X"))


def test_composite_rest_404_falls_back_to_web() -> None:
    # REST 404 por endpoint ausente, mas o web implementa → usa o web.
    c = CompositeBackend(_Rest404(), _WebRelac())
    out = asyncio.run(c.listar_relacionamentos("X"))
    assert out["src"] == "web"


def test_composite_web_only_when_rest_none() -> None:
    c = CompositeBackend(None, _FakeWeb())
    out = asyncio.run(c.verificar_acesso("X"))
    assert out["src"] == "web"


class _WebParseErr(SEIBackend):
    name = "web"

    async def listar_documentos(self, processo: str) -> dict:
        raise SEIParseError(processo)


class _WebPermErr(SEIBackend):
    name = "web"

    async def listar_documentos(self, processo: str) -> dict:
        raise SEIPermissionError(processo)


def test_composite_web_first_falls_back_to_rest_on_parse_error() -> None:
    # listar_documentos é _WEB_FIRST; se o scraper quebra (HTML mudou →
    # SEIParseError) o REST disponível atende.
    c = CompositeBackend(_FakeRest(), _WebParseErr())
    out = asyncio.run(c.listar_documentos("X"))
    assert out["src"] == "rest"


def test_composite_does_not_fall_back_on_permission_error() -> None:
    # Erro definitivo de domínio (sem acesso) NÃO deve cair para o outro backend.
    c = CompositeBackend(_FakeRest(), _WebPermErr())
    with pytest.raises(SEIPermissionError):
        asyncio.run(c.listar_documentos("X"))


def test_composite_consultar_processo_merges_rest_and_web() -> None:
    c = CompositeBackend(_FakeRest(), _FakeWeb())
    out = asyncio.run(c.consultar_processo("X"))
    # REST is canonical for shared keys; web only fills gaps (documentos)
    assert out["src"] == "rest"
    assert out["tipo"] == "Administrativo"
    assert out["documentos"] == [1, 2]


def test_build_backend_web_only_without_base_url() -> None:
    backend = build_backend(SEIClient(), SEIWebClient())
    assert isinstance(backend, CompositeBackend)
    assert backend._rest is None


def test_build_backend_includes_rest_with_base_url() -> None:
    client = SEIClient(sei_url="https://example.gov.br/sei/modulos/wssei/api/v2")
    backend = build_backend(client, SEIWebClient())
    assert isinstance(backend, CompositeBackend)
    assert backend._rest is not None
