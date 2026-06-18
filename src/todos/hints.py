"""Domain guidance hints injected into entry-point tool responses.

Built-in hints guide AI agents through the most common SEI workflows.
Override via SEI_HINTS env var with a JSON array of strings.
"""

from __future__ import annotations

import json
import os

_DEFAULT_HINTS: list[str] = [
    "Após listar processos, use sei_consultar_processo com o protocolo_formatado para ver detalhes.",
    "Use sei_arvore_processo para listar os documentos de um processo.",
    "Use sei_ler_documento com o id do documento para ler o conteúdo.",
    "Para criar um processo, use sei_criar_processo. Descubra tipos com sei_pesquisar_tipos_processo.",
    "Para enviar um processo a outra unidade, use sei_enviar_processo.",
]


def get_hints() -> list[str]:
    """Return hints from SEI_HINTS (JSON array of strings) or built-in defaults."""
    raw = os.getenv("SEI_HINTS", "").strip()
    if raw:
        try:
            parsed = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            return _DEFAULT_HINTS
        if isinstance(parsed, list):
            return [str(h) for h in parsed if str(h)]
    return _DEFAULT_HINTS
