# RFC 0009 — Protocol split, cobertura de backend, constraints de schema e guidance de domínio

**Status**: Proposta · **Atualizado**: 2026-06-18
**Data**: 2026-06-17
**Autores**: Claude (com Franklin Baldo)
**RFCs relacionados**: RFC 0003 (ergonomia FastMCP), RFC 0006 (backend abstrato), RFC 0007 (response shaping/paginação)

## 1. Diagnóstico: interface monolítica gorda

### 1.1 Arquitetura atual

O projeto usa **Chain of Responsibility + Strategy** com implementação por mixins:

| Componente | Papel |
|---|---|
| `SEIBackend` | 125 métodos async, todos levantando `NotImplementedError` |
| `SEIRestBackend` | Strategy REST — implementa 111/125 (88%) via mixins por domínio |
| `SEIWebBackend` | Strategy Web — implementa 81/125 (64%) via mixins por domínio |
| `CompositeBackend` | Chain of Responsibility — REST-first com fallback web; composição paralela para `consultar_processo` |

O padrão de roteamento é **apropriado para o use case**: dois backends com
capacidades assimétricas, preferência configurável por operação (`_WEB_FIRST`),
e degradação graciosa quando o mod-wssei não está disponível. O problema não é
o padrão — é a forma como o contrato está modelado.

### 1.2 O problema: interface única monolítica

`SEIBackend` tenta ser o contrato de dois backends com capacidades muito
diferentes usando um único vetor de 125 métodos. Isso força uma cadeia de
consequências negativas:

1. **Stubs `raise NotImplementedError` em todo lugar** — backends precisam
   herdar 14–44 stubs do contrato para cada domínio que não suportam.

2. **`try/except NotImplementedError` no `CompositeBackend`** — o mecanismo de
   fallback depende de capturar a exceção do stub como sinal de "este backend
   não atende". Erro de implementação e "feature ausente" usam o mesmo canal.

3. **`_install_dispatchers()`** — geração de ~55 métodos delegadores por
   introspecção no nível de módulo, para evitar escrever a delegação manualmente.
   IDE não navega para a implementação; stack traces ficam confusos.

4. **Contrato desatualizado** — 4 métodos (`cancelar_assinatura`,
   `gerar_referencia`, `marcar_nao_lido`, `resumo_processos`) não têm
   implementação em nenhum backend: as tools os contornam compondo outros
   métodos ou chamando o REST client diretamente. O contrato perdeu sincronia
   com a realidade sem que nenhum CI detectasse.

5. **Ausência de `@abstractmethod` não é feature** — a justificativa no
   docstring ("subclasses sobrescrevem só o que suportam") é a consequência do
   problema 1, não a solução. Com uma interface gorda, `@abstractmethod`
   obrigaria 125 implementações em cada backend — por isso foi omitido. O fix
   correto é enxugar o contrato, não abrir mão da verificação estática.

### 1.3 Direção: Protocol por domínio

O contrato já está naturalmente dividido em **10 seções** no `base.py`
(unidades/sessão, processos-leitura, processos-escrita, documentos, catálogos,
marcadores, acompanhamento, blocos internos, blocos assinatura, credenciamento).
Essas seções são os Protocols naturais do sistema.

Com `typing.Protocol`:

- Backends **não herdam de `SEIBackend`** — implementam os Protocols dos
  domínios que realmente suportam.
- O compilador estático (mypy/ty) verifica conformidade; `@abstractmethod`
  torna-se desnecessário.
- `CompositeBackend` roteia por `isinstance(backend, XProtocol)` — sem
  `try/except NotImplementedError`, sem `_install_dispatchers`.
- Métodos mortos desaparecem: o contrato só tem o que algum backend implementa.

## 2. Propostas

### 2.1 (Alta) Protocol split: substituir `SEIBackend` monolítico por Protocols por domínio

**O que muda:**

```python
# Antes: um contrato com 125 stubs
class SEIBackend:
    async def consultar_processo(self, processo: str) -> dict:
        raise NotImplementedError

# Depois: 10 Protocols pequenos
class ProcessosProtocol(Protocol):
    async def consultar_processo(self, processo: str) -> dict: ...
    async def criar_processo(self, dados: NovoProcesso) -> dict: ...
    # (~34 métodos)

class DocumentosProtocol(Protocol):
    async def criar_documento_interno(self, ...) -> dict: ...
    # (~19 métodos)
```

`SEIRestBackend` e `SEIWebBackend` deixam de herdar de `SEIBackend` e
implementam apenas os Protocols de cada domínio que suportam. `CompositeBackend`
roteia por `isinstance`:

```python
class CompositeBackend:
    async def consultar_processo(self, processo: str) -> dict:
        if self._rest and isinstance(self._rest, ProcessosProtocol):
            try:
                return await self._rest.consultar_processo(processo)
            except (SEINotFoundError, SEIParseError, SEIConnectionError):
                pass
        return await self._web.consultar_processo(processo)
```

**O que desaparece:** `_install_dispatchers()`, stubs `raise NotImplementedError`,
`try/except NotImplementedError` no fallback, 4 métodos mortos do contrato.

**O que fica:** lógica de roteamento REST-first/web-first/composição paralela —
apenas explicitada em vez de gerada.

**Esforço estimado:** médio — é refator estrutural sem mudança de comportamento
observável. Os mixins existentes (`ProcessosRest`, `ProcessosWeb`, etc.) já
organizam o código na estrutura correta; a mudança é na camada de tipos.

### 2.2 (Alta) Testes de conformidade de Protocol por backend

Com o Protocol split (2.1), conformidade de backend vira verificação estática:
mypy/ty reporta qual backend não implementa qual Protocol. Sem 2.1, a cobertura
precisa ser medida por introspecção em runtime.

**Proposta (independente de 2.1):** dois testes que medem e protegem a cobertura
atual do contrato `SEIBackend` enquanto o Protocol split não é feito:

- `tests/test_rest_backend.py` — verifica via introspecção quais métodos do
  `SEIRestBackend` sobrescrevem o stub base; falha se a cobertura cair abaixo
  do limiar atual (88%, 111/125).
- `tests/test_web_backend.py` — idem para `SEIWebBackend` (64%, 81/125).

Esses testes são descartados quando 2.1 for concluído — a cobertura passa a ser
verificada estaticamente.

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

Com 118 tools, a curva de "qual tool/qual ordem" é íngreme. As instruções do
servidor MCP já existem (domínio do SEI), mas são estáticas e globais.

**Proposta:** um campo de instruções **configurável** (por órgão/unidade),
devolvido nas tools de entrada (ex.: `sei_listar_processos`,
`sei_resumo_processos`) como `dicas`/`_hints`, para guiar fluxos recorrentes
do usuário sem depender de prompt estático.

### 2.5 (Média) Checagem de error-boundary no CI

O `mcp-sei` já tem `SEIError` e `ToolError` (RFC 0003/0004). Falta o guarda
automatizado que garanta que a tradução de erros acontece só na fronteira — a
convenção hoje depende de disciplina manual.

**Proposta:** um check leve de CI que falhe se um módulo de domínio levantar
erro de transporte (ou vice-versa) fora da fronteira esperada.

### 2.6 (Baixa, opcional) Superfície CLI a partir da mesma definição

O `mcp-sei` é essencialmente MCP-only. Uma CLI fina derivada das mesmas funções
ajudaria smoke-tests e operação manual sem duplicar lógica.

**Proposta:** avaliar um adaptador CLI opcional sobre as funções de tool
existentes (esforço alto; registrar como direção, não compromisso).

## 3. Priorização

| Prioridade | Item | Esforço | Valor |
|---|---|---|---|
| **Alta** | 2.1 Protocol split | Médio | Remove débito arquitetural raiz |
| **Alta** | 2.2 Cobertura de backend (interim) | Baixo | Rede de segurança até 2.1 |
| **Alta** | 2.3 Constraints de schema | Médio | Rejeita entrada malformada antes do SEI |
| Média | 2.4 Guidance de domínio | Médio | Orienta navegação entre 118 tools |
| Média | 2.5 Error-boundary no CI | Médio | Garante envelope de erro consistente |
| Baixa | 2.6 CLI de fonte única | Alto | Debug/automação manual |

## 4. Não-objetivos

- Mudar o padrão de roteamento (REST-first/web-fallback/composição paralela) — esse é o correto.
- Re-fazer annotations/paginação/keyring — já existem (RFC 0003/0007/0002).
- Renomear ou remover tools.

## 5. Plano de implementação

1. **PR 1 — cobertura interim** (2.2): `tests/test_rest_backend.py` +
   `tests/test_web_backend.py` + CI workflow. Sem mudança de runtime. Serve de
   rede de segurança durante o Protocol split.
2. **PR 2 — Protocol split** (2.1): substitui `SEIBackend` monolítico por 10
   Protocols de domínio; deleta `_install_dispatchers`; torna 2.2 estático.
3. **PR 3 — constraints de schema** (2.3): `Field(pattern=…)` nas tools de
   maior uso, incremental tool a tool.
4. **PR 4 — guidance de domínio** (2.4) + **error-boundary check** (2.5).
5. **2.6** fica como direção futura, dependente de demanda real de CLI.
