#!/usr/bin/env python3
"""Bootstrap para o todos MCP (Desktop Extension).

Na primeira execucao, cria um venv em ~/.todos/.venv e instala
as dependencias. Nas execucoes seguintes, apenas executa o servidor.

Este script usa apenas a stdlib do Python.
"""

import os
import platform
import subprocess
import sys
from pathlib import Path

VENV_HOME = Path.home() / ".todos"
VENV_DIR = VENV_HOME / ".venv"
IS_WINDOWS = platform.system() == "Windows"
PYTHON = VENV_DIR / "Scripts" / "python.exe" if IS_WINDOWS else VENV_DIR / "bin" / "python"
TODOS = VENV_DIR / "Scripts" / "todos.exe" if IS_WINDOWS else VENV_DIR / "bin" / "todos"
SRC_DIR = Path(__file__).resolve().parent


def setup() -> None:
    """Cria venv e instala o pacote na primeira execucao."""
    sys.stderr.write("todos: configurando ambiente (primeira execucao)...\n")
    VENV_HOME.mkdir(parents=True, exist_ok=True)
    # S603: argv is [sys.executable (absolute interpreter path), "-m", "venv", str(VENV_DIR)].
    # All elements are hard-coded literals or Path constants — no user input involved.
    subprocess.run(
        [sys.executable, "-m", "venv", str(VENV_DIR)],
        check=True,
    )
    # S603: argv is [str(PYTHON), "-m", "pip", "install", "--quiet", str(SRC_DIR)].
    # PYTHON and SRC_DIR are Path constants derived from __file__ and VENV_DIR — not user input.
    subprocess.run(
        [str(PYTHON), "-m", "pip", "install", "--quiet", str(SRC_DIR)],
        check=True,
    )
    sys.stderr.write("todos: ambiente configurado.\n")


def main() -> None:
    """Run todos MCP, installing into a venv on first execution."""
    if not TODOS.exists():
        setup()

    if IS_WINDOWS:
        # No Windows, os.execv cria um processo novo e mata o atual.
        # O cliente MCP monitora o PID original e fecha ao detectar a saída.
        # subprocess.call mantém o processo-pai vivo enquanto o filho roda.
        # S603: executable is str(TODOS), a Path constant built from VENV_DIR (not user input).
        # sys.argv[1:] are pass-through args forwarded to the installed todos command.
        sys.exit(subprocess.call([str(TODOS), *sys.argv[1:]]))
    else:
        # S606/S603: TODOS is an absolute Path derived from VENV_DIR — not a partial or user-supplied path.
        # os.execv is used (not subprocess) to replace the process image and keep the original PID.
        os.execv(str(TODOS), [str(TODOS), *sys.argv[1:]])


if __name__ == "__main__":
    main()
