# RFC 0012 — Auditoria mcp-coding: Violações de Princípios no Servidor todos

**Status:** ✅ Concluída · **Atualizado**: 2026-06-19

- **P1** ✅: todas as 6 tools com `destructiveHint` ausente corrigidas para `_DEST` (`sei_enviar_processo`, `sei_concluir_processo`, `sei_assinar_documento`, `sei_assinar_bloco`, `sei_assinar_documentos_bloco`, `sei_conceder_credenciamento`)
- **P2** ✅: `suppress(Exception)` em `setup_wizard.py` corrigido para tipos estreitos com `logger.warning`
- **P3** ✅: cursor opaco em `sei_listar_processos`, `sei_listar_meus_acompanhamentos` e `sei_listar_acompanhamentos_unidade`
- **P4** ✅: limit/cursor em `sei_pesquisar_blocos_assinatura`, `sei_listar_documentos_bloco_assinatura`, `sei_pesquisar_contatos`, `sei_listar_processos_bloco_interno`
- **P5** ✅: descrições enriquecidas — `sei_dar_ciencia` (texto corrigido), `sei_excluir_marcador`, `sei_criar_bloco_interno`, `sei_remover_acompanhamento`
**Data:** 2026-06-19
**Referência:** skill `mcp-coding` (`.agents/skills/mcp-coding/SKILL.md`)

---

## Contexto

Este RFC documenta as violações dos princípios de design de tool MCP identificadas na auditoria do codebase `todos`, realizada contra os princípios definidos na skill `mcp-coding`. O objetivo é fornecer um mapa objetivo das correções necessárias, sem implementá-las.

---

## Diagnóstico

| # | Tool / Arquivo | Princípio violado | Severidade | Evidência |
|---|---|---|---|---|
| 1 | `sei_enviar_processo` — `server.py:614` | **Anotações incorretas** — `destructiveHint` ausente | Alta | `@mcp.tool(annotations=_WRITE)`. O CLAUDE.md documenta explicitamente que esta tool deve ter `destructiveHint=true`. `_WRITE = {"readOnlyHint": False, "idempotentHint": False}` não inclui `destructiveHint`. A tramitação é imediata, visível para outros usuários e remove o processo da inbox da unidade remetente. |
| 2 | `sei_concluir_processo` — `tools/processos.py:589` | **Anotações incorretas** — `destructiveHint` ausente e `idempotentHint` errado | Alta | `@mcp.tool(annotations=_IDEM)`. CLAUDE.md lista `sei_concluir_processo` entre as tools com `destructiveHint=true`. Além disso, concluir um processo não é idempotente: a segunda chamada tem efeito observável diferente (processo já estava concluído). |
| 3 | `sei_assinar_documento` — `tools/assinatura.py:122` | **Anotações incorretas** — `destructiveHint` ausente | Alta | `@mcp.tool(annotations=_IDEM)`. CLAUDE.md lista `sei_assinar_documento` entre as tools com `destructiveHint=true`. Assinatura é irreversível e tem efeito jurídico permanente. `_IDEM` é semanticamente errado: assinar duas vezes adiciona duas assinaturas. |
| 4 | `sei_assinar_bloco` — `tools/assinatura.py:168` | **Anotações incorretas** — `destructiveHint` ausente | Alta | `@mcp.tool(annotations=_IDEM)`. Mesmas razões que `sei_assinar_documento`. Assina todos os documentos do bloco de uma vez — ação jurídica irreversível. |
| 5 | `sei_assinar_documentos_bloco` — `tools/assinatura.py:191` | **Anotações incorretas** — `destructiveHint` ausente | Alta | `@mcp.tool(annotations=_IDEM)`. Mesmo padrão dos dois anteriores. |
| 6 | `sei_conceder_credenciamento` — `tools/credenciamento.py:31` | **Anotações incorretas** — `destructiveHint` ausente para operação de segurança | Alta | `@mcp.tool(annotations=_WRITE)`. Conceder credenciamento a um processo sigiloso concede acesso imediato a dados classificados. Deveria ser `_DEST` para forçar confirmação humana, como já ocorre com `sei_cassar_credenciamento`. A assimetria entre `_WRITE` (conceder) e `_DEST` (cassar) é inconsistente. |
| 7 | `setup_wizard.py:353` | **Erro design** — `suppress(Exception)` proibido | Média | `with contextlib.suppress(Exception): info["unidade"] = await web_client.unidade_atual()`. Captura toda e qualquer exceção incluindo `RuntimeError`, `SystemExit`, `KeyboardInterrupt`. A regra do CLAUDE.md proíbe `suppress(Exception)` sem log. O tipo estreito correto seria `suppress(SEIError, httpx.HTTPError)` com `logger.debug`. |
| 8 | `sei_pesquisar_blocos_assinatura` — `tools/blocos_assinatura.py:85` | **Descrição genérica** e **sem cursor** | Média | Descrição de uma linha, sem "quando usar", sem shape do retorno. Tem parâmetro `limit` mas não retorna `proximo_cursor`/`has_more`, violando o contrato de listas paginadas. |
| 9 | `sei_listar_documentos_bloco_assinatura` — `tools/blocos_assinatura.py:97` | **Descrição genérica** e **lista sem limit/cursor** | Média | Descrição de uma linha. Sem parâmetro `limit`, sem `proximo_cursor`. Um bloco pode conter centenas de documentos; lista ilimitada viola o token budget. |
| 10 | `sei_listar_meus_acompanhamentos` — `tools/acompanhamento.py:103` | **Paginação sem cursor opaco** | Média | Aceita `limit`/`pagina` mas retorna resultado sem `proximo_cursor`. O agente não consegue paginar automaticamente — precisa inferir `pagina+1` manualmente. |
| 11 | `sei_listar_acompanhamentos_unidade` — `tools/acompanhamento.py:120` | **Paginação sem cursor opaco** | Média | Mesmo padrão de `sei_listar_meus_acompanhamentos`. |
| 12 | `sei_pesquisar_contatos` — `tools/catalogos.py:305` | **Paginação sem cursor** e **descrição insuficiente** | Média | Aceita `limit` mas retorna resultado bruto sem `proximo_cursor`/`has_more`. Descrição de uma linha que omite: shape do retorno, quando usar vs. `sei_listar_usuarios`, o que é retornado como "id". |
| 13 | `sei_dar_ciencia` — `tools/assinatura.py:214` | **Descrição com texto truncado** | Média | A descrição termina abruptamente: `"instâncias sem mod-wssei. Tipo 'documento' exige REST."` — falta o início da frase. Sem shape do retorno, sem "quando NÃO usar". |
| 14 | `sei_listar_processos_bloco_interno` — `tools/blocos_internos.py:64` | **Lista sem limit/cursor** | Média | Sem parâmetros `limit`/`cursor`, sem `has_more` no retorno. Um bloco interno pode ter centenas de processos. |
| 15 | `sei_listar_processos` — `tools/processos.py:445` | **Paginação sem cursor opaco** | Baixa | Usa `pagina: int` diretamente, sem `cursor` opaco. Inconsistente com todas as outras tools paginadas (`sei_pesquisar_processos`, etc.) que já usam `_add_cursor`. |
| 16 | `sei_remover_acompanhamento` — `tools/acompanhamento.py:55` | **Descrição genérica** | Baixa | Descrição de uma linha. Sem "quando NÃO usar" vs. `sei_excluir_grupo_acompanhamento`, sem efeitos colaterais documentados. |
| 17 | `sei_criar_grupo_acompanhamento` — `tools/acompanhamento.py:66` | **Descrição genérica** | Baixa | Sem "quando usar vs. `sei_acompanhar_processo`", sem shape de retorno, sem exemplo de `nome`. |
| 18 | `sei_excluir_marcador` — `tools/marcadores.py:52` | **Descrição genérica** | Baixa | Sem "quando NÃO usar" vs. `sei_desativar_marcador`, sem efeitos colaterais (o que acontece com processos que tinham o marcador aplicado?). |
| 19 | `sei_criar_bloco_interno` — `tools/blocos_internos.py:18` | **Descrição insuficiente** | Baixa | Não documenta o fluxo completo (criar → incluir → concluir), não distingue de bloco de assinatura, sem shape de retorno. |

---

## Proposta

### P1 — Corrigir anotações `destructiveHint` em tools write/sign críticas (itens 1–6)

As seguintes tools devem ter suas anotações alteradas para incluir `destructiveHint=True`:

| Tool | Arquivo | De | Para |
|---|---|---|---|
| `sei_enviar_processo` | `server.py:614` | `_WRITE` | `_DEST` |
| `sei_concluir_processo` | `tools/processos.py:589` | `_IDEM` | `_DEST` |
| `sei_assinar_documento` | `tools/assinatura.py:122` | `_IDEM` | `_DEST` |
| `sei_assinar_bloco` | `tools/assinatura.py:168` | `_IDEM` | `_DEST` |
| `sei_assinar_documentos_bloco` | `tools/assinatura.py:191` | `_IDEM` | `_DEST` |
| `sei_conceder_credenciamento` | `tools/credenciamento.py:31` | `_WRITE` | `_DEST` |

Justificativa: `destructiveHint=True` permite que clientes como Claude Desktop solicitem confirmação humana antes de executar. O CLAUDE.md já documenta o comportamento esperado; as anotações devem refletir essa documentação.

### P2 — Corrigir `suppress(Exception)` em `setup_wizard.py:353` (item 7)

Substituir:
```python
with contextlib.suppress(Exception):
    info["unidade"] = await web_client.unidade_atual()
```
Por:
```python
with suppress(SEIError, httpx.HTTPError, OSError):
    logger.debug("setup_wizard: obtendo unidade atual (best-effort)")
    info["unidade"] = await web_client.unidade_atual()
```

### P3 — Adicionar cursor opaco às tools com paginação manual (itens 10, 11, 15)

- `sei_listar_meus_acompanhamentos` e `sei_listar_acompanhamentos_unidade`: adicionar parâmetro `cursor` e envolver resposta com `_add_cursor`, mantendo `pagina` internamente.
- `sei_listar_processos`: mesmo padrão, para consistência com demais tools paginadas.

### P4 — Adicionar `limit`/cursor a lists sem paginação (itens 8, 9, 12, 14)

- `sei_pesquisar_blocos_assinatura`: já tem `limit`, falta envolver com `_add_cursor` e retornar `proximo_cursor`.
- `sei_listar_documentos_bloco_assinatura`: adicionar `limit` (default 50) + `cursor`.
- `sei_pesquisar_contatos`: já tem `limit`, falta `_add_cursor`.
- `sei_listar_processos_bloco_interno`: adicionar `limit` + `cursor`.

### P5 — Enriquecer descrições das tools (itens 8, 13, 16–19 e demais com docstring de 1 linha)

Para cada tool listada, a descrição deve responder: o quê faz, quando usar, quando NÃO usar (especialmente se há tool irmã confusível), parâmetros com exemplos, shape do retorno, efeitos colaterais.

Prioridades de correção:
1. `sei_dar_ciencia` — corrigir o texto truncado (frase cortada) **(bug imediato)**
2. `sei_excluir_marcador` vs. `sei_desativar_marcador` — confusão mais provável
3. `sei_criar_bloco_interno` vs. `sei_criar_bloco_assinatura` — fluxos diferentes
4. `sei_remover_acompanhamento` vs. `sei_excluir_grupo_acompanhamento`

---

## Severidade consolidada

| Severidade | Quantidade | Itens |
|---|---|---|
| Alta | 6 | 1, 2, 3, 4, 5, 6 |
| Média | 7 | 7, 8, 9, 10, 11, 12, 13, 14 |
| Baixa | 5 | 15, 16, 17, 18, 19 |
