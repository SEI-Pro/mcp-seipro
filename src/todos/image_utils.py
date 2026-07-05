"""Helpers para embutir imagens como ``data:`` URI em seções de documento SEI.

Confirmado ao vivo em sei.sistemas.ro.gov.br, 2026-07-04 (ver
docs/rfc/0024-limite-tamanho-imagem-embutida-sei.md): o editor de seções do
SEI (``alterar_secoes_web``) **descarta silenciosamente** o parágrafo inteiro
que contém um ``<img src="data:...;base64,...">`` acima de um certo tamanho —
sem erro, sem aviso, `{"status": "ok"}` normalmente. Testado: uma imagem de
~104KB em base64 foi removida; uma de ~26KB sobreviveu. O limite exato não foi
determinado (está em algum ponto entre esses dois valores) — ``comprimir_para_embed``
mira bem abaixo da faixa testada como margem de segurança.

Requer Pillow, disponível apenas via o extra opcional ``llm`` (mesmo guard já
usado em ``tools/analise.py`` para pymupdf/Pillow — ver RFC 0019 §2.6).
"""

from __future__ import annotations

import base64
import io
import logging
from typing import TYPE_CHECKING, Any

from todos.exceptions import SEIValidationError

if TYPE_CHECKING:
    from pathlib import Path

logger = logging.getLogger(__name__)

try:
    from PIL import Image as _PILImage

    _PIL_AVAILABLE = True
except ImportError:
    _PIL_AVAILABLE = False

# Confirmado ao vivo: ~26.104 chars base64 (19.576 bytes JPEG) embutiu com
# sucesso; ~104KB base64 foi silenciosamente descartado pelo SEI. Mira bem
# abaixo dessa faixa testada, já que o limite exato não foi isolado.
DEFAULT_MAX_BASE64_CHARS = 30_000

# Sequência de tentativas (escala, qualidade JPEG) da menos para a mais
# agressiva — para de tentar assim que uma combinação couber no limite.
_TENTATIVAS = (
    (1.0, 70),
    (0.8, 60),
    (0.6, 45),
    (0.5, 35),
    (0.35, 25),
)


def _flatten_alpha(img: Any) -> Any:
    """Flatten RGBA/LA/P-transparent images onto a white RGB background.

    ``Image.convert("RGB")`` on a transparent image just discards the alpha
    channel — it does not composite it, so a transparent screenshot/print
    (a common case: browser screenshots frequently carry an alpha channel)
    would silently keep whatever garbage/black RGB values sat under the
    transparent pixels instead of a clean white background. Same fix already
    applied in ``tools/analise.py`` for the PDF-compression path.
    """
    if img.mode in {"RGBA", "LA"} or (img.mode == "P" and "transparency" in img.info):
        rgba = img.convert("RGBA")
        bg = _PILImage.new("RGB", img.size, (255, 255, 255))
        bg.paste(rgba, mask=rgba.split()[-1])
        return bg
    return img.convert("RGB")


def comprimir_para_embed(
    caminho: str | Path,
    *,
    max_base64_chars: int = DEFAULT_MAX_BASE64_CHARS,
) -> str:
    """Comprime uma imagem (PNG/JPEG/etc.) até caber no limite de embed do SEI.

    Tenta uma sequência de combinações (escala, qualidade JPEG), da mais leve
    para a mais agressiva, parando na primeira que produzir um base64 dentro
    de ``max_base64_chars``. Retorna o base64 (sem prefixo ``data:``) pronto
    para uso em ``<img src="data:image/jpeg;base64,{retorno}">``.

    Levanta ``SEIValidationError`` se nem a compressão mais agressiva couber
    no limite — nesse caso a imagem original provavelmente precisa ser
    redimensionada/cortada manualmente antes de tentar de novo.

    Requer Pillow (extra ``llm`` — ``uv sync --extra llm``); levanta
    ``SEIValidationError`` com essa instrução se não estiver instalado.
    """
    if not _PIL_AVAILABLE:
        msg = (
            "Pillow não está instalado — necessário para comprimir imagens "
            "para embed em documento SEI. Instale com `uv sync --extra llm`."
        )
        raise SEIValidationError(msg)

    img_original = _flatten_alpha(_PILImage.open(caminho))
    largura, altura = img_original.size

    for escala, qualidade in _TENTATIVAS:
        img = img_original
        if escala != 1.0:
            img = img_original.resize(
                (max(1, int(largura * escala)), max(1, int(altura * escala))),
                _PILImage.Resampling.LANCZOS,
            )
        buffer = io.BytesIO()
        img.save(buffer, "JPEG", quality=qualidade, optimize=True)
        b64 = base64.b64encode(buffer.getvalue()).decode("ascii")
        if len(b64) <= max_base64_chars:
            logger.debug(
                "comprimir_para_embed: escala=%.2f qualidade=%d -> %d chars base64",
                escala,
                qualidade,
                len(b64),
            )
            return b64

    msg = (
        f"Não foi possível comprimir {caminho} para caber em {max_base64_chars} "
        "chars base64 mesmo na configuração mais agressiva testada "
        f"({_TENTATIVAS[-1]}) — redimensione/corte a imagem manualmente antes "
        "de tentar de novo."
    )
    raise SEIValidationError(msg)


def montar_data_uri(
    caminho: str | Path, *, max_base64_chars: int = DEFAULT_MAX_BASE64_CHARS
) -> str:
    """Atalho: ``comprimir_para_embed`` + monta a data URI completa (``data:image/jpeg;base64,...``)."""
    b64 = comprimir_para_embed(caminho, max_base64_chars=max_base64_chars)
    return f"data:image/jpeg;base64,{b64}"
