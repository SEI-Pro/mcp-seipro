#!/usr/bin/env python3
"""Smoke test para o SEIWebClient — critério de aceite do PR #2.

Verifica login + ações simples em instâncias SEI sem mod-wssei (ex: SEI-RO).
Requer as mesmas variáveis de ambiente que o servidor MCP:
    SEI_USUARIO, SEI_SENHA, SEI_SIGLA_ORGAO (e SEI_WEB_URL ou SEI_URL)

Uso:
    python3 scripts/smoke_web.py
    python3 scripts/smoke_web.py --protocolo 0001.000001/2024-01
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from todos.sei_web_client import SEIWebClient, parse_inbox

_SEP = "=" * 60


def _out(msg: str) -> None:
    """Write a line to stdout."""
    sys.stdout.write(msg + "\n")


def _err(msg: str) -> None:
    """Write a line to stderr."""
    sys.stderr.write(msg + "\n")


async def _step_login(client: SEIWebClient) -> None:
    """Perform login and report result."""
    _out("\n[1] Login...")
    await client.login()
    _out("    ✓ Login OK")


async def _step_fetch_inbox(client: SEIWebClient) -> tuple[str, list]:
    """Fetch inbox and return (layout, rows)."""
    _out("\n[2] Listar processos (fetch_inbox)...")
    _, html = await client.fetch_inbox(detalhada=True)
    _out(f"    ✓ HTML recebido ({len(html):,} bytes)")
    _out("\n[3] Parsear inbox...")
    layout, rows = parse_inbox(html)
    _out(f"    ✓ Layout={layout!r}, {len(rows)} processos")
    return layout, rows


async def _step_consultar(client: SEIWebClient, protocolo: str) -> None:
    """Consult a single process and report result."""
    _out(f"\n[4] Consultar processo {protocolo!r}...")
    try:
        dados = await client.consultar_processo(protocolo)
        _out(f"    ✓ Tipo={dados.get('tipo_processo')!r}")
        _out(f"    ✓ Documentos={len(dados.get('documentos', []))}")
    except Exception as e:  # noqa: BLE001
        _out(f"    ✗ {e}")


async def _step_executar_acao(client: SEIWebClient, protocolo: str) -> None:
    """Execute a dry-run read-only action and report result."""
    _out(f"\n[5] executar_acao_processo dry-run ({protocolo!r})...")
    try:
        result = await client.executar_acao_processo(protocolo, "procedimento_visualizar")
        _out(f"    ✓ {result}")
    except RuntimeError as e:
        msg = str(e)
        if "não encontrada" in msg:
            _out(f"    ~ ação não disponível neste processo (ok): {msg[:80]}")
        else:
            _out(f"    ✗ {msg}")


async def smoke(protocolo: str | None) -> None:
    """Run the SEIWebClient smoke test suite."""
    _out(_SEP)
    _out("  SEI Web Client — Smoke Test")
    _out(_SEP)

    client = SEIWebClient()
    try:
        await _step_login(client)

        _, rows = await _step_fetch_inbox(client)
        if rows:
            primeiro = rows[0]
            protocolo_inbox = primeiro.get("protocolo", "")
            _out(f"    Primeiro processo: {protocolo_inbox}")
            if not protocolo:
                protocolo = protocolo_inbox

        if protocolo:
            await _step_consultar(client, protocolo)
        else:
            _out("\n[4] Pular consultar_processo (nenhum protocolo disponível)")

        # Usa a ação `procedimento_visualizar` (somente leitura — não altera nada).
        # Se não existir, apenas informa — não falha o smoke test.
        if protocolo:
            await _step_executar_acao(client, protocolo)
        else:
            _out("\n[5] Pular executar_acao_processo (nenhum protocolo)")

        _out("\n" + _SEP)
        _out("  Smoke test concluído com sucesso.")
        _out(_SEP)

    except Exception as e:  # noqa: BLE001
        _err(f"\n✗ FALHA: {e}")
        sys.exit(1)
    finally:
        await client.close()


def _check_env() -> None:
    """Verify required environment variables are set."""
    missing = [v for v in ("SEI_USUARIO", "SEI_SENHA") if not os.getenv(v)]
    if not (os.getenv("SEI_WEB_URL") or os.getenv("SEI_URL")):
        missing.append("SEI_WEB_URL ou SEI_URL")
    if missing:
        sys.stderr.write(f"Variáveis obrigatórias ausentes: {', '.join(missing)}\n")
        sys.exit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Smoke test SEIWebClient")
    parser.add_argument("--protocolo", default=None, help="Número SEI para testar ações")
    args = parser.parse_args()
    _check_env()
    asyncio.run(smoke(args.protocolo))
