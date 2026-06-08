"""Diagnóstico de conectividade do SEI — REST (wssei) + frontend web.

Responde de forma objetiva: o MCP consegue alcançar o SEI, ou há um WAF
(Cloudflare) bloqueando na borda? Útil para revalidar após qualquer mudança
de infraestrutura na ANTAQ. Não imprime a senha.

Uso:  .venv/bin/python scripts/diag_conectividade.py
"""
import asyncio
import os
import sys

import httpx

ENV = {}
_env_path = os.path.join(os.path.dirname(__file__), "..", ".env")
if os.path.exists(_env_path):
    with open(_env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                ENV.setdefault(k, v)
ENV = {**ENV, **os.environ}

BASE = ENV.get("SEI_URL", "").rstrip("/")
ROOT = BASE.split("/sei/", 1)[0] if "/sei/" in BASE else BASE
USER = ENV.get("SEI_USUARIO", "")
SENHA = ENV.get("SEI_SENHA", "")
ORGAO = ENV.get("SEI_ORGAO", "0")
CONTEXTO = ENV.get("SEI_CONTEXTO", "")


def classify(resp):
    h = {k.lower(): v for k, v in resp.headers.items()}
    server = h.get("server", "?")
    if h.get("cf-mitigated", "").lower() == "challenge":
        return "CLOUDFLARE-CHALLENGE", server, h.get("cf-ray", "-")
    body = (resp.text or "")[:2000].lower()
    if "cloudflare" in server.lower() and ("just a moment" in body or "challenges.cloudflare.com" in body):
        return "CLOUDFLARE-CHALLENGE", server, h.get("cf-ray", "-")
    if "json" in h.get("content-type", ""):
        return "JSON(app)", server, h.get("cf-ray", "-")
    return f"HTTP {resp.status_code}", server, h.get("cf-ray", "-")


async def main():
    print(f"SEI_URL   = {BASE}")
    print(f"sei_root  = {ROOT}")
    print(f"usuario   = {USER}  orgao={ORGAO}  contexto={CONTEXTO!r}\n")
    if not BASE:
        print("SEI_URL não configurado.")
        sys.exit(2)

    probes = [
        ("REST POST /autenticar", "POST", f"{BASE}/autenticar",
         {"usuario": USER, "senha": SENHA, "orgao": ORGAO, "contexto": CONTEXTO}),
        ("WEB  GET  /sip/login.php", "GET", f"{ROOT}/sip/login.php", None),
    ]
    blocked = False
    async with httpx.AsyncClient(verify=False, timeout=30.0, follow_redirects=False) as c:
        for label, method, url, data in probes:
            try:
                r = await c.request(method, url, data=data)
                kind, server, ray = classify(r)
                flag = "🟥" if kind == "CLOUDFLARE-CHALLENGE" else "🟩"
                if kind == "CLOUDFLARE-CHALLENGE":
                    blocked = True
                print(f"{flag} {label:26} {r.status_code}  [{kind}] server={server} cf-ray={ray}")
            except Exception as e:
                print(f"⚠️  {label:26} ERRO {type(e).__name__}: {e}")

    print()
    if blocked:
        print("DIAGNÓSTICO: o SEI está atrás de um desafio do Cloudflare. As")
        print("requisições do MCP são barradas na borda, ANTES do wssei. O 403")
        print("ocorre com qualquer credencial. Correção é do lado da ANTAQ/infra:")
        print("regra de bypass no Cloudflare para /sei/modulos/wssei/ e /sip/login.php.")
        sys.exit(1)
    print("DIAGNÓSTICO: sem bloqueio de borda detectado — tráfego chega ao app.")


asyncio.run(main())
