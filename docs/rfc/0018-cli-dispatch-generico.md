# RFC 0018 — Dispatch genérico de tools via `todos <tool> chave=valor`

**Status:** Proposta
**Data:** 2026-07-01
**Motivação:** gap real encontrado em campo durante uma sessão de triagem — sem
forma confiável de chamar uma tool via terminal, uma verificação simples (SEI
por número de processo) consumiu quase uma hora de investigação.
**RFCs relacionados:** RFC 0009 §2.6 (já registrava esta direção como "baixa
prioridade, opcional"); pink RFC 0030 (`pink <tool> chave=valor` — mesmo
padrão, já implementado e em produção no projeto irmão)

---

## 1. Problema

`todos` não tem forma suportada de chamar uma tool MCP fora de um host MCP.
O `_app` Typer (`src/todos/server.py`) só expõe `setup` e `set-password`; sem
subcomando, `todos` sobe o servidor stdio e bloqueia — não há como invocar
`sei_consultar_processo` uma vez, de forma ad-hoc, num script ou terminal.

Isso obriga a depender inteiramente da conexão MCP ao vivo do host. Quando
essa conexão cai (aconteceu em produção, 2026-07-01), não existe fallback:
nenhuma tool do `todos` fica utilizável até o host reconectar — diferente do
`pink`, que sobrevive à queda do host porque `pink <tool>` despacha
in-process, sem depender de handshake MCP algum.

### 1.1 O workaround que existe hoje não funciona sem ajuda

O caminho "óbvio" seria `fastmcp call <arquivo.py> <tool> chave=valor` — o
atalho de CLI que o próprio pacote `fastmcp` (dependência principal do
`todos`) já fornece. **Ele não funciona out-of-the-box para o `todos`:**

1. `StdioTransport`/`StdioServerParameters` do SDK MCP usam `env=None` por
   padrão nesse caminho, que **não herda** o ambiente do processo pai — todas
   as variáveis `SEI_*` (URL, usuário, órgão…) somem no subprocesso que sobe o
   servidor. O catálogo de 127 tools carrega normalmente (o registro independe
   de login bem-sucedido), mas qualquer chamada real falha em runtime com
   `RuntimeError: Nenhuma URL do SEI configurada` — sintoma que não aponta
   para "faltam env vars no subprocesso", então o diagnóstico é enganoso.
2. O alvo certo é `src/todos/server.py` (onde as tools são de fato decoradas
   com `@mcp.tool`) — apontar para `src/todos/mcp_app.py` (só instancia o
   `FastMCP` base) produz um `Tool ... not found` que parece — de novo — um
   problema de ambiente, mas é só o arquivo errado.
3. `todos` **não carrega `.env` automaticamente** (decisão deliberada, ver
   `settings.py` / PR #110) — então nem essa rede de segurança existe para
   compensar o item 1.

Cada um desses três pontos, isolado, é um mistério de 15–30 minutos para quem
não conhece o código. Juntos, numa sessão real, consumiram quase uma hora
antes de se chegar à causa raiz e a um workaround funcional
(`scripts/call_tool.py`, adicionado nesta mesma sessão — reusa o padrão já
validado em `scripts/smoke_mcp.py`, `StdioServerParameters(...,
env=dict(os.environ))` explícito).

### 1.2 Por que isso importa além do caso de debug

- **Resiliência a queda de host.** Se o MCP do `todos` desconecta no meio de
  uma triagem, hoje não há como continuar sem esperar reconexão (ou sem
  reconstruir o workaround do zero). Um dispatcher de CLI reaproveitando a
  mesma stack de auth/config do servidor real dá um caminho alternativo
  imediato.
- **Scripts e automação.** `scripts/bench_*.py` e `scripts/smoke_*.py` já
  reimplementam boilerplate de `StdioServerParameters` cada um à sua maneira.
  Um dispatcher único elimina essa duplicação.
- **Paridade com o projeto irmão.** `pink <tool> chave=valor` já resolveu
  exatamente este problema (RFC 0030, "Implementado"). Os dois projetos
  compartilham usuário-alvo (agentes triando processos) e frequentemente são
  usados na mesma sessão — a assimetria de ergonomia entre os dois é hoje uma
  fonte de confusão (foi confundido nesta própria sessão, daí este RFC).

## 2. Proposta

Adicionar dispatch de tool por nome ao `_app` Typer existente, no mesmo
espírito da RFC 0030 do `pink`:

```bash
todos sei_consultar_processo protocolo_formatado="0020.009181/2025-68" backend=web
todos sei_pesquisar_processos palavras_chave="fulano"
todos --help          # lista comandos fixos (setup, set-password) + tools
```

### 2.1 Mecanismo

Diferente do `pink` (que despacha **in-process** via `fastmcp.Client` contra o
próprio objeto `FastMCP` do processo atual — RFC 0030 §"Dispatch de tools por
nome"), o `todos` já tem o padrão de subir o servidor real como **subprocesso
stdio** e conectar via `mcp.client.stdio` (`scripts/smoke_mcp.py`,
`scripts/call_tool.py`). Duas opções:

**Opção A — in-process, como o pink.** Reusa `mcp` (a instância `FastMCP` de
`mcp_app.py`) diretamente no processo do CLI, sem subprocesso, via
`fastmcp.Client(mcp)`. Mais rápido (sem custo de subprocesso/handshake stdio),
mas exige que o `lifespan` (login eager, pool de sessões) rode no mesmo
processo do comando `todos <tool>` — hoje o lifespan assume que só roda uma
vez, na subida do servidor MCP real; teria que ser auditado para rodar
seguramente também neste caminho.

**Opção B — subprocesso stdio, como `scripts/call_tool.py`.** Reusa
literalmente o padrão já provado nesta sessão: `todos <tool> chave=valor`
sobe `python -m todos` como subprocesso com `env=dict(os.environ)` explícito e
conversa via `mcp.client.stdio`. Mais simples de implementar com confiança
(zero mudança em `mcp_app.py`/lifespan), mas paga o custo de um subprocesso +
handshake MCP a cada chamada — cerca de 1–2s a mais que a Opção A pelo que se
observou nesta sessão.

**Recomendação:** começar pela Opção B (menor risco, reusa código já testado
em produção nesta sessão) e medir se o custo por chamada é sensível na prática
antes de investir na Opção A.

### 2.2 Parsing de argumentos

Mesma convenção do `pink` e do `fastmcp call`: `chave=valor` posicional,
`--json` para saída bruta. Não introduzir uma convenção nova.

### 2.3 Descoberta

`todos --help` deve listar tanto os comandos fixos (`setup`, `set-password`)
quanto o fato de que qualquer nome de tool é aceito — sem necessariamente
enumerar as 127 tools no `--help` (ruído); `todos --help` pode apontar para
`todos <tool> --help` ou para a lista via `fastmcp list` para o catálogo
completo.

## 3. Escopo desta RFC

Esta RFC cobre **só** o dispatcher de CLI. Não inclui:

- Consertar o `fastmcp call <arquivo.py>` upstream (é um comportamento do
  pacote `fastmcp`, fora do controle deste repo — reportar upstream é uma
  ação separada, não bloqueante).
- Qualquer mudança em `mcp_app.py`/lifespan além do necessário para a Opção A,
  se ela for escolhida.
- `scripts/call_tool.py` continua existindo como está (já commitado nesta
  sessão) até o dispatcher formal substituí-lo.

## 4. Alternativas rejeitadas

- **Deixar como está, `scripts/call_tool.py` é suficiente.** Rejeitado: um
  script solto em `scripts/` não aparece em `todos --help`, não é descoberto
  por quem não sabe que ele existe — reproduz o mesmo problema de descoberta
  que a RFC 0030 do pink resolveu para os entry points `pink-*`.
- **Documentar o workaround do `fastmcp call` em vez de construir um
  dispatcher.** Rejeitado: não resolve a fragilidade de fundo (três
  armadilhas independentes, RFC 0009 §1.1) nem a resiliência a queda de host
  (§1.2) — só reduz o tempo de redescoberta na próxima vez.

## 5. Plano de implementação

1. Promover `scripts/call_tool.py` para dentro de `src/todos/` (ex.:
   `src/todos/cli_call.py`), com testes.
2. Adicionar dispatch por nome ao `_app` Typer de `server.py` — comando
   fixo não reconhecido vira chamada de tool (Opção B).
3. Atualizar `CLAUDE.md`/`README.md` para `todos <tool> chave=valor` como
   caminho recomendado no terminal, com o workaround do `fastmcp call`
   removido ou rebaixado a nota de rodapé.
4. (Opcional, follow-up) Medir custo por chamada da Opção B; considerar
   migrar para a Opção A (in-process) se o custo for sensível em uso real.
