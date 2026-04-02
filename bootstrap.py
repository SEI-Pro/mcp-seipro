#!/usr/bin/env python3
"""
Bootstrap para o MCP SEI Pro (Desktop Extension).

Na primeira execucao, cria um venv em ~/.mcp-seipropro/.venv e instala
as dependencias. Nas execucoes seguintes, apenas executa o servidor.

Este script usa apenas a stdlib do Python.
"""

import os
import platform
import subprocess
import sys
from pathlib import Path

VENV_HOME = Path.home() / ".mcp-seipropro"
VENV_DIR = VENV_HOME / ".venv"
IS_WINDOWS = platform.system() == "Windows"
PYTHON = VENV_DIR / "Scripts" / "python.exe" if IS_WINDOWS else VENV_DIR / "bin" / "python"
MCP_SEIPRO = VENV_DIR / "Scripts" / "mcp-seipro.exe" if IS_WINDOWS else VENV_DIR / "bin" / "mcp-seipro"
SRC_DIR = Path(__file__).resolve().parent


def setup():
    """Cria venv e instala o pacote na primeira execucao."""
    print("SEI Pro: configurando ambiente (primeira execucao)...", file=sys.stderr)
    VENV_HOME.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [sys.executable, "-m", "venv", str(VENV_DIR)],
        check=True,
    )
    subprocess.run(
        [str(PYTHON), "-m", "pip", "install", "--quiet", str(SRC_DIR)],
        check=True,
    )
    print("SEI Pro: ambiente configurado.", file=sys.stderr)


def main():
    if not MCP_SEIPRO.exists():
        setup()

    os.execv(str(MCP_SEIPRO), [str(MCP_SEIPRO)])


if __name__ == "__main__":
    main()
