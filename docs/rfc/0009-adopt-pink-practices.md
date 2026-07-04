# RFC 0009 — Protocol split, cobertura de backend, constraints de schema e guidance de domínio

**Status**: Proposta · **Atualizado**: 2026-06-18
**Data**: 2026-06-17
**Autores**: Claude (com Franklin Baldo)
**RFCs relacionados**: RFC 0003 (ergonomia FastMCP), RFC 0006 (backend abstrato), RFC 0007 (response shaping/paginação)

## 1. Diagnóstico

### 1.1 Arquitetura atual

O projeto usa **Chain of Responsibility + Strategy** com implementação por mixins:

| Componente | Papel |
|---|---|
| `SEIBackend` | 125 métodos async, todos levantando `NotImplementedError` |
| `SEIRestBackend` | Strategy REST — implementa 111/125 (88%) via mixins por domínio |
| `SEIWebBackend` | Strategy Web — implementa 81/125 (64%) via mixins por domínio |
| `CompositeBackend` | Router dinâmico — 3 métodos explícitos + ~55 gerados por `_install_dispatchers` |

O padrão de roteamento é **apropriado para o use case**. Os problemas são de
implementação, não de escolha de padrão.

### 1.2 Dois problemas raiz

**Problema 1 — Interface monolítica gorda.**
`SEIBackend` tenta ser o contrato de dois backends com capacidades muito
diferentes usando um único vetor de 125 métodos. Isso força stubs
`raise NotImplementedError` em todo lugar, o que força `try/except
NotImplementedError` no `CompositeBackend`, o que criou a necessidade de
`_install_dispatchers()`. É uma cadeia de consequências do mesmo problema.
A ausência de `@abstractmethod` não é uma feature — é a consequência inevitável
de uma interface gorda: com 125 abstratos, cada backend seria obrigado a
implementar tudo.

**Problema 2 — Roteamento REST-first otimista.**
Para 65 das 71 operações que ambos os backends implementam, o Composite tenta
REST primeiro e cai para Web se falhar — o inverso do que a realidade exige.
Web é o baseline garantido (funciona em qualquer SEI 4.0+, sem mod-wssei).
REST é uma camada de enhancement disponível em algumas instalações. Tratar Web
como fallback é modelar a incerteza ao contrário.

Evidência: o conjunto `_WEB_FIRST` tem apenas 3 operações (exceções à regra
REST-first). Na prática, Web deveria ser o default e REST a exceção explícita.

**Problema 3 — CompositeBackend como router especial.**
As 3 operações com lógica própria no Composite (`consultar_processo`,
`trocar_unidade`, `criar_documento_externo`) são tratadas como casos especiais
do router. São, na realidade, **implementações de Protocol** para operações que
precisam coordenar dois backends — e deveriam ser modeladas como tal.

**Efeito colateral dos 3 problemas:** 4 métodos do contrato (`cancelar_assinatura`,
`gerar_referencia`, `marcar_nao_lido`, `resumo_processos`) não têm implementação
em nenhum backend — as tools os contornam compondo outros métodos ou chamando o
REST client diretamente. O contrato perdeu sincronia com a realidade sem que
nenhum CI detectasse.

## 2. Propostas

### 2.1 (Alta) Protocol split + roteamento estático no startup

Resolve os três problemas raiz de uma vez.

#### Contratos: 10 Protocols por domínio

Substituir `SEIBackend` monolítico por 10 `typing.Protocol` espelhando as
seções já existentes no `base.py`:

```
UnidadesProtocol      (~13 métodos)   ProcessosLeituraProtocol  (~15 métodos)
ProcessosEscritaProtocol (~19 métodos)  DocumentosProtocol      (~19 métodos)
CatalogosProtocol     (~11 métodos)   MarcadoresProtocol         (~10 métodos)
AcompanhamentoProtocol (~8 métodos)   BlocosInternosProtocol     (~10 métodos)
BlocosAssinaturaProtocol (~16 métodos) CredenciamentoProtocol    (~4 métodos)
```

Com `typing.Protocol`, implementação é por estrutura — não por herança. Mypy/ty
verifica conformidade estaticamente. `@abstractmethod` torna-se desnecessário.

#### Três implementadores, não dois

```
SEIRestBackend   → implementa os Protocols das operações que a REST suporta
SEIWebBackend    → implementa os Protocols das operações que o Web suporta
CompositeBackend → implementa os Protocols das operações que precisam de AMBOS
```

`CompositeBackend` deixa de ser um router e se torna um implementador legítimo:

```python
class CompositeBackend(ProcessosLeituraProtocol, UnidadesProtocol, DocumentosProtocol):

    async def consultar_processo(self, processo: str) -> dict:
        """Fusão paralela: REST (metadata) + Web (árvore de documentos)."""

    async def trocar_unidade(self, id_unidade: str) -> dict:
        """Troca no Web (sessão primária) e sincroniza REST (best-effort)."""

    async def criar_documento_externo(self, processo: str, dados: ...) -> dict:
        """Roteia por tipo de upload: base64 → web, caminho de arquivo → REST."""
```

#### Roteamento estático no startup

Em vez de `try/except NotImplementedError` a cada chamada, uma tabela
construída uma vez na inicialização:

```python
def _build_routing(
    rest: SEIRestBackend | None,
    web: SEIWebBackend,
    composite: CompositeBackend,
) -> dict[str, SEIBackend]:
    routing = {}

    # Web é o default: cobre todas as operações que implementa
    for op in web.CAPABILITIES:
        routing[op] = web

    # REST sobrescreve onde é explicitamente preferido (e está disponível)
    if rest is not None:
        for op in REST_PREFERRED:  # conjunto curado e documentado
            routing[op] = rest

    # CompositeBackend para operações que precisam de coordenação
    for op in ("consultar_processo", "trocar_unidade", "criar_documento_externo"):
        routing[op] = composite

    return routing
```

`CompositeBackend.__getattr__` consulta a tabela:

```python
def __getattr__(self, name: str) -> Callable:
    backend = self._routing.get(name)
    if backend is None:
        raise SEINotImplementedError(f"Operação '{name}' não suportada.")
    return getattr(backend, name)
```

**O que desaparece:** `_install_dispatchers()`, `_WEB_FIRST`, stubs
`raise NotImplementedError`, `try/except NotImplementedError` no fallback,
4 métodos mortos do contrato.

**O que fica com comportamento inalterado:** toda a lógica de roteamento,
fusão de dados, sincronização de sessão e preferências por operação — apenas
expressa de forma explícita em vez de gerada.

### 2.2 (Alta) Testes de conformidade de Protocol por backend

Com o Protocol split (2.1), conformidade é verificação estática: mypy/ty
reporta qual backend não implementa qual Protocol. Sem 2.1, a cobertura
precisa ser medida por introspecção em runtime.

**Proposta (independente de 2.1, como rede de segurança interim):**

- `tests/test_rest_backend.py` — verifica via introspecção quais métodos do
  `SEIRestBackend` sobrescrevem o stub base; falha se a cobertura cair abaixo
  do limiar atual (88%, 111/125).
- `tests/test_web_backend.py` — idem para `SEIWebBackend` (64%, 81/125).

Esses testes são descartados quando 2.1 for concluído — a cobertura passa a
ser verificada estaticamente.

### 2.3 (Alta) Constraints de parâmetro no schema, não só na prosa

Vários params têm formato implícito documentado só em prosa —
`protocolo_formatado` (ex.: `50300.000123/2025-00`), datas (`YYYY-MM-DD`), IDs
numéricos. Um agente pode enviar `17/06/2026` ou um protocolo sem máscara e a
falha só aparece no SEI.

**Proposta:** anotar os params de formato conhecido com
`Annotated[str, Field(pattern=…, examples=…)]`, de modo que o `inputSchema`
carregue a constraint e o pydantic **rejeite entrada malformada antes da
chamada**. Começar pelas tools mais usadas (`sei_consultar_processo`,
`sei_criar_processo`, `sei_criar_documento`).

### 2.4 (Média) Guidance de domínio configurável e injetada na resposta

Com 124 tools, a curva de "qual tool/qual ordem" é íngreme. As instruções do
servidor MCP já existem (domínio do SEI), mas são estáticas e globais.

**Proposta:** um campo de instruções **configurável** (por órgão/unidade),
devolvido nas tools de entrada (ex.: `sei_listar_processos`,
`sei_resumo_processos`) como `dicas`/`_hints`, para guiar fluxos recorrentes
do usuário sem depender de prompt estático.

### 2.5 (Média) Checagem de error-boundary no CI

O `todos` já tem `SEIError` e `ToolError` (RFC 0003/0004). Falta o guarda
automatizado que garanta que a tradução de erros acontece só na fronteira — a
convenção hoje depende de disciplina manual.

**Proposta:** um check leve de CI que falhe se um módulo de domínio levantar
erro de transporte (ou vice-versa) fora da fronteira esperada.

### 2.6 (Baixa, opcional) Superfície CLI a partir da mesma definição

O `todos` é essencialmente MCP-only. Uma CLI fina derivada das mesmas funções
ajudaria smoke-tests e operação manual sem duplicar lógica.

**Proposta:** avaliar um adaptador CLI opcional sobre as funções de tool
existentes (esforço alto; registrar como direção, não compromisso).

> **Atualização (2026-07-01):** implementado por RFC 0018 —
> `todos <tool> chave=valor` (`src/todos/cli_call.py`), sem duplicar as
> funções de tool: despacha via `python -m todos` como subprocesso stdio,
> reusando o mesmo catálogo MCP que um host normal usaria.

## 3. Priorização

| Prioridade | Item | Esforço | Valor |
|---|---|---|---|
| **Alta** | 2.2 Cobertura interim | Baixo | Rede de segurança até 2.1 |
| **Alta** | 2.1 Protocol split | Médio | Remove os 3 problemas raiz |
| **Alta** | 2.3 Constraints de schema | Médio | Rejeita entrada malformada antes do SEI |
| Média | 2.4 Guidance de domínio | Médio | Orienta navegação entre 124 tools |
| Média | 2.5 Error-boundary no CI | Médio | Garante envelope de erro consistente |
| Baixa | 2.6 CLI de fonte única | Alto | Debug/automação manual |

## 4. Não-objetivos

- Mudar o padrão de roteamento (Web baseline + REST enhancement) — esse é o correto.
- Re-fazer annotations/paginação/keyring — já existem (RFC 0003/0007/0002).
- Renomear ou remover tools.

## 5. Plano de implementação

1. **PR 1 — cobertura interim** (2.2): `tests/test_rest_backend.py` +
   `tests/test_web_backend.py` + CI workflow. Sem mudança de runtime.
2. **PR 2 — Protocol split** (2.1): 10 Protocols de domínio; `CompositeBackend`
   como implementador; tabela de roteamento no startup; deleta
   `_install_dispatchers`, stubs e `_WEB_FIRST`.
3. **PR 3 — constraints de schema** (2.3): `Field(pattern=…)` nas tools de
   maior uso, incremental tool a tool.
4. **PR 4 — guidance de domínio** (2.4) + **error-boundary check** (2.5).
5. **2.6** fica como direção futura, dependente de demanda real de CLI.
