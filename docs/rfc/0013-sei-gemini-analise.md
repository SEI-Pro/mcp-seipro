# RFC 0013 — Tool `sei_analisar_processo`: análise de processo via LiteLLM

- **Status:** Proposta
- **Autor:** Franklin Baldo
- **Data:** 2026-06-23 (rev. 2026-06-25)
- **RFCs relacionados:** RFC 0011 (documentos web parity), RFC 0009 (adopt pink practices)

---

## 1. Contexto

A análise de processos SEI por agentes hoje exige múltiplas chamadas MCP para ler
documentos individualmente. Para processos com muitos documentos, isso é:

- **Lento**: cada `sei_ler_documento` é um round-trip HTTP ao SEI
- **Impreciso**: documentos externos (PDFs escaneados) têm qualidade OCR variável
- **Custoso em contexto**: o agente acumula texto bruto de múltiplos documentos

A tool `sei_gerar_pdf_processo` já consolida todos os documentos num único PDF — mas
não há forma de analisá-lo automaticamente. Este RFC propõe preencher essa lacuna.

---

## 2. Proposta

Adicionar a tool `sei_analisar_processo` ao servidor `todos` (mcp-sei). A tool:

1. Chama internamente `sei_gerar_pdf_processo` para consolidar o processo
2. Envia o PDF ao LLM escolhido via **LiteLLM** com um prompt estruturado
3. Retorna análise estruturada pronta para uso pelo agente

Usar LiteLLM como camada de abstração evita lock-in com qualquer provider: o mesmo
código funciona com Gemini, OpenAI, Anthropic ou qualquer modelo suportado pelo
LiteLLM — basta trocar o parâmetro `modelo`.

---

## 3. Especificação da tool

```python
@mcp.tool(annotations=_READ)
async def sei_analisar_processo(
    processo: str,
    prompt_extra: str = "",
    modelo: str = "gemini/gemini-2.5-flash",
    timeout: float = 60.0,
) -> dict:
    """
    Analisa um processo SEI usando LLM multimodal, retornando resumo estruturado.

    Consolida todos os documentos do processo em PDF (via sei_gerar_pdf_processo)
    e envia ao LLM para análise. Útil para triagem e diagnóstico rápido de
    processos sem precisar ler documento por documento.

    Parâmetros:
    - processo: número SEI formatado (ex: "0020.009007/2026-04")
    - prompt_extra: instruções adicionais ao modelo (ex: "foque no cumprimento de sentença")
    - modelo: modelo LiteLLM a usar (ex: "gemini/gemini-2.5-flash", "openai/gpt-4o",
              "anthropic/claude-opus-4-8"); padrão: "gemini/gemini-2.5-flash"
    - timeout: segundos máximos para aguardar resposta do LLM (padrão: 60s)

    Retorno:
    {
      "numero_sei": str,
      "modelo_usado": str,
      "documentos": list[dict],       # [{pagina, tipo, sei_id, pje_id, data, signatario}]
      "resumo": str,
      "situacao_atual": str,
      "acao_necessaria": str,
      "prazo": str | None,            # ISO date se identificado
      "documento_prazo": str | None,  # SEI nº do documento que estabelece o prazo
      "comentario_triagem": str       # pronto para postar no Kanoê
    }

    Requer: API key do provider no ambiente (ex: GEMINI_API_KEY para Gemini,
    OPENAI_API_KEY para OpenAI). Para rotação com múltiplas keys Gemini,
    use GEMINI_API_KEYS (vírgula-separado).
    """
```

---

## 4. Implementação

### 4.1 Localização

`src/todos/tools/analise.py` — novo módulo no pacote `src/todos/tools/` existente,
importado em `server.py`.

### 4.2 Dependência

`litellm>=1.0` — adicionar a `pyproject.toml` como dependência opcional:

```toml
[project.optional-dependencies]
llm = ["litellm>=1.0"]
```

Instalação: `uv pip install "todos[llm]"` ou adicionar `litellm` ao grupo principal
se já usado em outros serviços do ecossistema.

**Importação lazy** para evitar que a ausência do pacote derrube os 126 tools ao
iniciar o servidor:

```python
try:
    import litellm
    _LITELLM_AVAILABLE = True
except ImportError:
    _LITELLM_AVAILABLE = False

# No corpo da tool:
if not _LITELLM_AVAILABLE:
    raise SEIError(
        "litellm não instalado. Execute: uv pip install 'todos[llm]'"
    )
```

### 4.3 Key loading

LiteLLM lê as keys do provider automaticamente das variáveis de ambiente padrão
(`GEMINI_API_KEY`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, etc.). Para rotação
entre múltiplas keys Gemini, usa-se `GEMINI_API_KEYS`:

```python
import os

def _load_provider_keys(modelo: str) -> list[str]:
    """Retorna lista de API keys para o provider do modelo dado."""
    provider = modelo.split("/")[0] if "/" in modelo else "gemini"
    multi = os.getenv(f"{provider.upper()}_API_KEYS", "")
    if multi:
        return [k.strip() for k in multi.split(",") if k.strip()]
    single = os.getenv(f"{provider.upper()}_API_KEY", "")
    return [single] if single else []
```

Configurar no ambiente do servidor ou via `pink setup` (RFC 0032 do pink).

### 4.4 Prompt base

```python
_PROMPT_BASE = """
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
  "acao_necessaria": "o que IPERON/PGE-IPERON deve fazer agora",
  "prazo": "YYYY-MM-DD ou null",
  "documento_prazo": "SEI nº que estabelece o prazo, ou null",
  "comentario_triagem": "parágrafo formal; citar SEI nº relevantes entre parênteses"
}}

Não use markdown fora do JSON. Foco em ação, não em teoria jurídica.
{extra}
"""
```

O campo `documentos` é a lista indexada de todos os documentos encontrados no PDF,
em ordem de aparição. Permite ao agente fazer `sei_ler_documento(id_documento=d["sei_id"])`
para aprofundamento seletivo sem precisar varrer a árvore do processo.

### 4.5 Chamada LiteLLM com key rotation e model fallback

LiteLLM suporta fallback de modelos nativamente via parâmetro `fallbacks`. A key
rotation é feita no loop externo — uma tentativa por key antes de passar ao próximo
modelo:

```python
import base64
import litellm

_FALLBACK_MODELS = [
    "gemini/gemini-2.5-flash-lite-preview-06-17",
    "gemini/gemini-2.0-flash-lite",
    "gemini/gemini-1.5-flash",
]

_MAX_PDF_BYTES = 20 * 1024 * 1024  # 20MB — limite de payload inline

async def _call_llm(
    pdf_bytes: bytes, prompt: str, modelo: str, *, timeout: float = 60.0
) -> str:
    """Envia pdf_bytes ao LLM via LiteLLM com fallback de modelo e rotação de keys."""
    keys = _load_provider_keys(modelo)
    if not keys:
        provider = modelo.split("/")[0] if "/" in modelo else "gemini"
        raise SEIError(
            f"Nenhuma {provider.upper()}_API_KEY configurada. "
            f"Defina {provider.upper()}_API_KEY ou {provider.upper()}_API_KEYS no ambiente."
        )
    if len(pdf_bytes) > _MAX_PDF_BYTES:
        raise SEIError(
            f"PDF muito grande ({len(pdf_bytes) // 1024 // 1024}MB > 20MB). "
            "Use sei_ler_documento seletivo para processos muito extensos."
        )

    pdf_b64 = base64.b64encode(pdf_bytes).decode()
    messages = [{
        "role": "user",
        "content": [
            {"type": "image_url", "image_url": {"url": f"data:application/pdf;base64,{pdf_b64}"}},
            {"type": "text", "text": prompt},
        ],
    }]
    fallbacks = [m for m in _FALLBACK_MODELS if m != modelo]

    last_exc: Exception | None = None
    for key in keys:
        try:
            resp = await litellm.acompletion(
                model=modelo,
                messages=messages,
                api_key=key,
                fallbacks=fallbacks,
                timeout=timeout,
            )
            return resp.choices[0].message.content
        except litellm.RateLimitError as exc:
            logger.warning("LiteLLM RateLimitError (key=...%s): %s", key[-4:], exc)
            last_exc = exc
        except litellm.APIError as exc:
            logger.warning("LiteLLM APIError (key=...%s): %s", key[-4:], exc)
            last_exc = exc

    raise SEIError(f"LLM falhou em todas as keys: {last_exc}")
```

**Vantagens sobre implementação manual com google-genai:**
- `litellm.acompletion` é nativo async — sem `asyncio.to_thread`
- `fallbacks` delega o fallback de modelo ao LiteLLM — sem loop duplo manual
- Trocar de provider é só mudar o parâmetro `modelo` (ex: `"openai/gpt-4o"`)
- `litellm.RateLimitError` e `litellm.APIError` cobrem todos os providers uniformemente

---

## 5. Critérios de aceitação

- [ ] `sei_analisar_processo("0020.009007/2026-04")` retorna JSON estruturado válido
- [ ] Key rotation automática em 429 RESOURCE_EXHAUSTED (tenta próxima key do mesmo provider)
- [ ] Model fallback via `fallbacks` do LiteLLM: falha no modelo primário tenta os seguintes da lista
- [ ] Erro descritivo se nenhuma key estiver configurada (SEIError imediato com nome da var de ambiente)
- [ ] Erro descritivo se todas as keys/modelos falharem (não levantar `Exception` genérica)
- [ ] `annotations=_READ` — tool é somente-leitura (não modifica nada no SEI)
- [ ] Documentação da tool menciona: formato `provider/modelo`, providers suportados, vars de ambiente
- [ ] Tamanho máximo de PDF: 20MB (limite de payload inline; acima disso retornar erro sugerindo `sei_ler_documento` seletivo)
- [ ] Funciona com pelo menos: `gemini/gemini-2.5-flash`, `openai/gpt-4o`, `anthropic/claude-opus-4-8`

---

## 6. Impacto em outros RFCs

- **RFC 0011** (documentos web parity): `sei_analisar_processo` complementa, não substitui,
  `sei_ler_documento`. Para documentos individuais o fluxo atual permanece.
- **pink RFC 0032**: pink consumirá esta tool via MCP para a feature `processo_analisar`.

---

## 7. Riscos e mitigações

| Risco | Mitigação |
|---|---|
| Quota free tier esgotada | Key rotation (`GEMINI_API_KEYS`) + model fallback via LiteLLM |
| PDFs > 20MB | Limite hardcoded em `_MAX_PDF_BYTES`; erro descritivo sugere `sei_ler_documento` seletivo |
| Dados sigilosos enviados a provider externo | Avisar na descrição da tool; configurar provider com data-residency ou usar modelo local via LiteLLM Ollama |
| Latência alta (PDF generation + LLM) | Tool assíncrona; parâmetro `timeout` (padrão 60s) repassado ao LiteLLM |
| Provider não suporta PDF multimodal | LiteLLM lança `litellm.UnsupportedParamsError` — surfaceado como SEIError descritivo |
