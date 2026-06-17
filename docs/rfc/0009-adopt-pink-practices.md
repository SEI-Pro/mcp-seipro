# RFC 0009 — Adotar práticas do pink: guard de paridade, constraints de schema e guidance de domínio

**Status**: Proposta · **Atualizado**: 2026-06-17
**Data**: 2026-06-17
**Autores**: Claude (com Franklin Baldo)
**RFCs relacionados**: RFC 0003 (ergonomia FastMCP), RFC 0007 (response shaping/paginação)
**Origem**: comparativo TODOS × PINK — os dois MCPs jurídicos do mesmo autor. Cataloga o que o PINK amadureceu e o `mcp-sei` ainda não tem.

## 1. Contexto

`mcp-sei` (SEI) e PINK (Kanoê/Caipora) são duas camadas do mesmo fluxo. O
`mcp-sei` já está à frente em vários eixos de MCP (annotations — RFC 0003;
paginação + `next_actions` — RFC 0007; keyring — RFC 0002; backend abstrato —
RFC 0006). Este RFC só propõe o **delta** que o PINK tem e o `mcp-sei` não — com
ênfase em **qualidade em escala de 124 tools**, onde drift e entrada malformada
são riscos reais.

## 2. Propostas

### 2.1 (Alta) Guard de paridade tool↔serviço no CI

**Lá no PINK:** um teste de CI percorre todas as tools MCP e verifica que os
parâmetros de cada tool são um subconjunto da função de serviço correspondente
(parâmetros obrigatórios do serviço expostos; nenhum parâmetro de tool órfão).
Quebra o CI no commit que introduz o drift, não em produção.

**Aqui:** com **124 tools** roteando para backends rest/web, divergência entre a
assinatura da tool e o que o backend espera é um risco silencioso.

**Proposta:** um `tests/test_tool_signatures.py` que enumere as tools
registradas no `mcp` e cheque a paridade contra os métodos de backend/serviço
que cada uma chama (mesma ideia do PINK, adaptada ao despacho web/REST).

### 2.2 (Alta) Constraints de parâmetro no schema, não só na prosa

**Lá no PINK:** params críticos usam `Annotated[str, Field(pattern=…, examples=…)]`,
então o `inputSchema` carrega a constraint e o pydantic **rejeita entrada
malformada antes da chamada**.

**Aqui:** vários params têm formato implícito documentado só em prosa —
`protocolo_formatado` (NNNNN.NNNNNN/AAAA-DD), datas, IDs. Um agente pode mandar
`17/06/2026` ou um protocolo sem máscara, e a falha só aparece no SEI.

**Proposta:** anotar os params de formato conhecido com `Field(pattern=…)`
(protocolo, datas, número de documento), começando pelas tools mais usadas
(`sei_consultar_processo`, `sei_criar_documento`, `sei_enviar_processo`).

### 2.3 (Média) Guidance de domínio configurável e injetada na resposta

**Lá no PINK:** o setor configura `instrucoes_triagem`/`instrucoes_kanban`, que
voltam **dentro do payload** das tools-chave para orientar o agente em tempo de
uso (qual caixa, qual prazo, qual prioridade).

**Aqui:** com 124 tools, a curva de "qual tool/qual ordem" é íngreme. As
instruções do servidor MCP já existem (domínio do SEI), mas são estáticas e
globais.

**Proposta:** um campo de instruções **configurável** (por órgão/unidade),
devolvido em tools de entrada (ex.: `sei_listar_processos`, `sei_resumo_processos`)
como `dicas`/`_hints`, para guiar fluxos recorrentes do usuário.

### 2.4 (Média) Checagem de error-boundary no CI

**Lá no PINK:** além do envelope de erro com `code`/`exit_code`/`hint`, há um
**script de CI** (`check_error_boundary.py`) que força *onde* os erros são
traduzidos — a convenção não depende de disciplina manual.

**Aqui:** o `mcp-sei` já tem `SEIError` e `ToolError` (RFC 0003/0004). Falta o
guarda automatizado que garanta que a tradução acontece só na fronteira.

**Proposta:** portar um check leve que falhe o CI se um módulo de domínio
levantar erro de transporte (ou vice-versa) fora da fronteira.

### 2.5 (Baixa, opcional) Superfície CLI a partir da mesma definição

**Lá no PINK:** o decorator de tool materializa a **mesma função** como tool MCP
e como comando CLI (Typer). Uma definição, dois transportes — ótimo para debug,
scripts e automação.

**Aqui:** o `mcp-sei` é essencialmente MCP-only. Uma CLR fina derivada das
mesmas funções ajudaria smoke-tests e operação manual.

**Proposta:** avaliar um adaptador CLI opcional sobre as funções de tool
existentes (esforço alto; registrar como direção, não compromisso).

## 3. Priorização

| Prioridade | Item | Esforço | Risco que mitiga |
|---|---|---|---|
| **Alta** | 2.1 Guard de paridade | Baixo | Drift tool↔backend em 124 tools |
| **Alta** | 2.2 Constraints de schema | Médio | Entrada malformada chega ao SEI |
| Média | 2.3 Guidance de domínio | Médio | Curva de escolha entre 124 tools |
| Média | 2.4 Error-boundary no CI | Médio | Envelope de erro inconsistente |
| Baixa | 2.5 CLI de fonte única | Alto | Debug/automação manual |

## 4. Não-objetivos

- Mudar a arquitetura web-first/REST (RFC 0001) ou os backends (RFC 0006).
- Re-fazer annotations/paginação/keyring — já existem (RFC 0003/0007/0002).
- Renomear ou remover tools.

## 5. Plano de implementação

1. **PR 1 — guard de paridade** (2.1): teste de CI, sem mudança de runtime.
2. **PR 2 — constraints de schema** (2.2): `Field(pattern=…)` nas tools de maior
   uso, incremental tool a tool.
3. **PR 3 — guidance de domínio** (2.3) + **error-boundary check** (2.4).
4. **2.5** fica como direção futura, dependente de demanda real de CLI.
