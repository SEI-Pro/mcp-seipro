"""Runtime HTTP remoto do todos, isolado do servidor stdio."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING

import anyio
import uvicorn
from starlette.responses import HTMLResponse, Response
from starlette.routing import Route

from todos.auth import SEIProOAuthProvider, login_page, login_submit, validate_jwt_secret

if TYPE_CHECKING:
    from fastmcp import FastMCP
    from fastmcp.server.http import StarletteWithLifespan
    from starlette.requests import Request

logger = logging.getLogger(__name__)


_MAX_ICON_BYTES = 1 * 1024 * 1024  # 1 MB


def _icon_bytes() -> bytes:
    """Load the server icon, skipping files larger than _MAX_ICON_BYTES."""
    for candidate in (
        Path(__file__).resolve().parent.parent.parent / "icon.png",
        Path("/app/icon.png"),
    ):
        if not candidate.exists():
            continue
        stat = candidate.stat()
        if stat.st_size > _MAX_ICON_BYTES:
            logger.warning(
                "Ícone ignorado: arquivo muito grande (%d bytes): %s",
                stat.st_size,
                candidate,
            )
            continue
        return candidate.read_bytes()
    return b""


def build_remote_app(mcp: FastMCP, *, base_url: str) -> StarletteWithLifespan:
    """Monta o app HTTP/OAuth sem afetar o runtime stdio."""
    mcp.auth = SEIProOAuthProvider(base_url)
    app = mcp.http_app(path="/mcp", transport="http", stateless_http=False)
    icon = _icon_bytes()

    async def favicon(_request: Request) -> Response:
        if not icon:
            return Response(b"", status_code=404)
        return Response(
            icon,
            media_type="image/png",
            headers={"Cache-Control": "public, max-age=86400"},
        )

    root_html = f"""<!DOCTYPE html>
<html><head>
<link rel="icon" type="image/png" href="{base_url}/favicon.ico">
<link rel="icon" type="image/png" sizes="128x128" href="{base_url}/icon.png">
<link rel="apple-touch-icon" href="{base_url}/icon.png">
<title>todos MCP Server</title>
</head><body><h1>todos MCP Server</h1></body></html>"""

    async def root_page(_request: Request) -> HTMLResponse:
        return HTMLResponse(root_html)

    app.routes.insert(0, Route("/", root_page, methods=["GET"]))
    app.routes.insert(1, Route("/favicon.ico", favicon, methods=["GET"]))
    app.routes.insert(2, Route("/icon.png", favicon, methods=["GET"]))
    app.routes.insert(3, Route("/login", login_page, methods=["GET"]))
    app.routes.insert(4, Route("/login", login_submit, methods=["POST"]))
    return app


def run_remote(mcp: FastMCP, *, port: int) -> None:
    """Executa o app HTTP remoto."""
    validate_jwt_secret()
    base_url = os.environ.get("BASE_URL", f"http://localhost:{port}").rstrip("/")
    app = build_remote_app(mcp, base_url=base_url)
    # §34.1 — Default to 127.0.0.1 (loopback only). Cloud/Railway deployments that
    # need to bind to all interfaces should set SEI_HOST=0.0.0.0 explicitly.
    # MCP_HOST is accepted as a backwards-compatible alias (deprecated; use SEI_HOST).
    host = os.environ.get("SEI_HOST") or os.environ.get("MCP_HOST", "127.0.0.1")
    if host == "0.0.0.0":
        logger.warning(
            "Servidor MCP vinculado a 0.0.0.0 (todas as interfaces). "
            "Defina SEI_HOST=127.0.0.1 para restringir o acesso em ambientes locais."
        )
    config = uvicorn.Config(app, host=host, port=port, log_level="info")
    anyio.run(uvicorn.Server(config).serve)
