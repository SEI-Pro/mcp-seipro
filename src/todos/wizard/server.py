"""Local HTTP wizard server for `todos setup --web`.

Mirrors pink's wizard (`pink.wizard.server`, sibling repo) — same stdlib
`http.server` approach, same CSRF/host-check pattern, same masked-password
sentinel. Business logic (organ/mod-wssei detection, keyring writes, MCP
config deployment) is NOT reimplemented here: this module composes the
existing, already-tested helpers in :mod:`todos.setup_wizard` that are pure
enough to call from an HTTP handler (no Rich console output, no
``Confirm.ask``/``sys.exit``) — only the two steps that the CLI wizard
performs interactively (login validation, keyring write) get thin adapters
here that drop the terminal-specific side effects.
"""

from __future__ import annotations

import asyncio
import http.server
import json
import logging
import mimetypes
import secrets
import socket
import socketserver
import threading
import webbrowser
from pathlib import Path
from typing import Any

import httpx
import keyring as _keyring

from todos.backends.models import SEIWebClientConfig
from todos.exceptions import SEIAuthError, SEICredenciaisError, SEIError
from todos.sei_web_client import SEIWebClient
from todos.setup_wizard import (
    _build_mcp_env,
    _compute_keyring_user,
    _deploy_mcp_configs,
    _detect_modsei_url,
    _detect_organs,
    _read_existing_todos_env,
    _SEIConnConfig,
    _SEIInstanceConfig,
)

logger = logging.getLogger(__name__)

_STATIC_DIR = Path(__file__).parent / "static"
_MASKED = "__UNCHANGED__"
_KR_SERVICE = "todos-mcp"


def _find_free_port(preferred: int) -> int:
    with socket.socket() as s:
        try:
            s.bind(("127.0.0.1", preferred))
        except OSError:
            s.bind(("127.0.0.1", 0))
            return int(s.getsockname()[1])
        else:
            return preferred


async def _validate_login(conn: _SEIConnConfig) -> dict[str, Any]:
    """Attempt a real SEI login — the web-wizard equivalent of `_validate_credentials`.

    Same as `setup_wizard._validate_credentials`, minus the Rich console
    output and the `Confirm.ask`/`sys.exit` escape hatch (there's no TTY to
    prompt on here; a validation failure is just reported back as JSON and
    the user decides in the browser whether to save anyway).
    """
    client = SEIWebClient(
        SEIWebClientConfig(
            sei_web_url=conn.sei_root,
            sei_usuario=conn.usuario,
            sei_senha=conn.senha,
            sei_sigla_orgao=conn.sigla_orgao,
            sei_sigla_orgao_sistema=conn.sigla_orgao_sistema,
            sei_sigla_sistema=conn.sigla_sistema,
            sei_verify_ssl=not conn.verify_ssl_disabled,
        )
    )
    try:
        await client.ensure_authenticated()
        unidade: dict[str, Any] = {}
        try:
            unidade = await client.unidade_atual()
        except (SEIError, httpx.HTTPError, OSError) as exc:
            logger.warning("wizard: could not fetch unidade_atual during validation: %s", exc)
        return {
            "ok": True,
            "nome": client.nome_usuario or conn.usuario,
            "unidade_sigla": unidade.get("sigla") or "",
            "unidade_nome": unidade.get("nome") or "",
        }
    except SEICredenciaisError as exc:
        return {"ok": False, "error": f"Credenciais rejeitadas pelo SEI: {exc}"}
    except SEIAuthError as exc:
        return {"ok": False, "error": f"Falha de autenticação: {exc}"}
    except (SEIError, httpx.HTTPError, OSError, ValueError, RuntimeError) as exc:
        return {"ok": False, "error": f"Erro na validação: {exc}"}
    finally:
        client.limpar_senha()
        await client.close()


def _save_password(keyring_user: str, senha: str) -> tuple[bool, str]:
    """Keyring write — the web-wizard equivalent of `_save_password_to_keyring`.

    Same as `setup_wizard._save_password_to_keyring`, minus the Rich
    spinner/prompt. Returns (ok, error_message).
    """
    try:
        _keyring.set_password(_KR_SERVICE, keyring_user, senha)
    except (ImportError, OSError, RuntimeError, ValueError) as exc:
        logger.warning("wizard: keyring write failed for %s: %s", keyring_user, exc)
        return False, str(exc)
    return True, ""


class _WizardHandler(http.server.BaseHTTPRequestHandler):
    csrf_token: str = ""
    done_event: threading.Event
    port: int
    force: bool

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        pass  # suppress access log

    def _check_host(self) -> bool:
        host = self.headers.get("Host", "")
        allowed = {f"127.0.0.1:{self.port}", f"localhost:{self.port}"}
        return host in allowed

    def _check_csrf(self) -> bool:
        return self.headers.get("X-CSRF-Token") == self.csrf_token

    def _send_json(self, data: dict[str, Any], status: int = 200) -> None:
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self) -> dict[str, Any] | None:
        max_body = 1_048_576  # 1 MB
        length = int(self.headers.get("Content-Length", 0))
        if length > max_body:
            self.send_response(413)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"Request body too large")
            return None
        raw = self.rfile.read(length) if length else b"{}"
        try:
            return json.loads(raw)  # type: ignore[no-any-return]
        except (json.JSONDecodeError, ValueError):
            return {}

    def do_GET(self) -> None:
        if not self._check_host():
            self.send_response(400)
            self.end_headers()
            return

        if self.path == "/api/csrf":
            self._send_json({"csrf": self.csrf_token})
            return

        if self.path == "/api/current-config":
            self._current_config()
            return

        # Serve static files
        rel = self.path.lstrip("/") or "index.html"
        target = (_STATIC_DIR / rel).resolve()
        # Path traversal guard
        if not target.is_relative_to(_STATIC_DIR.resolve()):
            self.send_response(403)
            self.end_headers()
            return
        if not target.is_file():
            self.send_response(404)
            self.end_headers()
            return
        mime, _ = mimetypes.guess_type(str(target))
        body = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mime or "application/octet-stream")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _current_config(self) -> None:
        """Pre-populate the form from ~/.claude.json's mcpServers.todos.env, if configured.

        ``already_configured`` mirrors the CLI wizard's idempotency guard
        (``run_setup_wizard``'s ``force`` check): true only when a prior
        config exists AND the wizard was started without ``--force`` — the
        frontend uses it to show a "already configured, reconfigure anyway?"
        step instead of jumping straight into overwriting it.
        """
        result: dict[str, Any] = {
            "sei_url": "",
            "sei_web_url": "",
            "sei_usuario": "",
            "sei_senha": "",
            "sei_orgao": "",
            "sei_sigla_orgao": "",
            "sei_sigla_orgao_sistema": "",
            "sei_sigla_sistema": "",
            "already_configured": False,
        }
        env = _read_existing_todos_env()
        if env:
            result["already_configured"] = not self.force
            for key in (
                "sei_url",
                "sei_web_url",
                "sei_usuario",
                "sei_orgao",
                "sei_sigla_orgao",
                "sei_sigla_orgao_sistema",
                "sei_sigla_sistema",
            ):
                if env.get(key.upper()):
                    result[key] = env[key.upper()]
            usuario = env.get("SEI_USUARIO", "")
            sei_root = env.get("SEI_WEB_URL") or env.get("SEI_URL", "")
            if usuario and sei_root:
                try:
                    if _keyring.get_password(_KR_SERVICE, _compute_keyring_user(usuario, sei_root)):
                        result["sei_senha"] = _MASKED
                except (ImportError, OSError, RuntimeError) as exc:
                    logger.warning("wizard: keyring unavailable for pre-populate: %s", exc)
        self._send_json(result)

    def do_POST(self) -> None:
        if not self._check_host():
            self._send_json({"ok": False, "error": "bad host"}, 400)
            return
        if not self._check_csrf():
            self._send_json({"ok": False, "error": "csrf"}, 403)
            return

        routes = {
            "/api/detect-instance": self._detect_instance,
            "/api/validate": self._validate,
            "/api/save": self._save,
            "/done": self._done,
        }
        handler = routes.get(self.path)
        if handler is None:
            self.send_response(404)
            self.end_headers()
            return
        handler()

    # ── Route handlers ─────────────────────────────────────────────────────

    def _detect_instance(self) -> None:
        """Probe the given SEI URL for available organs + a mod-wssei REST endpoint."""
        body = self._read_body()
        if body is None:
            return
        sei_root = str(body.get("sei_url", "")).strip().rstrip("/")
        if not sei_root:
            self._send_json({"ok": False, "error": "URL do SEI é obrigatória"})
            return
        login_url = f"{sei_root}/sei/controlador.php?acao=login"

        organs: list[tuple[str, str]] = []
        verify_ssl_disabled = False
        try:
            organs, _sos, _ss = _detect_organs(login_url, "", "", verify_ssl=True)
        except (httpx.RequestError, httpx.HTTPStatusError) as exc:
            logger.warning("wizard: organ detection over TLS failed for %s: %s", sei_root, exc)

        modsei = _detect_modsei_url(sei_root, verify_ssl=not verify_ssl_disabled)
        self._send_json(
            {
                "ok": True,
                "organs": [{"id": val, "nome": nome} for val, nome in organs],
                "rest_url": modsei.url,
                "rest_url_confirmed": modsei.confirmed,
            }
        )

    def _validate(self) -> None:
        body = self._read_body()
        if body is None:
            return
        sei_root = str(body.get("sei_root", "")).strip().rstrip("/")
        usuario = str(body.get("sei_usuario", "")).strip()
        senha = str(body.get("sei_senha", ""))
        sigla_orgao = str(body.get("sei_sigla_orgao", ""))

        if senha == _MASKED:
            try:
                senha = (
                    _keyring.get_password(_KR_SERVICE, _compute_keyring_user(usuario, sei_root))
                    or ""
                )
            except (ImportError, OSError, RuntimeError) as exc:
                logger.warning("wizard: keyring unavailable during validation: %s", exc)
                self._send_json({"ok": True, "warn": "Credencial salva — não verificada online"})
                return

        if not sei_root or not usuario or not senha:
            self._send_json({"ok": False, "error": "Preencha URL, usuário e senha"})
            return

        conn = _SEIConnConfig(
            sei_root=sei_root,
            usuario=usuario,
            senha=senha,
            sigla_orgao=sigla_orgao,
            sigla_orgao_sistema=str(body.get("sei_sigla_orgao_sistema", "")),
            sigla_sistema=str(body.get("sei_sigla_sistema", "")),
            verify_ssl_disabled=bool(body.get("verify_ssl_disabled", False)),
        )
        result = asyncio.run(_validate_login(conn))
        self._send_json(result)

    def _save(self) -> None:
        body = self._read_body()
        if body is None:
            return

        sei_root = str(body.get("sei_web_url", "")).strip().rstrip("/")
        usuario = str(body.get("sei_usuario", "")).strip()
        senha_raw = str(body.get("sei_senha", ""))

        if not sei_root or not usuario:
            self._send_json({"ok": False, "error": "URL do SEI e usuário são obrigatórios"})
            return
        if not senha_raw:
            self._send_json({"ok": False, "error": "Senha é obrigatória"})
            return

        if senha_raw != _MASKED:
            ok, err = _save_password(_compute_keyring_user(usuario, sei_root), senha_raw)
            if not ok:
                self._send_json({"ok": False, "error": f"Falha ao gravar no Keyring: {err}"})
                return
        # senha_raw == _MASKED: an entry already exists in the keyring from a
        # prior save — nothing to write, the runtime client resolves it via
        # keyring_user the same way regardless of when it was stored.

        inst = _SEIInstanceConfig(
            sei_root=sei_root,
            rest_url=str(body.get("rest_url", "")),
            sigla_orgao=str(body.get("sei_sigla_orgao", "")),
            sigla_orgao_sistema=str(body.get("sei_sigla_orgao_sistema", "")),
            sigla_sistema=str(body.get("sei_sigla_sistema", "")),
            orgao_id=str(body.get("sei_orgao", "")),
            verify_ssl_disabled=bool(body.get("verify_ssl_disabled", False)),
        )
        # SEI_SENHA always stays empty in the deployed MCP env — the web
        # wizard never falls back to a plaintext env password (unlike the
        # CLI wizard's Confirm.ask escape hatch): a keyring write failure
        # above already returned an error instead of reaching this point.
        mcp_env, _unused = _build_mcp_env(inst, usuario, "")
        _deploy_mcp_configs(mcp_env, using_plaintext_password=False)

        self._send_json({"ok": True, "saved": [f"SEI ({usuario}) → keyring"]})

    def _done(self) -> None:
        self._send_json({"ok": True})
        self.done_event.set()


class _ThreadedHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True


def run_wizard(port: int = 7789, *, open_browser: bool = True, force: bool = False) -> None:
    """Start the local wizard server, open the browser, and wait for completion.

    ``force`` mirrors `run_setup_wizard`'s flag: when False (default) and a
    config already exists, the frontend shows an "already configured" step
    instead of silently walking into overwriting it.
    """
    port = _find_free_port(port)
    csrf = secrets.token_urlsafe(32)
    done = threading.Event()

    handler = type(
        "_H",
        (_WizardHandler,),
        {
            "csrf_token": csrf,
            "done_event": done,
            "port": port,
            "force": force,
        },
    )

    server = _ThreadedHTTPServer(("127.0.0.1", port), handler)

    def _timeout() -> None:
        done.set()

    timer = threading.Timer(600, _timeout)
    timer.daemon = True
    timer.start()

    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()

    url = f"http://127.0.0.1:{port}"
    if open_browser:
        webbrowser.open(url)

    try:
        while not done.is_set():
            done.wait(timeout=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        timer.cancel()
        server.shutdown()
