---
okf:
  type: spec
  subtype: okf-md/audit
  version: "1.0"
  status: draft
  scope: "Especificação do tipo de documento OKF MD — audit"
  authors:
    - franklinbaldo
  created: 2026-06-25
---

# OKF MD — Spec: `audit`

**OKF MD** (Open Knowledge Format Markdown) é um conjunto de tipos de documento com frontmatter YAML padronizado e estrutura de seções definida por tipo. Documentos OKF MD são legíveis por humanos *e* parseáveis por ferramentas automatizadas.

---

## 1. Estrutura obrigatória de todo documento OKF MD

```markdown
---
okf:
  type: <tipo>          # string, obrigatório — identifica o template (audit, rfc, adr, etc.)
  subtype: <subtipo>    # string, opcional — especialização (ex: "return-contract")
  version: "<semver>"   # string entre aspas, obrigatório
  status: <status>      # draft | review | final | superseded
  scope: <escopo>       # string descrevendo o que está sendo auditado
  authors:              # lista de strings, obrigatório
    - <autor>
  created: <YYYY-MM-DD> # data ISO, obrigatório
  updated: <YYYY-MM-DD> # data ISO, opcional
  tags:                 # lista de strings, opcional
    - <tag>
---

# <Título do documento>

<Parágrafo de contexto: por que este documento existe, qual regra está sendo verificada.>

---
```

---

## 2. Tipo `audit`

Documenta violações de uma regra ou padrão de código, organizadas por arquivo.

### 2.1 Frontmatter adicional para `audit`

| Campo | Tipo | Obrigatório | Descrição |
|---|---|---|---|
| `okf.type` | `"audit"` | sim | — |
| `okf.rule` | string | sim | Nome da regra auditada (ex: `return-contract`) |
| `okf.rule_ref` | string | não | Referência externa (livro, RFC, link) |
| `okf.severity_scale` | lista | não | Escala de severidade usada (padrão: `[critical, high, medium, low, info]`) |
| `okf.codebase` | string | não | Repositório ou caminho raiz auditado |
| `okf.audited_at` | `YYYY-MM-DD` | sim | Data da auditoria |

### 2.2 Seções obrigatórias do documento

```
# <Título>
## Contexto
## Regra auditada
## Resumo executivo
## Capítulos (um por arquivo)
## Índice de severidade
```

### 2.3 Template de capítulo (por arquivo)

Cada arquivo auditado recebe um capítulo `##` nomeado com o caminho relativo ao root do projeto.

````markdown
## `src/foo/bar.py`

> **Estado:** clean | violations-found | not-applicable

### Findings

| ID | Função / linha | Tipo | Severidade | Descrição |
|---|---|---|---|---|
| F-001 | `func_name:42` | <tipo> | high | <uma linha> |

#### F-001 — `func_name` (linha 42)

**Tipo:** <tipo-de-violação>
**Severidade:** high
**Padrão atual:**
```python
# código ofensivo
```
**Problema:** <explicação de por que viola a regra, qual invariante quebra>
**Refatoração sugerida:**
```python
# código corrigido
```
**Esforço:** low | medium | high
**Impacto se não corrigido:** <consequência prática>
````

Se o arquivo não tem violações:

````markdown
## `src/foo/bar.py`

> **Estado:** clean — nenhuma violação encontrada.
````

### 2.4 Tipos de violação canônicos para `return-contract`

| Código | Nome | Descrição |
|---|---|---|
| `RC-TUPLE` | Tuple-as-discriminated-union | Função retorna `tuple[status, payload]` onde `status` é sentinel |
| `RC-SENTINEL` | Sentinel return | Função retorna string/int mágico para codificar estado |
| `RC-NONE-AS-ERROR` | None-as-error | `None` significa erro, não "ausência de valor" |
| `RC-UNION-STATUS` | Union-status | `-> X \| None` onde `None` codifica falha, não resultado vazio legítimo |
| `RC-BOOL-ERROR` | Bool-as-error | `bool` retornado para indicar sucesso/falha em vez de exceção |
| `RC-DICT-STATUS` | Dict-with-status-key` | Dict retornado com chave `"ok"`, `"status"`, `"error"` codificando estado |

### 2.5 Escala de severidade

| Nível | Critério |
|---|---|
| `critical` | Oculta falhas silenciosamente em path de segurança / privacidade |
| `high` | Caller pode ignorar o erro e continuar com dados inválidos |
| `medium` | Dificulta entendimento mas raramente produz bug em produção |
| `low` | Inconsistência com o padrão; low chance de bug real |
| `info` | Oportunidade de melhoria sem risco imediato |

### 2.6 Seção `## Índice de severidade`

Tabela consolidada no final do documento:

```markdown
## Índice de severidade

| Severidade | Quantidade | Arquivos afetados |
|---|---|---|
| critical | N | arquivo1, arquivo2 |
| high | N | … |
| medium | N | … |
| low | N | … |
| **Total** | **N** | — |
```

---

## 3. Extensibilidade

Novos tipos de documento OKF MD (ex: `rfc`, `adr`, `runbook`) seguem a mesma estrutura de frontmatter obrigatório + seções definidas pelo tipo. O campo `okf.type` é o discriminador. Parsers devem falhar graciosamente em campos desconhecidos — tratar como `info`.

---

## 4. Convenções de nomenclatura de arquivo

```
docs/audits/<YYYY-MM-DD>-<slug>.md        # audit datado
docs/audits/_spec/<slug>.md               # specs dos tipos OKF MD
docs/rfc/<NNN>-<slug>.md                  # type: rfc (singular — convenção do repo)
docs/adr/<NNN>-<slug>.md                  # type: adr (singular — convenção do repo)
```

---

*Este documento é ele mesmo um OKF MD do tipo `spec` e serve como exemplo canônico.*
