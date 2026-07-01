"""End-to-end coverage of _ReauthTransport — the centralized session-expiry
self-heal wrapping SEIWebClient's httpx transport.

Unlike test_sei_web_client_reauth.py (which mocks client._http.get/post
directly and exercises the per-method retry logic), these tests drive a real
httpx.AsyncClient through httpx.MockTransport → _ReauthTransport, so they
validate the actual transport-layer mechanics: response body re-readability
after .aread(), request re-send safety, and the reentrancy guard around
login()'s own requests — not just the higher-level retry decision logic.
"""

from __future__ import annotations

import asyncio

import httpx

from todos.backends.models import SEIWebClientConfig
from todos.sei_web_client import SEIWebClient, _ReauthTransport

_LOGIN_PAGE = '<html><body><form><input name="txtUsuario"></form></body></html>'


def make_client() -> SEIWebClient:
    return SEIWebClient(
        SEIWebClientConfig(sei_web_url="http://sei.test", sei_usuario="u", sei_senha="p")
    )


class TestTransportWiring:
    def test_client_is_wrapped_in_reauth_transport(self) -> None:
        client = make_client()
        assert isinstance(client._http._transport, _ReauthTransport)


class TestReauthTransportPassthrough:
    def test_normal_response_is_returned_unmodified(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text="<html>tudo normal</html>", request=request)

        wrapped = httpx.MockTransport(handler)
        login_calls = 0

        class _FakeClient:
            async def login(self) -> None:
                nonlocal login_calls
                login_calls += 1

        transport = _ReauthTransport(wrapped, _FakeClient())  # type: ignore[arg-type]

        async def run() -> httpx.Response:
            async with httpx.AsyncClient(transport=transport) as http:
                return await http.get("http://sei.test/x")

        response = asyncio.run(run())
        assert response.status_code == 200
        assert "tudo normal" in response.text
        assert login_calls == 0

    def test_non_get_post_methods_bypass_interception(self) -> None:
        """DELETE isn't part of SEIWebClient's vocabulary, but the guard
        should be explicit about only intercepting GET/POST."""
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            return httpx.Response(200, text=_LOGIN_PAGE, request=request)

        wrapped = httpx.MockTransport(handler)

        class _FakeClient:
            async def login(self) -> None:
                msg = "login() must not be called for a DELETE request"
                raise AssertionError(msg)

        transport = _ReauthTransport(wrapped, _FakeClient())  # type: ignore[arg-type]

        async def run() -> httpx.Response:
            async with httpx.AsyncClient(transport=transport) as http:
                return await http.delete("http://sei.test/x")

        response = asyncio.run(run())
        assert response.status_code == 200
        assert calls["n"] == 1  # no retry attempted


class TestReauthTransportSelfHeals:
    def test_get_expiry_triggers_relogin_and_retry(self) -> None:
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            if calls["n"] == 1:
                return httpx.Response(200, text=_LOGIN_PAGE, request=request)
            return httpx.Response(200, text="<html>conteúdo real</html>", request=request)

        wrapped = httpx.MockTransport(handler)
        login_calls = 0

        class _FakeClient:
            async def login(self) -> None:
                nonlocal login_calls
                login_calls += 1

        transport = _ReauthTransport(wrapped, _FakeClient())  # type: ignore[arg-type]

        async def run() -> httpx.Response:
            async with httpx.AsyncClient(transport=transport) as http:
                return await http.get("http://sei.test/x")

        response = asyncio.run(run())
        assert response.status_code == 200
        assert "conteúdo real" in response.text
        assert calls["n"] == 2  # first (expired) + retry (fresh)
        assert login_calls == 1

    def test_post_expiry_triggers_relogin_and_resends_the_body(self) -> None:
        """A retried POST must resend the *same* form body, not an empty one —
        this is the concrete risk in resending an httpx.Request a second time."""
        received_bodies: list[bytes] = []

        def handler(request: httpx.Request) -> httpx.Response:
            received_bodies.append(request.content)
            if len(received_bodies) == 1:
                return httpx.Response(200, text=_LOGIN_PAGE, request=request)
            return httpx.Response(200, text="<html>ok</html>", request=request)

        wrapped = httpx.MockTransport(handler)

        class _FakeClient:
            async def login(self) -> None:
                pass

        transport = _ReauthTransport(wrapped, _FakeClient())  # type: ignore[arg-type]

        async def run() -> httpx.Response:
            async with httpx.AsyncClient(transport=transport) as http:
                return await http.post("http://sei.test/x", data={"a": "1", "b": "2"})

        response = asyncio.run(run())
        assert response.status_code == 200
        assert len(received_bodies) == 2
        assert received_bodies[0] == received_bodies[1] == b"a=1&b=2"

    def test_persisting_expiry_returns_the_still_expired_response(self) -> None:
        """If relogin doesn't actually fix the session (bad credentials), the
        transport gives up after one retry and hands back whatever it got —
        it does not loop forever. Higher-level callers turn this into a
        clear SEIAuthError (see test_sei_web_client_reauth.py)."""
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            return httpx.Response(200, text=_LOGIN_PAGE, request=request)

        wrapped = httpx.MockTransport(handler)
        login_calls = 0

        class _FakeClient:
            async def login(self) -> None:
                nonlocal login_calls
                login_calls += 1

        transport = _ReauthTransport(wrapped, _FakeClient())  # type: ignore[arg-type]

        async def run() -> httpx.Response:
            async with httpx.AsyncClient(transport=transport) as http:
                return await http.get("http://sei.test/x")

        response = asyncio.run(run())
        assert "txtUsuario" in response.text
        assert calls["n"] == 2  # exactly one retry, not unbounded
        assert login_calls == 1

    def test_login_own_requests_do_not_trigger_nested_reauth(self) -> None:
        """The reentrancy guard: login() sends requests through this same
        transport. If those responses also happened to look like a login
        page (plausible — SEIWebClient's own login() re-checks its POST
        response for the same marker on bad credentials), the transport
        must not recurse into another relogin attempt of its own."""
        outer_calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            outer_calls["n"] += 1
            return httpx.Response(200, text=_LOGIN_PAGE, request=request)

        wrapped = httpx.MockTransport(handler)
        login_invocations = 0

        class _FakeClient:
            async def login(self) -> None:
                nonlocal login_invocations
                login_invocations += 1
                # Simulate login() itself making an HTTP call through the
                # same client/transport (as the real SEIWebClient.login does).
                async with httpx.AsyncClient(transport=transport) as http:
                    await http.get("http://sei.test/sip/login.php")

        transport = _ReauthTransport(wrapped, _FakeClient())  # type: ignore[arg-type]

        async def run() -> httpx.Response:
            async with httpx.AsyncClient(transport=transport) as http:
                return await http.get("http://sei.test/x")

        # Must terminate (not hang/recurse) even though every response is a
        # login page, including the one login() itself receives.
        response = asyncio.run(run())
        assert "txtUsuario" in response.text
        assert login_invocations == 1


class TestReauthTransportPreservesTLSVerification:
    def test_verify_false_is_forwarded_to_the_inner_transport(self) -> None:
        """Regression guard: passing transport= to httpx.AsyncClient makes
        it ignore its own verify=/cert=/etc. kwargs entirely (confirmed via
        httpx.Client._init_transport — `if transport is not None: return
        transport`). If SEIWebClient.__init__ ever stops threading _verify
        into the inner httpx.AsyncHTTPTransport explicitly, TLS verification
        would silently do the wrong thing."""
        client = SEIWebClient(
            SEIWebClientConfig(
                sei_web_url="http://sei.test",
                sei_usuario="u",
                sei_senha="p",
                sei_verify_ssl="false",
            )
        )
        reauth_transport = client._http._transport
        assert isinstance(reauth_transport, _ReauthTransport)
        inner = reauth_transport._wrapped
        # httpx.AsyncHTTPTransport stores the ssl context on the pool; a
        # disabled-verify pool's context has check_hostname=False.
        assert inner._pool._ssl_context.check_hostname is False

    def test_verify_true_by_default(self) -> None:
        client = make_client()
        reauth_transport = client._http._transport
        inner = reauth_transport._wrapped
        assert inner._pool._ssl_context.check_hostname is True
