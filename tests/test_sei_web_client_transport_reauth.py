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
        session_valid = {"v": False}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            if not session_valid["v"]:
                return httpx.Response(200, text=_LOGIN_PAGE, request=request)
            return httpx.Response(200, text="<html>conteúdo real</html>", request=request)

        wrapped = httpx.MockTransport(handler)
        login_calls = 0

        class _FakeClient:
            async def login(self) -> None:
                nonlocal login_calls
                login_calls += 1
                session_valid["v"] = True

        transport = _ReauthTransport(wrapped, _FakeClient())  # type: ignore[arg-type]

        async def run() -> httpx.Response:
            async with httpx.AsyncClient(transport=transport) as http:
                return await http.get("http://sei.test/x")

        response = asyncio.run(run())
        assert response.status_code == 200
        assert "conteúdo real" in response.text
        # initial (expired) + re-probe under the lock (still expired) + final retry (fresh)
        assert calls["n"] == 3
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
        # initial + re-probe under the lock + final retry — still bounded, not unbounded
        assert calls["n"] == 3
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


class TestReauthTransportAvoidsRedundantRelogin:
    def test_concurrent_expiry_detections_share_a_single_relogin(self) -> None:
        """Two requests that both observe an expired session concurrently
        must not each perform their own full login() — whichever one queues
        up behind `_reauth_lock` must re-probe (resend) rather than trust a
        snapshot taken before it waited, so it notices the session the first
        request just fixed and skips its own redundant login().

        The handler models a real dead-then-fixed session (`session_valid`),
        not an arbitrary call-count threshold — a request only ever gets a
        login page while the session is genuinely still dead, and the fake
        login() is what flips it valid. This is what makes the test able to
        catch a generation-*counter* implementation that only compares a
        snapshot against the current value: such a counter can't tell "the
        session was already fixed before I even asked" apart from "no fix
        has happened yet", because a coroutine that is scheduled late reads
        the post-fix counter value on its first (and only) read of it.
        Only an actual resend can tell the two apart, which is what this
        test would fail against if the retry logic went back to trusting a
        cheap in-memory snapshot instead of re-probing under the lock."""
        calls = {"n": 0}

        async def run() -> tuple[list[httpx.Response], int]:
            session_valid = {"v": False}

            async def handler(request: httpx.Request) -> httpx.Response:
                calls["n"] += 1
                if not session_valid["v"]:
                    await asyncio.sleep(0)  # let both requests observe the dead session
                    return httpx.Response(200, text=_LOGIN_PAGE, request=request)
                return httpx.Response(200, text="<html>conteúdo real</html>", request=request)

            wrapped = httpx.MockTransport(handler)
            login_calls = 0

            class _FakeClient:
                async def login(self) -> None:
                    nonlocal login_calls
                    login_calls += 1
                    await asyncio.sleep(0)
                    session_valid["v"] = True

            transport = _ReauthTransport(wrapped, _FakeClient())  # type: ignore[arg-type]
            async with httpx.AsyncClient(transport=transport) as http:
                responses = await asyncio.gather(
                    http.get("http://sei.test/a"), http.get("http://sei.test/b")
                )
            return responses, login_calls

        responses, login_calls = asyncio.run(run())
        assert all(r.status_code == 200 for r in responses)
        assert all("conteúdo real" in r.text for r in responses)
        assert login_calls == 1


class TestReauthTransportSuppressedDuringLogin:
    def test_initial_login_does_not_trigger_a_redundant_nested_login(self) -> None:
        """Regression test: SEIWebClient.login()'s own first GET to the login
        page naturally returns login-page HTML. Before login() suppressed
        _ReauthTransport around its own flow, the transport misread that as
        an expired session and ran the *entire* login flow a second time —
        doubling every login's network round trips (and latency)."""
        login_page = (
            '<html><body><form action="/sip/login.php" method="post">'
            '<input type="hidden" name="hdnAcao" value="1">'
            '<input name="txtUsuario"><input name="pwdSenha" type="password">'
            '<select name="selOrgao"><option value="1" selected>ORG</option></select>'
            '<input type="submit" name="sbmLogin" value="Acessar"></form></body></html>'
        )
        post_count = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            path = request.url.path
            if request.method == "GET" and path == "/sip/login.php":
                return httpx.Response(200, text=login_page, request=request)
            if request.method == "POST" and path == "/sip/login.php":
                post_count["n"] += 1
                return httpx.Response(
                    302,
                    headers={"Location": "http://sei.test/sei/inicializar.php"},
                    request=request,
                )
            if request.method == "GET" and path == "/sei/inicializar.php":
                return httpx.Response(
                    302,
                    headers={
                        "Location": (
                            "http://sei.test/sei/controlador.php"
                            "?acao=procedimento_controlar&infra_hash=abc123"
                        )
                    },
                    request=request,
                )
            if request.method == "GET" and path == "/sei/controlador.php":
                return httpx.Response(200, text="<html>inbox aqui</html>", request=request)
            return httpx.Response(200, text="<html>ok</html>", request=request)

        client = make_client()
        client._reauth_transport._wrapped = httpx.MockTransport(handler)

        asyncio.run(client.login())

        assert post_count["n"] == 1  # exactly one credential POST, not two
        assert client.is_authenticated
