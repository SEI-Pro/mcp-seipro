"""HTTP-level tests for `todos.wizard.server` — the local wizard's routes.

`test_wizard_server.py` only unit-tests the two adapter functions
(`_validate_login`, `_save_password`) and explicitly does not spin up a
real HTTP server. This module fills that gap: it starts a real
`_ThreadedHTTPServer` bound to an ephemeral port in a background thread
(mirroring `run_wizard`'s own setup) and drives it with real HTTP requests
built by hand via `http.client` (so the `Host` header can be forged
deliberately — `HTTPConnection.request()` would otherwise always send the
correct one, making the host-check test impossible to write).
"""

from __future__ import annotations

import http.client
import json
import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import pytest

from todos.wizard import server as wizard_server

if TYPE_CHECKING:
    from collections.abc import Iterator


@dataclass
class _RunningWizard:
    port: int
    csrf: str
    handler: type[wizard_server._WizardHandler]
    done_event: threading.Event


def _request(
    port: int,
    method: str,
    path: str,
    *,
    headers: dict[str, str] | None = None,
    body: bytes = b"",
) -> tuple[int, bytes]:
    """Send a hand-built HTTP request so the `Host` header can be forged.

    A `Host` entry in `headers` overrides the default (matching) one — this
    is how the host-check test forges a mismatching header; `putrequest`'s
    own auto-`Host` is disabled (`skip_host=True`) so there is never a
    second, conflicting `Host` header on the wire.
    """
    headers = dict(headers or {})
    headers.setdefault("Host", f"127.0.0.1:{port}")
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        conn.connect()
        conn.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
        for key, value in headers.items():
            conn.putheader(key, value)
        if body:
            conn.putheader("Content-Type", "application/json")
            conn.putheader("Content-Length", str(len(body)))
        conn.endheaders(body or None)
        resp = conn.getresponse()
        data = resp.read()
        return resp.status, data
    finally:
        conn.close()


def _post_json(
    wizard: _RunningWizard,
    path: str,
    payload: dict[str, Any],
    *,
    csrf: str | None = "__use_valid__",
    host: str | None = None,
) -> tuple[int, bytes]:
    token = wizard.csrf if csrf == "__use_valid__" else csrf
    headers = {} if token is None else {"X-CSRF-Token": token}
    if host is not None:
        headers["Host"] = host
    return _request(wizard.port, "POST", path, headers=headers, body=json.dumps(payload).encode())


@pytest.fixture
def wizard(monkeypatch: pytest.MonkeyPatch) -> Iterator[_RunningWizard]:
    """Start a real wizard HTTP server on an ephemeral port for the test.

    `force` starts `False` (fresh-install default) — tests that need the
    `--force` behaviour flip `handler.force` directly since it is a class
    attribute read fresh on every request (same as `run_wizard`'s
    dynamically-built subclass).
    """
    # Never touch the real OS keyring or SEI network from these HTTP tests.
    monkeypatch.setattr(wizard_server._keyring, "get_password", lambda *_a, **_k: None)
    monkeypatch.setattr(wizard_server, "_read_existing_todos_env", lambda: None)

    csrf = "test-csrf-token"
    done = threading.Event()
    handler = type(
        "_TestWizardHandler",
        (wizard_server._WizardHandler,),
        {"csrf_token": csrf, "done_event": done, "port": 0, "force": False},
    )

    httpd = wizard_server._ThreadedHTTPServer(("127.0.0.1", 0), handler)
    port = httpd.server_address[1]
    handler.port = port  # only known after bind — patch the class attribute

    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield _RunningWizard(port=port, csrf=csrf, handler=handler, done_event=done)
    finally:
        httpd.shutdown()
        thread.join(timeout=5)
        httpd.server_close()


# ── Host header guard ──────────────────────────────────────────────────────


def test_wrong_host_header_rejected(wizard: _RunningWizard) -> None:
    status, _body = _request(wizard.port, "GET", "/api/csrf", headers={"Host": "evil.example.com"})
    assert status == 400


def test_correct_host_header_accepted(wizard: _RunningWizard) -> None:
    status, body = _request(wizard.port, "GET", "/api/csrf")
    assert status == 200
    assert json.loads(body) == {"csrf": wizard.csrf}


# ── CSRF guard ────────────────────────────────────────────────────────────


def test_post_without_csrf_token_rejected(wizard: _RunningWizard) -> None:
    status, _body = _post_json(wizard, "/done", {}, csrf=None)
    assert status == 403


def test_post_with_wrong_csrf_token_rejected(wizard: _RunningWizard) -> None:
    status, _body = _post_json(wizard, "/done", {}, csrf="not-the-right-token")
    assert status == 403


def test_post_with_valid_csrf_token_succeeds(wizard: _RunningWizard) -> None:
    status, body = _post_json(wizard, "/done", {})
    assert status == 200
    assert json.loads(body) == {"ok": True}
    # `_done` sets the event right after writing the response, so the
    # client may observe the reply before that happens — wait() instead of
    # is_set() avoids a race against the server thread.
    assert wizard.done_event.wait(timeout=2)


# ── /done ───────────────────────────────────────────────────────────────────


def test_done_sets_done_event(wizard: _RunningWizard) -> None:
    assert not wizard.done_event.is_set()
    status, body = _post_json(wizard, "/done", {})
    assert status == 200
    assert json.loads(body)["ok"] is True
    # `_done` sets the event right after writing the response, so the
    # client may observe the reply before that happens — wait() instead of
    # is_set() avoids a race against the server thread.
    assert wizard.done_event.wait(timeout=2)


# ── /api/current-config ─────────────────────────────────────────────────────


def test_current_config_not_configured_when_env_empty(
    wizard: _RunningWizard, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(wizard_server, "_read_existing_todos_env", dict)
    status, body = _request(wizard.port, "GET", "/api/current-config")
    assert status == 200
    assert json.loads(body)["already_configured"] is False


def test_current_config_already_configured_when_env_populated(
    wizard: _RunningWizard, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        wizard_server,
        "_read_existing_todos_env",
        lambda: {
            "SEI_USUARIO": "fulano",
            "SEI_WEB_URL": "https://sei.example.gov.br",
            "SEI_SIGLA_ORGAO": "RO",
        },
    )
    status, body = _request(wizard.port, "GET", "/api/current-config")
    assert status == 200
    result = json.loads(body)
    assert result["already_configured"] is True
    assert result["sei_usuario"] == "fulano"
    assert result["sei_sigla_orgao"] == "RO"


def test_current_config_force_flag_suppresses_already_configured(
    wizard: _RunningWizard, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        wizard_server,
        "_read_existing_todos_env",
        lambda: {"SEI_USUARIO": "fulano", "SEI_WEB_URL": "https://sei.example.gov.br"},
    )
    wizard.handler.force = True
    try:
        status, body = _request(wizard.port, "GET", "/api/current-config")
        assert status == 200
        assert json.loads(body)["already_configured"] is False
    finally:
        wizard.handler.force = False


# ── Static file serving ─────────────────────────────────────────────────────


def test_root_path_serves_index_html(wizard: _RunningWizard) -> None:
    status, body = _request(wizard.port, "GET", "/")
    assert status == 200
    assert b"<title>TOdos" in body


def test_static_path_traversal_rejected(wizard: _RunningWizard) -> None:
    # Escapes `wizard/static/` to a sibling file (`wizard/server.py`) that
    # genuinely exists — proves the guard checks path *containment*, not
    # merely file existence.
    status, _body = _request(wizard.port, "GET", "/../server.py")
    assert status == 403
