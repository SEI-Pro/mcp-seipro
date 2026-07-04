"""Fronteira única de saída do CLI: resultado de tool vs mensagem para humano.

`emit_result` escreve em stdout, sem markup, para consumo por outro
processo/script (pipe, `--json`). `emit_human` escreve em stderr, com
markup Rich, para o usuário no terminal — mantém stdout limpo quando o
resultado de uma tool está sendo emitido na mesma invocação. Diagnóstico
(warnings, erros de parsing, estado inesperado) continua via `logger.*`,
não por aqui.
"""

from __future__ import annotations

import sys

from rich.console import Console

_console = Console(stderr=True)


def emit_result(text: str) -> None:
    """Escreve o resultado final de uma tool em stdout, para consumo por script."""
    sys.stdout.write(text + "\n")


def emit_human(message: str) -> None:
    """Escreve uma mensagem de status/erro em stderr, com markup Rich, para o usuário."""
    _console.print(message, soft_wrap=True)
