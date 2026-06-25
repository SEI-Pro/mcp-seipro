# RFC 0015 — Return contract: domínio tipado + `raise`, sem envelopes manuais

**Status:** 📋 Proposta
**Data:** 2026-06-25
**Relacionado:** RFC 0004 (exceções), RFC 0008 (saída estruturada), audit `docs/audits/2026-06-25-return-contract.md`

---

## Contexto

O audit `2026-06-25-return-contract.md` encontrou **34 violações** de contrato de retorno em 10 arquivos: funções que retornam `tuple[status, payload]`, strings/ints mágicos, `None`-como-erro, `bool`-como-erro e dicts com chave de status. A regra violada é *Clean Code* cap. 7 — "Use Exceptions Rather Than Return Codes".

Duas peças do desenho já existem e **não devem ser reinventadas**:

- **RFC 0004** estabeleceu a hierarquia `SEIError(ToolError)`. As tools deixam o erro propagar; o host MCP recebe a mensagem legível. Esse é o modelo de erro do projeto — este RFC o reafirma, não o substitui.
- **RFC 0008** fez as tools retornarem `BaseModel` Pydantic direto. O FastMCP serializa em `content[0].text` + `structured_content` + `outputSchema` automaticamente. `next_actions: list[NextAction]` já é campo tipado nos modelos de `responses.py`.

Este RFC fecha a lacuna entre esses dois: **o que cada camada retorna no caminho feliz**, e como eliminar os sentinels do audit sem regredir o que o 0008 já entregou.

---

## Problema

### 1. O backend conhece o formato de resposta MCP

12 métodos de escrita em `sei_web_client.py` (audit F-009) retornam `{"ok": True, ...}` ou `{"status": "ok", ...}` diretamente. O backend fala SEI — não deveria saber que existe um `"ok"` do outro lado. Isso acopla a camada de scraping ao envelope MCP e esconde o contrato do type checker (`-> dict`).

### 2. Tuplas nuas codificam estado por posição

`setup_wizard.py` concentra 13 instâncias (F-012–F-018) de `tuple[str, str, ...]` onde o primeiro elemento vazio ou o `bool` final codifica um caso de falha. O caller desempacota por posição (`a, b = f()`) e compara com sentinel.

### 3. `None` colapsa "não encontrado" com "falhou"

`_buscar_documento_em_processo` / `_buscar_documento_via_solr` (F-001/F-002) capturam erro de rede, logam e retornam `None` — indistinguível de "documento não existe". O caller degrada silenciosamente para o fallback errado.

### 4. Resquício de sentinel no boundary

`RespostaEscrita` ainda tem `status: str = "ok"` — um campo que é sempre `"ok"` no retorno normal (anomalia propaga como `SEIError`). Carrega zero bits: é a cicatriz de uma união cujo braço de erro já foi amputado para o espaço de exceções.

---

## Princípio: o tipo de retorno segue a camada

| Camada | Retorna no caminho feliz | Sinaliza anomalia |
|---|---|---|
| **Backend** (`backends/`, `sei_*_client`) | objeto de domínio Pydantic | `raise SEIError` tipado |
| **Tool** (`tools/`, `server.py`) | o `BaseModel` direto (FastMCP serializa) | deixa `SEIError` propagar |
| **Helper interno** (`setup_wizard`, `_resolver_*`) | value object nomeado (frozen dataclass) | `raise` |

O ponto central: **nenhuma camada de baixo conhece o envelope da camada de cima.** O backend não constrói `{"ok": ...}`; a tool não constrói dict manual — retorna o modelo e o FastMCP faz o wire.

---

## Decisões

### D1. `raise` em todo lugar — sem `Result[T, E]`

Anomalias propagam como `SEIError` (subclasse de `ToolError`), como no RFC 0004. **Rejeitamos** introduzir `Result`/`Either` mesmo no seam do `_dispatch_in_order` do composite (que hoje faz `match` por tipo de erro): `Result` é não-idiomático em Python async, adiciona cerimônia de `match` sem do-notation, e luta contra o grain do FastMCP (que quer exceções propagando). O custo da invisibilidade do erro na assinatura é mitigado **documentando os subtipos de `SEIError` no docstring** das funções de dispatch/resolução.

### D2. Regra de escolha de tipo (mecânica)

- **Cruza fronteira** (parsing de dado não-confiável do SEI **ou** serialização para o host MCP) → **Pydantic `BaseModel`**. Ganha validação (se o SEI mudar o HTML, falha alto) + `model_dump`/`outputSchema` de graça.
- **Nasce e morre dentro de um módulo** → **`@dataclass(frozen=True, slots=True)`**. Sem custo de validação, imutável, força acesso por atributo.
- **Nunca `NamedTuple`** — continua desempacotável por posição, reabre o anti-pattern do audit.

### D3. `None` continua valor legítimo de ausência

`-> X | None` é correto quando `None` é *ausência* (cache miss, lookup opcional que o caller trata sem agir como erro). `raise SEINotFoundError` **apenas** quando o not-found é uma condição sobre a qual o caller age como falha. Critério: *o caller continua normalmente com `None`?* → mantém `None`. *O caller teria que `try/except` ou abortar?* → `raise`. Forçar `try/except` em hot path (ex.: `catalog_cache`) seria control-flow-por-exceção — o próprio anti-pattern que o audit cita.

### D4. Sem envelope manual — reusar a saída estruturada do RFC 0008

**Proibido** reintroduzir um helper `_ok()` ou `TypedDict` de envelope. Isso regrediria o RFC 0008: um `TypedDict` não gera `outputSchema`; um `BaseModel` gera. A tool retorna o modelo; o `_shape_resposta_escrita` existente continua sendo o único adapter `dict → modelo`. Remover `status: str = "ok"` de `RespostaEscrita` (D1 + §Problema.4) — o sucesso é implícito no retorno normal; a falha já é `isError` do MCP.

### D5. Múltiplos resultados tipados → discriminated union

Quando uma tool tem mais de um resultado estruturalmente distinto, usar `Annotated[Criado | Alterado, Field(discriminator="acao")]` em vez de campos `| None` mutuamente exclusivos no mesmo modelo.

---

## Exemplo ponta a ponta

```python
# responses.py — modelo de domínio (boundary)
class ProcessoAlterado(BaseModel):
    """Resultado de alteração de processo."""
    acao: str = "alterar_processo"
    protocolo: str
    mensagem: str | None = None
    next_actions: list[NextAction] = Field(default_factory=list)

# backends/web/processos.py — sem conhecimento de MCP, levanta SEIError
async def alterar_processo_web(self, ...) -> ProcessoAlterado:
    erro = _extrair_erro_sei(...)
    if erro:
        raise SEIConnectionError(erro)
    return ProcessoAlterado(protocolo=protocolo, mensagem="Processo alterado.")

# tools/processos.py — retorna o modelo; FastMCP serializa
@mcp.tool(annotations=_WRITE)
async def sei_alterar_processo(...) -> ProcessoAlterado:
    res = await backend.alterar_processo(...)          # SEIError propaga → ToolError
    res.next_actions = [NextAction(
        tool="sei_consultar_processo",
        args={"nup": res.protocolo},
        reason="confirme a alteração",
    )]
    return res
```

```python
# setup_wizard.py — multi-valor interno vira frozen dataclass (F-012–F-018)
@dataclass(frozen=True, slots=True)
class CredentialsResult:
    usuario: str
    senha_config: str   # "" = usar keyring
    senha_validacao: str
```

---

## Plano de migração (incremental, cada passo um PR, testes verdes)

1. **`setup_wizard.py` + `tools/configuracao.py`** — frozen dataclasses puramente internas. Zero impacto de wire, zero churn de teste de saída. Resolve ~14 dos 34 findings (F-012–F-019).
2. **`backends/{rest,web}/documentos.py`** — `buscar_documento` para de emitir `{"encontrado": False}`; `raise SEINotFoundError`. Toca só os callers de `buscar_documento` (F-021, F-023, F-024).
3. **`sei_web_client.py` F-009** — remove `ok`/`status` dos 12 dicts de escrita; `_shape_resposta_escrita` já absorve os campos por alias, então as tools não mudam.
4. **`mcp_app.py` / `composite.py` / `catalog_cache.py`** — propagar-não-engolir (RFC 0004 §6) em F-001/F-002; o `IntEnum` do `_prioridade_erro` (F-020); manter `None` legítimo no cache (D3).
5. **Remover `status: "ok"` de `RespostaEscrita`** — mudança de `outputSchema`, coordenar como o RFC 0013 fez (compat de clientes que introspectam schema).
6. **`auth.py` por último** — F-001–F-004 são constrangidos pela interface `OAuthTokenStore` do FastMCP (assinaturas `-> T | None` externas). Precisa de adapter; fazer deliberadamente, não mecânico.

---

## Alternativas consideradas e rejeitadas

- **`Result[T, SEIError]` no seam do composite.** Daria exhaustividade checável pelo type checker onde o código já faz `match` por tipo de erro. Rejeitado em D1: cerimônia não-idiomática contra o grain do FastMCP, para um único maintainer. Mitigação: docstring dos subtipos.
- **Pydantic em tudo, inclusive interno.** Uma regra a menos no code review, mas paga validação onde não há fronteira (`_build_mcp_env` não valida nada). Rejeitado em D2: frozen dataclass é mais honesto sobre custo e força imutabilidade.
- **Helper `_ok()` + `TypedDict` de envelope.** Rejeitado em D4: regride o RFC 0008 (sem `outputSchema`).

---

## Não-objetivos

- Reescrever o modelo de erro do RFC 0004 — este RFC o reafirma.
- Migrar tools de leitura que já retornam `BaseModel` corretamente (a maioria, pós-0008).
- Tocar os 42 arquivos `clean` do audit.
