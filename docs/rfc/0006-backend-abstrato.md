# RFC 0006 — Backend abstrato: contrato único REST/web + tools modulares

**Status**: Em andamento
**Data**: 2026-06-13
**Autores**: Franklin Baldo (com Claude Code)
**Relacionado**: supersede o mecanismo de roteamento da [RFC 0001 §4.1](0001-web-first.md) (web-first); preserva todo o comportamento web-first descrito lá.

## 0. Estado atual (2026-06-14)

Infraestrutura completa e testada; split de `server.py` em módulos por domínio
**concluído**. `server.py` passou de ~5000 → **727 linhas**: restam apenas 6 tools
de orquestração ainda não absorvidas pelo contrato. Suíte verde (219 testes) e
servidor executável (121 tools registradas) a cada commit.

### ✅ Concluído

| Item | Arquivo | Notas |
|---|---|---|
| Contrato abstrato | `backends/base.py` | `SEIBackend` (~100 ops, stubs `NotImplementedError`, dataclasses de parâmetros) |
| Backend REST | `backends/rest.py` | `SEIRestBackend` — 109 ops sobre `SEIClient` |
| Backend web | `backends/web.py` | `SEIWebBackend` — 74 ops sobre `SEIWebClient` |
| Composite + factory | `backends/composite.py` | `CompositeBackend` + `build_backend` |
| Exceção | `exceptions.py` | `SEINotImplementedError(SEIError)` |
| Núcleo extraído | `mcp_app.py` | `mcp`, lifespan, accessors, resources, gates, resolvers, helpers |
| Tools modulares | `tools/*.py` | **10 módulos**, 115 tools (ver tabela abaixo) |
| Guards estáticos | `tests/test_backends.py` | drift de assinatura; `self._rest/_web.X` existe; `backend.X` em tools resolve a op do contrato; nenhuma tool delega a op *always-raise* |

Módulos de tools (115 tools): `unidades` (13), `processos` (25), `documentos`
(14 + 5 helpers), `assinatura` (7), `catalogos` (12), `marcadores` (8),
`acompanhamento` (8), `blocos_internos` (10), `blocos_assinatura` (14),
`credenciamento` (4).

### ✅ Concluído (cont.) — facade legado removido

- **`sei_backend.SEIBackend` + `_get_backend` deletados.** O composite (`_backend`)
  é o **único** acessor de backend.
- **4 das 6 tools de orquestração migradas** para o composite: `sobrestar_processo`,
  `buscar_documento`, `enviar_processo` (sigla→id agora no backend REST/web) e
  `atribuir_processo` (nome→id no backend). Cada uma também aposentou um check de
  substring/`has_rest`.
- **`tools/documentos.py` saiu do facade** (usa `_get_client`/`_get_web_client` +
  `_has_rest` direto — substituição mecânica, sem tocar gates/binário).
- **Camada `_to_tool_error` eliminada** (`SEIError(ToolError)` + composite como
  fronteira httpx); guidance acionável vive em erros tipados por cenário.

### 🚧 Pendente

- **2 tools de orquestração legítima de tool-layer** em `server.py` (usam
  `_get_client`, não o facade): `resumo_processos` (agregação REST sobre
  `_CAMPOS_AGRUPAMENTO`) e `pesquisar_processos` (REST-first + envelope/paginação/
  drop de filtros web). Provavelmente ficam.
- **Migrar `tools/documentos.py` para o composite** (gates/binário/base64) — tarefa
  separada, exige validação contra SEI ao vivo. Hoje está facade-free mas ainda
  faz dispatch REST/web no tool via clientes crus.
- **Podar ops vestigiais do contrato**: `cancelar_assinatura`, `gerar_referencia`,
  `marcar_nao_lido`, `resumo_processos` são *always-raise* (orquestração de tool);
  os stubs na base são inertes e podem sair do contrato.

## 1. Problema

A RFC 0001 entregou paridade web, mas o **mecanismo de roteamento** ficou
espalhado: cada uma das 121 tools repete o padrão

```python
backend = _get_backend(ctx)
if backend.has_rest:
    result = await backend.rest.metodo(id_proc, ...)
else:
    result = await backend.web.metodo_web(protocolo, ...)
```

Consequências observadas (auditoria SEI-RO, `docs/test-results-sei-ro.md`):

1. **Decisão de roteamento duplicada 121×** — cada tool reimplementa o `if`, e
   erra de formas sutis e diferentes.
2. **Falsos sucessos silenciosos** — vários ramos web retornavam `{"itens": []}`
   sem erro quando o backend não suportava a operação (bugs #3, #6 da auditoria).
3. **Assinaturas divergentes vazam para a tool** — REST usa `IdProcedimento`
   interno, web usa protocolo formatado; cada tool resolve isso à mão
   (`_resolver_processo`), com inconsistências.
4. **Sem "não implementado" tipado** — instâncias sem mod-wssei devolviam o erro
   cru `httpx.UnsupportedProtocol` ("missing http:// protocol") em vez de uma
   mensagem clara.
5. **`server.py` monolítico** — ~5000 linhas, 121 tools + helpers entrelaçados,
   inviável de revisar ou paralelizar.

## 2. Objetivo

1. **Uma decisão de roteamento, em um lugar.** A tool chama
   `backend.consultar_processo(protocolo)`; o backend decide REST/web.
2. **Contrato explícito.** Toda operação tem assinatura canônica (independente de
   backend) e tipo de retorno. Operação não suportada levanta
   `SEINotImplementedError` (subclasse de `SEIError`, capturável pelas tools).
3. **Tools modulares.** Cada domínio em `tools/<domínio>.py`, importando o `mcp`
   compartilhado — permite revisão e migração paralela por arquivo.
4. **Zero regressão e zero novos ignores band-aid.** Suíte verde a cada commit;
   `ruff` com todas as regras, sem `# noqa`.

Fora de escopo: mudar qualquer comportamento de scraping/REST já entregue na
RFC 0001. Isto é refatoração de **arquitetura de despacho**, não de I/O.

## 3. Arquitetura

```
tool (tools/<domínio>.py)
   │  backend = _backend(ctx)
   │  await backend.consultar_processo(protocolo)
   ▼
CompositeBackend            (backends/composite.py)
   │  REST-first, fallback web em NotImplementedError
   ├──► SEIRestBackend      (backends/rest/)  → SEIClient   (REST mod-wssei)
   └──► SEIWebBackend       (backends/web/)   → SEIWebClient (scraper)
            ▲
            └─ ambos implementam ──► SEIBackend (backends/base.py, contrato)
```

### 3.0 Layout de arquivos

```
backends/
  base.py        contrato SEIBackend (arquivo único — o índice da interface)
  models.py      as 5 dataclasses de parâmetros
  rest/          _session (init + resolvers) + mixins por domínio
                 (unidades, processos, documentos, catalogos, marcadores,
                  acompanhamento, blocos, credenciamento);
                 __init__ monta SEIRestBackend(*mixins, SEIBackend)
  web/           mesmos mixins por domínio; __init__ monta SEIWebBackend
  composite.py   CompositeBackend + build_backend
```

Cada backend cresce muito (REST ~110 ops, web ~74), então virou um **pacote de
mixins por domínio** (espelhando `tools/`): cada arquivo é uma classe simples com
os métodos daquele domínio usando `self._rest`/`self._web`, e o `__init__.py`
compõe a classe concreta via herança. A MRO resolve cada chamada para o mixin que
a define, caindo no stub de `SEIBackend` (último na MRO) para o que nenhum mixin
sobrescreve — comportamento idêntico ao da classe única. `base.py` permanece
**arquivo único** (são ~100 stubs de uma linha — a fonte única da interface).

### 3.1 Contrato (`SEIBackend`)

`backends/base.py` enumera **todas** as operações expostas pelas tools, com
parâmetros canônicos: processos pelo **protocolo formatado** (não pelo id
interno). Cada método é um *stub* que levanta `NotImplementedError`. As
subclasses sobrescrevem **apenas** o que suportam; o que não sobrescrevem
permanece "não implementado".

- **Não usa `abc.ABC`/`@abstractmethod`** de propósito: abstratos forçariam toda
  subclasse a implementar as ~100 ops. O stub `raise NotImplementedError` é a
  forma reconhecida pelo ruff (sem ARG002/RUF029 nas assinaturas não usadas).
- **Operações de alta aridade** (`criar_processo`, `pesquisar_processos`,
  `enviar_processo`, `criar_documento_interno/externo`) recebem **dataclasses
  congeladas** (`NovoProcesso`, `FiltrosPesquisaProcessos`, `EnvioProcesso`,
  `NovoDocumentoInterno`, `NovoDocumentoExterno`) em vez de kwargs planos. Isso
  resolve `PLR0913` sem per-file-ignore — a objeção "dataclasses escondem o
  schema MCP" vale para a camada de tool, **não** para o backend.

### 3.2 Backends concretos

| Backend | Encapsula | Implementa | Herda stub |
|---|---|---|---|
| `SEIRestBackend` | `SEIClient` | 109 ops; resolve protocolo→id internamente (`_resolver_processo`/`_resolver_documento` movidos para cá) | web-only: `unidade_atual`, `listar_unidades`, `arvore_processo`, `gerar_pdf/zip_processo`, `executar_acao`, … |
| `SEIWebBackend` | `SEIWebClient` | 74 ops; usa o protocolo direto | REST-only: `versao`, assinatura PKI, credenciamento, CRUD de marcador, bloco interno, … |

Garantia verificada por teste: **toda** chamada `self._rest.X`/`self._web.X` nos
backends resolve para um membro real do cliente (a classe de erro que o ruff não
pega), e **toda** sobrescrita casa exatamente a assinatura da base (zero drift).

### 3.3 Composite + factory

`CompositeBackend(rest|None, web)` expõe o mesmo contrato e roteia:

- **Padrão**: tenta REST; se a op cai no stub (`NotImplementedError`), usa web;
  se nenhum backend disponível implementa, levanta `SEINotImplementedError`.
- **`_WEB_FIRST`** (conjunto data-driven): `listar_processos`,
  `listar_documentos`, `listar_atividades` — invertem a ordem (web primeiro),
  por serem fonte canônica/muito mais rápidas.
- **Sobrescritas explícitas** (composição genuína, não cabe no padrão):
  - `consultar_processo` — combina metadados ricos da REST + árvore de
    documentos do web em paralelo (REST canônica; web preenche lacunas).
  - `trocar_unidade` — web controla a sessão; REST é sincronizada best-effort.
- Os **dispatchers genéricos** das ~100 ops restantes são **gerados** a partir do
  contrato em `_install_dispatchers` — graças ao contrato compartilhado, a
  composição é genérica (sem ~100 métodos de delegação idênticos).

`build_backend(rest_client, web_client)` inclui o REST só quando há `base_url`
configurada (mod-wssei presente); caso contrário, tudo roteia para web. É **a
realização** do objetivo "detecção automática de capacidade" da RFC 0001 §2.3.

### 3.4 Modularização das tools

- **`mcp_app.py`** — núcleo compartilhado: instância `mcp` (FastMCP), `lifespan`,
  accessors (`_get_client`, `_get_web_client`, `_backend`), resources, gates de
  consentimento/acesso, `_resolver_processo`, `_json`/`_error`/`_to_tool_error`,
  perfis de anotação (`_READ`/`_IDEM`/`_WRITE`/`_DEST`), constantes.
- **`tools/<domínio>.py`** — importa `mcp` + helpers de `mcp_app` e registra suas
  tools via `@mcp.tool`.
- **`server.py`** — importa os módulos de tools (dispara o registro) + `main()`.

Convenções (travadas no piloto `tools/credenciamento.py`):

1. **Sem `from __future__ import annotations`** nos módulos de tools: o FastMCP
   introspecta os type hints em **tempo de execução** para montar o schema; com
   anotações adiadas (strings) o schema quebra.
2. **Registro via `_TOOL_MODULES`**: `server.py` mantém uma tupla referenciando
   cada módulo importado — dispara o side-effect de registro **sem** violação de
   import não usado (`F401`), sem `# noqa`.

## 4. Migração incremental

Estratégia: introduzir o accessor novo `_backend(ctx)` (retorna o composite) ao
lado do facade legado `_get_backend`. Migrar tool a tool / domínio a domínio;
a cada lote a suíte fica verde e o servidor executável. Quando tudo migrar,
remover o facade legado e renomear `_backend` → `_get_backend`.

Lotes entregues:

1. **Sessão/unidades/usuários** (13 tools) — `unidade_atual`, `trocar_unidade`
   (dual-write no composite), `pesquisar_unidades`, `versao`, `parametros_upload`
   (defaults SEI 4.x agora no `SEIWebBackend`), …
2. **Leitura de processos** (10 tools) — `consultar_processo` (merge no
   composite; tool mantém só o disclaimer de acesso), `listar_sobrestamentos`/
   `interessados`/`unidades_processo` (extração de detalhe agora no backend), …

Cada tool migrada colapsa para uma delegação de uma linha:

```python
async def sei_consultar_atribuicao(processo: str, ctx: Context | None = None) -> str:
    try:
        backend = _backend(ctx)
        return _json(await backend.consultar_atribuicao(processo))
    except (SEIError, httpx.RequestError) as e:
        raise _to_tool_error(e) from e
```

### Plano de paralelização (pendente)

Cada agente escreve **um** `tools/<domínio>.py` (arquivos separados, sem
conflito); a remoção das tools de `server.py` é sequencial. Domínios:
`marcadores` (8), `acompanhamento` (8), `blocos_internos` (10),
`blocos_assinatura` (14), `catalogos` (12), `assinatura/ciência` (7),
`documentos` (~14, **+ helpers** de leitura/PDF), `processos` (~25, **dataclasses
+ orquestração**: `buscar_documento`, `resumo_processos`, `marcar_nao_lido`,
`gerar_referencia`), `unidades` (13, já migradas — só relocar).

## 5. Onde a lógica de cada coisa vive

| Lógica | Camada | Por quê |
|---|---|---|
| Roteamento REST vs web | `CompositeBackend` | é sobre combinar backends |
| Merge REST+web (`consultar_processo`) | `CompositeBackend` | composição de backends |
| Resolução protocolo→id | `SEIRestBackend` | detalhe do backend REST |
| Disclaimer de acesso restrito | tool (`mcp_app`/`tools/*`) | política/apresentação |
| Encode JSON, `ctx.report_progress` | tool | glue específico do MCP |
| base64 / binário (PDF/ZIP) | tool | formatação de saída |
| Orquestração (`marcar_nao_lido` = enviar p/ si) | tool | compõe primitivas do backend |

## 6. Decisões

1. **Dataclasses no backend, kwargs planos na tool.** A objeção "dataclasses
   escondem o schema MCP" (RFC 0003, `server.py` ignora `PLR0913`) vale só na
   fronteira MCP. No backend, dataclasses são limpas e eliminam o `PLR0913` sem
   ignore. (Decidido 2026-06-13.)
2. **Composite genérico via dispatchers gerados, não 100 métodos.** O contrato
   compartilhado permite `try REST except NotImplementedError: web` genérico; só
   as ~3 ops de composição genuína (merge, dual-write) são explícitas.
3. **Ignores seguem o código.** Ao extrair o núcleo para `mcp_app.py`, o bloco
   de per-file-ignores do `server.py` passou a cobrir ambos via glob
   `"src/todos/{server,mcp_app}.py"` — é o **mesmo código** realocado, com as
   mesmas justificativas (framework-dictated/boundary-file), **não** uma nova
   supressão. Compatível com a [RFC 0005](0005-noqa-zero.md).
4. **`SEINotImplementedError(SEIError)`** em vez de `NotImplementedError` cru na
   fronteira: as tools capturam `SEIError` e devolvem mensagem legível.

## 7. Compatibilidade com a RFC 0001

A RFC 0001 (web-first) continua válida em tudo que descreve sobre o **scraping**
e os invariantes do SEI (§3, §4.2–4.6). O que muda é apenas a **§4.1 (camada de
roteamento)**: o facade `backend.has_rest` / `backend.rest` / `backend.web` é
substituído pelo `CompositeBackend` desta RFC. A lista de tools
"permanentemente REST-only" (0001 §4.4) mapeia para as ops que `SEIWebBackend`
herda como stub. A 0001 foi anotada para apontar para cá.

## 8. Riscos

| Risco | Mitigação |
|---|---|
| Tradução errada de tool→backend (op/args) na migração | guards: assinatura sem drift + existência de método (teste); `ruff`; suíte; import do `server`; (pendente) guard estático de `backend.X` |
| Dupla validação só pega estrutura, não semântica | smoke test ao vivo contra SEI-RO/ANTAQ antes de declarar concluído |
| Regressão de performance (REST-first em op cara) | `_WEB_FIRST` preserva web para listagens; `consultar_processo` mantém merge paralelo |
| Quebra de registro cross-módulo (FastMCP) | piloto `credenciamento` provou 121 tools registradas; convenção "sem future annotations" documentada |
