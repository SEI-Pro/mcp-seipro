# RFC 0013 — Tool `sei_analisar_processo`: análise via Gemini API

- **Status:** Proposta
- **Autor:** Franklin Baldo
- **Data:** 2026-06-23
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
2. Envia o PDF ao **Gemini API** (Google) com um prompt estruturado
3. Retorna análise estruturada pronta para uso pelo agente

---

## 3. Especificação da tool

```python
@mcp.tool(annotations=_READ)
async def sei_analisar_processo(
    processo: str,
    prompt_extra: str = "",
    modelo: str = "gemini-2.5-flash",
    timeout: float = 60.0,
) -> dict:
    """
    Analisa um processo SEI usando Gemini API, retornando resumo estruturado.

    Consolida todos os documentos do processo em PDF (via sei_gerar_pdf_processo)
    e envia ao Gemini para análise. Útil para triagem e diagnóstico rápido de
    processos sem precisar ler documento por documento.

    Parâmetros:
    - processo: número SEI formatado (ex: "0020.009007/2026-04")
    - prompt_extra: instruções adicionais ao modelo (ex: "foque no cumprimento de sentença")
    - modelo: modelo Gemini a usar (padrão: gemini-2.5-flash)
    - timeout: segundos máximos para aguardar resposta do Gemini (padrão: 60s)

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

    Requer: GEMINI_API_KEY ou GEMINI_API_KEYS no ambiente.
    Fallback: tenta múltiplas keys/modelos antes de falhar.
    """
```

---

## 4. Implementação

### 4.1 Localização

`src/todos/tools/analise.py` — novo módulo, importado em `server.py`.

### 4.2 Dependência

`google-genai>=1.0` — adicionar a `pyproject.toml` como dependência opcional
ou no grupo principal (já usado em outros serviços do ecossistema).

**Importação em `analise.py` deve ser lazy** para evitar que um `ImportError` em startup
derrube os 126 tools existentes quando a dependência não está instalada:

```python
try:
    from google import genai
    from google.genai import types
    _GENAI_AVAILABLE = True
except ImportError:
    _GENAI_AVAILABLE = False

# No corpo da tool:
if not _GENAI_AVAILABLE:
    raise SEIError("google-genai não instalado. Adicione 'google-genai>=1.0' ao pyproject.toml.")
```

### 4.3 Key loading

```python
import os

def _load_gemini_keys() -> list[str]:
    keys_env = os.getenv("GEMINI_API_KEYS", "")
    if keys_env:
        return [k.strip() for k in keys_env.split(",") if k.strip()]
    single = os.getenv("GEMINI_API_KEY", "")
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

### 4.5 Key rotation e model fallback

```python
_MODELS_FREE_TIER = [
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite-preview-06-17",
    "gemini-2.0-flash-lite",
    "gemini-1.5-flash",
]

_MAX_PDF_BYTES = 20 * 1024 * 1024  # 20MB — limite da API inline do Gemini

async def _call_gemini(
    pdf_bytes: bytes, prompt: str, modelo: str, *, timeout: float = 60.0
) -> str:
    """Envia pdf_bytes ao Gemini com fallback de modelo e rotação de keys."""
    keys = _load_gemini_keys()
    if not keys:
        raise SEIError(
            "Nenhuma GEMINI_API_KEY configurada. "
            "Defina GEMINI_API_KEY ou GEMINI_API_KEYS no ambiente."
        )
    if len(pdf_bytes) > _MAX_PDF_BYTES:
        raise SEIError(
            f"PDF muito grande ({len(pdf_bytes) // 1024 // 1024}MB > 20MB). "
            "Use sei_ler_documento seletivo para processos muito extensos."
        )
    # Pré-cria um client por key — evita instanciar N×M clientes no loop
    clients = {key: genai.Client(api_key=key) for key in keys}
    models = [modelo] + [m for m in _MODELS_FREE_TIER if m != modelo]
    last_exc: Exception | None = None
    for m in models:
        for key, client in clients.items():
            try:
                resp = await asyncio.wait_for(
                    asyncio.to_thread(
                        client.models.generate_content,
                        model=m,
                        contents=[
                            types.Part.from_bytes(data=pdf_bytes, mime_type="application/pdf"),
                            prompt,
                        ],
                    ),
                    timeout=timeout,
                )
                return resp.text
            except asyncio.TimeoutError as exc:
                logger.warning("Gemini timeout após %.0fs (model=%s)", timeout, m)
                last_exc = exc
            except genai.errors.APIError as exc:
                logger.warning("Gemini APIError (model=%s key=...%s): %s", m, key[-4:], exc)
                last_exc = exc
    raise SEIError(f"Gemini falhou em todos os modelos/keys: {last_exc}")
```

---

## 5. Critérios de aceitação

- [ ] `sei_analisar_processo("0020.009007/2026-04")` retorna JSON estruturado válido
- [ ] Key rotation automática em 429 RESOURCE_EXHAUSTED
- [ ] Model fallback: falha no modelo primário tenta os seguintes da lista
- [ ] Erro descritivo se nenhuma key estiver configurada (`GEMINI_API_KEY` ausente → SEIError imediato, não "None")
- [ ] Erro descritivo se todas as keys/modelos falharem (não levantar `Exception` genérica)
- [ ] `annotations=_READ` — tool é somente-leitura (não modifica nada no SEI)
- [ ] Documentação da tool menciona: requisito de `GEMINI_API_KEY`, modelos suportados, formato do retorno
- [ ] Tamanho máximo de PDF: 20MB (limite da API inline do Gemini; acima disso retornar erro sugerindo `sei_ler_documento` seletivo)

---

## 6. Impacto em outros RFCs

- **RFC 0011** (documentos web parity): `sei_analisar_processo` complementa, não substitui,
  `sei_ler_documento`. Para documentos individuais o fluxo atual permanece.
- **pink RFC 0032**: pink consumirá esta tool via MCP para a feature `processo_analisar`.

---

## 7. Riscos e mitigações

| Risco | Mitigação |
|---|---|
| Quota free tier esgotada | Key rotation + model fallback; recomendar billing ativo |
| PDFs > 20MB enviados à API externa | Limite de 20MB hardcoded em `_MAX_PDF_BYTES`; erro descritivo sugere `sei_ler_documento` seletivo |
| Dados sigilosos enviados ao Google | Avisar na descrição da tool; Gemini não usa dados para treino com billing ativo |
| Latência alta (PDF generation + LLM) | Tool assíncrona; parâmetro `timeout` (padrão 60s) repassado a `asyncio.wait_for` |
