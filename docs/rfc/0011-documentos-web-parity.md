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

> **Nota de estimativa**: o editor foi inicialmente classificado como "Hard" antes desta
> pesquisa, por se assumir que a API JavaScript do TinyMCE seria necessária. A pesquisa
> no SEI-Pro corrigiu esse diagnóstico — é **Medium**, igual às outras ops de scraping.
> A evidência que suporta essa revisão está documentada acima e em §5.

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

**Contrato atualizado**: `listar_secoes` e `alterar_secoes` ganham `processo: str | None = None`
no contrato (`backends/base.py`) — necessário para que o web backend obtenha `id_procedimento`
via `consultar_documento_web(processo, id_documento)`. O REST backend ignora o parâmetro
(nunca precisou dele). O composite repassa. As tools `sei_listar_secoes` e `sei_editar_secao`
expõem `processo` como parâmetro opcional (mesmo padrão de `sei_baixar_anexo`).

**`listar_secoes(id_documento, processo=None)`**:
```python
async def listar_secoes(self, id_documento: str, processo: str | None = None) -> dict:
    if processo is None:
        msg = (
            "Em instâncias sem mod-wssei, forneça o parâmetro 'processo' "
            "para listar seções de documento."
        )
        raise SEINotImplementedError(msg)
    # id_procedimento vem dos metadados do documento (campo 'id_procedimento')
    meta = await self._web.consultar_documento_web(processo, id_documento)
    id_procedimento = meta.get("id_procedimento") or meta.get("idProcedimento", "")
    html = await self._web.get("controlador.php", params={
        "acao": "editor_montar",
        "id_procedimento": id_procedimento,
        "id_documento": id_documento,
    })
    soup = BeautifulSoup(html, "html.parser")
    textareas = soup.select("div#divEditores textarea")
    # _extrair_versao: busca hdnVersao ou hdnUltimaVersaoDocumento no form
    versao_inp = soup.find("input", {"name": lambda n: n and "versao" in n.lower()})
    versao = versao_inp.get("value", "") if versao_inp else ""
    secoes = [
        # "id" deve ser preenchido (mesmo valor que idSecaoModelo) para que
        # sei_editar_secao não descarte a seção no filtro `sid = s.get("id") or ...`.
        {"id": ta.get("name", ""), "idSecaoModelo": ta.get("name", ""), "conteudo": ta.get_text(), "somenteLeitura": False}
        for ta in textareas
        if ta.get("name")
    ]
    return {"secoes": secoes, "ultimaVersaoDocumento": versao}
```

**`alterar_secoes(id_documento, secoes, versao, processo=None)`**:
```python
async def alterar_secoes(self, id_documento: str, secoes: list[dict], versao: str = "", processo: str | None = None) -> dict:
    if processo is None:
        msg = "Em instâncias sem mod-wssei, forneça 'processo' para editar seções."
        raise SEINotImplementedError(msg)
    meta = await self._web.consultar_documento_web(processo, id_documento)
    id_procedimento = meta.get("id_procedimento") or meta.get("idProcedimento", "")
    html = await self._web.get("controlador.php", params={"acao": "editor_montar", "id_procedimento": id_procedimento, "id_documento": id_documento})  # mesmo GET que listar_secoes
    soup = BeautifulSoup(html, "html.parser")
    form = soup.find("form", id="frmEditor")
    if form is None:
        raise SEIError("Formulário frmEditor não encontrado na página do editor.")
    save_url = form["action"]

    params = {
        inp["name"]: inp.get("value", "")
        for inp in form.find_all("input", type="hidden")
        if "unidade" not in inp.get("name", "").lower()
    }
    # Botão submit obrigatório — PHP ignora POST sem ele silenciosamente
    # (mesmo padrão de criar_documento_interno_web / _extrair_submit_btn)
    sbm = _extrair_submit_btn(form)
    if sbm:
        params[sbm[0]] = sbm[1]

    alteracoes = {s["idSecaoModelo"]: s["conteudo"] for s in secoes}
    for ta in soup.select("div#divEditores textarea"):
        nome = ta.get("name", "")
        if nome:
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

**Contrato**: `alterar_documento_interno` ganha `processo: str | None = None`, pelo mesmo
motivo de `listar_secoes`/`alterar_secoes`: o web backend precisa de `processo` para obter
`id_procedimento` e construir a URL assinada via `_get_doc_signed_url(processo, id_documento,
acao)` (padrão existente em todos os leitores de documento web). O REST backend ignora o
parâmetro.

Tela `controlador.php?acao=documento_alterar&id_documento=X`. Scraping padrão:
GET (com `processo` para obter URL assinada) → parsear form → substituir `txaDescricao`,
`selNivelAcesso`, `selHipoteseLegal` → POST ao action do form + submit button
(`_extrair_submit_btn`). Mesma estrutura de `criar_documento_interno_web`.

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
            suggested_args={"protocolo_formatado": "protocolo_do_processo"},
        )
    doc = result["documento"]
    # tipo_documento do scraper web é o label humano (ex: "Despacho", "Relatório"),
    # não os códigos "I"/"X" que _ler_documento_via_backend espera. Normalizar:
    # só propagar se já vier como código; caso contrário usar "auto" para que a
    # tool tente interno e caia para externo se falhar (comportamento seguro).
    raw_tipo = doc.get("tipo_documento", "")
    tipo = raw_tipo if raw_tipo in ("I", "X") else "auto"
    return doc["id"], tipo
```

Em `tools/documentos.py`, substituir:
```python
# ANTES
if await _has_rest(ctx) and tipo_documento == "auto":
    id_documento, tipo_doc = await _resolver_documento(await _get_client(ctx), id_documento)

# DEPOIS — o resolver é chamado sempre que tipo_documento == "auto".
# O Solr REST resolve corretamente IDs internos (numérico puro) e números SEI
# (numérico puro ou formatado) — o composite decide qual backend usar.
backend = await _backend(ctx)
if tipo_documento == "auto":
    id_documento, tipo_doc = await backend.resolver_documento(id_documento)
```

**Nota sobre discriminação interna vs. SEI**: `isdigit()` não é um discriminador confiável —
tanto IDs internos (ex: `"3149544"`) quanto números SEI (ex: `"2874369"`) podem ser
puramente numéricos. O Solr REST já lida com ambos via `atributos.protocoloFormatado`.
O resolver web (`_resolver_documento_web`) deve ser chamado sempre para `tipo_documento == "auto"`;
se o valor já for um ID interno numérico, o resolver pode retorná-lo diretamente (sem busca
extra) após verificar que o documento existe — estratégia a ser definida na Fase 4. Quando
`processo` também é fornecido, o `_ler_documento_via_backend` já usa o id+processo
diretamente sem resolver. Esse comportamento é preservado.

O composite implementa `resolver_documento`: REST-first (`_resolver_documento` Solr, rápido),
fallback web (`_resolver_documento_web`, mais lento — pesquisa múltiplos processos). A tool
não sabe qual backend resolveu.

**Trade-off de velocidade**: a resolução web é mais cara que Solr (O(processos candidatos)
vs O(1)). Documentada na descrição da tool: `processo=` acelera a busca quando fornecido.

---

## 4. `tools/documentos.py` após a migração

Os usos de `_get_client`/`_has_rest` para **resolução de documento** desaparecem.
**Atenção**: a guarda de validação em `sei_criar_documento` (linha 311) **permanece**:

```python
# PRESERVAR — não é roteamento, é validação de entrada:
if await _has_rest(ctx) and not id_serie:
    raise SEIValidationError("id_serie é obrigatório no modo REST. ...")
```

Esta guarda verifica a capacidade do backend para dar uma mensagem útil ao agente. Ela
será migrada para `backend.requer_id_serie()` (método no composite que retorna `True`
quando REST está ativo) em Fase 4 — não removida.

| Antes | Depois |
|---|---|
| `_get_client` importado de `mcp_app` | removido do import |
| `_has_rest` para resolução em `sei_ler_documento`, `sei_baixar_anexo` | `await backend.resolver_documento(id_documento)` (com guarda `isdigit`) |
| `sei_gerar_referencia` usa `_get_client` direto | usa `backend.resolver_documento` |
| `_has_rest` para validação em `sei_criar_documento` | `await backend.requer_id_serie()` |

Resultado: `tools/documentos.py` não referencia mais clientes crus — apenas `_backend(ctx)`.
Fecha o item pendente da RFC 0006.

---

## 5. Plano de implantação

| Fase | Esforço | Confiança | Evidência de suporte |
|---|---|---|---|
| 1 — editor_montar (listar/alterar_secoes) | Médio | Alta | SEI-Pro faz exatamente esse fluxo; form HTML confirmado na análise da extensão |
| 2 — alterar_documento_interno | Médio | Média | Mesmo padrão de `criar_documento_interno_web` (GET form → substituir campos → POST); URL/acao precisa de confirmação ao vivo |
| 3 — listar_blocos + sugestao_assuntos | Médio | Média | Padrão de scraping de tabela; URLs desconhecidas mas padrão do SEI previsível |
| 4 — resolver_documento + limpeza | Baixo | Alta | Refactor interno puro; sem novo scraping; `_resolver_documento` REST existe e funciona |

### Fase 1 — `listar_secoes` + `alterar_secoes` web (maior impacto, menor risco)
1. Implementar em `backends/web/documentos.py` com o parâmetro `processo` adicionado ao contrato.
2. Confirmar HTML do `editor_montar` contra SEI ao vivo: nomes de textarea, campo versão,
   URL de save, campo `id_procedimento` nos metadados do documento (ver §6).
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
1. Adicionar `resolver_documento` e `requer_id_serie` ao contrato (`backends/base.py`).
2. Implementar no `SEIRestBackend` (delega para `_resolver_documento` Solr existente) e no
   `SEIWebBackend` (delega para `_resolver_documento_web`).
3. Implementar no `CompositeBackend` (REST-first, fallback web).
4. Migrar os três usos de resolução em `tools/documentos.py`; migrar guarda de validação
   de `_has_rest` para `backend.requer_id_serie()`.
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

| Incógnita | Fonte | Confiança | Impacto se diferente do esperado |
|---|---|---|---|
| `editor_montar` usa `id_procedimento` + `id_documento` como params? | SEI-Pro usa esses params no JS | Alta | Ajustar GET; `id_procedimento` pode ser opcional |
| `div#divEditores` existe com textareas `txaEditor_N`? | SEI-Pro seleciona exatamente esse seletor | Alta | Ajustar seletor |
| Textareas têm `data-*` com ID numérico de seção? | Não confirmado no código SEI-Pro | Baixa | Usar atributo real em vez de nome de textarea como `idSecaoModelo` |
| URL de save lida dinamicamente do atributo `action` do `#frmEditor`? | Padrão confirmado no SEI-Pro | Alta | Nenhum — estratégia é robusta por ser dinâmica |
| Metadados de documento incluem `id_procedimento` / `idProcedimento`? | Inferido de `consultar_documento_web`; campo precisa de verificação | Média | Buscar `id_procedimento` por outra rota (ex: regex na URL da página) |
| Form `documento_alterar` existe com esse nome de `acao`? | Inferido do padrão `criar`/`alterar` do SEI | Média | Ajustar `acao=` |
| `listar_blocos_documento` web: tela `bloco_disponibilizar` ou `bloco_listar`? | Não confirmado | Baixa | Ajustar URL |
| Campo de versão: `hdnVersao` ou `hdnUltimaVersaoDocumento`? | Não confirmado | Baixa | Ajustar parser |

Itens de confiança Alta podem ser implementados com base na análise do SEI-Pro.
Itens de confiança Baixa requerem uma sessão de DevTools contra SEI ao vivo antes de implementar.

---

## 7. Riscos

| Risco | Prob. | Mitigação |
|---|---|---|
| `id_procedimento` não disponível nos metadados do documento | Médio | `consultar_documento_web` pode não retornar esse campo; fallback: regex na URL de resposta ou campo adicional em `listar_documentos` web |
| Editor com múltiplos iframes (CKEditor em subframe) | Médio | SEI-Pro parseia `frmEditor` no frame principal; o conteúdo serializado já está nas textareas do DOM antes do submit. Confirmar ao vivo. |
| `resolver_documento_web` chamado com ID interno numérico | Baixo | `isdigit()` não discrimina IDs internos de números SEI — ambos são numéricos puros. Resolver sempre chamado quando `tipo_documento == "auto"`; custo extra aceitável pois REST Solr é O(1) |
| `resolver_documento_web` lento para docs sem `processo` | Alto | Documentar na description da tool; a tool já recomenda `processo=` para web-only |
| `_has_rest` removido mas guarda de `id_serie` perdida | Baixo | §4 preserva a guarda via `backend.requer_id_serie()` explicitamente |
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
