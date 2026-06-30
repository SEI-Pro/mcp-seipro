"""Transporte HTTP via browser real (Playwright/Chromium).

Motivação: quando o domínio do SEI está atrás de um desafio do Cloudflare
(Managed Challenge), clientes HTTP comuns (httpx) levam 403 na borda. Um
Chromium de verdade resolve o desafio automaticamente; roteando TODAS as
requisições por dentro da página (via fetch no mesmo contexto), IP, User-Agent,
fingerprint TLS e cookies ficam coerentes com o desafio resolvido — e o WAF
deixa passar.

`BrowserClient` expõe o subconjunto da interface de `httpx.AsyncClient` que o
`SEIClient` usa (`request`, `post`, `aclose`) e devolve objetos `httpx.Response`
reais, para compatibilidade total com o restante do código.

Ativação: `SEI_TRANSPORT=browser`. Requer o extra `playwright` instalado e o
Chromium baixado (`playwright install chromium`). Pesado — use como contingência
enquanto não há regra de bypass no WAF.

Limitações:
- Serializa as requisições (um único contexto de página + lock) — sem paralelismo
  real; mais lento que httpx.
- Respostas grandes trafegam como base64 via page.evaluate (ok para documentos,
  evite downloads enormes).
"""

from __future__ import annotations

import asyncio
import base64
import json as _json
import logging
import os
from typing import Any, Optional
from urllib.parse import urlencode

import httpx

logger = logging.getLogger(__name__)

# fetch genérico executado DENTRO da página (mesmo IP/UA/TLS/cookies do browser).
# Recebe url, method, headers, body (string ou null). Retorna status, headers e
# o corpo em base64 (preserva binário e qualquer charset).
_JS_FETCH = r"""
async ([url, method, headers, body]) => {
  const opt = { method, headers: headers || {} };
  if (body !== null && body !== undefined) opt.body = body;
  let r;
  try { r = await fetch(url, opt); }
  catch (e) { return { networkError: String(e) }; }
  const buf = await r.arrayBuffer();
  const bytes = new Uint8Array(buf);
  let bin = '';
  const CH = 0x8000;
  for (let i = 0; i < bytes.length; i += CH) {
    bin += String.fromCharCode.apply(null, bytes.subarray(i, i + CH));
  }
  const hdrs = {};
  r.headers.forEach((v, k) => { hdrs[k] = v; });
  return { status: r.status, headers: hdrs, b64: btoa(bin) };
}
"""

# fetch multipart: monta FormData (campos + 1 arquivo) dentro da página.
_JS_FETCH_MULTIPART = r"""
async ([url, method, headers, fields, fileField, fileName, fileB64, fileType]) => {
  const fd = new FormData();
  for (const k in fields) fd.append(k, fields[k]);
  const binStr = atob(fileB64);
  const arr = new Uint8Array(binStr.length);
  for (let i = 0; i < binStr.length; i++) arr[i] = binStr.charCodeAt(i);
  fd.append(fileField, new Blob([arr], { type: fileType || 'application/octet-stream' }), fileName);
  let r;
  try { r = await fetch(url, { method, headers: headers || {}, body: fd }); }
  catch (e) { return { networkError: String(e) }; }
  const buf = await r.arrayBuffer();
  const bytes = new Uint8Array(buf);
  let bin = '';
  const CH = 0x8000;
  for (let i = 0; i < bytes.length; i += CH) {
    bin += String.fromCharCode.apply(null, bytes.subarray(i, i + CH));
  }
  const hdrs = {};
  r.headers.forEach((v, k) => { hdrs[k] = v; });
  return { status: r.status, headers: hdrs, b64: btoa(bin) };
}
"""


class BrowserClient:
    """Adaptador que fala a interface do httpx.AsyncClient, mas roteia tudo por
    um Chromium real (Playwright). Inicialização preguiçosa (a primeira chamada
    sobe o browser e resolve o desafio do Cloudflare)."""

    def __init__(
        self,
        sei_url: str,
        *,
        user_agent: str = "",
        headless: Optional[bool] = None,
        verify: bool = True,
        extra_headers: Optional[dict] = None,
        nav_timeout_ms: int = 45000,
        challenge_timeout_s: int = 45,
    ) -> None:
        self.base_url = sei_url.rstrip("/")
        self.sei_root = self.base_url.split("/sei/", 1)[0] if "/sei/" in self.base_url else self.base_url
        self._ua = user_agent or (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36"
        )
        if headless is None:
            headless = os.environ.get("SEI_BROWSER_HEADLESS", "true").lower() != "false"
        self._headless = headless
        self._verify = verify
        self._extra_headers = extra_headers or {}
        self._nav_timeout = nav_timeout_ms
        self._challenge_timeout = challenge_timeout_s

        self._pw = None
        self._browser = None
        self._ctx = None
        self._page = None
        self._lock = asyncio.Lock()
        self._ready = False

    # ------------------------------------------------------------------
    # Ciclo de vida
    # ------------------------------------------------------------------

    async def _ensure(self) -> None:
        if self._ready:
            return
        async with self._lock:
            if self._ready:
                return
            try:
                from playwright.async_api import async_playwright
            except ImportError as e:
                raise RuntimeError(
                    "SEI_TRANSPORT=browser requer o pacote 'playwright'. "
                    "Instale com: pip install playwright && playwright install chromium"
                ) from e

            logger.info("Browser transport: subindo Chromium (headless=%s)...", self._headless)
            self._pw = await async_playwright().start()
            self._browser = await self._pw.chromium.launch(
                headless=self._headless,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                ],
            )
            self._ctx = await self._browser.new_context(
                user_agent=self._ua,
                locale="pt-BR",
                timezone_id="America/Sao_Paulo",
                viewport={"width": 1280, "height": 800},
                ignore_https_errors=not self._verify,
                extra_http_headers=self._extra_headers or None,
            )
            await self._ctx.add_init_script(
                "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"
            )
            self._page = await self._ctx.new_page()
            await self._solve_challenge()
            self._ready = True

    async def _solve_challenge(self) -> None:
        """Navega ao SEI e aguarda o desafio do Cloudflare ser resolvido."""
        target = f"{self.sei_root}/sei/"
        try:
            await self._page.goto(target, wait_until="domcontentloaded", timeout=self._nav_timeout)
        except Exception as e:  # noqa: BLE001 — navegação pode falhar e ainda assim resolver
            logger.warning("Browser transport: goto inicial falhou (%s); seguindo.", e)
        for i in range(self._challenge_timeout):
            try:
                title = (await self._page.title()) or ""
            except Exception:  # noqa: BLE001
                title = ""
            if "just a moment" not in title.lower() and i >= 2:
                logger.info("Browser transport: desafio resolvido (title=%r)", title)
                return
            await asyncio.sleep(1)
        logger.warning("Browser transport: desafio talvez não resolvido (title=%r)", title)

    async def aclose(self) -> None:
        try:
            if self._ctx:
                await self._ctx.close()
            if self._browser:
                await self._browser.close()
            if self._pw:
                await self._pw.stop()
        except Exception:  # noqa: BLE001
            pass
        finally:
            self._ready = False

    # ------------------------------------------------------------------
    # Interface estilo httpx.AsyncClient
    # ------------------------------------------------------------------

    async def request(
        self,
        method: str,
        url: str,
        *,
        params: Any = None,
        data: Any = None,
        json: Any = None,
        headers: Optional[dict] = None,
        files: Any = None,
        **_ignored: Any,
    ) -> httpx.Response:
        await self._ensure()
        full_url = url
        if params:
            qs = urlencode({k: v for k, v in params.items() if v is not None}, doseq=True)
            full_url = f"{url}?{qs}" if "?" not in url else f"{url}&{qs}"

        req_headers = dict(headers or {})

        async with self._lock:
            if files:
                result = await self._do_multipart(method, full_url, req_headers, data, files)
            else:
                body, ctype = self._encode_body(data, json)
                if ctype and "content-type" not in {k.lower() for k in req_headers}:
                    req_headers["Content-Type"] = ctype
                result = await self._page.evaluate(_JS_FETCH, [full_url, method, req_headers, body])
            # Retry uma vez se o WAF re-desafiar um fetch isolado.
            resp = self._build_response(method, full_url, result)
            if self._looks_challenge(resp):
                logger.info("Browser transport: re-desafio do WAF; re-resolvendo sessão.")
                await self._solve_challenge()
                if files:
                    result = await self._do_multipart(method, full_url, req_headers, data, files)
                else:
                    result = await self._page.evaluate(_JS_FETCH, [full_url, method, req_headers, body])
                resp = self._build_response(method, full_url, result)
        return resp

    async def post(self, url: str, **kwargs: Any) -> httpx.Response:
        return await self.request("POST", url, **kwargs)

    async def get(self, url: str, **kwargs: Any) -> httpx.Response:
        return await self.request("GET", url, **kwargs)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _encode_body(data: Any, json: Any) -> tuple[Optional[str], Optional[str]]:
        if json is not None:
            return _json.dumps(json), "application/json"
        if data is None:
            return None, None
        if isinstance(data, (str, bytes)):
            body = data.decode() if isinstance(data, bytes) else data
            return body, "application/x-www-form-urlencoded"
        # dict -> urlencoded
        return urlencode({k: ("" if v is None else v) for k, v in data.items()}), \
            "application/x-www-form-urlencoded"

    async def _do_multipart(self, method, url, headers, data, files) -> dict:
        # files no formato httpx: {"anexo": (nome, fileobj[, content_type])}
        field, spec = next(iter(files.items()))
        if isinstance(spec, (tuple, list)):
            file_name = spec[0]
            file_obj = spec[1]
            file_type = spec[2] if len(spec) > 2 else ""
        else:
            file_name, file_obj, file_type = "arquivo", spec, ""
        raw = file_obj.read() if hasattr(file_obj, "read") else file_obj
        if isinstance(raw, str):
            raw = raw.encode()
        file_b64 = base64.b64encode(raw).decode()
        fields = {k: ("" if v is None else str(v)) for k, v in (data or {}).items()}
        return await self._page.evaluate(
            _JS_FETCH_MULTIPART,
            [url, method, headers, fields, field, file_name, file_b64, file_type],
        )

    @staticmethod
    def _build_response(method: str, url: str, result: dict) -> httpx.Response:
        if not isinstance(result, dict) or "status" not in result:
            err = result.get("networkError") if isinstance(result, dict) else str(result)
            raise httpx.TransportError(f"Browser fetch falhou: {err}")
        content = base64.b64decode(result.get("b64", "")) if result.get("b64") else b""
        headers = result.get("headers", {}) or {}
        # O fetch() do browser JÁ descomprime o corpo (gzip/br/deflate) e o
        # arrayBuffer vem em claro. Remover headers que fariam o httpx tentar
        # descomprimir de novo (DecodingError) ou conferir um tamanho errado.
        drop = {"content-encoding", "content-length", "transfer-encoding"}
        clean = [(k, v) for k, v in headers.items() if k.lower() not in drop]
        request = httpx.Request(method, url)
        return httpx.Response(
            status_code=int(result["status"]),
            headers=clean,
            content=content,
            request=request,
        )

    @staticmethod
    def _looks_challenge(resp: httpx.Response) -> bool:
        if resp.headers.get("cf-mitigated", "").lower() == "challenge":
            return True
        if resp.status_code in (403, 429, 503):
            snippet = (resp.text or "")[:1500].lower()
            return "just a moment" in snippet or "challenges.cloudflare.com" in snippet
        return False
