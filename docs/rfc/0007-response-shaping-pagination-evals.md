# RFC 0007 — Response shaping, paginação por cursor, erros recuperáveis e eval harness

**Status**: Proposta
**Data**: 2026-06-17
**Autores**: Franklin Baldo (com Claude Code)
**Relacionado**: RFC 0006 (backend abstrato), RFC 0004 (exceções)
**Baseado em**: skill `mcp-coding` (best practices de tool surface, schemas, token-economy, erros, evals)

---

## 0. Contexto e motivação

A RFC 0006 entregou arquitetura limpa (composite backend, módulos por domínio,
124 tools, suíte verde). O próximo eixo é a **qualidade da fronteira agente↔tool**:
o que cada tool devolve e como o agente recupera quando algo falha.

A regra-mãe da skill `mcp-coding`: **o agente paga cada token duas vezes — uma
para ler a tool, outra para raciocinar sobre o que ela retorna.** Tudo abaixo
deriva disso. Auditamos as 124 tools contra a skill e encontramos cinco lacunas:

| Lacuna | Regra da skill violada | Seção |
|---|---|---|
| Payload bruto não-shaped em tools de leitura pesada | "Never return a raw upstream payload by default" | §1 |
| Shapes são dicts ad-hoc, não schemas | "Design every shaped response as a real schema now" | §1.1 |
| Truncamento sem continuação estruturada | "Every truncated response carries its own continuation" | §1.4 |
| Paginação por número de página, `has_more` inferido sem marcar | "Prefer opaque cursor; keep `has_more` honest" | §2 |
| Sem eval de seleção/sequência sob agente | "Evals — non-negotiable" | §4 |

E uma lacuna factual: documentação diz "121 tools", o código tem **124** (§6).

O que **não** mudamos (a skill confirma que já está certo): hierarquia
`SEIError(ToolError)` propagada sem rewrap (§3 só a *enriquece*); gate de acesso
restrito/sigiloso com `elicit` que falha fechado; secrets via env/keyring;
stdio→stderr; resources para catálogos estáticos (`sei://status`,
`sei://estilos-css`, `sei://hipoteses-legais`).

---

## 1. Response shaping

### 1.1 Schemas Pydantic, não dicts ad-hoc

> Skill: *"Define the shape as a Pydantic model / JSON Schema, not an ad-hoc
> dict. Then adopting `outputSchema` + `structuredContent` later is wiring, not a
> redesign."*

Hoje os poucos shapers existentes (`_shape_consultar_processo`) retornam `dict`.
A RFC anterior pôs `outputSchema` fora de escopo. **Revertemos essa decisão**: as
respostas shaped passam a ser **modelos Pydantic** definidos em um módulo novo
`src/todos/responses.py`. Continuamos serializando para string JSON via `_json`
por compatibilidade — mas a partir de um modelo, não de um dict solto. Quando o
FastMCP/cliente suportar `structuredContent` de forma estável, trocar
`return _json(model.model_dump())` por `return model` é fiação, não redesenho.

**Importante** (invariante da RFC 0006): os modelos de *resposta* não afetam a
introspecção de type hints de *entrada* do FastMCP — logo não conflitam com a
regra "sem `from __future__ import annotations`" nos módulos de tools. Os modelos
ficam isolados em `responses.py`.

```python
# src/todos/responses.py
from pydantic import BaseModel, Field

class NextAction(BaseModel):
    """Próxima ação sugerida ao agente para continuar (paginação ou recuperação)."""
    tool: str = Field(description="Nome da tool a chamar em seguida, ex: 'sei_arvore_processo'")
    args: dict = Field(description="Argumentos para a tool, ex: {'cursor': 'eyJwIjoxfQ'}")
    reason: str = Field(description="Por que esta ação, em uma linha")

class DocumentoResumo(BaseModel):
    """Documento na árvore de um processo (campos essenciais para chaining)."""
    id: str = Field(description="idDocumento interno, ex: '2843449'")
    numero_sei: str = Field(default="", description="Número visível, ex: '2843449'")
    tipo_documento: str = Field(default="", description="ex: 'Despacho'")
    nome_composto: str = Field(default="", description="ex: 'Despacho GPF 2874369'")
    sigla_unidade: str = Field(default="", description="unidade geradora, ex: 'GPF'")
    assinado: bool | None = None
    cancelado: bool | None = None
    volume: int | None = None

class ListaDocumentos(BaseModel):
    """Resposta de sei_arvore_processo / sei_listar_documentos."""
    processo: str
    total_documentos: int = Field(description="total real no servidor")
    documentos: list[DocumentoResumo]
    next_actions: list[NextAction] = Field(default_factory=list)

class RespostaEscrita(BaseModel):
    """Resposta enxuta de tools de criação/alteração."""
    acao: str
    status: str = "ok"
    id_procedimento: str | None = None
    protocolo: str | None = None
    id_documento: str | None = None
    numero_sei: str | None = None
    mensagem: str | None = None
```

### 1.2 `summary` + `items`, IDs com nomes

> Skill: *"Return `summary` + `items`. Preserve stable IDs. Put the
> human-readable name next to the ID."*

Cada lista shaped tem o agregado no topo (`total_*`) e os registros podados
abaixo. Toda entidade carrega **id + nome juntos** — `{"id": "...", "nome": "..."}`,
nunca id sem nome nem nome sem id. Isso já é parcialmente verdade
(`DocumentoResumo.id` + `nome_composto`); a regra passa a valer para
interessados, unidades, assuntos, signatários.

### 1.3 Tools de leitura pesada — truncar com sinal

`_LISTA_DOCS_LIMIT = 50`. Aplicado a `sei_arvore_processo` e
`sei_listar_documentos` via shaper que produz `ListaDocumentos`:

```python
def _shape_lista_documentos(docs: list[dict], protocolo: str) -> ListaDocumentos:
    total = len(docs)
    shaped = [DocumentoResumo(**_pick_doc(d)) for d in docs[:_LISTA_DOCS_LIMIT]]
    actions: list[NextAction] = []
    if total > _LISTA_DOCS_LIMIT:
        actions.append(NextAction(
            tool="sei_arvore_processo",
            args={"protocolo_formatado": protocolo, "include_raw": True},
            reason=f"Exibindo {_LISTA_DOCS_LIMIT} de {total} documentos; "
                   "include_raw=True retorna a árvore completa.",
        ))
    return ListaDocumentos(
        processo=protocolo, total_documentos=total,
        documentos=shaped, next_actions=actions,
    )
```

`sei_listar_atividades`: shaper análogo, trunca para as 50 mais **recentes**
(ordem cronológica inversa), com parâmetro `ordem: Literal["desc","asc"]="desc"`.

`sei_consultar_processo`: o `_shape_consultar_processo` atual vira um modelo
Pydantic `ProcessoDetalhe`; o `_documentos_truncados` (string) vira `next_actions`.

### 1.4 Continuação estruturada — `next_actions`, não strings

> Skill: *"Don't just say 'truncated' — return the next action: the tool + args
> to get more (`next_actions: [{tool, args, reason}]`)."*

Eliminamos as strings `_truncado` / `_documentos_truncados`. Toda resposta
truncada carrega `next_actions: list[NextAction]` — o agente nunca precisa
adivinhar como continuar. Mesma estrutura usada por paginação (§2) e por erros
recuperáveis (§3), unificando o "como prosseguir".

### 1.5 Gate do firehose — `include_raw: bool = False`

> Skill: *"Gate the firehose behind `include_raw: false`. When the full payload
> is genuinely needed, make the agent ask for it explicitly."*

As tools de leitura pesada (`sei_arvore_processo`, `sei_listar_documentos`,
`sei_consultar_processo`, `sei_listar_atividades`, `sei_pesquisar_processos`)
ganham `include_raw: bool = False`. Com `True`, a tool pula o shaping/truncamento
e devolve o payload completo do backend. Default `False`. As `next_actions` de
truncamento já apontam `include_raw=True` como a continuação (§1.3), fechando o
loop: o agente vê que foi truncado *e* como pegar tudo.

### 1.6 Respostas de escrita — só o essencial

`_shape_resposta_escrita(result, acao) -> RespostaEscrita` aplicado a
`sei_criar_processo`, `sei_alterar_processo`, `sei_criar_documento`,
`sei_criar_documento_externo`, `sei_alterar_documento_interno`. **Invariante
testado**: id e protocolo do recurso criado nunca se perdem no shaping.

### 1.7 `response_format` — onde ganha o custo

> Skill: *"Offer `response_format: 'markdown' | 'json'` when both views earn
> their keep."*

Decisão deliberada de **não** adicionar `response_format` agora às tools de
árvore/documentos: o agente **encadeia** essas respostas para `sei_ler_documento`
via `id`, então precisa de JSON estruturado; a apresentação markdown ao humano já
é instruída no bloco `instructions` ("use tabela markdown, 📄/📎…"), feita pelo
agente. Adicionar o parâmetro aqui inflaria o schema sem ganho — a própria skill
adverte contra over-augmentar. Reavaliar só se surgir uma tool de leitura cujo
output o humano consome direto sem chaining.

### 1.8 O que NÃO mudar

`sei_ler_documento` (já devolve o conteúdo útil), `sei_consultar_documento_*`
(objeto único), `sei_listar_sobrestamentos`/`interessados`/`unidades_processo`
(listas < 20 itens) — apenas ganham `total_itens` e, quando entidades,
id+nome juntos (§1.2).

---

## 2. Paginação por cursor opaco

### 2.1 Estado atual e a regra

O backend SEI é página-numerada (`start` = página 0-indexed). Hoje as tools
expõem `pagina: int` na entrada e devolvem `tem_proxima` na saída — quando
devolvem. A inferência `len(items) >= limit` aparece crua, sem marcar que é
inferência.

> Skill: *"Prefer an opaque `next_cursor` rather than exposing the backend's page
> numbers. If the backend only does numeric pages, hide that behind the cursor
> (encode it). Keep `has_more` honest: if you only inferred it from
> `len(items) == limit`, mark it (`has_more_inferred: true`)."*

### 2.2 Proposta: cursor opaco que encapsula a página

Cursor = `base64url(json compacto {p: <pagina>, ...filtros})`. O agente trata
**ausência de cursor como fim** e nunca assume tamanho de página. Helpers em
`mcp_app.py`:

```python
def _encode_cursor(pagina: int, **extra: object) -> str:
    return base64.urlsafe_b64encode(
        json.dumps({"p": pagina, **extra}, separators=(",", ":")).encode()
    ).decode()

def _decode_cursor(cursor: str) -> dict:
    if not cursor:
        return {}
    try:
        return json.loads(base64.urlsafe_b64decode(cursor.encode()))
    except (binascii.Error, ValueError, UnicodeDecodeError) as e:
        # Cursor opaco corrompido/truncado pelo agente: erro acionável, não crash.
        raise SEIValidationError(
            "Cursor de paginação inválido. Use o `proximo_cursor` retornado pela "
            "última chamada, ou omita `cursor` para começar da primeira página.",
            error_code="CURSOR_INVALIDO",
            recoverable=True,
        ) from e
```

Coerente com o princípio de erro recuperável da §3: o decoder valida e levanta
`SEIValidationError` tipada em vez de propagar `binascii.Error`/`JSONDecodeError`
cru como "internal error".

Modelo de envelope (Pydantic, em `responses.py`):

```python
class Paginado(BaseModel):
    total_itens: int | None = Field(default=None, description="total no servidor, None se desconhecido")
    proximo_cursor: str | None = Field(default=None, description="passe em `cursor`; None = fim")
    tem_proxima_inferida: bool = Field(default=False, description="True se inferido de len(items)>=limit")
    next_actions: list[NextAction] = Field(default_factory=list)
```

**Entrada**: cada tool paginada ganha `cursor: str = ""` (opaco). Mantemos
`pagina: int = 0` por compatibilidade com a surface e os testes existentes;
quando `cursor` é passado, ele tem precedência (decodifica a página). Novos
agentes usam só `cursor`. **Saída**: `proximo_cursor` é `None` quando não há mais
páginas; quando há, `next_actions` inclui `{tool, args:{cursor}, reason}`.

**Honestidade do `has_more`**: se o total é conhecido,
`proximo_cursor` é exato e `tem_proxima_inferida=False`. Se só sabemos
`len(items) >= limit`, emitimos o cursor mas com `tem_proxima_inferida=True` —
o agente sabe que pode receber página vazia na borda.

### 2.3 Tools afetadas

Catálogos e blocos (12 tools): `sei_pesquisar_tipos_processo/documento`,
`sei_pesquisar_hipoteses_legais`, `sei_pesquisar_marcadores`,
`sei_pesquisar_unidades`, `sei_listar_modelos`, `sei_listar_assuntos`,
`sei_pesquisar_contatos`, `sei_listar_textos_padrao`, `sei_listar_blocos`,
`sei_listar_blocos_assinatura`, `sei_pesquisar_processos`. `sei_listar_processos`
migra de `tem_proxima`/`pagina` para o envelope `Paginado` (mantendo os campos
legados durante a transição).

Listas inerentemente completas (`sobrestamentos`, `interessados`,
`unidades_processo`, `relacionamentos`): `total_itens = len(items)`,
`proximo_cursor = None` — semântica "isto é tudo".

---

## 3. Erros recuperáveis com continuação

> Skill: *"Where there's a clear next step, return a structured, recoverable
> error: `error_code`, `message`, `recoverable: true`, and the key part —
> `suggested_next_tool` + `suggested_args`. Raise it at the origin; let the tool
> layer serialize it; never leak internals."*

A RFC 0004 já entregou `SEIError(ToolError)` tipado e propagado sem rewrap — a
base está certa. Esta RFC **enriquece** as exceções com a continuação que a skill
pede, levantada na origem (cliente/backend que conhece o contexto), não
reconstruída na tool.

### 3.1 Atributos de recuperação na base

```python
class SEIError(ToolError):
    def __init__(self, message: str, *, error_code: str = "",
                 recoverable: bool = False,
                 suggested_next_tool: str | None = None,
                 suggested_args: dict | None = None) -> None:
        self.error_code = error_code
        self.recoverable = recoverable
        self.suggested_next_tool = suggested_next_tool
        self.suggested_args = suggested_args or {}
        super().__init__(self._render(message))

    def _render(self, message: str) -> str:
        # A continuação precisa viajar DENTRO da string da mensagem (ver §3.1.1).
        if not self.suggested_next_tool:
            return message
        args = json.dumps(self.suggested_args, ensure_ascii=False, separators=(",", ":"))
        return (
            f"{message}\n\nPróximo passo sugerido: chame `{self.suggested_next_tool}` "
            f"com {args}."
        )
```

### 3.1.1 Por que a continuação vai na mensagem, não em campos soltos

**Restrição do transporte MCP** (verificada no FastMCP/SDK desta árvore): quando
um `ToolError` cruza a fronteira, o servidor serializa **apenas
`str(exception)`** em `CallToolResult.content[0].text` com `isError=True`
(`mcp/server/lowlevel/server.py`). Atributos Python extras
(`suggested_next_tool`, `suggested_args`) **NÃO** chegam ao agente — somem com a
exceção. Não existe, hoje, "serializador de erro" no tool layer: `_json`
(`mcp_app.py`) é só para respostas de sucesso.

Portanto, a continuação recuperável precisa estar **embutida na string da
mensagem** (feito por `_render` acima) — é o único canal que sobrevive ao
transporte. Os atributos estruturados (`error_code`, `recoverable`,
`suggested_*`) permanecem no objeto para (a) testes, (b) logging server-side, e
(c) adoção futura de `structuredContent` (RFC 0008), quando passarão a viajar
como estrutura sem reescrever os call-sites. Até lá, **a fonte da verdade que o
agente lê é o texto da mensagem**.

> Nota de escopo: isto resolve a contradição apontada na revisão — a §11 adia
> `structuredContent`, então a §3 **não pode** depender de campos de erro
> estruturados chegando ao agente. A mensagem renderizada é a ponte até a
> RFC 0008.

### 3.2 Casos canônicos do SEI (mapeiam direto ao exemplo da skill)

O exemplo da skill — *"Unit 'GPF' not found → try `search_units` with
query='GPF'"* — é literal no SEI, onde tramitação/atribuição resolvem sigla→id:

| Origem | Exceção enriquecida |
|---|---|
| `enviar_processo` com sigla de unidade inexistente | `SEINotFoundError(..., error_code="UNIDADE_NAO_ENCONTRADA", recoverable=True, suggested_next_tool="sei_pesquisar_unidades", suggested_args={"filtro": "<sigla>"})` |
| `atribuir_processo` com nome de usuário ambíguo | `suggested_next_tool="sei_listar_usuarios_unidade"` |
| `criar_processo` restrito sem hipótese legal | `suggested_next_tool="sei_pesquisar_hipoteses_legais"` |
| documento recém-criado não indexado no Solr | `recoverable=True`, mensagem: use o `idDocumento` retornado pela criação |
| sessão expirada (401/403) | `SEIAuthError(..., error_code="SESSAO_EXPIRADA", suggested_next_tool="sei_status")` |

Terminais (sem próximo passo claro) seguem mensagem acionável simples, como hoje.
`raise X from e` preserva a cadeia; `logger.exception(...)` registra o trace
server-side; **nada de stack trace/SQL/segredo vaza ao agente**.

---

## 4. Eval harness — redesenho conforme a skill

### 4.1 Por que o design anterior estava errado

A versão anterior desta RFC propunha eval single-turn com `tool_choice:"any"`,
checando só *qual* tool o modelo escolheria. A skill é explícita:

> *"Single-tool questions don't test selection under pressure. Include questions
> that (a) discriminate between confusable siblings and (b) require a sequence of
> calls, not a single lookup."*

E exige 10 perguntas **independentes, read-only, realistas, complexas
(multi-call), verificáveis por comparação de string**, rodadas por um agente que
só tem as nossas tools — quando falha, conserta-se a **description** ou a
**response shape**, não a pergunta.

### 4.2 Determinismo — cassetes HTTP

Respostas verificáveis por string exigem dados estáveis. Não dependemos de SEI ao
vivo em CI (muda, exige rede/credencial). Gravamos **cassetes VCR** (`vcr.py` via
`pytest-recording`, suporte httpx) uma vez contra ANTAQ/SEI-RO; em CI o `httpx`
do `SEIClient`/`SEIWebClient` replay offline. As respostas das 10 QA ficam
**pinadas ao snapshot do cassete** — determinístico e sem rede.

### 4.3 Estrutura

```
evals/
  cassettes/            # gravações VCR (um por cenário de processo-fixture)
  golden.xml            # as 10 QA no formato <evaluation>
  runner.py             # loop de agente (Anthropic SDK) sobre o servidor com cassete
  conftest.py           # fixture: sobe o servidor MCP com VCR ativo
  test_evals.py         # pytest -m eval: roda cada QA, compara string final
  README.md
```

O `runner.py` roda um **loop de agente real** (não single-turn): expõe as 124
tools ao modelo, deixa-o explorar e encadear chamadas, e compara a resposta final
com o `<answer>` esperado (substring case-insensitive, normalizada). Modelo sob
teste: um modelo capaz o suficiente para multi-call (representativo do uso real);
Haiku é barato demais para a exploração que essas QA exigem. Marker `eval`,
pulado sem `ANTHROPIC_API_KEY`.

### 4.4 As 10 perguntas (`golden.xml`)

Cobrem discriminação de irmãs confundíveis **e** sequências. Respostas pinadas ao
cassete-fixture do processo `50300.001234/2025-00` (valores ilustrativos abaixo;
fixados no momento da gravação).

```xml
<evaluation>
  <qa_pair>
    <question>Quantos documentos tem o processo 50300.001234/2025-00 e qual o tipo do documento mais recente?</question>
    <answer>12 documentos; o mais recente é um Despacho</answer>
    <!-- sequência: consultar/arvore → contar → tipo do último. Irmãs: arvore vs consultar -->
  </qa_pair>
  <qa_pair>
    <question>No processo 50300.001234/2025-00, qual unidade gerou o Ofício mais recente?</question>
    <answer>GPF</answer>
    <!-- arvore → filtrar Ofício → sigla_unidade. Multi-call -->
  </qa_pair>
  <qa_pair>
    <question>Quantos tipos de processo disponíveis têm "Fiscalização" no nome?</question>
    <answer>4</answer>
    <!-- pesquisar_tipos_processo(filtro) + paginação. Irmãs: tipos_processo vs tipos_documento -->
  </qa_pair>
  <qa_pair>
    <question>O processo 50300.001234/2025-00 já esteve sobrestado? Qual o motivo do último sobrestamento?</question>
    <answer>Sim; aguardando manifestação da área técnica</answer>
  </qa_pair>
  <qa_pair>
    <question>Em quais unidades o processo 50300.001234/2025-00 está aberto atualmente?</question>
    <answer>GPF e GRP</answer>
    <!-- Irmãs: listar_unidades_processo vs pesquisar_unidades -->
  </qa_pair>
  <qa_pair>
    <question>Qual a especificação e o nível de acesso do processo 50300.001234/2025-00?</question>
    <answer>Fiscalização de rotina; Público</answer>
  </qa_pair>
  <qa_pair>
    <question>Quantos eventos de envio (tramitação) constam no histórico do processo 50300.001234/2025-00?</question>
    <answer>3</answer>
    <!-- listar_atividades → filtrar envios → contar -->
  </qa_pair>
  <qa_pair>
    <question>Na caixa da unidade atual, quantos processos são do tipo "Pessoal: Férias"?</question>
    <answer>7</answer>
    <!-- resumo_processos(agrupar_por=tipo). Irmãs: resumo vs listar_processos -->
  </qa_pair>
  <qa_pair>
    <question>O documento SEI 2843449 pertence a qual processo?</question>
    <answer>50300.001234/2025-00</answer>
    <!-- Irmãs: buscar_documento vs ler_documento -->
  </qa_pair>
  <qa_pair>
    <question>Liste os interessados do processo 50300.001234/2025-00.</question>
    <answer>Departamento Jurídico; Diretoria de Fiscalização</answer>
    <!-- Irmãs: listar_interessados vs listar_unidades_processo -->
  </qa_pair>
</evaluation>
```

### 4.5 Tier rápido opcional

Mantemos, como pré-filtro barato e determinístico, um tier T1 de smoke de
seleção single-turn (a matriz de 20 casos do design anterior, via `tool_choice`),
que pega misrouting grosseiro em segundos. O tier T2 (as 10 QA acima) é o
primário e o que a skill exige. CI roda T1 sempre; T2 on-demand
(`workflow_dispatch` + PRs que tocam `tools/`/`mcp_app.py`/`server.py`).

---

## 5. Surface grande — descoberta progressiva

> Skill: *"Past a few dozen tools, loading every definition up front burns
> context and degrades selection. Group by domain; short catalog description +
> fuller candidate description; consider a capability-search tool; lean on
> server `instructions`."*

124 tools é "grande" pelo critério da skill. O que já temos e o que falta:

| Recomendação da skill | Estado | Ação |
|---|---|---|
| Agrupar por domínio + prefixo | Parcial: tudo `sei_*` (prefixo único, não por domínio `process_*`/`sign_*`) | Manter `sei_*` — renomear 124 tools é breaking; o agrupamento por domínio existe nos **módulos** (`tools/processos.py`…) e nas recipes do `instructions`. Não renomear agora. |
| `instructions` com modelo de domínio + recipes | **Forte** — bloco extenso com entidades, fluxos, formatação | Manter; é o que mais barateia seleção. |
| Descrição curta (catálogo) + longa (candidata) | Ausente — descrições são uniformemente longas | **Adotar** quando o cliente suportar descrição em dois níveis; por ora, podar as descrições mais infladas para o essencial (a skill mediu: over-augmentar sobe sucesso ~6pp mas infla passos ~67%). |
| Capability-search tool | Ausente | **Avaliar** `sei_buscar_ferramentas(intencao: str)` que retorna as N tools mais relevantes por tag de domínio — só se evals (T1) mostrarem degradação de seleção com a surface cheia. Não construir especulativamente. |

Decisão: a alavanca de maior retorno aqui é **podar descrições infladas** (§5
backfill) e **manter o `instructions` afiado**. Renomeação por domínio e
capability-search ficam condicionadas a sinal de eval, não feitas às cegas.

---

## 6. Drift de contagem de tools

Código tem **124** `@mcp.tool` (processos 27, documentos 14, blocos_assinatura
14, unidades 13, catálogos 12, blocos_internos 10, marcadores 9, acompanhamento
8, assinatura 7, credenciamento 4, server.py 6). A contagem real foi confirmada
via `len(asyncio.run(mcp.list_tools())) == 124`.

A string de contagem desatualizada aparece em **cinco** lugares (não três) — a
auditoria inicial omitiu README e manifest:

| Arquivo | Diz hoje | Corrigir para |
|---|---|---|
| `mcp_app.py` (instructions) | "121 tools" | 124 |
| `server.py` (comentário) | "115 tools" / "121" | 124 |
| `CLAUDE.md` (3 ocorrências) | "121 tools" | 124 |
| `README.md:5` | "121 tools" | 124 |
| `manifest.json:6` (description) | "116 ferramentas" | 124 |

Teste de regressão. **API**: o FastMCP não expõe `_tool_manager`; a forma pública
de enumerar tools (já usada em `tests/test_tool_routing.py:749`) é a coroutine
`mcp.list_tools()`. Além de validar o número, o teste varre os arquivos de
documentação — o número sozinho não pega strings desatualizadas em README/manifest:

```python
import asyncio
import re
from pathlib import Path

from todos.server import mcp

_TOOL_COUNT = 124
_DOC_FILES = ("README.md", "CLAUDE.md", "manifest.json", "src/todos/mcp_app.py")
_STALE = re.compile(r"\b(1[01][0-9]|12[0-3])\s*(tools|ferramentas)\b")  # 100–123

def test_tool_count_matches_runtime() -> None:
    registered = len(asyncio.run(mcp.list_tools()))
    assert registered == _TOOL_COUNT, (
        f"{registered} tools registradas, {_TOOL_COUNT} documentadas. "
        "Atualize os arquivos de _DOC_FILES e este teste."
    )

def test_no_stale_tool_count_in_docs() -> None:
    root = Path(__file__).resolve().parent.parent
    for rel in _DOC_FILES:
        text = (root / rel).read_text(encoding="utf-8")
        assert not _STALE.search(text), (
            f"{rel} contém uma contagem de tools desatualizada (esperado {_TOOL_COUNT})."
        )
```

O segundo teste falha enquanto qualquer doc citar uma contagem de 100–123, então
README e manifest não podem ficar para trás silenciosamente.

---

## 7. Plano de implantação

### Fase 0 — Fundação de schemas (habilita o resto)
1. Criar `src/todos/responses.py` com `NextAction`, `DocumentoResumo`,
   `ListaDocumentos`, `RespostaEscrita`, `Paginado`, `ProcessoDetalhe`.
2. Adicionar `_encode_cursor`/`_decode_cursor` (com validação que levanta
   `SEIValidationError`, §2.2) em `mcp_app.py`.
3. Corrigir contagem (124) nos **5 arquivos** de doc + os 2 testes de regressão
   (`mcp.list_tools()` e varredura de strings, §6).

**Conclusão**: `ruff` limpo, suíte verde, `responses.py` importável.

### Fase 1 — Paginação por cursor (aditivo, sem quebrar)
1. Aplicar envelope `Paginado` + `cursor` nas 12 tools de catálogo/blocos (§2.3).
2. `tem_proxima_inferida` honesto onde a inferência é `len>=limit`.
3. Manter campos legados (`pagina`, `tem_proxima`) durante transição.
4. Documentar `cursor`/`proximo_cursor` nas descriptions afetadas.

**Dependência**: Fase 0. **Conclusão**: toda tool paginável emite
`proximo_cursor`; nenhum `has_more` inferido sem marca.

### Fase 2 — Shaping de leitura pesada (paralela à Fase 3)
1. `_shape_lista_documentos` → `ListaDocumentos` em `arvore_processo`,
   `listar_documentos`.
2. `_shape_atividades` + parâmetro `ordem` em `listar_atividades`.
3. `_shape_consultar_processo` → modelo `ProcessoDetalhe`; `next_actions` no lugar
   de `_documentos_truncados`.
4. `sei_pesquisar_processos` → poda Solr + `Paginado`.
5. `include_raw: bool = False` nas 5 tools de leitura pesada (§1.5).

**Dependência**: Fases 0–1. **Conclusão**: nenhuma leitura pesada retorna lista
raw sem envelope; truncamento sempre com `next_actions`.

### Fase 3 — Shaping de escrita (paralela à Fase 2)
1. `_shape_resposta_escrita` → `RespostaEscrita` nas 5 tools de criação/alteração.
2. Teste mock por tool: id+protocolo do recurso criado nunca se perdem.

**Dependência**: Fase 0. Arquivos distintos da Fase 2 (sem conflito de merge).

### Fase 4 — Erros recuperáveis
1. Enriquecer `SEIError` com `error_code`/`recoverable`/`suggested_*` + `_render`
   que **embute a continuação na string da mensagem** (§3.1, §3.1.1) — único canal
   que sobrevive ao transporte enquanto `structuredContent` não é adotado.
2. Levantar na origem os 5 casos canônicos (§3.2).
3. Testes: a mensagem entregue ao agente contém `suggested_next_tool`/`args`;
   nenhum stack trace/segredo no corpo; atributos estruturados ficam no objeto
   para logging e adoção futura de `structuredContent`.

**Dependência**: Fase 0.

### Fase 5 — Evals
1. Gravar cassetes VCR contra ANTAQ/SEI-RO para o processo-fixture.
2. `evals/golden.xml` (10 QA, §4.4), `runner.py` (loop de agente), `conftest.py`,
   `test_evals.py`, marker `eval`, workflow CI (T1 sempre, T2 on-demand).
3. Rodar; onde a taxa < 90%, corrigir **description ou response shape**, nunca a
   pergunta.

**Dependência**: Fases 1–4 verdes (shaping/erros afetam o que o agente vê).

### Sequência
```
Fase 0 ─► Fase 1 ─┬─► Fase 2 ──┐
                  ├─► Fase 3 ──┤
                  └─► Fase 4 ──┴─► Fase 5
```

---

## 8. Critérios de conclusão

| Critério | Verificação |
|---|---|
| Respostas shaped são modelos Pydantic, não dicts | grep por `_shape_*` retorna modelos de `responses.py` |
| Toda paginável emite `proximo_cursor`; inferência marcada | teste de envelope por tool |
| Leitura pesada trunca em ≤50 com `next_actions` | teste mock de lista > 50 |
| `include_raw=True` devolve payload completo | teste por tool |
| Escrita não vaza payload REST; id+protocolo preservados | teste mock por tool |
| Erros recuperáveis carregam `suggested_next_tool`/`args`; nada vaza | teste de serialização de erro |
| 10 QA multi-call passam sobre cassetes | CI `test_evals.py` |
| Contagem documentada == real (124) | `test_tool_count_matches_documentation` |
| `ruff check . && ruff format --check .` limpos | CI |
| Suíte 219+ verde | CI |

---

## 9. Riscos

| Risco | Prob. | Mitigação |
|---|---|---|
| Shaper poda campo que downstream precisa | Médio | Testes mock por tool; nunca podar `id*`/`protocolo*` |
| Cursor opaco confunde agentes que esperavam `pagina` | Baixo | Campos legados mantidos na transição; `cursor` tem precedência só quando passado |
| `tem_proxima_inferida` → página vazia na borda | Baixo | Flag avisa o agente; receber vazio é tolerável e documentado |
| Eval flaky (LLM não-determinístico) sobre cassete | Médio | `temperature=0`; rodar 3×, maioria; cassete fixa o I/O — variância só do modelo |
| Cassetes desatualizam vs SEI real | Médio | Job mensal de regravação; cassetes versionados; divergência vira issue |
| Pydantic de resposta quebra "no future annotations" | Baixo | Modelos isolados em `responses.py`; não afetam type hints de entrada das tools |
| Renomear/contagem diverge de novo | Garantido sem teste | `test_tool_count_matches_documentation` falha no CI |

---

## 10. Smell test (auto-revisão da skill)

| Smell | Veredito |
|---|---|
| Tools `get`/`list`/`query` sem intenção | ✅ Limpo — tudo `sei_<verbo>_<objeto>` |
| Espelho 1:1 do REST (Adapter) | ✅ Facade — composite, web backend, orquestração, resources |
| Retorna upstream sem shape | ⚠️→✅ após Fases 2–3 |
| List tool sem `limit` | ⚠️→✅ após Fase 1 (cursor + limit) |
| Trunca/erra sem dizer como continuar | ⚠️→✅ após Fases 2 e 4 (`next_actions`/`suggested_*`) |
| Descrições "gets data" ou infladas | ⚠️ parcial — podar infladas (§5) |
| IDs sem nomes | ⚠️→✅ após §1.2 |
| Write/sign/delete sem confirmação | ✅ gate `elicit` que falha fechado (RFC 0006) |
| Throw em input ruim em vez de erro acionável | ✅ `SEIError(ToolError)`, +recuperável na Fase 4 |
| Log em stdout sob stdio | ✅ stderr |
| Sem evals | ⚠️→✅ após Fase 5 |

Pré-RFC: 4 smells verdadeiros (≥3 = "agente vai sofrer"). Pós-RFC: 0 bloqueantes,
1 contínuo (poda de descrições, §5).

---

## 11. Fora de escopo

- **`structuredContent`/`outputSchema` ativo**: a Fase 0 deixa os modelos prontos
  (adoção vira fiação), mas ligar depende de suporte estável do cliente — RFC 0008.
- **Renomeação por domínio (`process_*`/`sign_*`)**: breaking em 124 tools;
  condicionada a sinal de eval (§5).
- **Capability-search tool**: só se T1 mostrar degradação de seleção (§5).
- **Consolidação de tools (reduzir a surface)**: decisão de produto.
- **Paginação interna em `arvore_processo`**: o scraper web busca tudo de uma vez;
  paginar exige mudança no backend web.
