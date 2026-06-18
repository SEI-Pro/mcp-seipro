# RFC 0009 — Cobertura de backend, constraints de schema e guidance de domínio

**Status**: Proposta · **Atualizado**: 2026-06-18
**Data**: 2026-06-17
**Autores**: Claude (com Franklin Baldo)
**RFCs relacionados**: RFC 0003 (ergonomia FastMCP), RFC 0006 (backend abstrato), RFC 0007 (response shaping/paginação)

## 1. Contexto e design pattern

### 1.1 Arquitetura atual

O projeto usa **Chain of Responsibility + Strategy** com implementação por mixins:

| Componente | Papel |
|---|---|
| `SEIBackend` | Contrato com 125 métodos async (sem `@abstractmethod`) |
| `SEIRestBackend` | Strategy REST — implementa 111/125 (88%) via mixins por domínio |
| `SEIWebBackend` | Strategy Web — implementa 81/125 (64%) via mixins por domínio |
| `CompositeBackend` | Chain of Responsibility — REST-first com fallback web; web-first para 3 operações; composição paralela para `consultar_processo` |

Este padrão é **apropriado para o use case**: dois backends com capacidades
assimétricas, preferência configurável por operação (`_WEB_FIRST`), e
degradação graciosa quando o mod-wssei não está disponível.

### 1.2 Pontos de atenção na implementação

Três áreas onde a implementação pode melhorar — sem trocar o padrão:

1. **`_install_dispatchers()` opaco.** Gera ~100 métodos delegadores por
   introspecção no nível de módulo. IDE não navega para a implementação e
   stack traces ficam confusos. `__getattr__` em `CompositeBackend` faria o
   mesmo de forma transparente.

2. **`SEIBackend` sem `@abstractmethod`.** Justificado (subclasses implementam
   só o que suportam), mas o custo é que um backend incompleto só falha em
   runtime. `typing.Protocol` eliminaria o problema sem perder a flexibilidade.

3. **4 métodos sem implementação em nenhum backend:** `cancelar_assinatura`,
   `gerar_referencia`, `marcar_nao_lido`, `resumo_processos`. Ou o contrato
   está desatualizado ou esses métodos fazem bypass do contrato nas tools.

Os itens 1 e 2 são melhorias incrementais de RFC futuro. O item 3 é resolvido
pela proposta 2.1 abaixo.

## 2. Propostas

### 2.1 (Alta) Testes de cobertura de contrato por backend

A cobertura atual do contrato `SEIBackend` (125 métodos) não está medida nem
protegida pelo CI. Uma regressão silenciosa — remover um método de um mixin ou
renomear sem atualizar o outro backend — não quebra nenhum teste.

**Proposta:** dois testes de cobertura de contrato:

- `tests/test_rest_backend.py` — verifica via introspecção quais métodos do
  `SEIRestBackend` sobrescrevem o stub base; falha se a cobertura cair abaixo
  do limiar atual (88 %, 111/125) ou se um método previously implementado
  desaparecer.
- `tests/test_web_backend.py` — idem para `SEIWebBackend` (limiar: 64 %,
  81/125).

Os testes também reportam os 4 métodos sem implementação em nenhum backend
(`cancelar_assinatura`, `gerar_referencia`, `marcar_nao_lido`,
`resumo_processos`), para forçar decisão explícita: implementar ou remover do
contrato.

### 2.2 (Alta) Constraints de parâmetro no schema, não só na prosa

Vários params têm formato implícito documentado só em prosa —
`protocolo_formatado` (ex.: `50300.000123/2025-00`), datas (`YYYY-MM-DD`), IDs
numéricos. Um agente pode enviar `17/06/2026` ou um protocolo sem máscara e a
falha só aparece no SEI.

**Proposta:** anotar os params de formato conhecido com
`Annotated[str, Field(pattern=…, examples=…)]`, de modo que o `inputSchema`
carregue a constraint e o pydantic **rejeite entrada malformada antes da
chamada**. Começar pelas tools mais usadas (`sei_consultar_processo`,
`sei_criar_processo`, `sei_criar_documento`).

### 2.3 (Média) Guidance de domínio configurável e injetada na resposta

Com 118 tools, a curva de "qual tool/qual ordem" é íngreme. As instruções do
servidor MCP já existem (domínio do SEI), mas são estáticas e globais.

**Proposta:** um campo de instruções **configurável** (por órgão/unidade),
devolvido nas tools de entrada (ex.: `sei_listar_processos`,
`sei_resumo_processos`) como `dicas`/`_hints`, para guiar fluxos recorrentes
do usuário sem depender de prompt estático.

### 2.4 (Média) Checagem de error-boundary no CI

O `mcp-sei` já tem `SEIError` e `ToolError` (RFC 0003/0004). Falta o guarda
automatizado que garanta que a tradução de erros acontece só na fronteira — a
convenção hoje depende de disciplina manual.

**Proposta:** um check leve de CI que falhe se um módulo de domínio levantar
erro de transporte (ou vice-versa) fora da fronteira esperada.

### 2.5 (Baixa, opcional) Superfície CLI a partir da mesma definição

O `mcp-sei` é essencialmente MCP-only. Uma CLI fina derivada das mesmas funções
ajudaria smoke-tests e operação manual sem duplicar lógica.

**Proposta:** avaliar um adaptador CLI opcional sobre as funções de tool
existentes (esforço alto; registrar como direção, não compromisso).

## 3. Priorização

| Prioridade | Item | Esforço | Risco que mitiga |
|---|---|---|---|
| **Alta** | 2.1 Cobertura de backend | Baixo | Regressão silenciosa no contrato |
| **Alta** | 2.2 Constraints de schema | Médio | Entrada malformada chega ao SEI |
| Média | 2.3 Guidance de domínio | Médio | Curva de escolha entre 118 tools |
| Média | 2.4 Error-boundary no CI | Médio | Envelope de erro inconsistente |
| Baixa | 2.5 CLI de fonte única | Alto | Debug/automação manual |

## 4. Não-objetivos

- Mudar a arquitetura web-first/REST (RFC 0001) ou os backends (RFC 0006).
- Re-fazer annotations/paginação/keyring — já existem (RFC 0003/0007/0002).
- Renomear ou remover tools.

## 5. Plano de implementação

1. **PR 1 — cobertura de backend** (2.1): `tests/test_rest_backend.py` +
   `tests/test_web_backend.py` + CI workflow, sem mudança de runtime.
2. **PR 2 — constraints de schema** (2.2): `Field(pattern=…)` nas tools de maior
   uso, incremental tool a tool.
3. **PR 3 — guidance de domínio** (2.3) + **error-boundary check** (2.4).
4. **2.5** fica como direção futura, dependente de demanda real de CLI.
