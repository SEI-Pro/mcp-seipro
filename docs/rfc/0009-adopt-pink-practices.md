# RFC 0009 — Guard de paridade, constraints de schema e guidance de domínio

**Status**: Proposta · **Atualizado**: 2026-06-18
**Data**: 2026-06-17
**Autores**: Claude (com Franklin Baldo)
**RFCs relacionados**: RFC 0003 (ergonomia FastMCP), RFC 0007 (response shaping/paginação)

## 1. Contexto

`mcp-sei` já está à frente em vários eixos de qualidade MCP (annotations — RFC
0003; paginação + `next_actions` — RFC 0007; keyring — RFC 0002; backend
abstrato — RFC 0006). Este RFC aborda lacunas que se tornaram riscos reais na
escala atual de **118 tools** (em `src/todos/tools/`, roteadas pelo
`SEIBackend`): drift silencioso entre assinatura de tool e backend, entrada
malformada que só falha no SEI, e ausência de orientação dinâmica para o agente
navegar entre as tools.

## 2. Propostas

### 2.1 (Alta) Guard de paridade tool↔serviço no CI

Com **118 tools** distribuídas em 10 módulos, divergência entre a assinatura de
uma tool e o método correspondente no `SEIBackend` é um risco silencioso —
detectado em produção, não no commit.

**Mecanismo de paridade:** cada tool `sei_X` corresponde, por convenção, ao
método `SEIBackend.X` (ex.: `sei_consultar_processo` →
`SEIBackend.consultar_processo`). O guard verifica:

1. Toda tool `sei_X` tem método correspondente em `SEIBackend` (ou está
   declarada como exceção — ver abaixo).
2. Nenhum parâmetro obrigatório do método de backend está ausente na tool
   (parâmetros opcionais do backend podem ser omitidos na tool).

**Exceções documentadas** (não têm método `SEIBackend` correspondente):
- `sei_estilos` — utilitário local, sem chamada de backend.
- `sei_versao` — REST-only, chama diretamente o cliente REST.
- `sei_editar_secao` — mapeia para `alterar_secoes` (nome diverge por convenção
  de UX).

**Proposta:** `tests/test_tool_parity.py` que enumera via introspecção todas as
funções decoradas com `@mcp.tool()` nos módulos de tools, aplica as regras
acima e quebra o CI no commit que introduz o drift.

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
| **Alta** | 2.1 Guard de paridade | Baixo | Drift tool↔backend em 118 tools |
| **Alta** | 2.2 Constraints de schema | Médio | Entrada malformada chega ao SEI |
| Média | 2.3 Guidance de domínio | Médio | Curva de escolha entre 118 tools |
| Média | 2.4 Error-boundary no CI | Médio | Envelope de erro inconsistente |
| Baixa | 2.5 CLI de fonte única | Alto | Debug/automação manual |

## 4. Não-objetivos

- Mudar a arquitetura web-first/REST (RFC 0001) ou os backends (RFC 0006).
- Re-fazer annotations/paginação/keyring — já existem (RFC 0003/0007/0002).
- Renomear ou remover tools.

## 5. Plano de implementação

1. **PR 1 — guard de paridade** (2.1): `tests/test_tool_parity.py` + CI
   workflow, sem mudança de runtime.
2. **PR 2 — constraints de schema** (2.2): `Field(pattern=…)` nas tools de maior
   uso, incremental tool a tool.
3. **PR 3 — guidance de domínio** (2.3) + **error-boundary check** (2.4).
4. **2.5** fica como direção futura, dependente de demanda real de CLI.
