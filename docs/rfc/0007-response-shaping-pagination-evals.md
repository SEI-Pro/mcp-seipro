# RFC 0007 — Response shaping, paginação completa e eval harness

**Status**: Proposta
**Data**: 2026-06-17
**Autores**: Franklin Baldo (com Claude Code)
**Relacionado**: RFC 0006 (backend abstrato), RFC 0004 (exceções)

---

## 0. Contexto e motivação

Com a RFC 0006 concluída, o servidor tem arquitetura limpa (composite backend,
módulos por domínio, 124 tools registradas) e suíte verde (219 testes). O
próximo eixo de melhoria é a **qualidade das respostas** — o que cada tool
devolve ao agente depois que o backend executa.

Três lacunas independentes, em ordem de impacto:

| Lacuna | Impacto direto | Seção |
|---|---|---|
| **Response shaping incompleto** | Payload bruto pode ter centenas de itens; agente gasta contexto sem necessidade | §1 |
| **Contrato de paginação incompleto** | Ferramentas de listagem sem `tem_proxima` enganam o agente sobre completude | §2 |
| **Ausência de eval harness** | Com 124 tools, seleção errada é o maior risco real; sem evals, é invisível | §3 |

Uma lacuna menor mas concreta:

| Lacuna | Impacto | Seção |
|---|---|---|
| **Drift de contagem de tools** | Documentação diz "121 tools", código tem 124 | §4 |

---

## 1. Response shaping

### 1.1 Estado atual

`_json` já usa `separators=(",", ":")` — sem `indent=2`, sem overhead de
whitespace. O gargalo é o **conteúdo**, não a formatação.

`sei_consultar_processo` tem `_shape_consultar_processo` (trunca `documentos[]`
para 30, adiciona `_documentos_truncados`). `sei_listar_processos` tem envelope
com `total_itens`/`tem_proxima`/`total_filtrados`. `sei_resumo_processos`
devolve envelope `{agrupamento, total_processos, grupos}`.

Todos os demais fazem `return _json(result)` com o dict bruto do backend.

### 1.2 Problema

| Tool | Risco | Payload típico |
|---|---|---|
| `sei_arvore_processo` | Processo com 200+ docs: lista bruta inteira | 30–150 KB |
| `sei_listar_documentos` | Mesma lista, campos duplicados | 20–100 KB |
| `sei_listar_atividades` | Histórico completo sem paginação | 5–60 KB |
| `sei_pesquisar_processos` | Resultado Solr sem `tem_proxima` | 5–40 KB |
| `sei_criar_processo` / `sei_criar_documento` | REST response completo; agente só precisa de id+protocolo | 1–5 KB |
| `sei_listar_meus_acompanhamentos` | Lista raw; sem total | variável |
| `sei_listar_blocos` / `sei_listar_blocos_assinatura` | Lista raw; sem total | variável |

### 1.3 Proposta: camada de shaping por categoria

#### 1.3.1 Listas de documentos / árvore (truncamento com sinal)

**Princípio**: retornar os primeiros N itens, sempre com `total` no topo,
e `_truncado` com instrução de refinamento se houver mais.

Constante proposta: `_LISTA_DOCS_LIMIT = 50` (aumenta o `_DOCS_INLINE_LIMIT`
atual de 30 para consistência com `listar_documentos`).

`sei_arvore_processo` e `sei_listar_documentos` devem aplicar
`_shape_lista_documentos(result, protocolo)`:

```python
def _shape_lista_documentos(result: dict | list, protocolo: str) -> dict:
    docs: list = result if isinstance(result, list) else result.get("documentos", [])
    total = len(docs)
    truncado = total > _LISTA_DOCS_LIMIT
    campos_essenciais = {"id", "tipo_documento", "nome_composto", "sigla_unidade",
                         "numero_sei", "assinado", "cancelado", "volume"}
    shaped = [
        {k: v for k, v in doc.items() if k in campos_essenciais}
        for doc in docs[:_LISTA_DOCS_LIMIT]
    ]
    out: dict = {
        "processo": protocolo,
        "total_documentos": total,
        "documentos": shaped,
    }
    if truncado:
        out["_truncado"] = (
            f"Exibindo {_LISTA_DOCS_LIMIT} de {total}. "
            "Para a lista completa, use sei_arvore_processo com paginação futura "
            "ou sei_listar_documentos."
        )
    return out
```

**Nota de design**: `sei_arvore_processo` e `sei_listar_documentos` servem
propósitos diferentes (visualização estrutural vs. enumeração plana) mas ambas
retornam listas de documentos — o mesmo shaper serve os dois.

#### 1.3.2 Histórico de atividades (truncamento por recência)

`sei_listar_atividades` retorna o histórico completo de um processo (pode ter
centenas de entradas). O agente quase sempre quer as entradas mais recentes.

Shaper `_shape_atividades(result, processo)`:
- `total_atividades`: total real
- `atividades`: últimas 50, ordem cronológica inversa (mais recente primeiro)
- `_truncado`: instrução se `total > 50`
- Campos por item: `data_hora`, `unidade`, `usuario`, `descricao` (podar
  campos de metadata interna que o backend REST eventualmente inclui)

Parâmetro opcional `ordem: Literal["asc", "desc"] = "desc"` na tool
(default desc = mais recente primeiro) — mudança de interface, documentada.

#### 1.3.3 Respostas de escrita (só o essencial)

Criação e alteração devolvem o id do recurso criado, protocolo, e confirmação.
O payload REST completo vai para `backend.*`, não chega ao agente.

`_shape_resposta_escrita(result, acao)`:

```python
def _shape_resposta_escrita(result: dict, acao: str) -> dict:
    out: dict = {"acao": acao, "status": "ok"}
    for campo in ("IdProcedimento", "id_procedimento", "id"):
        if campo in result:
            out["id_procedimento"] = result[campo]
            break
    for campo in ("ProtocoloProcedimentoFormatado", "protocolo"):
        if campo in result:
            out["protocolo"] = result[campo]
            break
    for campo in ("IdDocumento", "id_documento"):
        if campo in result:
            out["id_documento"] = result[campo]
            break
    for campo in ("ProtocoloDocumentoFormatado", "numero_sei"):
        if campo in result:
            out["numero_sei"] = result[campo]
            break
    if "mensagem" in result:
        out["mensagem"] = result["mensagem"]
    return out
```

Tools afetadas: `sei_criar_processo`, `sei_alterar_processo`,
`sei_criar_documento`, `sei_criar_documento_externo`,
`sei_alterar_documento_interno`.

**Exceção importante**: `sei_criar_documento` já retorna o id do documento
criado em `result["id_documento"]` (ou similar) — o shaper só precisa
normalizar e prunear, não perder o id.

#### 1.3.4 Pesquisa de processos (envelope + campos essenciais)

`sei_pesquisar_processos` retorna um dict com chave `processos` (lista) mais
metadados Solr. O shaper deve:
- Preservar `total_resultados`, `pagina_atual`
- Adicionar `tem_proxima` (ver §2)
- Em cada item, preservar: `protocolo`, `tipo`, `especificacao`, `data_geracao`,
  `id_procedimento`, `unidade_geradora` (se presente)
- Podar campos Solr internos (`_version_`, `score`, etc.)

#### 1.3.5 Listas de acompanhamentos e blocos

`sei_listar_meus_acompanhamentos`, `sei_listar_acompanhamentos_processo`,
`sei_listar_blocos`, `sei_listar_blocos_assinatura`: devem adicionar
`total_itens` no envelope se ausente, e `tem_proxima` quando aplicável.

### 1.4 O que NÃO mudar

- `sei_ler_documento`: retorna conteúdo do documento (texto/markdown/HTML) —
  já é o conteúdo útil, não um payload estruturado.
- `sei_consultar_documento_interno` / `sei_consultar_documento_externo`: já
  retornam um único objeto — shaping de objeto único não é necessário agora.
- `sei_listar_sobrestamentos`, `sei_listar_interessados`,
  `sei_listar_unidades_processo`: listas pequenas (< 20 itens em 99% dos
  casos), risco de token baixo, sem mudança.
- Todas as tools de escrita que já devolvem `{"mensagem": "..."}` ou shapes
  manuais (ex: `sei_marcar_nao_lido`, `sei_executar_acao`).

### 1.5 Onde o código fica

Todos os shapers (`_shape_lista_documentos`, `_shape_atividades`,
`_shape_resposta_escrita`, `_shape_pesquisa_processos`) ficam em
`src/todos/tools/processos.py` e `documentos.py` ao lado dos tools que
os chamam — **não** em `mcp_app.py` (que é camada de infra, não de domínio).

---

## 2. Contrato de paginação

### 2.1 Estado atual

| Sinal | Tools com | Tools sem |
|---|---|---|
| `total_itens` | `sei_listar_processos`, catalogs (web) | `sei_listar_atividades`, `sei_listar_relacionamentos`, `sei_arvore_processo` |
| `tem_proxima` | `sei_listar_processos`, `sei_listar_blocos_assinatura` (web) | catalogs, `sei_pesquisar_processos`, blocos_internos |
| `pagina_atual` | `sei_listar_processos` | todos os demais |

### 2.2 Problema

Sem `tem_proxima`, o agente não sabe se recebeu a lista completa ou uma fatia.
Exemplos concretos:

- `sei_pesquisar_tipos_processo(limit=50)`: retorna 50 tipos. Há 200? O agente
  não sabe; pode tomar uma decisão errada usando um tipo que não é o correto.
- `sei_listar_blocos(limit=20)`: retorna 20 blocos. Tem mais? Sem sinal, o
  agente assume que é tudo.

### 2.3 Proposta: contrato uniforme de listagem

Todo `return _json(result)` de uma tool de listagem paginada deve garantir o
envelope `ListagemEnvelope`:

```
{
  "items_key": [...],        # chave existente (processos, tipos, blocos, etc.)
  "total_itens": int | None, # total do servidor (None se desconhecido)
  "pagina_atual": int,       # 0-indexed
  "tem_proxima": bool,       # true se há mais páginas
}
```

**Regra de inferência `tem_proxima`** quando o backend não informa o total:
`len(items) >= limit` — se recebemos exatamente `limit` itens, pode haver mais.
Quando o total é conhecido: `(pagina_atual + 1) * limit < total_itens`.

**Implementação**: helper `_envelope_listagem` em `mcp_app.py`:

```python
def _envelope_listagem(
    result: dict,
    chave: str,
    limit: int,
    pagina: int,
) -> dict:
    """Garante que result tem total_itens, pagina_atual e tem_proxima."""
    items = result.get(chave, [])
    total = result.get("total_itens")
    tem_proxima = result.get("tem_proxima")
    if tem_proxima is None:
        if total is not None:
            tem_proxima = (pagina + 1) * limit < total
        else:
            tem_proxima = len(items) >= limit
    return {
        chave: items,
        "total_itens": total,
        "pagina_atual": pagina,
        "tem_proxima": tem_proxima,
    }
```

### 2.4 Tools afetadas

| Tool | Chave | Situação atual | Ação |
|---|---|---|---|
| `sei_pesquisar_tipos_processo` | `tipos` | `total_itens` OK, sem `tem_proxima` | adicionar `_envelope_listagem` |
| `sei_pesquisar_tipos_documento` | `tipos` | idem | idem |
| `sei_pesquisar_hipoteses_legais` | `hipoteses` | idem | idem |
| `sei_pesquisar_marcadores` | `marcadores` | idem | idem |
| `sei_pesquisar_unidades` | `unidades` | idem | idem |
| `sei_listar_modelos` | `modelos` | idem | idem |
| `sei_listar_assuntos` | `assuntos` | idem | idem |
| `sei_pesquisar_contatos` | `contatos` | idem | idem |
| `sei_listar_textos_padrao` | `textos` | idem | idem |
| `sei_listar_blocos` | `blocos` | sem `tem_proxima` | idem |
| `sei_listar_blocos_assinatura` | `blocos` | `tem_proxima` OK na web | verificar REST |
| `sei_pesquisar_processos` | `processos` | sem `tem_proxima` | shaper §1.3.4 + envelope |

### 2.5 Tools que ficam sem paginação

Listas inerentemente completas (sem `limit` no backend): `listar_sobrestamentos`,
`listar_interessados`, `listar_unidades_processo`, `listar_relacionamentos`,
`listar_atividades` (paginação aqui é por truncamento do shaper — ver §1.3.2).
Para essas, adicionar `total_itens: len(items)` mas sem `tem_proxima`
(semântica: "isto é tudo").

---

## 3. Eval harness de seleção de tool

### 3.1 Por que evals de seleção

A suíte de 219 testes verifica que o código faz o que está escrito — parsers,
backends, gates. **Não** verifica que o agente *escolhe a tool certa* entre 124
candidatas ao receber uma pergunta em linguagem natural. Com 124 tools, a
probabilidade de ambiguidade é alta:

| Pergunta | Tools candidatas | Confusão esperada |
|---|---|---|
| "Liste os processos da minha unidade" | `sei_listar_processos` vs `sei_resumo_processos` | resumo é mais rico, mas não lista |
| "Busca o documento 2843449" | `sei_buscar_documento` vs `sei_ler_documento` | ler é direto; buscar resolve número SEI |
| "Qual a árvore do processo X?" | `sei_arvore_processo` vs `sei_consultar_processo` | consultar inclui árvore truncada |
| "Anote observação no processo X" | `sei_anotar_processo` vs `sei_registrar_andamento` | anotação é um post-it; andamento é log oficial |
| "Liste meus acompanhamentos" | `sei_listar_meus_acompanhamentos` vs `sei_listar_acompanhamentos_processo` | sujeito diferente |
| "Assine o bloco de assinatura" | `sei_assinar_bloco` vs `sei_assinar_documento` | contexto de bloco vs. doc individual |
| "Adicione o processo X ao bloco" | `sei_incluir_processo_bloco` vs `sei_incluir_documento_bloco_assinatura` | bloco interno vs. bloco de assinatura |
| "Ver tipos de processo disponíveis" | `sei_pesquisar_tipos_processo` + resource `sei://hipoteses-legais` | resource pode ser suficiente |

### 3.2 Estrutura proposta

```
evals/
  golden.json          # casos de teste (query → tool esperada + params subset)
  conftest.py          # fixtures: carrega golden.json, instancia runner
  test_tool_selection.py  # pytest: chama runner, compara seleção
  runner.py            # usa Anthropic SDK para chamar agente com tool list
  README.md            # como rodar, como adicionar casos
```

#### `evals/golden.json` — estrutura de cada caso

```json
{
  "id": "listar-processos-unidade",
  "query": "Liste os processos abertos na minha unidade",
  "expected_tool": "sei_listar_processos",
  "expected_params": {},
  "not_tools": ["sei_resumo_processos", "sei_pesquisar_processos"],
  "tags": ["processos", "listagem", "inbox"],
  "rationale": "sei_resumo_processos agrega; sei_pesquisar_processos busca por texto. A caixa de entrada é sei_listar_processos."
}
```

Campos:
- `id`: slug único
- `query`: pergunta em linguagem natural (em português, como um usuário real escreveria)
- `expected_tool`: nome exato da tool que deve ser chamada
- `expected_params`: subconjunto de parâmetros esperados (match parcial; vazio = só a tool importa)
- `not_tools`: lista de tools que NÃO devem ser chamadas (para distinguir entre similares)
- `tags`: domínio do caso (para filtragem)
- `rationale`: justificativa do caso (documentação, não executada)

#### `evals/runner.py`

```python
"""Runner de evals de seleção de tool.

Usa o Anthropic SDK com tool_choice="any" para forçar o modelo a
selecionar uma tool dado o schema completo do servidor + a query.
Compara a tool selecionada contra o expected_tool do caso.
"""
import anthropic
import json
from pathlib import Path

from todos.mcp_app import mcp  # acessa o registro de tools

_MODEL = "claude-haiku-4-5-20251001"  # barato para evals

def _tools_schema() -> list[dict]:
    """Exporta o schema de todas as tools registradas no servidor."""
    # FastMCP expõe as tools via mcp._tool_manager ou similar
    ...

def run_case(case: dict) -> dict:
    """Executa um caso de eval. Retorna {passed, selected_tool, error}."""
    client = anthropic.Anthropic()
    response = client.messages.create(
        model=_MODEL,
        max_tokens=256,
        tools=_tools_schema(),
        tool_choice={"type": "any"},
        messages=[{"role": "user", "content": case["query"]}],
    )
    selected = response.content[0].name if response.stop_reason == "tool_use" else None
    passed = selected == case["expected_tool"]
    if not_tools := case.get("not_tools", []):
        passed = passed and selected not in not_tools
    return {"passed": passed, "selected": selected, "expected": case["expected_tool"]}
```

**Nota**: o runner usa `claude-haiku-4-5-20251001` (barato) para manter o
custo de evals em ~$0.01 por caso. A suíte completa de 20 casos custa ~$0.20
por rodada, viável em CI com `pytest -m eval --api-key=$ANTHROPIC_API_KEY`.

#### `evals/test_tool_selection.py`

```python
import pytest
import json
from pathlib import Path

GOLDEN = json.loads((Path(__file__).parent / "golden.json").read_text())

@pytest.mark.eval
@pytest.mark.parametrize("case", GOLDEN, ids=[c["id"] for c in GOLDEN])
def test_tool_selection(case):
    from evals.runner import run_case
    result = run_case(case)
    assert result["passed"], (
        f"Expected {result['expected']!r}, got {result['selected']!r}"
    )
```

O marker `eval` é declarado em `pyproject.toml`:
```toml
[tool.pytest.ini_options]
markers = ["eval: testes de seleção de tool via API Anthropic (requerem ANTHROPIC_API_KEY)"]
```

Sem `ANTHROPIC_API_KEY`, os testes são pulados automaticamente via fixture.

### 3.3 Conjunto inicial de 20 casos (golden.json)

Cobrindo as confusões de maior risco:

| id | query | expected_tool | not_tools |
|---|---|---|---|
| listar-processos-unidade | "Liste os processos abertos na minha unidade" | `sei_listar_processos` | `sei_resumo_processos` |
| resumo-por-tipo | "Quantos processos de cada tipo tem na minha caixa?" | `sei_resumo_processos` | `sei_listar_processos` |
| ler-documento-numero | "Leia o documento SEI nº 2843449" | `sei_ler_documento` | `sei_buscar_documento` |
| buscar-sem-ler | "Existe o documento SEI 2843449?" | `sei_buscar_documento` | `sei_ler_documento` |
| arvore-completa | "Quero ver todos os documentos do processo 50300.000123/2025-00" | `sei_arvore_processo` | `sei_consultar_processo` |
| consultar-processo | "Mostre os dados do processo 50300.000123/2025-00" | `sei_consultar_processo` | `sei_arvore_processo` |
| registrar-andamento | "Registre que analisamos e aprovamos o pedido no processo X" | `sei_registrar_andamento` | `sei_anotar_processo` |
| anotar-processo | "Coloque uma anotação rápida no processo X" | `sei_anotar_processo` | `sei_registrar_andamento` |
| meus-acompanhamentos | "Quais processos estou acompanhando?" | `sei_listar_meus_acompanhamentos` | `sei_listar_acompanhamentos_processo` |
| acompanhamentos-processo | "Quem está acompanhando o processo X?" | `sei_listar_acompanhamentos_processo` | `sei_listar_meus_acompanhamentos` |
| criar-documento-interno | "Crie um despacho no processo X" | `sei_criar_documento` | `sei_criar_documento_externo` |
| criar-documento-externo | "Anexe o PDF /tmp/laudo.pdf ao processo X" | `sei_criar_documento_externo` | `sei_criar_documento` |
| assinar-documento | "Assine o documento 12345 do processo X" | `sei_assinar_documento` | `sei_assinar_bloco` |
| assinar-bloco | "Assine todos os documentos do bloco de assinatura 99" | `sei_assinar_bloco` | `sei_assinar_documento` |
| incluir-bloco-assinatura | "Adicione o documento 12345 ao bloco de assinatura 99" | `sei_incluir_documento_bloco_assinatura` | `sei_incluir_processo_bloco` |
| incluir-bloco-interno | "Adicione o processo X ao bloco interno de revisão" | `sei_incluir_processo_bloco` | `sei_incluir_documento_bloco_assinatura` |
| pesquisar-tipos | "Quais tipos de processo posso abrir?" | `sei_pesquisar_tipos_processo` | `sei_pesquisar_tipos_documento` |
| trocar-unidade | "Mude minha unidade ativa para GPF" | `sei_trocar_unidade` | `sei_pesquisar_unidades` |
| enviar-processo | "Encaminhe o processo X para a unidade GPF" | `sei_enviar_processo` | `sei_atribuir_processo` |
| atribuir-processo | "Atribua o processo X para o servidor João" | `sei_atribuir_processo` | `sei_enviar_processo` |

### 3.4 CI

Os evals ficam em job separado no GitHub Actions, disparado manualmente
(`workflow_dispatch`) ou em PRs que tocam `tools/`, `mcp_app.py` ou
`src/todos/server.py`. Rodam em ambiente com `ANTHROPIC_API_KEY` em secret.

```yaml
# .github/workflows/evals.yml
name: Evals (tool selection)
on:
  workflow_dispatch:
  pull_request:
    paths:
      - 'src/todos/tools/**'
      - 'src/todos/mcp_app.py'
      - 'src/todos/server.py'
      - 'evals/**'
jobs:
  evals:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v3
      - run: uv sync
      - run: uv run pytest evals/ -m eval -v
        env:
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
```

---

## 4. Drift de contagem de tools

### 4.1 Estado atual

O arquivo `mcp_app.py` (instructions string) e o CLAUDE.md dizem "121 tools".
O comentário em `server.py` (linha 68) diz "115 tools" nos módulos + 6 em
`server.py` = 121. A contagem real por `@mcp.tool`:

| Arquivo | Registros |
|---|---|
| `tools/processos.py` | 27 |
| `tools/documentos.py` | 14 |
| `tools/blocos_assinatura.py` | 14 |
| `tools/catalogos.py` | 12 |
| `tools/unidades.py` | 13 |
| `tools/marcadores.py` | 9 |
| `tools/acompanhamento.py` | 8 |
| `tools/blocos_internos.py` | 10 |
| `tools/assinatura.py` | 7 |
| `tools/credenciamento.py` | 4 |
| `server.py` | 6 |
| **Total** | **124** |

Drift de 3 tools — provavelmente ferramentas adicionadas à RFC 0006 sem
atualizar a docstring.

### 4.2 Proposta

1. Atualizar `mcp_app.py` `instructions`: "124 tools" → manter em sync com o
   total real.
2. Atualizar comentário em `server.py` linha 68.
3. Atualizar CLAUDE.md.
4. Adicionar teste de regressão em `tests/test_tool_routing.py`:

```python
def test_tool_count_matches_documentation():
    """Garante que a contagem documentada bate com o registro real."""
    from todos.server import mcp  # dispara registro de todas as tools
    registered = len(mcp._tool_manager.tools)
    assert registered == 124, (
        f"Contagem de tools diverge: {registered} registradas, 124 documentadas. "
        "Atualize o número em mcp_app.py instructions, server.py e CLAUDE.md."
    )
```

Quando uma nova tool for adicionada, o teste falha e força atualização da
documentação — sem drift silencioso.

---

## 5. Plano de implantação

### Fase 1 — Drift e contrato de paginação (baixo risco, sem mudança de comportamento)

**Objetivo**: corrigir a contagem de tools e fechar o contrato de paginação
nos catálogos. Sem mudança de campos, só adição de campos novos.

**Tarefas**:
1. Adicionar `_envelope_listagem` em `mcp_app.py`.
2. Aplicar `_envelope_listagem` nas 11 tools de catálogos e blocos (§2.4).
3. Adicionar `total_itens: len(items)` nas 5 listas inerentemente completas
   (§2.5).
4. Atualizar contagem (124) em 3 lugares + teste de regressão.
5. Atualizar descriptions das tools modificadas para documentar os campos de
   envelope.

**Critério de conclusão**: `ruff check .` limpo; `uv run pytest` verde;
todas as tools de listagem documentam `tem_proxima` na descrição.

**Estimativa**: 1 sessão de código, sem dependências externas.

### Fase 2 — Response shaping das tools de leitura pesada

**Objetivo**: `sei_arvore_processo`, `sei_listar_documentos`,
`sei_listar_atividades`, `sei_pesquisar_processos` devolvem payloads shaped.

**Tarefas**:
1. Implementar `_shape_lista_documentos` em `tools/processos.py` e aplicar
   em `sei_arvore_processo` e `sei_listar_documentos`.
2. Implementar `_shape_atividades` e aplicar em `sei_listar_atividades`.
   Adicionar parâmetro `ordem: str = "desc"` na tool.
3. Implementar `_shape_pesquisa_processos` em `server.py` e aplicar em
   `sei_pesquisar_processos`. Integrar `_envelope_listagem` (Fase 1).
4. Atualizar descriptions das 4 tools com os campos shaped e o comportamento
   de truncamento.
5. Atualizar testes em `test_tool_routing.py` para verificar presença de
   `total_documentos`/`tem_proxima`/`_truncado` nos shapes.

**Critério de conclusão**: suíte verde; nenhuma das 4 tools retorna lista raw
sem envelope; truncamento documentado nas descriptions.

**Dependência**: Fase 1 (usa `_envelope_listagem`).

### Fase 3 — Response shaping das tools de escrita

**Objetivo**: tools de criação/alteração devolvem só id+protocolo+mensagem.

**Tarefas**:
1. Implementar `_shape_resposta_escrita` em `mcp_app.py`.
2. Aplicar em: `sei_criar_processo`, `sei_alterar_processo`,
   `sei_criar_documento`, `sei_criar_documento_externo`,
   `sei_alterar_documento_interno`.
3. Verificar que id e protocolo do recurso criado **não** se perdem no shaping
   (teste unitário mock para cada tool).
4. Atualizar descriptions: "Retorna `{id_procedimento, protocolo, status}`".

**Critério de conclusão**: nenhuma tool de escrita retorna payload REST bruto;
testes unitários verificam que id+protocolo chegam ao agente.

**Dependência**: nenhuma (paralela às Fases 1–2).

### Fase 4 — Eval harness

**Objetivo**: 20 casos golden rodando em CI.

**Tarefas**:
1. Criar `evals/` com `golden.json` (20 casos da §3.3), `runner.py`,
   `conftest.py`, `test_tool_selection.py`.
2. Implementar `_tools_schema()` em `runner.py` — exporta o schema FastMCP.
3. Adicionar marker `eval` em `pyproject.toml`.
4. Criar `.github/workflows/evals.yml`.
5. Rodar localmente contra `ANTHROPIC_API_KEY` e fixar quaisquer casos
   com taxa de aprovação < 90% (ajustar a description da tool afetada, não o
   caso).

**Critério de conclusão**: 20/20 casos passando em 3 rodadas consecutivas;
workflow de CI configurado.

**Dependência**: Fases 1–3 devem estar concluídas antes de rodar evals
definitivos (shaping afeta a description e a usabilidade que o modelo vê).

### Sequência recomendada

```
Fase 1 (paginação + drift)
    │
    ├── Fase 2 (shaping leitura)   ── paralela ──► Fase 3 (shaping escrita)
    │                                                      │
    └───────────────────────────────────────────── Fase 4 (evals)
```

Fases 2 e 3 podem ser desenvolvidas em paralelo (arquivos distintos, sem
conflito de merge). Fase 4 começa depois que 1, 2 e 3 estão verdes.

---

## 6. Critérios de conclusão globais

| Critério | Verificação |
|---|---|
| Toda tool de listagem paginável tem `tem_proxima` | `grep -r "tem_proxima\|has_more"` cobre todas as `list/search` tools |
| Toda tool de listagem completa tem `total_itens` | idem |
| `sei_arvore_processo` e `sei_listar_documentos` não retornam mais de 50 items sem sinal | teste unitário com mock de lista > 50 |
| `sei_listar_atividades` não retorna mais de 50 atividades sem sinal | idem |
| `sei_criar_processo` / `sei_criar_documento` não vazam payload REST bruto | teste unitário mock |
| Contagem de tools documentada bate com a real | `test_tool_count_matches_documentation` |
| 20 evals de seleção passando | CI green em `evals.yml` |
| `ruff check . && ruff format --check .` limpos | CI pre-existing |
| Suíte de 219+ testes verde | CI pre-existing |

---

## 7. Riscos e mitigações

| Risco | Probabilidade | Mitigação |
|---|---|---|
| Shaper poda campo que alguma tool downstream precisa | Médio | Testes unitários mock por tool; nunca podar `id*` ou `protocolo*` |
| `tem_proxima` inferido errado (`len >= limit`) causa falso positivo | Baixo | Só ocorre na última página exata; agente pede página N+1 e recebe vazio — comportamento tolerável |
| Eval runner flaky (LLM não-determinístico) | Médio | Rodar 3× e contar maioria; parametrizar `temperature=0`; casos não-determinísticos → ajustar description |
| `_tools_schema()` quebra com FastMCP update | Baixo | Travar versão `mcp>=1.12,<2` e adicionar teste de importação |
| Fase 3 quebra integração de algum cliente externo que lia campos REST | Baixo | Shaping é **aditivo** nas tools de escrita (adiciona `status`/`acao`); campos id/protocolo são preservados |
| Tool count diverge novamente | Garantido sem teste | Teste de regressão (§4.2) falha no CI antes de mergear |

---

## 8. Fora de escopo desta RFC

- **`outputSchema` / structured content**: exige mudança no FastMCP (suporte
  experimental); adiado para RFC 0008 quando a API estabilizar.
- **Consolidação de tools**: reduzir a surface de 124 tools por agrupamento
  — decisão de produto, não de qualidade de resposta.
- **Paginação em `sei_arvore_processo`**: o scraper web busca todos os
  documentos de uma vez; paginar exigiria mudança no backend web. Adiado.
- **Localização de mensagens de erro**: os `_truncado` e `_documentos_truncados`
  já existem em português — padrão a manter.
