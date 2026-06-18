# RFC 0011 — Paridade web para o domínio de documentos

**Status**: Proposta
**Data**: 2026-06-18
**Autores**: Franklin Baldo (com Claude Code)
**Relacionado**: RFC 0006 (backend abstrato — pendente: `tools/documentos.py` raw clients), RFC 0001 (web-first)
**Inspiração**: extensão SEI-Pro (`SEI-Pro/sei-pro`) — análise do fluxo `editor_montar`

---

## 0. Contexto

RFC 0006 §Pendente registrou duas tarefas abertas no domínio de documentos:

1. **`tools/documentos.py` usa clientes crus** (`_get_client`, `_has_rest`) em três pontos para
   resolução Solr (numero SEI → id interno). Identificado como "facade-free mas ainda sem
   composite completo".
2. **Ops vestigiais no contrato** (`listar_secoes`, `alterar_secoes`, `alterar_documento_interno`,
   `listar_blocos_documento`, `sugestao_assuntos_documento`) são stubs `NotImplementedError` no
   `SEIWebBackend` — travando essas tools em modo web-only.

Esta RFC fecha ambos, sem exigir mod-wssei.

### O que a pesquisa no SEI-Pro revelou

A extensão de browser SEI-Pro interage com o editor de documentos via HTTP puro — sem
precisar da API JavaScript do TinyMCE/CKEditor. O fluxo é:

1. **GET** `controlador.php?acao=editor_montar&id_procedimento=X&id_documento=Y`
2. Parsear `#frmEditor` → atributo `action` = URL de salvamento (dinâmico)
3. Parsear `div#divEditores textarea` → uma textarea por seção, nomeadas `txaEditor_1`,
   `txaEditor_2`, … (ou `txaConteudo` para documentos de seção única)
4. Coletar todos os `input[type=hidden]` do `#frmEditor` (exceto campos com "unidade" no nome)
5. **POST** para a URL do passo 2 com os campos ocultos + conteúdo das textareas → resposta
   começa com `"OK"` em caso de sucesso

O editor é um formulário HTML convencional. O CKEditor serializa o conteúdo em textareas
antes do submit — o scraper trabalha com o HTML resultante, não com a API do editor.

---

## 1. Problema

### 1.1 Três chamadas de cliente cru em `tools/documentos.py`

| Linha | Tool | Uso |
|---|---|---|
| 193–194 | `sei_ler_documento` | `_resolver_documento(await _get_client(ctx), id_documento)` quando `_has_rest` |
| 264–265 | `sei_baixar_anexo` | idem |
| 364–365 | `sei_gerar_referencia` | `_get_client` direto, sem fallback web |

As duas primeiras já degradam corretamente em modo web-only (pulam a resolução, exigem
`processo`). A terceira (`sei_gerar_referencia`) falha se REST não estiver disponível e
`id_documento` não for passado explicitamente.

### 1.2 Cinco ops sem implementação web

| Operação no contrato | Tool afetada | Stubs hoje |
|---|---|---|
| `listar_secoes` | `sei_listar_secoes` | `SEIWebBackend` herda stub |
| `alterar_secoes` | `sei_editar_secao` | idem |
| `alterar_documento_interno` | `sei_alterar_documento_interno` | idem |
| `listar_blocos_documento` | `sei_listar_blocos_documento` | idem |
| `sugestao_assuntos_documento` | `sei_sugestao_assuntos_documento` | idem |

Em instâncias sem mod-wssei, essas cinco tools levantam `SEINotImplementedError` silencioso
onde deveriam funcionar.

---

## 2. Objetivo

1. Implementar as cinco ops em `backends/web/documentos.py` via scraping HTTP.
2. Adicionar resolver web (`_resolver_documento_web`) como fallback para numero SEI → id,
   permitindo que `sei_gerar_referencia` funcione sem REST.
3. Remover `_get_client` e `_has_rest` de `tools/documentos.py` — o composite roteia.
4. Nenhuma regressão: suíte verde, `ruff` limpo, servidor executável.

Fora de escopo: alterar o comportamento de qualquer tool já funcional em modo REST.

---

## 3. Arquitetura das novas ops web

### 3.1 `listar_secoes` e `alterar_secoes` — via `editor_montar`

O fluxo descoberto no SEI-Pro mapeia diretamente para as duas ops:

**`listar_secoes(id_documento)`**:
```python
async def listar_secoes(self, id_documento: str) -> dict:
    # Precisa do id_procedimento; busca via consultar_documento_web se não fornecido.
    # O editor_montar requer ambos os parâmetros.
    html = await self._web.get("controlador.php", params={
        "acao": "editor_montar",
        "id_procedimento": id_procedimento,
        "id_documento": id_documento,
    })
    soup = BeautifulSoup(html, "html.parser")
    textareas = soup.select("div#divEditores textarea")
    secoes = [
        {"idSecaoModelo": ta["name"], "conteudo": ta.get_text(), "somenteLeitura": False}
        for ta in textareas
    ]
    return {"secoes": secoes, "ultimaVersaoDocumento": _extrair_versao(soup)}
```

**`alterar_secoes(id_documento, secoes, versao)`**:
```python
async def alterar_secoes(self, id_documento: str, secoes: list[dict], versao: str = "") -> dict:
    html = await self._web.get("controlador.php", params={...})  # mesmo GET
    soup = BeautifulSoup(html, "html.parser")
    form = soup.find("form", id="frmEditor")
    save_url = form["action"]

    params = {
        inp["name"]: inp.get("value", "")
        for inp in form.find_all("input", type="hidden")
        if "unidade" not in inp.get("name", "").lower()
    }
    alteracoes = {s["idSecaoModelo"]: s["conteudo"] for s in secoes}
    for ta in soup.select("div#divEditores textarea"):
        nome = ta["name"]
        params[nome] = sanitize_iso8859(alteracoes.get(nome, ta.get_text()))

    resp = await self._web.post(save_url, data=params)
    if not resp.text.startswith("OK"):
        raise SEIError(f"Editor não salvou: {resp.text[:200]}")
    return {"status": "ok", "id_documento": id_documento}
```

**Reconciliação `idSecaoModelo`**: o REST usa IDs numéricos (ex: `"42"`); o web usa nomes de
textarea (`"txaEditor_1"`). O composite resolve: quando REST disponível, usa REST; quando
web-only, o campo `idSecaoModelo` retornado é o nome da textarea — opaco para a tool, que
já usa esse campo como chave de correspondência em `sei_editar_secao`. Nenhuma mudança
na tool layer.

Uma segunda verificação no HTML da página pode revelar se há atributo `data-id-secao` ou
similar nas textareas — se sim, usamos o ID numérico real. Isso requer confirmação contra
SEI ao vivo (ver §6).

### 3.2 `alterar_documento_interno` — formulário de metadados

Tela `controlador.php?acao=documento_alterar&id_documento=X`. Scraping padrão:
GET → parsear form → substituir `txaDescricao`, `selNivelAcesso`, `selHipoteseLegal` →
POST ao action do form. Mesma estrutura de `criar_documento_interno_web`.

### 3.3 `listar_blocos_documento` — página de blocos do documento

Tela `controlador.php?acao=bloco_disponibilizar&id_documento=X` (ou equivalente de consulta).
Retorna lista de blocos em que o documento está incluído. Scraping de tabela.

### 3.4 `sugestao_assuntos_documento` — catálogo via web

Tela de sugestão de assuntos acessível pelo formulário de criação/alteração de documento.
Retorno como lista `[{id, descricao}]`. Scraping de `select` ou tabela.

### 3.5 `_resolver_documento_web` — fallback de resolução de numero SEI

O `SEIWebBackend` já tem `buscar_documento(numero_sei, processo="")` que faz pesquisa web
e retorna o documento encontrado com seu `id` interno. Envolver esse resultado em uma
função equivalente ao `_resolver_documento` REST:

```python
async def _resolver_documento_web(self, numero_sei: str) -> tuple[str, str]:
    """Resolve numero SEI → (id_interno, tipo_documento) via pesquisa web."""
    result = await self.buscar_documento(numero_sei)
    if not result.get("encontrado"):
        raise SEINotFoundError(
            f"Documento SEI {numero_sei} não encontrado.",
            error_code="DOCUMENTO_NAO_ENCONTRADO",
            recoverable=True,
            suggested_next_tool="sei_arvore_processo",
            suggested_args={"protocolo_formatado": "<processo>"},
        )
    doc = result["documento"]
    return doc["id"], doc.get("tipo_documento", "auto")
```

Em `tools/documentos.py`, substituir:
```python
# ANTES
if await _has_rest(ctx) and tipo_documento == "auto":
    id_documento, tipo_doc = await _resolver_documento(await _get_client(ctx), id_documento)

# DEPOIS
backend = await _backend(ctx)
if tipo_documento == "auto":
    id_documento, tipo_doc = await backend.resolver_documento(id_documento)
```

O composite implementa `resolver_documento`: REST-first (`_resolver_documento` Solr, rápido),
fallback web (`_resolver_documento_web`, mais lento — pesquisa múltiplos processos). A tool
não sabe qual backend resolveu.

**Trade-off de velocidade**: a resolução web é mais cara que Solr (O(processos candidatos)
vs O(1)). Documentada na descrição da tool: `processo=` acelera a busca quando fornecido.

---

## 4. `tools/documentos.py` após a migração

Os três pontos de `_get_client`/`_has_rest` desaparecem:

| Antes | Depois |
|---|---|
| `_get_client`, `_has_rest` importados de `mcp_app` | removidos do import |
| Resolução Solr condicional em `sei_ler_documento`, `sei_baixar_anexo` | `await backend.resolver_documento(id_documento)` incondicionalmente |
| `sei_gerar_referencia` usa `_get_client` direto | usa `backend.resolver_documento` |

Resultado: `tools/documentos.py` não referencia mais clientes crus — apenas `_backend(ctx)`.
Fecha o item pendente da RFC 0006.

---

## 5. Plano de implantação

### Fase 1 — `listar_secoes` + `alterar_secoes` web (maior impacto, menor risco)
1. Implementar em `backends/web/documentos.py`.
2. Confirmar HTML do `editor_montar` contra SEI ao vivo: nomes de textarea, campo versão,
   URL de save (ver §6).
3. Teste: mock de GET `editor_montar` + assert POST com conteúdo correto.
4. `sei_listar_secoes` e `sei_editar_secao` passam a funcionar em modo web-only.

### Fase 2 — `alterar_documento_interno` web
1. Implementar em `backends/web/documentos.py`.
2. Confirmar campos do form `documento_alterar` ao vivo.
3. Teste mock.

### Fase 3 — `listar_blocos_documento` + `sugestao_assuntos_documento` web
1. Implementar em `backends/web/documentos.py`.
2. Confirmar URLs/estrutura ao vivo.
3. Testes mock.

### Fase 4 — `resolver_documento` no composite + limpeza de `tools/documentos.py`
1. Adicionar `resolver_documento` ao contrato (`backends/base.py`).
2. Implementar no `SEIRestBackend` (delega para `_resolver_documento` Solr existente) e no
   `SEIWebBackend` (delega para `_resolver_documento_web`).
3. Implementar no `CompositeBackend` (REST-first, fallback web).
4. Remover `_get_client`, `_has_rest` de `tools/documentos.py`.
5. Testes: resolver com REST mock, com web mock, e composite com REST indisponível.

### Sequência
```
Fase 1 ──► Fase 2 ──► Fase 3
                         │
                         └──► Fase 4  (independente das fases 1–3, pode ser paralela)
```

Fases 1–3 exigem validação ao vivo. Fase 4 é puramente interna (sem scraping novo).

---

## 6. Incógnitas que requerem SEI ao vivo

| Incógnita | Impacto se diferente do esperado |
|---|---|
| `editor_montar` usa `id_procedimento` + `id_documento` como params? | Ajustar GET; `id_procedimento` pode ser opcional |
| Textareas em `div#divEditores` têm `data-*` com ID numérico de seção? | Usar atributo real em vez de nome de textarea como `idSecaoModelo` |
| URL de save do `#frmEditor` é `editor_salvar` ou outro? | Nenhum — é lida dinamicamente do atributo `action` |
| Form `documento_alterar` existe com esse nome de `acao`? | Ajustar `acao=` |
| `listar_blocos_documento` web: tela `bloco_disponibilizar` ou `bloco_listar`? | Ajustar URL |
| Versão do documento: campo `hdnVersao` ou `hdnUltimaVersaoDocumento`? | Ajustar parser |

Nenhuma dessas incógnitas bloqueia o design — todas são detalhes de URL/campo que se
resolvem com uma sessão de inspeção de rede (DevTools) contra qualquer instância SEI 4.0+.

---

## 7. Riscos

| Risco | Prob. | Mitigação |
|---|---|---|
| Editor com múltiplos iframes (CKEditor em subframe) | Médio | SEI-Pro parseia `frmEditor` no frame principal; o conteúdo serializado já está nas textareas do DOM antes do submit. Confirmar ao vivo. |
| `resolver_documento_web` lento para docs sem `processo` | Alto | Documentar na description da tool; a tool já recomenda `processo=` para web-only |
| `idSecaoModelo` numérico vs nome de textarea incompatível com REST | Baixo | Composite isola; tool usa o valor como chave opaca; testes cobrem os dois caminhos |
| Form `editor_montar` muda entre SEI 4.x e 5.x | Médio | SEI-Pro já lida com SEI 5.x (`infra-editor__editor-completo`); testar contra ambas as versões |
| Salvamento silencioso falha (response não começa com "OK") | Baixo | Verificação explícita + `SEIError` com mensagem da resposta |

---

## 8. Critérios de conclusão

| Critério | Verificação |
|---|---|
| `sei_listar_secoes`, `sei_editar_secao` funcionam sem REST | teste mock Fase 1 + smoke ao vivo |
| `sei_alterar_documento_interno` funciona sem REST | teste mock Fase 2 |
| `sei_listar_blocos_documento`, `sei_sugestao_assuntos_documento` sem REST | teste mock Fase 3 |
| `sei_ler_documento`, `sei_baixar_anexo`, `sei_gerar_referencia` sem REST nem `_get_client` | teste de import + rota composite Fase 4 |
| Nenhum `_get_client` ou `_has_rest` em `tools/documentos.py` | `grep -n "_get_client\|_has_rest" tools/documentos.py` vazio |
| `ruff check . && ruff format --check .` limpos | CI |
| Suíte verde | CI |
