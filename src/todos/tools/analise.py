"""Tool de análise de processos SEI via LLM multimodal (LiteLLM).

Consolida o processo em PDF via sei_gerar_pdf_processo, comprime as imagens
opcionalmente via pymupdf/pillow, e envia ao LLM escolhido para produzir um
resumo estruturado pronto para triagem.

Sem `from __future__ import annotations`: o FastMCP introspecta os type hints em
tempo de execução para montar o schema de cada tool, então as anotações precisam
ser objetos reais (não strings adiadas).
"""

import base64
import io
import json
import logging
import os
import re
from typing import Any

from fastmcp import Context

from todos.exceptions import SEIError
from todos.mcp_app import (
    _READ,
    _json,
    _web_backend,
    mcp,
)

logger = logging.getLogger(__name__)

try:
    import litellm

    _LITELLM_AVAILABLE = True
except ImportError:
    _LITELLM_AVAILABLE = False

try:
    import fitz as _fitz  # pymupdf — compressão de imagens no PDF
    from PIL import Image as _PILImage

    _FITZ_AVAILABLE = True
except ImportError:
    _FITZ_AVAILABLE = False

_MAX_PDF_BYTES = 20 * 1024 * 1024  # 20 MB — limite de payload inline

# Parâmetros de compressão de imagens
_COMPRESS_MAX_DIM = 1200  # pixels — downscale se maior
_COMPRESS_JPEG_QUALITY = 50  # qualidade JPEG para gray/color
_COMPRESS_SKIP_SMALL = 150  # ignora imagens menores (ícones, bullets)
_COMPRESS_SCANNED_AREA_RATIO = 0.85  # fração da página coberta por imagem → escaneado
_COMPRESS_SCANNED_TEXT_MAX = 800  # chars — escasso texto reforça hipótese escaneado
_COMPRESS_BW_THRESHOLD = 128  # limiar para binarização em modo B&W

_FALLBACK_MODELS = [
    "gemini/gemini-2.5-flash-lite-preview-06-17",
    "gemini/gemini-2.0-flash-lite",
    "gemini/gemini-1.5-flash",
]

_PROMPT_BASE = """\
Você é um assistente jurídico. Analise o processo SEI/PJe consolidado em PDF.

IMPORTANTE — IDs dos documentos:
Cada documento no PDF possui rodapé com identificadores únicos:
- SEI: texto "SEI nº XXXXXXXX" (8 dígitos) — esse número é o id_documento
  para uso em sei_ler_documento caso seja necessário aprofundamento.
- PJe: ID de referência do tribunal no rodapé da página.
Para cada documento, anote tipo, SEI nº/ID PJe, página inicial e data.

Retorne JSON com exatamente estes campos:

{{
  "documentos": [
    {{"pagina": 1, "tipo": "Ofício", "sei_id": "73270751", "pje_id": null,
      "data": "2026-06-12", "signatario": "Nome Sobrenome"}}
  ],
  "resumo": "3-4 linhas: partes, pedido, natureza",
  "situacao_atual": "último ato; citar SEI nº do documento mais recente",
  "acao_necessaria": "o que deve ser feito agora",
  "prazo": "YYYY-MM-DD ou null",
  "documento_prazo": "SEI nº que estabelece o prazo, ou null",
  "comentario_triagem": "parágrafo formal; citar SEI nº relevantes entre parênteses"
}}

Não use markdown fora do JSON. Foco em ação, não em teoria jurídica.
{extra}\
"""

_ERR_SEM_KEY = (
    "Nenhuma {prefix}_API_KEY configurada. "
    "Defina {prefix}_API_KEY ou {prefix}_API_KEYS no ambiente."
)
_ERR_TODAS_KEYS_FALHARAM = "LLM falhou em todas as keys: {exc}"
_ERR_JSON_INVALIDO = "LLM não retornou JSON válido: {snippet!r}"
_ERR_RESPOSTA_VAZIA = "LLM retornou resposta vazia ou sem conteúdo (choices={choices!r})"
_ERR_SEM_LITELLM = "litellm não instalado. Execute: uv pip install 'todos-sei[llm]'"
_ERR_PDF_GRANDE = "PDF muito grande ({mb}MB > 20MB). Use sei_ler_documento seletivo para processos muito extensos."
_ERR_COMPRESSAO_INVALIDA = (
    "Valor inválido para compressao: {val!r}. Use: auto, bw, gray, color, nao."
)
_VALID_COMPRESSAO = frozenset({"auto", "bw", "gray", "color", "nao"})


# ── Compressão de PDF ─────────────────────────────────────────────────────────


def _is_scanned_page(page: Any, images: list | None = None) -> bool:
    """Return True when the page appears to be a scanned image rather than native digital text."""
    text = page.get_text().strip()
    imgs = images if images is not None else page.get_images(full=True)
    if not text:
        return bool(imgs)
    if imgs:
        page_area = page.rect.width * page.rect.height
        for img in imgs:
            rects = page.get_image_rects(img[0])
            if rects:
                img_area = rects[0].width * rects[0].height
                if (img_area / page_area) > _COMPRESS_SCANNED_AREA_RATIO and len(
                    text
                ) < _COMPRESS_SCANNED_TEXT_MAX:
                    return True
    return False


def _flatten_alpha(img: Any) -> Any:
    """Flatten RGBA/LA/P-transparent images onto a white RGB background."""
    if img.mode in {"RGBA", "LA"} or (img.mode == "P" and "transparency" in img.info):
        rgba = img.convert("RGBA")
        bg = _PILImage.new("RGB", img.size, (255, 255, 255))
        bg.paste(rgba, mask=rgba.split()[-1])
        return bg
    return img


def _encode_image(img: Any, mode: str) -> bytes:
    """Re-encode a PIL image to compressed bytes using the requested mode."""
    out = io.BytesIO()
    if mode == "bw":
        bw = img.convert("L").point(lambda x: 0 if x < _COMPRESS_BW_THRESHOLD else 255, mode="1")
        bw.save(out, format="TIFF", compression="group4")
    elif mode == "gray":
        img.convert("L").save(out, format="JPEG", quality=_COMPRESS_JPEG_QUALITY, optimize=True)
    else:
        img.convert("RGB").save(out, format="JPEG", quality=_COMPRESS_JPEG_QUALITY, optimize=True)
    return out.getvalue()


def _select_mode(requested: str, page_type: str, img_mode: str) -> str:
    """Choose final compression mode for an image given page classification and PIL mode."""
    if requested != "auto":
        return requested
    if page_type == "scanned":
        return "bw"
    return "gray" if img_mode in {"L", "1"} else "color"


def _compress_pdf(pdf_bytes: bytes, *, mode: str = "auto") -> bytes:
    """Compress PDF images (downscale + re-encode) to reduce LLM payload.

    mode: "auto" detects scanned vs digital pages automatically;
          "bw"/"gray"/"color" force an encoding for all images.
    Returns original bytes unchanged if compression would increase size.
    """
    with _fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
        image_to_page: dict[int, int] = {}
        page_classifications: dict[int, str] = {}
        for page_idx in range(len(doc)):
            imgs = doc[page_idx].get_images(full=True)
            if mode == "auto":
                page_classifications[page_idx] = (
                    "scanned" if _is_scanned_page(doc[page_idx], imgs) else "digital"
                )
            for img in imgs:
                xref = img[0]
                if xref not in image_to_page:
                    image_to_page[xref] = page_idx

        for xref, page_idx in image_to_page.items():
            try:
                base_image = doc.extract_image(xref)
                w, h = base_image["width"], base_image["height"]
                if w <= _COMPRESS_SKIP_SMALL and h <= _COMPRESS_SKIP_SMALL:
                    continue
                img = _PILImage.open(io.BytesIO(base_image["image"]))
                if w > _COMPRESS_MAX_DIM or h > _COMPRESS_MAX_DIM:
                    img.thumbnail(
                        (_COMPRESS_MAX_DIM, _COMPRESS_MAX_DIM), _PILImage.Resampling.LANCZOS
                    )
                img = _flatten_alpha(img)
                current = _select_mode(
                    mode, page_classifications.get(page_idx, "digital"), img.mode
                )
                doc[page_idx].replace_image(xref, stream=_encode_image(img, current))
            except (OSError, RuntimeError, ValueError) as exc:
                logger.warning("PDF compression: skipping image xref=%d: %s", xref, exc)

        result = doc.tobytes(garbage=4, deflate=True, clean=True)

    if len(result) >= len(pdf_bytes):
        return pdf_bytes
    return result


# ── LiteLLM helpers ───────────────────────────────────────────────────────────


def _provider_prefix(modelo: str) -> str:
    """Extrai o prefixo do provider a partir do nome do modelo LiteLLM."""
    return modelo.split("/", 1)[0].upper() if "/" in modelo else "GEMINI"


def _load_provider_keys(modelo: str) -> list[str]:
    """Retorna lista de API keys para o provider do modelo dado."""
    prefix = _provider_prefix(modelo)
    multi = os.getenv(f"{prefix}_API_KEYS", "")
    if multi:
        return [k.strip() for k in multi.split(",") if k.strip()]
    single = os.getenv(f"{prefix}_API_KEY", "")
    return [single] if single else []


def _extract_json(raw: str) -> dict:
    """Extrai dict do texto retornado pelo LLM (JSON puro ou envolvido em markdown)."""
    raw = raw.strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass
    msg = _ERR_JSON_INVALIDO.format(snippet=raw[:200])
    raise SEIError(msg)


async def _call_llm(
    pdf_bytes: bytes,
    prompt: str,
    modelo: str,
    *,
    request_timeout: float,
) -> str:
    """Envia pdf_bytes ao LLM via LiteLLM com fallback de modelo e rotação de keys."""
    keys = _load_provider_keys(modelo)
    if not keys:
        prefix = _provider_prefix(modelo)
        msg = _ERR_SEM_KEY.format(prefix=prefix)
        raise SEIError(msg)

    pdf_b64 = base64.b64encode(pdf_bytes).decode()
    messages = [
        {
            "role": "user",
            "content": [
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:application/pdf;base64,{pdf_b64}"},
                },
                {"type": "text", "text": prompt},
            ],
        }
    ]
    fallbacks = [m for m in _FALLBACK_MODELS if m != modelo]

    last_exc: Exception | None = None
    for key in keys:
        try:
            resp = await litellm.acompletion(
                model=modelo,
                messages=messages,
                api_key=key,
                fallbacks=fallbacks,
                timeout=request_timeout,
            )
            if not resp.choices or resp.choices[0].message.content is None:
                msg = _ERR_RESPOSTA_VAZIA.format(choices=resp.choices)
                raise SEIError(msg)
            return resp.choices[0].message.content
        except litellm.RateLimitError as exc:
            logger.warning("LiteLLM RateLimitError (key=...%s): %s", key[-4:], exc)
            last_exc = exc
        except litellm.APIError as exc:
            logger.warning("LiteLLM APIError (key=...%s): %s", key[-4:], exc)
            last_exc = exc
    msg = _ERR_TODAS_KEYS_FALHARAM.format(exc=last_exc)
    raise SEIError(msg)


@mcp.tool(annotations=_READ)
async def sei_analisar_processo(
    processo: str,
    prompt_extra: str = "",
    modelo: str = "gemini/gemini-2.5-flash",
    timeout: float = 60.0,
    compressao: str = "auto",
    ctx: Context | None = None,
) -> str:
    """Analisa um processo SEI usando LLM multimodal, retornando resumo estruturado.

    Consolida todos os documentos do processo em PDF (via sei_gerar_pdf_processo),
    opcionalmente comprime as imagens para reduzir o payload, e envia ao LLM.
    Útil para triagem e diagnóstico rápido sem precisar ler documento por documento.

    Parâmetros:
    - processo: número SEI formatado (ex: "0020.009007/2026-04")
    - prompt_extra: instruções adicionais (ex: "foque no cumprimento de sentença")
    - modelo: modelo LiteLLM — ex: "gemini/gemini-2.5-flash", "openai/gpt-4o",
              "anthropic/claude-opus-4-8"; padrão: "gemini/gemini-2.5-flash"
    - timeout: segundos máximos aguardando o LLM (padrão: 60s)
    - compressao: modo de compressão do PDF antes de enviar ao LLM:
        "auto"  — detecta páginas escaneadas (B&W CCITT-4) vs digitais (JPEG); padrão
        "bw"    — força CCITT Group 4 (mínimo tamanho, ideal para texto escaneado)
        "gray"  — força JPEG escala de cinza
        "color" — força JPEG preservando cores
        "nao"   — sem compressão (envia PDF original)
      Requer pymupdf (incluído em todos-sei[llm]).

    Requer: GEMINI_API_KEY (ou OPENAI_API_KEY / ANTHROPIC_API_KEY conforme o provider).
    Para múltiplas keys Gemini: GEMINI_API_KEYS=key1,key2,...
    `todos setup` pergunta pela(s) chave(s) Gemini opcionalmente (pode pular
    e configurar depois rodando o setup de novo, ou setando a env var
    diretamente) — gratuita em ai.google.dev (free tier).

    Retorno:
    {
      "numero_sei": str,
      "modelo_usado": str,
      "documentos": [...],       // [{pagina, tipo, sei_id, pje_id, data, signatario}]
      "resumo": str,
      "situacao_atual": str,
      "acao_necessaria": str,
      "prazo": str | null,
      "documento_prazo": str | null,
      "comentario_triagem": str,
      "_next": [...]
    }
    """
    if not _LITELLM_AVAILABLE:
        raise SEIError(_ERR_SEM_LITELLM)

    if compressao not in _VALID_COMPRESSAO:
        raise SEIError(_ERR_COMPRESSAO_INVALIDA.format(val=compressao))

    backend = await _web_backend(ctx)

    if ctx:
        await ctx.report_progress(0, 100, "Gerando PDF do processo…")

    pdf_bytes = await backend.gerar_pdf_processo(processo)

    if compressao != "nao":
        if _FITZ_AVAILABLE:
            if ctx:
                await ctx.report_progress(33, 100, "Comprimindo PDF…")
            original_size = len(pdf_bytes)
            pdf_bytes = _compress_pdf(pdf_bytes, mode=compressao)
            logger.info(
                "PDF comprimido: %.1fMB → %.1fMB (%.0f%%)",
                original_size / 1024 / 1024,
                len(pdf_bytes) / 1024 / 1024,
                (1 - len(pdf_bytes) / original_size) * 100,
            )
        else:
            logger.warning(
                "pymupdf não instalado — compressão desativada. Execute: uv pip install 'todos-sei[llm]'"
            )

    if len(pdf_bytes) > _MAX_PDF_BYTES:
        msg = _ERR_PDF_GRANDE.format(mb=len(pdf_bytes) // 1024 // 1024)
        raise SEIError(msg)

    if ctx:
        await ctx.report_progress(60, 100, "Enviando ao LLM para análise…")

    prompt = _PROMPT_BASE.format(extra=prompt_extra)
    raw_text = await _call_llm(pdf_bytes, prompt, modelo, request_timeout=timeout)
    parsed = _extract_json(raw_text)

    if ctx:
        await ctx.report_progress(100, 100)

    return _json(
        {
            "numero_sei": processo,
            "modelo_usado": modelo,
            **parsed,
            "_next": [f'sei_consultar_processo("{processo}")'],
        }
    )
