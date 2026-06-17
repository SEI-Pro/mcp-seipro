# RFC 0008 — Saída estruturada (structuredContent + outputSchema)

**Status**: Proposta
**Data**: 2026-06-17
**Autores**: Franklin Baldo (com Claude Code)
**Relacionado**: RFC 0007 (response shaping, Pydantic models em `responses.py`)
**Baseado em**: skill `mcp-coding` §structuredContent

---

## 0. Contexto

RFC 0007 §11 (Fora de escopo) registrou explicitamente:

> *"`structuredContent`/`outputSchema` ativo: a Fase 0 deixa os modelos prontos
> (adoção vira fiação), mas ligar depende de suporte estável do cliente — RFC 0008."*

Verificação de pré-condição: **FastMCP 3.4.2** (instalado) expõe `output_schema`
no decorador `@mcp.tool()` e, quando uma tool retorna um `BaseModel`, serializa
automaticamente em *dois* canais:

1. `content[0].text` — JSON compacto (retro-compatível com clientes antigos)
2. `structured_content` — dict tipado para clientes MCP que suportam o campo
3. `outputSchema` publicado no catálogo de tools (auto-inferido do tipo de retorno)

Isso realiza a promessa da RFC 0007: *"trocar `return _json(model.model_dump())`
por `return model` é só fiação"*.

---

## 1. Mudança principal

Para toda tool que já usa um shaper que retorna `BaseModel`:

```python
# ANTES
def _shape_resposta_escrita(result: dict, acao: str) -> dict:
    return RespostaEscrita(...).model_dump(exclude_none=True)

async def sei_criar_processo(...) -> str:
    return _json(_shape_resposta_escrita(result, "criar_processo"))

# DEPOIS
def _shape_resposta_escrita(result: dict, acao: str) -> RespostaEscrita:
    return RespostaEscrita(...)

async def sei_criar_processo(...) -> RespostaEscrita:
    return _shape_resposta_escrita(result, "criar_processo")
```

FastMCP recebe o modelo, gera `content[0].text = json.dumps(model.model_dump())`
E `structured_content = model.model_dump()` — sem código extra.

---

## 2. Plano de fases

### Fase 0 (esta) — Write tools: RespostaEscrita

As 6 tools de escrita que **sempre** retornam `RespostaEscrita` (sem branch `include_raw`):

| Tool | Módulo |
|---|---|
| `sei_criar_processo` | `tools/processos.py` |
| `sei_alterar_processo` | `tools/processos.py` |
| `sei_criar_documento` | `tools/documentos.py` |
| `sei_criar_documento_externo` | `tools/documentos.py` |
| `sei_alterar_documento_externo` | `tools/documentos.py` |
| `sei_incluir_documento_externo` | `tools/documentos.py` |

**Resultado**: 6 tools ganham `outputSchema` + `structured_content` automáticos.
Clientes antigos não percebem diferença (JSON string em `content[0].text` idêntico).

### Fase 1 — Read-heavy: ListaDocumentos, ProcessoDetalhe

Tools com `include_raw` branch: retornam `ListaDocumentos | str` quando
`include_raw=True` (raw JSON). Estratégia: tratar `include_raw=True` como
overload separado ou manter `str` nesse branch.

Afetadas: `sei_arvore_processo`, `sei_listar_documentos`, `sei_consultar_processo`,
`sei_listar_atividades`, `sei_pesquisar_processos`.

### Fase 2 — Catálogos paginados: Paginado

11 tools de catálogo e unidades retornam um dict com campos `Paginado`.
Migrar `_add_cursor()` para retornar o modelo `Paginado` e tools para `-> Paginado`.

---

## 3. Retro-compatibilidade

| Canal | Antes | Depois |
|---|---|---|
| `content[0].text` | `'{"acao":"criar_processo",...}'` | idêntico (FastMCP serializa) |
| `structured_content` | ausente | `{"acao":"criar_processo",...}` |
| `outputSchema` em `list_tools()` | ausente | JSON Schema do modelo |

Clientes que só lêem `content[0].text` não percebem nenhuma diferença.

---

## 4. Critérios de conclusão

| Critério | Verificação |
|---|---|
| 6 write tools retornam `RespostaEscrita` | `isinstance(result, RespostaEscrita)` nos testes |
| `ruff check . && ruff format --check .` limpos | CI |
| 547+ testes verdes | CI |
