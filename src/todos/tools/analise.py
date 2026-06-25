"""Tool de análise de processos SEI via LLM multimodal (LiteLLM).

Consolida o processo em PDF via sei_gerar_pdf_processo e envia ao LLM
escolhido para produzir um resumo estruturado pronto para triagem.

Sem `from __future__ import annotations`: o FastMCP introspecta os type hints em
tempo de execução para montar o schema de cada tool, então as anotações precisam
ser objetos reais (não strings adiadas).
"""

import base64
import json
import logging
import os
import re

from fastmcp import Context

from todos.exceptions import SEIError
from todos.mcp_app import (
    _READ,
    _backend,
    _json,
    mcp,
)

logger = logging.getLogger(__name__)

try:
    import litellm

    _LITELLM_AVAILABLE = True
except ImportError:
    _LITELLM_AVAILABLE = False

_MAX_PDF_BYTES = 20 * 1024 * 1024  # 20 MB — limite de payload inline

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
_ERR_SEM_LITELLM = "litellm não instalado. Execute: uv pip install 'todos-sei[llm]'"
_ERR_PDF_GRANDE = "PDF muito grande ({mb}MB > 20MB). Use sei_ler_documento seletivo para processos muito extensos."


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
        return json.loads(m.group(1))
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
    ctx: Context | None = None,
) -> str:
    """Analisa um processo SEI usando LLM multimodal, retornando resumo estruturado.

    Consolida todos os documentos do processo em PDF (via sei_gerar_pdf_processo)
    e envia ao LLM para análise. Útil para triagem e diagnóstico rápido de
    processos sem precisar ler documento por documento.

    Parâmetros:
    - processo: número SEI formatado (ex: "0020.009007/2026-04")
    - prompt_extra: instruções adicionais (ex: "foque no cumprimento de sentença")
    - modelo: modelo LiteLLM — ex: "gemini/gemini-2.5-flash", "openai/gpt-4o",
              "anthropic/claude-opus-4-8"; padrão: "gemini/gemini-2.5-flash"
    - timeout: segundos máximos aguardando o LLM (padrão: 60s)

    Requer: GEMINI_API_KEY (ou OPENAI_API_KEY / ANTHROPIC_API_KEY conforme o provider).
    Para múltiplas keys Gemini: GEMINI_API_KEYS=key1,key2,...

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

    backend = await _backend(ctx)

    if ctx:
        await ctx.report_progress(0, 100, "Gerando PDF do processo…")

    pdf_bytes = await backend.gerar_pdf_processo(processo)

    if len(pdf_bytes) > _MAX_PDF_BYTES:
        msg = _ERR_PDF_GRANDE.format(mb=len(pdf_bytes) // 1024 // 1024)
        raise SEIError(msg)

    if ctx:
        await ctx.report_progress(50, 100, "Enviando ao LLM para análise…")

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
