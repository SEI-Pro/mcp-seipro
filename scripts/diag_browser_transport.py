"""Valida o transporte via browser (SEI_TRANSPORT=browser) ponta a ponta.

Sobe o Chromium, tenta autenticar e classifica o resultado:
  ✅ token            → tudo funcionando (servidor já corrigido + CF contornado)
  🟡 500 wssei        → transporte OK (passou o Cloudflare), mas wssei quebrado
                        no servidor (ex.: ConfiguracaoMdWSSEI ausente)
  🟥 Cloudflare       → o browser não conseguiu passar o desafio
Não imprime a senha. Requer: pip install ".[browser]" && playwright install chromium

Uso:  .venv/bin/python scripts/diag_browser_transport.py
"""
import asyncio
import os
import re
import sys

# carregar .env e forçar modo browser
for _line in open(os.path.join(os.path.dirname(__file__), "..", ".env")):
    _line = _line.strip()
    if _line and not _line.startswith("#") and "=" in _line:
        k, v = _line.split("=", 1)
        os.environ.setdefault(k, v)
os.environ["SEI_TRANSPORT"] = "browser"
os.environ.setdefault("SEI_BROWSER_HEADLESS", "true")

import httpx  # noqa: E402

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from src.mcp_seipro.sei_client import SEIClient, SEICloudflareBlocked  # noqa: E402


async def main():
    cli = SEIClient()
    code = 0
    try:
        token = await cli.autenticar()
        print(f"✅ AUTENTICOU via browser — token obtido ({token[:8]}...). Tudo operacional.")
    except SEICloudflareBlocked as e:
        print("🟥 Cloudflare AINDA barra mesmo via browser.")
        print("   " + str(e)[:200])
        code = 2
    except httpx.HTTPStatusError as e:
        body = e.response.text or ""
        m = re.search(r"Message:</strong>\s*([^<]+)", body)
        msg = m.group(1).strip() if m else f"HTTP {e.response.status_code}"
        print(f"🟡 Transporte OK — passou o Cloudflare e chegou ao wssei (status {e.response.status_code}).")
        print(f"   wssei respondeu: {msg}")
        if "ConfiguracaoMdWSSEI" in body:
            print("   → Bug de instalação do servidor (ANTAQ): recriar ConfiguracaoMdWSSEI.php.")
        code = 1
    except Exception as e:  # noqa: BLE001
        print(f"⚠️  Erro inesperado: {type(e).__name__}: {str(e)[:300]}")
        code = 3
    finally:
        await cli.close()
    sys.exit(code)


asyncio.run(main())
