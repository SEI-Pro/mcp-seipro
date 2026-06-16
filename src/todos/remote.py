"""Runtime HTTP remoto do todos, isolado do servidor stdio."""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

import anyio
import uvicorn
from starlette.responses import HTMLResponse, Response
from starlette.routing import Route

from todos.auth import SEIProOAuthProvider, login_page, login_submit

if TYPE_CHECKING:
    from fastmcp import FastMCP
    from fastmcp.server.http import StarletteWithLifespan
    from starlette.requests import Request


def _icon_bytes() -> bytes:
    for candidate in (
        Path(__file__).resolve().parent.parent.parent / "icon.png",
        Path("/app/icon.png"),
    ):
        if candidate.exists():
            return candidate.read_bytes()
    return b""


def build_remote_app(mcp: FastMCP, *, base_url: str) -> StarletteWithLifespan:
    """Monta o app HTTP/OAuth sem afetar o runtime stdio."""
    mcp.auth = SEIProOAuthProvider(base_url)
    app = mcp.http_app(path="/mcp", transport="http", stateless_http=False)
    icon = _icon_bytes()

    async def favicon(_request: Request) -> Response:
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
    if not os.environ.get("JWT_SECRET"):
        msg = "JWT_SECRET e obrigatorio no modo HTTP."
        raise RuntimeError(msg)
    base_url = os.environ.get("BASE_URL", f"http://localhost:{port}").rstrip("/")
    app = build_remote_app(mcp, base_url=base_url)
    config = uvicorn.Config(
        app, host=os.environ.get("SEI_HOST", "0.0.0.0"), port=port, log_level="info"
    )
    anyio.run(uvicorn.Server(config).serve)
