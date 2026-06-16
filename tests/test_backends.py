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
import dis
import inspect

import httpx
import pytest

from todos.backends import NovoDocumentoExterno
from todos.backends.base import SEIBackend
from todos.backends.composite import CompositeBackend, build_backend
from todos.backends.rest import SEIRestBackend
from todos.backends.web import SEIWebBackend
from todos.exceptions import (
    SEIConnectionError,
    SEINotFoundError,
    SEINotImplementedError,
    SEIParseError,
    SEIPermissionError,
)
from todos.sei_client import SEIClient
from todos.sei_web_client import SEIWebClient


def _mixin_async_ops(cls: type) -> set[str]:
    """Return async methods actually implemented in *cls* or its mixins.

    Walks the MRO, skipping `SEIBackend` (base stubs) and `object`.  Every
    ``async def`` found in a concrete mixin is counted as an implementation,
    regardless of whether it is exposed under a different name by the class.
    This replaces the former regex approach (``_async_defs``) which was fragile
    to refactoring and could silently miss methods defined across mixin files.
    """
    skip = {SEIBackend, object}
    return {
        name
        for klass in cls.__mro__
        if klass not in skip
        for name, val in vars(klass).items()
        if not name.startswith("_") and inspect.iscoroutinefunction(val)
    }


def _client_attr_calls(backend_cls: type, attr_name: str) -> set[str]:
    """Return attribute names called as ``self.{attr_name}.X`` in *backend_cls*.

    Uses bytecode inspection (``dis``) instead of source-text regex so that the
    check is tied to the compiled code, not the formatting of the source file.
    Attribute accesses are identified by looking for a LOAD_FAST/LOAD_DEREF on
    ``self`` followed by LOAD_ATTR on ``attr_name`` followed by another LOAD_ATTR.
    """
    skip = {SEIBackend, object}
    calls: set[str] = set()
    for klass in backend_cls.__mro__:
        if klass in skip:
            continue
        for val in vars(klass).values():
            if not inspect.isfunction(val):
                continue
            instrs = list(dis.get_instructions(val.__code__))
            for i in range(len(instrs) - 2):
                instr = instrs[i]
                if instr.opname in {"LOAD_FAST", "LOAD_DEREF"} and instr.argval == "self":
                    next1 = instrs[i + 1]
                    next2 = instrs[i + 2]
                    if next1.argval == attr_name and next2.argval is not None:
                        calls.add(next2.argval)
    return calls


def _public_ops(cls: type) -> dict[str, inspect.Signature]:
    """Return public non-dunder method signatures for a class."""
    return {
        n: inspect.signature(m)
        for n, m in inspect.getmembers(cls, inspect.isfunction)
        if not n.startswith("_")
    }


def _members(cls: type) -> set[str]:
    """Return the full set of attribute names exposed by a class."""
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
    ("backend_cls", "attr", "client"),
    [
        (SEIRestBackend, "_rest", SEIClient),
        (SEIWebBackend, "_web", SEIWebClient),
    ],
)
def test_wrapped_client_calls_exist(backend_cls: type, attr: str, client: type) -> None:
    """Every ``self.{attr}.X`` call in *backend_cls* must resolve to a real member.

    Uses bytecode inspection (``dis``) instead of source-text regex so that the
    check is tied to compiled code, not source formatting.  Catches typos and
    renames that ruff cannot see.
    """
    calls = _client_attr_calls(backend_cls, attr)
    valid = _members(client)
    missing = sorted(c for c in calls if c not in valid)
    assert not missing, (
        f"{backend_cls.__name__} calls non-existent {client.__name__} members: {missing}"
    )


# ---------------------------------------------------------------------------
# 4. Always-raise ops are genuinely unimplemented in every backend
# ---------------------------------------------------------------------------


def _always_raise_ops() -> set[str]:
    """Contract ops implemented by no backend (composite would always raise).

    Uses live class introspection instead of regex over source files so that
    renaming, moving, or decorating a method is detected immediately.
    """
    base = {
        n for n, _ in inspect.getmembers(SEIBackend, inspect.isfunction) if not n.startswith("_")
    }
    impl = _mixin_async_ops(SEIRestBackend) | _mixin_async_ops(SEIWebBackend)
    return base - impl


def test_always_raise_ops_absent_from_both_backends() -> None:
    """Ops in ``_always_raise_ops()`` must not be implemented by any backend mixin.

    Uses ``hasattr`` + ``_mixin_async_ops`` (runtime introspection) to verify
    that each always-raise op is genuinely absent from both the REST and web
    backend implementations.  A failure here means a newly added mixin
    accidentally implements one of the tool-layer-only operations.
    """
    always_raise = _always_raise_ops()
    # Sanity: the set must not be empty; otherwise the guard is vacuous.
    assert always_raise, "_always_raise_ops() returned empty set — check introspection logic"

    impl_rest = _mixin_async_ops(SEIRestBackend)
    impl_web = _mixin_async_ops(SEIWebBackend)
    wrongly_implemented = sorted(always_raise & (impl_rest | impl_web))
    assert not wrongly_implemented, (
        f"ops that should always raise are now implemented in a backend mixin: "
        f"{wrongly_implemented}"
    )


def test_always_raise_ops_are_callable_on_base() -> None:
    """Every always-raise op exists as a method on ``SEIBackend`` (hasattr check).

    This verifies the contract list itself is well-formed: an op can only
    'always raise' if it's part of the declared contract on ``SEIBackend``.
    """
    always_raise = _always_raise_ops()
    for op in always_raise:
        assert hasattr(SEIBackend, op), (
            f"_always_raise_ops() lists {op!r} but SEIBackend has no such method"
        )


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

    def __getattr__(self, name: str) -> object:
        """Raise for any attribute not explicitly defined on this fake."""
        msg = f"_FakeRest.{name} not implemented — add it explicitly if needed by this test"
        raise NotImplementedError(msg)

    async def verificar_acesso(self, processo: str) -> dict:
        return {"src": "rest", "processo": processo}

    async def listar_documentos(self, processo: str) -> dict:
        return {"src": "rest", "processo": processo}

    async def consultar_processo(self, processo: str) -> dict:
        return {"src": "rest", "id": processo, "tipo": "Administrativo"}


class _FakeWeb(SEIBackend):
    name = "web"

    def __getattr__(self, name: str) -> object:
        """Raise for any attribute not explicitly defined on this fake."""
        msg = f"_FakeWeb.{name} not implemented — add it explicitly if needed by this test"
        raise NotImplementedError(msg)

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


# ---------------------------------------------------------------------------
# Composite: consultar_processo merge edge cases + warnings
# ---------------------------------------------------------------------------


class _RestRaises(SEIBackend):
    name = "rest"

    async def consultar_processo(self, processo: str) -> dict:
        raise SEIParseError(processo)


class _WebRaises(SEIBackend):
    name = "web"

    async def consultar_processo(self, processo: str) -> dict:
        raise SEIParseError(processo)


def test_consultar_processo_web_only_when_rest_none() -> None:
    c = CompositeBackend(None, _FakeWeb())
    out = asyncio.run(c.consultar_processo("X"))
    assert out["src"] == "web"
    assert "_warnings" not in out


def test_consultar_processo_records_warning_when_rest_fails() -> None:
    c = CompositeBackend(_RestRaises(), _FakeWeb())
    out = asyncio.run(c.consultar_processo("X"))
    # Web result still served; REST failure surfaced in _warnings.
    assert out["src"] == "web"
    assert any("REST falhou" in w for w in out["_warnings"])


def test_consultar_processo_records_warning_when_web_fails() -> None:
    c = CompositeBackend(_FakeRest(), _WebRaises())
    out = asyncio.run(c.consultar_processo("X"))
    assert out["tipo"] == "Administrativo"
    assert any("Web scraper falhou" in w for w in out["_warnings"])


def test_consultar_processo_raises_when_both_sources_fail() -> None:
    c = CompositeBackend(_RestRaises(), _WebRaises())
    with pytest.raises(SEIConnectionError):
        asyncio.run(c.consultar_processo("X"))


# ---------------------------------------------------------------------------
# Composite: trocar_unidade (web controls session, REST is synced best-effort)
# ---------------------------------------------------------------------------


class _WebTrocaOk(SEIBackend):
    name = "web"

    async def trocar_unidade(self, id_unidade: str) -> dict:
        return {"id_unidade": id_unidade, "ok": True}


class _WebTrocaConnErr(SEIBackend):
    name = "web"

    async def trocar_unidade(self, id_unidade: str) -> dict:
        raise httpx.ConnectError(id_unidade)


class _RestRecordsTroca(SEIBackend):
    name = "rest"

    def __init__(self) -> None:
        self.synced_to: str | None = None

    async def trocar_unidade(self, id_unidade: str) -> dict:
        self.synced_to = id_unidade
        return {"id_unidade": id_unidade}


class _RestTrocaRaises(SEIBackend):
    name = "rest"

    async def trocar_unidade(self, id_unidade: str) -> dict:
        raise SEIParseError(id_unidade)


def test_trocar_unidade_syncs_rest_after_web() -> None:
    rest = _RestRecordsTroca()
    c = CompositeBackend(rest, _WebTrocaOk())
    out = asyncio.run(c.trocar_unidade("42"))
    assert out["ok"] is True
    assert rest.synced_to == "42"


def test_trocar_unidade_web_connection_error_becomes_sei_connection_error() -> None:
    c = CompositeBackend(_RestRecordsTroca(), _WebTrocaConnErr())
    with pytest.raises(SEIConnectionError):
        asyncio.run(c.trocar_unidade("42"))


def test_trocar_unidade_suppresses_rest_sync_failure() -> None:
    # REST sync is best-effort: a failure there must not break the web success.
    c = CompositeBackend(_RestTrocaRaises(), _WebTrocaOk())
    out = asyncio.run(c.trocar_unidade("42"))
    assert out["ok"] is True


def test_trocar_unidade_no_rest_just_returns_web() -> None:
    c = CompositeBackend(None, _WebTrocaOk())
    out = asyncio.run(c.trocar_unidade("7"))
    assert out == {"id_unidade": "7", "ok": True}


# ---------------------------------------------------------------------------
# Composite dispatcher: transport error on one backend falls back to the other
# ---------------------------------------------------------------------------


class _RestTransportErr(SEIBackend):
    name = "rest"

    async def verificar_acesso(self, processo: str) -> dict:
        raise httpx.ConnectTimeout(processo)


def test_dispatcher_transport_error_falls_back_to_web() -> None:
    c = CompositeBackend(_RestTransportErr(), _FakeWeb())
    out = asyncio.run(c.verificar_acesso("X"))
    assert out["src"] == "web"


def test_dispatcher_transport_error_surfaces_when_no_fallback() -> None:
    # REST raises a transport error and web doesn't implement the op (inherited
    # NotImplementedError stub) → the transport failure (not "unsupported")
    # must be what propagates, since it is the more informative error.
    c = CompositeBackend(_RestTransportErr(), _WebRaises())
    with pytest.raises(SEIConnectionError):
        asyncio.run(c.verificar_acesso("X"))


# ---------------------------------------------------------------------------
# Composite: criar_documento_externo routes by upload form (base64 → web-first)
# ---------------------------------------------------------------------------


class _RestExt(SEIBackend):
    name = "rest"

    async def criar_documento_externo(self, processo: str, dados: object) -> dict:
        del processo, dados
        return {"src": "rest"}


class _WebExt(SEIBackend):
    name = "web"

    async def criar_documento_externo(self, processo: str, dados: object) -> dict:
        del processo, dados
        return {"src": "web"}


def test_criar_documento_externo_base64_prefers_web() -> None:
    c = CompositeBackend(_RestExt(), _WebExt())
    dados = NovoDocumentoExterno(id_serie="S", arquivo_base64="eA==", nome_arquivo="x.pdf")
    out = asyncio.run(c.criar_documento_externo("P", dados))
    assert out["src"] == "web"


def test_criar_documento_externo_file_path_prefers_rest() -> None:
    c = CompositeBackend(_RestExt(), _WebExt())
    dados = NovoDocumentoExterno(id_serie="S", arquivo_path="x.pdf")
    out = asyncio.run(c.criar_documento_externo("P", dados))
    assert out["src"] == "rest"


# ---------------------------------------------------------------------------
# Dispatch error priority: a real REST failure must win over a web "can't serve"
# ---------------------------------------------------------------------------


class _RestConnErr(SEIBackend):
    name = "rest"

    async def consultar_documento_externo(
        self, id_documento: str, processo: str | None = None
    ) -> dict:
        del id_documento, processo
        msg = "SEI inacessível: timeout"
        raise SEIConnectionError(msg)


class _WebNeedsProcesso(SEIBackend):
    name = "web"

    async def consultar_documento_externo(
        self, id_documento: str, processo: str | None = None
    ) -> dict:
        del id_documento, processo
        # Mirrors the web backend: "can't serve this op without processo".
        msg = "forneça o parâmetro 'processo'"
        raise SEINotImplementedError(msg)


def test_rest_connection_error_wins_over_web_unsupported() -> None:
    # Regression: a transient REST failure with processo=None must surface as a
    # connection error, NOT the web's misleading "no mod-wssei / forneça processo".
    c = CompositeBackend(_RestConnErr(), _WebNeedsProcesso())
    with pytest.raises(SEIConnectionError):
        asyncio.run(c.consultar_documento_externo("X", None))


def test_web_unsupported_surfaces_when_it_is_the_only_error() -> None:
    # Web-only (no REST): the SEINotImplementedError guidance must still surface.
    c = CompositeBackend(None, _WebNeedsProcesso())
    with pytest.raises(SEINotImplementedError, match="processo"):
        asyncio.run(c.consultar_documento_externo("X", None))


class _RestVersaoAntiga(SEIBackend):
    name = "rest"

    async def listar_relacionamentos(self, processo: str) -> dict:
        del processo
        msg = "requer mod-wssei 3.0.2+"
        raise SEINotImplementedError(msg)


def test_informative_not_implemented_preserved_over_base_stub() -> None:
    # REST raises an informative SEINotImplementedError; web only has the base
    # stub (bare NotImplementedError). The informative message must win.
    c = CompositeBackend(_RestVersaoAntiga(), _FakeWeb())
    with pytest.raises(SEINotImplementedError, match="mod-wssei"):
        asyncio.run(c.listar_relacionamentos("X"))
