# RFC 0019 — Adotar práticas do pink: exposição de tools, cache e credenciais

**Status**: Proposta · **Data**: 2026-07-04
**Autores**: Claude (com Franklin Baldo)
**RFCs relacionados**: RFC 0009 (Protocol split — arquitetura de backend, tópico distinto), RFC 0018 (CLI dispatch genérico)

## 1. Contexto

`pink` (Kanoê/Caipora + Metabase) e `todos` são MCP servers irmãos do mesmo
projeto (PGE-IPERON), mantidos pelo mesmo autor, com convenções que já
convergiram em vários pontos (keyring para credenciais, RFC 0018 espelhando o
dispatch `pink <tool> campo=valor`). Uma comparação lado a lado (4 agentes,
cobrindo CI, tratamento de erro, cache/credenciais e docs/arquitetura)
encontrou práticas do pink que o todos ainda não tem — nenhuma delas exige
mudar a arquitetura de backend do todos (RFC 0009 continua a referência para
isso); são todas aditivas.

## 2. Propostas

### 2.1 (Alta) Gerar e expor `SKILL.md` das tools (`todos skill install`)

**Motivação.** O pink introspecciona seu próprio servidor MCP em processo
(`fastmcp.Client` in-memory) via `fastmcp.cli.generate.generate_skill_content`
e gera um `SKILL.md` documentando todas as tools — nome, descrição, campos,
exemplos de invocação já convertidos pra sintaxe CLI (`pink <tool>
campo=valor`). `pink skill install` grava esse arquivo no path de skills do
agente detectado (Claude Code, Cursor, opencode, cline), sem precisar
registrar o MCP server no host. CI regenera e falha em drift
(`scripts/regen_mcp_skill.py` + `git diff --exit-code`).

O `todos` já tem a metade que falta conectar: RFC 0018 implementou
`todos <tool> chave=valor` (`src/todos/cli_call.py`), e a mesma dependência
`fastmcp` (incluindo `fastmcp.cli.generate.generate_skill_content`) já está
disponível no ambiente do todos — confirmado por inspeção direta do pacote
instalado. Falta só o gerador de skill e o subcomando de instalação.

**Proposta.**

- Novo módulo `src/todos/skill.py`, análogo a `pink/src/pink/skill.py`:
  introspecciona `todos.server.mcp` in-process via `fastmcp.Client`, chama
  `generate_skill_content("todos", ...)`, e transforma a saída para a sintaxe
  `todos <tool> campo=valor` (o transform do pink já faz exatamente esse tipo
  de conversão — adaptar, não reescrever do zero).
- Novo subcomando `todos skill install` no `_app` typer já existente em
  `server.py` (mesmo padrão de `setup`/`set-password`, já registrados via
  `@_app.command(...)`), com os mesmos flags `--agent`/`--scope`/`--target`
  do pink.
- `scripts/regen_mcp_skill.py` grava em `.agents/skills/todos-mcp/SKILL.md`
  (dev/CI); CI checa drift com `git diff --exit-code`.

### 2.2 (Alta) `cache_status`/`cache_clear` como tools MCP

**Motivação.** `CatalogCache` (`src/todos/catalog_cache.py`) já tem TTL
(`sei_cache_ttl_seconds`, default 24h), invalidação lazy e `cleanup()`
explícito — mas nenhuma tool MCP expõe essa informação ao agente ou permite
forçar limpeza. O pink expõe exatamente isso via `mcp__pink__cache_status`/
`mcp__pink__cache_clear` (`src/pink/service/cache.py:8-53`), retornando
`{total, fresh, bytes}` e aceitando `older_than_seconds` para purga seletiva.

**Proposta.** Duas novas tools em `src/todos/tools/configuracao.py` (mesmo
módulo que já tem `setup`/credenciais), espelhando a forma de resposta do
pink: `sei_cache_status` (retorna total/fresh/bytes) e `sei_cache_clear`
(aceita `older_than_seconds` opcional). Reusa `CatalogCache.ttl()`/
`cleanup()` já existentes — sem mudança no mecanismo de cache em si.

### 2.3 (Média) `todos setup` headless

**Motivação.** `todos setup` (`src/todos/setup_wizard.py`) exige TTY
interativo (`sys.stdin.isatty()`, linhas 978/1054) — inviável para
provisionamento por script ou CI. O pink resolve isso com
`pink setup --metabase-usuario X --metabase-senha Y`
(`src/pink/scripts/setup.py:25-132`, `_run_headless`), que grava direto no
keyring sem TTY, mantendo o wizard interativo como default sem flags.

**Proposta.** Adicionar `--usuario`/`--senha` (e demais campos hoje
perguntados interativamente) como flags opcionais em `todos setup`; se
fornecidos, pular o wizard e gravar direto no keyring (mesmo service
`"todos-mcp"` já usado por `_ler_senha_keyring`).

### 2.4 (Média) Changelog obrigatório no bump de versão

**Motivação.** O CI de bump do todos (`ci.yml`, job `version-bump`) só
verifica que `__version__` subiu e que `manifest.json` está em sincronia —
não exige nenhum registro legível do que mudou. O pink exige também um
arquivo `changelog/<versão>.md` (`scripts/check_version_bump.py`),
forçando uma entrada de cada bump.

**Proposta.** Adicionar ao job `version-bump` do todos a mesma checagem:
`changelog/<versão>.md` deve existir para o `head_ver` novo.

### 2.5 (Baixa) Script local único de CI

**Motivação.** O pink tem `scripts/run_ci.sh` espelhando exatamente os
checks do CI (ruff, vulture, error-boundary, ty, pytest) — um comando único
pra rodar antes de push. O todos só tem os jobs definidos no YAML,
duplicados manualmente quando alguém quer rodar tudo localmente (como
aconteceu nesta sessão: descobri via CI que faltava `ruff format --check`
depois de já ter empurrado).

**Proposta.** `scripts/run_ci.sh` no todos, espelhando os 6 jobs do
`ci.yml` em sequência.

### 2.6 (Baixa) `[tool.ty.rules] unresolved-import = "ignore"`

**Motivação.** O todos tem dependências opcionais (`llm`: litellm/pymupdf)
que gera ruído de `unresolved-import` no `ty` quando não instaladas. O pink
já documenta essa supressão explicitamente (`pyproject.toml:150-153`) com
comentário explicando o motivo.

**Proposta.** Adicionar a mesma seção `[tool.ty.rules]` ao `pyproject.toml`
do todos, com comentário equivalente.

## 3. Não-objetivos

- Mudar a arquitetura de backend (Protocol split, roteamento) — isso é RFC
  0009, tópico independente.
- Adotar o padrão de `hint` estático por classe de exceção do pink — o
  `SEIError` do todos (com `suggested_next_tool`/`suggested_args`/`_render()`)
  já é mais estruturado que o `PinkError` do pink nesse aspecto; não há nada
  a copiar aqui, e é o pink que deveria evoluir na direção do todos.
- Adotar `--confirm` obrigatório em CLI para tools destrutivas — o todos não
  tem CLI de uso interativo direto (a `todos <tool>` é majoritariamente
  scripted/automation), a imposição do host MCP via `destructiveHint` já
  cobre o caso de uso real.

## 4. Priorização

| Prioridade | Item | Esforço | Valor |
|---|---|---|---|
| **Alta** | 2.1 SKILL.md + `todos skill install` | Médio | Descoberta de tools sem precisar registrar MCP |
| **Alta** | 2.2 `cache_status`/`cache_clear` | Baixo | `CatalogCache` já suporta, só falta a tool |
| Média | 2.3 `todos setup` headless | Baixo | Provisionamento por script/CI |
| Média | 2.4 Changelog obrigatório no bump | Baixo | Disciplina de release |
| Baixa | 2.5 `scripts/run_ci.sh` | Baixo | Conveniência local |
| Baixa | 2.6 `ty` unresolved-import | Trivial | Reduz ruído de dependência opcional |

## 5. Plano de implementação

1. **PR 1** (2.1): `src/todos/skill.py` + `todos skill install` +
   `scripts/regen_mcp_skill.py` + drift-check no CI.
2. **PR 2** (2.2): `sei_cache_status`/`sei_cache_clear` em
   `tools/configuracao.py`.
3. **PR 3** (2.3 + 2.6): flags headless em `setup` + config `ty`.
4. **PR 4** (2.4 + 2.5): changelog obrigatório + `run_ci.sh`.
