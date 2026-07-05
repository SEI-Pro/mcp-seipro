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

import pytest

from todos.backends.base import SEIBackend
from todos.backends.rest import SEIRestBackend
from todos.backends.web import SEIWebBackend
from todos.exceptions import SEINotImplementedError
from todos.sei_client import SEIClient
from todos.sei_web_client import SEIWebClient


def _mixin_async_ops(cls: type) -> set[str]:
    """Return contract methods that *cls* actually overrides (not the base stub).

    For each name in the `SEIBackend` contract, resolves it on *cls* via
    `getattr` — the same attribute lookup Python itself performs on a call —
    and checks whether that's a different function object than
    `SEIBackend`'s own stub. This mirrors real method resolution instead of
    reimplementing a piece of it by walking `__mro__`/`vars()` by hand (the
    previous approach), which conflated "some non-base class in the MRO
    happens to define an async method with this name" with "this class's
    actual resolved method differs from the stub" — accidentally correct
    only because no mixin here shadows another's implementation.
    """
    contract = _contract_ops()
    base = SEIBackend
    return {
        name
        for name in contract
        if inspect.iscoroutinefunction(getattr(cls, name, None))
        and getattr(cls, name) is not getattr(base, name)
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
# Web backend: documentos — unit tests with faked SEIWebClient (RFC 0011)
# ---------------------------------------------------------------------------


class _FakeWebClient:
    """Minimal fake of SEIWebClient for documentos mixin tests."""

    def __init__(self) -> None:
        self.listar_secoes_calls: list[tuple[str, str]] = []
        self.alterar_secoes_calls: list[tuple[str, str, list]] = []
        self.alterar_doc_calls: list[dict] = []

    async def listar_secoes_web(self, protocolo: str, id_documento: str) -> dict:
        self.listar_secoes_calls.append((protocolo, id_documento))
        return {
            "secoes": [
                {"id": "s1", "idSecaoModelo": "s1", "conteudo": "texto", "somenteLeitura": False}
            ],
            "ultimaVersaoDocumento": "7",
        }

    async def alterar_secoes_web(
        self, protocolo: str, id_documento: str, secoes: list[dict]
    ) -> dict:
        self.alterar_secoes_calls.append((protocolo, id_documento, secoes))
        return {"status": "ok", "id_documento": id_documento}

    async def alterar_documento_interno_web(
        self,
        protocolo: str,
        id_documento: str,
        descricao: str = "",
        nivel_acesso: str = "",
        hipotese_legal: str = "",
    ) -> dict:
        del hipotese_legal
        self.alterar_doc_calls.append(
            {
                "protocolo": protocolo,
                "id": id_documento,
                "descricao": descricao,
                "nivel_acesso": nivel_acesso,
            }
        )
        return {"status": "ok"}


def _web_doc_backend(client: _FakeWebClient) -> SEIWebBackend:
    return SEIWebBackend(client)  # type: ignore[arg-type]


class TestWebDocumentosBackend:
    def setup_method(self) -> None:
        self.client = _FakeWebClient()
        self.backend = _web_doc_backend(self.client)

    def test_listar_secoes_delegates_to_web_client(self) -> None:
        result = asyncio.run(self.backend.listar_secoes("DOC1", processo="PF"))
        assert result["ultimaVersaoDocumento"] == "7"
        assert self.client.listar_secoes_calls == [("PF", "DOC1")]

    def test_listar_secoes_without_processo_raises(self) -> None:
        with pytest.raises(SEINotImplementedError, match="forneça o parâmetro 'processo'"):
            asyncio.run(self.backend.listar_secoes("DOC1", processo=None))

    def test_alterar_secoes_delegates_to_web_client(self) -> None:
        secoes = [{"idSecaoModelo": "s1", "conteudo": "<p>novo</p>"}]
        result = asyncio.run(self.backend.alterar_secoes("DOC1", secoes, processo="PF"))
        assert result["status"] == "ok"
        assert self.client.alterar_secoes_calls[0][0] == "PF"
        assert self.client.alterar_secoes_calls[0][1] == "DOC1"

    def test_alterar_secoes_without_processo_raises(self) -> None:
        with pytest.raises(SEINotImplementedError):
            asyncio.run(
                self.backend.alterar_secoes("DOC1", [{"idSecaoModelo": "s1", "conteudo": "x"}])
            )

    def test_alterar_documento_interno_delegates(self) -> None:
        result = asyncio.run(
            self.backend.alterar_documento_interno(
                "DOC1", descricao="Novo título", nivel_acesso="0", processo="PF"
            )
        )
        assert result["status"] == "ok"
        call = self.client.alterar_doc_calls[0]
        assert call["protocolo"] == "PF"
        assert call["descricao"] == "Novo título"

    def test_alterar_documento_interno_without_processo_raises(self) -> None:
        with pytest.raises(SEINotImplementedError):
            asyncio.run(self.backend.alterar_documento_interno("DOC1", processo=None))

    def test_listar_blocos_documento_raises_not_implemented(self) -> None:
        with pytest.raises(SEINotImplementedError, match="mod-wssei"):
            asyncio.run(self.backend.listar_blocos_documento("DOC1"))

    def test_sugestao_assuntos_documento_raises_not_implemented(self) -> None:
        with pytest.raises(SEINotImplementedError, match="mod-wssei"):
            asyncio.run(self.backend.sugestao_assuntos_documento("SERIE1"))


# ---------------------------------------------------------------------------
# Coverage thresholds — regression guard (RFC 0009 §2.2)
#
# These constants capture the implementation coverage at the time they were
# written. Raising them is always welcome; lowering them requires an explicit
# decision (add a comment explaining why a method was removed/moved).
# ---------------------------------------------------------------------------

# `inspecionar_pagina`/`submeter_form` were added to the SEIBackend contract
# (RFC 0020, web-only genérico form inspection/submission) — no REST
# equivalent exists (mod-wssei doesn't expose HTML to inspect), growing the
# contract from 126 to 128 ops while REST's implemented count stays at 112.
# `capturar_tela` was added next (RFC 0021, web-only browser screenshot) —
# same reasoning: no REST equivalent (mod-wssei has no rendered screen to
# photograph), growing the contract from 128 to 129 while REST stays at 112.
# Unlike inspecionar_pagina/submeter_form, capturar_tela IS implemented by the
# web mixin (that's the whole feature), so web's numerator grows too: 91→92.
_REST_COVERAGE_MIN = 112 / 129  # exact fraction; one drop → 111/129 = 0.860 < 0.868 → fails
_WEB_COVERAGE_MIN = 92 / 129  # exact fraction; one drop → 91/129 = 0.705 < 0.713 → fails


def _contract_ops() -> set[str]:
    """Public async methods declared in SEIBackend (the full contract)."""
    return {
        n for n, _ in inspect.getmembers(SEIBackend, inspect.isfunction) if not n.startswith("_")
    }


def test_rest_backend_coverage_threshold() -> None:
    """REST backend must implement ≥ _REST_COVERAGE_MIN of the SEIBackend contract.

    Catches silent regressions: a mixin method deleted or renamed without a
    replacement. Raise the constant when coverage improves; never lower it
    without a comment explaining the intentional removal.
    """
    contract = _contract_ops()
    implemented = _mixin_async_ops(SEIRestBackend) & contract
    coverage = len(implemented) / len(contract)
    missing = sorted(contract - implemented)
    assert coverage >= _REST_COVERAGE_MIN, (
        f"REST coverage dropped to {coverage:.0%} (min {_REST_COVERAGE_MIN:.0%}). "
        f"Methods no longer implemented: {missing}"
    )


def test_web_backend_coverage_threshold() -> None:
    """Web backend must implement ≥ _WEB_COVERAGE_MIN of the SEIBackend contract.

    Same contract as the REST threshold test above.
    """
    contract = _contract_ops()
    implemented = _mixin_async_ops(SEIWebBackend) & contract
    coverage = len(implemented) / len(contract)
    missing = sorted(contract - implemented)
    assert coverage >= _WEB_COVERAGE_MIN, (
        f"Web coverage dropped to {coverage:.0%} (min {_WEB_COVERAGE_MIN:.0%}). "
        f"Methods no longer implemented: {missing}"
    )
