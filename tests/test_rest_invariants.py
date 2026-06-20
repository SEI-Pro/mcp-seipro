"""Unit tests for REST backend invariants that are invisible to the type-checker.

Covers:
- _validar_pagina raises SEIValidationError (not ValueError) for negative pages
- _resolver_processo raises SEINotFoundError (not returning "") when the REST
  call returns a process record with an empty IdProcedimento
- _resolver_documento swallows only transport errors (httpx.RequestError) and
  lets HTTP-status errors (httpx.HTTPStatusError: 401, 403, 500) propagate
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from todos.backends.rest._session import _RestBase
from todos.backends.rest.catalogos import _validar_pagina
from todos.exceptions import SEINotFoundError, SEIValidationError

# ---------------------------------------------------------------------------
# _validar_pagina
# ---------------------------------------------------------------------------


class TestValidarPagina:
    def test_zero_is_valid(self) -> None:
        _validar_pagina(0)

    def test_positive_is_valid(self) -> None:
        _validar_pagina(10)

    def test_negative_one_raises_sei_validation_error(self) -> None:
        with pytest.raises(SEIValidationError):
            _validar_pagina(-1)

    def test_large_negative_raises_sei_validation_error(self) -> None:
        with pytest.raises(SEIValidationError):
            _validar_pagina(-100)

    def test_raises_sei_validation_error_not_value_error(self) -> None:
        with pytest.raises(SEIValidationError):
            _validar_pagina(-1)
        # Confirm it is NOT a plain ValueError (a subclass would pass isinstance
        # but the hierarchy must be SEIValidationError, not ValueError).
        try:
            _validar_pagina(-1)
        except SEIValidationError:
            pass
        except ValueError:
            pytest.fail("_validar_pagina raised ValueError instead of SEIValidationError")


# ---------------------------------------------------------------------------
# _resolver_processo
# ---------------------------------------------------------------------------


def _make_rest_base(consultar_retorno: dict) -> _RestBase:
    """Return a _RestBase instance whose self._rest.consultar_processo returns the given dict."""
    mock_client = MagicMock()
    mock_client.consultar_processo = AsyncMock(return_value=consultar_retorno)
    base = _RestBase.__new__(_RestBase)
    base._rest = mock_client
    return base


class TestResolverProcesso:
    def test_formatted_protocolo_with_dot_resolves_id(self) -> None:
        base = _make_rest_base({"IdProcedimento": "42"})
        result = asyncio.run(base._resolver_processo("0001.000001/2024-01"))
        assert result == "42"

    def test_bare_id_returned_as_is(self) -> None:
        base = _make_rest_base({})
        result = asyncio.run(base._resolver_processo("12345"))
        assert result == "12345"

    def test_empty_id_procedimento_raises_sei_not_found(self) -> None:
        base = _make_rest_base({"IdProcedimento": ""})
        with pytest.raises(SEINotFoundError, match="não retornou IdProcedimento"):
            asyncio.run(base._resolver_processo("0001.000001/2024-01"))

    def test_missing_id_procedimento_key_raises_sei_not_found(self) -> None:
        base = _make_rest_base({})
        with pytest.raises(SEINotFoundError):
            asyncio.run(base._resolver_processo("0001.000001/2024-01"))

    def test_slash_in_referencia_triggers_resolution(self) -> None:
        base = _make_rest_base({"IdProcedimento": "99"})
        result = asyncio.run(base._resolver_processo("ANTAQ-001/2024"))
        assert result == "99"


# ---------------------------------------------------------------------------
# _resolver_documento — exception narrowing
# ---------------------------------------------------------------------------


def _make_rest_base_for_doc(
    pesquisar_retorno: dict | Exception,
    listar_retorno: list | Exception,
    visualizar_retorno: str | Exception,
) -> _RestBase:
    """Return a _RestBase whose sub-calls yield the given results or raise the given exceptions."""
    mock_client = MagicMock()

    async def _pesquisar(*_a: object, **_kw: object) -> dict:
        if isinstance(pesquisar_retorno, BaseException):
            raise pesquisar_retorno
        return pesquisar_retorno

    async def _listar(*_a: object, **_kw: object) -> list:
        if isinstance(listar_retorno, BaseException):
            raise listar_retorno
        return listar_retorno

    async def _visualizar(*_a: object, **_kw: object) -> str:
        if isinstance(visualizar_retorno, BaseException):
            raise visualizar_retorno
        return visualizar_retorno

    mock_client.pesquisar_processos = _pesquisar
    mock_client.listar_documentos = _listar
    mock_client.visualizar_documento_interno = _visualizar
    base = _RestBase.__new__(_RestBase)
    base._rest = mock_client
    return base


class TestResolverDocumentoExceptions:
    def test_transport_error_is_swallowed_and_falls_through(self) -> None:
        """ConnectError during listar_documentos must be caught (logged), not propagated.

        The function continues to the visualizar fallback; if that also yields nothing
        it raises SEINotFoundError — not the raw ConnectError.
        """
        transport_exc = httpx.ConnectError("network down")
        base = _make_rest_base_for_doc(
            pesquisar_retorno={"processos": [{"idProcedimento": "1"}]},
            listar_retorno=transport_exc,
            visualizar_retorno="short",  # < _MIN_DOC_CONTENT_LENGTH (10), so falls through
        )
        # Transport error is swallowed; SEINotFoundError is raised at end (doc not found).
        # If ConnectError were NOT swallowed, pytest.raises(SEINotFoundError) would fail
        # because it would receive ConnectError instead — so this assertion doubles as the
        # "transport error was caught" check.
        with pytest.raises(SEINotFoundError):
            asyncio.run(base._resolver_documento("DOC-001"))

    def test_http_status_error_401_propagates(self) -> None:
        """HTTPStatusError (401) during listar_documentos must NOT be swallowed."""
        request = httpx.Request("GET", "http://sei.example.com")
        response = httpx.Response(401, request=request)
        http_exc = httpx.HTTPStatusError("401 Unauthorized", request=request, response=response)
        base = _make_rest_base_for_doc(
            pesquisar_retorno={"processos": [{"idProcedimento": "1"}]},
            listar_retorno=http_exc,
            visualizar_retorno="",
        )
        with pytest.raises(httpx.HTTPStatusError):
            asyncio.run(base._resolver_documento("DOC-001"))

    def test_http_status_error_403_propagates(self) -> None:
        """HTTPStatusError (403) during pesquisar_processos must NOT be swallowed."""
        request = httpx.Request("GET", "http://sei.example.com")
        response = httpx.Response(403, request=request)
        http_exc = httpx.HTTPStatusError("403 Forbidden", request=request, response=response)
        base = _make_rest_base_for_doc(
            pesquisar_retorno=http_exc,
            listar_retorno=[],
            visualizar_retorno="",
        )
        with pytest.raises(httpx.HTTPStatusError):
            asyncio.run(base._resolver_documento("DOC-001"))

    def test_timeout_error_is_swallowed(self) -> None:
        """TimeoutException (a RequestError subclass) must be caught (logged), not propagated.

        After swallowing, _resolver_documento raises SEINotFoundError — not TimeoutException.
        """
        timeout_exc = httpx.TimeoutException("timed out", request=httpx.Request("GET", "http://x"))
        base = _make_rest_base_for_doc(
            pesquisar_retorno={"processos": [{"idProcedimento": "1"}]},
            listar_retorno=timeout_exc,
            visualizar_retorno="",  # empty → not found
        )
        # Timeout is swallowed; SEINotFoundError is raised at end — NOT TimeoutException.
        # pytest.raises(SEINotFoundError) would fail if TimeoutException propagated instead.
        with pytest.raises(SEINotFoundError):
            asyncio.run(base._resolver_documento("DOC-001"))
