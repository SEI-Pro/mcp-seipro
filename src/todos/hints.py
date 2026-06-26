"""Domain guidance hints injected into entry-point tool responses.

Built-in hints guide AI agents through the most common SEI workflows.
Override via SEI_HINTS env var with a JSON array of strings.
"""

from __future__ import annotations

import json
import logging

from todos.settings import get_settings

logger = logging.getLogger(__name__)

_DEFAULT_HINTS: list[str] = [
    "Após listar processos, use sei_consultar_processo com o protocolo_formatado para ver detalhes.",
    "Use sei_arvore_processo para listar os documentos de um processo.",
    "Use sei_ler_documento com o id do documento para ler o conteúdo.",
    "Para criar um processo, use sei_criar_processo. Descubra tipos com sei_pesquisar_tipos_processo.",
    "Para enviar um processo a outra unidade, use sei_enviar_processo.",
    "Na primeira sessão com uma nova instância SEI, chame sei_detectar_formato_protocolo para configurar automaticamente a validação de números de processo.",
]


def get_hints() -> list[str]:
    """Return hints from SEI_HINTS (JSON array of strings) or built-in defaults."""
    raw = get_settings().sei_hints.strip()
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                return [str(h) for h in parsed if str(h)]
        except (json.JSONDecodeError, ValueError) as exc:
            logger.warning("SEI_HINTS inválido — usando hints padrão: %s", exc)
    return _DEFAULT_HINTS
