# RFC 0017 — Validação lazy de ProtocoloFormatado

**Status:** 🟡 Proposta
**Data:** 2026-06-26
**Motivação:** skipped finding do code review de PR #109

---

## 1. Problema

`tools/configuracao.py` define `_ProtocoloFormatado` como um tipo `Annotated`
no nível de módulo:

```python
_host = _sei_host()                                           # import-time
_SEI_PROTOCOLO_PATTERN = (                                    # import-time
    get_settings().sei_protocolo_pattern
    or _read_keyring_pattern_sync(_host)
)
_ProtocoloFormatado = Annotated[str, Field(pattern=_SEI_PROTOCOLO_PATTERN, ...)]
```

Esse tipo é usado como anotação nos parâmetros de três tools em `processos.py`:

```python
from todos.tools.configuracao import _ProtocoloFormatado

@mcp.tool()
async def sei_consultar_processo(protocolo_formatado: _ProtocoloFormatado, ...):
    ...
```

O FastMCP introspecta as anotações em **tempo de execução** para construir o
schema MCP de cada tool. Por isso o módulo tem o aviso:

> Sem `from __future__ import annotations`: o FastMCP introspecta os type hints
> em tempo de execução para montar o schema de cada tool, então as anotações
> precisam ser objetos reais (não strings adiadas).

Isso torna `_ProtocoloFormatado` — e portanto `_SEI_PROTOCOLO_PATTERN` e
`_host` — **constantes de import-time**. Se `SEI_PROTOCOLO_PATTERN` for
alterada após a primeira importação (via `monkeypatch.setenv` + `cache_clear`),
o tipo já está compilado e a mudança não tem efeito.

### Consequência prática

Testes que verificam o comportamento do padrão de protocolo não podem usar
`monkeypatch.setenv("SEI_PROTOCOLO_PATTERN", ...)` sem reimportar o módulo — e
reimportar quebra o registro de tools do FastMCP. O tool `sei_redefinir_formato_protocolo`
persiste um novo padrão no keyring mas **não atualiza** a validação do argumento
`protocolo_formatado` durante a sessão corrente.

---

## 2. Opções

### Opção A — Validação no corpo da função (recomendada)

Remover o `Field(pattern=...)` do tipo `_ProtocoloFormatado` e validar o
protocolo explicitamente no início de cada tool, usando o padrão lido de
`get_settings()` (lazy):

```python
# configuracao.py
_ProtocoloFormatado = Annotated[str, Field(description=_PROTOCOLO_DESC)]

def _validar_protocolo(protocolo: str) -> None:
    """Valida protocolo_formatado contra o padrão configurado; levanta SEIValidationError."""
    pattern = get_settings().sei_protocolo_pattern or _read_keyring_pattern(host)
    if pattern and not re.fullmatch(pattern, protocolo):
        msg = f"protocolo_formatado inválido para o padrão '{pattern}': {protocolo!r}"
        raise SEIValidationError(msg)
```

```python
# processos.py — no início de cada tool
await _validar_protocolo(protocolo_formatado)
```

**Vantagens:**
- Nenhuma magia de tipos; totalmente testável com `monkeypatch.setenv` + `cache_clear`
- `sei_redefinir_formato_protocolo` passa a valer imediatamente na sessão
- O schema MCP do argumento continua sendo `{ "type": "string" }` — o cliente MCP
  vê a mesma interface, a restrição é enforced pelo servidor

**Desvantagens:**
- O schema MCP perde o `pattern` field — clientes que usam o schema para pré-validar
  a entrada no lado do cliente (ex.: validação em IDEs com suporte MCP) não verão
  a constraint
- Três funções precisam chamar `_validar_protocolo` explicitamente (boilerplate mínimo)

---

### Opção B — Tipo pydantic com `__get_pydantic_core_schema__` lazy

Criar uma classe `LazyProtocoloFormatado` que implementa o protocolo pydantic v2
para tipos customizados e lê o padrão de `get_settings()` em tempo de validação:

```python
class _LazyProtocoloStr(str):
    @classmethod
    def __get_pydantic_core_schema__(cls, source_type, handler):
        from pydantic_core import core_schema

        def validate(value: str) -> str:
            pattern = get_settings().sei_protocolo_pattern
            if pattern and not re.fullmatch(pattern, value):
                raise ValueError(f"não bate com o padrão '{pattern}'")
            return value

        return core_schema.no_info_plain_validator_function(validate)

_ProtocoloFormatado = Annotated[_LazyProtocoloStr, Field(description=_PROTOCOLO_DESC)]
```

**Vantagens:**
- Validação lazy sem mudar a interface das tools
- O schema MCP ainda reporta `{ "type": "string" }` (sem `pattern`, pois a constraint
  é num validador customizado, não em `Field(pattern=...)`)

**Desvantagens:**
- A constraint continua ausente do schema MCP (mesmo que Opção A)
- Acrescenta complexidade: a classe `_LazyProtocoloStr` é não-óbvia para
  mantenedores; `__get_pydantic_core_schema__` é API interna de pydantic v2
- Ganha testabilidade mas perde legibilidade

---

### Opção C — Recarregar o tipo após `sei_redefinir_formato_protocolo`

Manter `_ProtocoloFormatado` como hoje, mas forçar uma reinicialização do
processo (ou do módulo) quando o padrão mudar. Não viável: FastMCP não suporta
re-registro dinâmico de tools sem reiniciar o processo.

---

## 3. Decisão proposta

**Adotar a Opção A.**

O benefício chave é tornar `sei_redefinir_formato_protocolo` efetivo na sessão
corrente sem reiniciar o processo — o caso de uso principal do tool. O custo
(`pattern` ausente do schema JSON) é aceitável porque:

1. Nenhum cliente MCP conhecido usa `pattern` para pré-validar inputs antes de
   invocar a tool
2. O erro do servidor em caso de protocolo inválido já é claro: `SEIValidationError`
   com a mensagem do padrão esperado
3. A Opção B entrega a mesma ausência de `pattern` no schema com complexidade maior

---

## 4. Plano de implementação

1. **`tools/configuracao.py`**
   - Remover `_SEI_PROTOCOLO_PATTERN`, `_host` e a lógica de `_ProtocoloFormatado`
     com `Field(pattern=...)`
   - Definir `_ProtocoloFormatado = Annotated[str, Field(description=_PROTOCOLO_DESC)]`
   - Adicionar `_validar_protocolo(protocolo: str, ctx: Context | None = None) -> None`
     que lê o padrão de `get_settings()` e keyring lazily (reutilizando
     `_sei_host_from_ctx` para obter o host correto em modo OAuth)

2. **`tools/processos.py`**
   - Nas três tools que recebem `protocolo_formatado: _ProtocoloFormatado`, adicionar
     `await _validar_protocolo(protocolo_formatado, ctx)` no início do corpo

3. **`tests/`**
   - Os testes de `sei_detectar_formato_protocolo` e `sei_redefinir_formato_protocolo`
     passam a usar `monkeypatch.setenv("SEI_PROTOCOLO_PATTERN", ...)` sem reimport

4. **Sem mudança de versão de schema MCP** — a interface das tools não muda (só o
   schema do argumento perde o `pattern`, que nunca apareceu no schema público)

---

## 5. Fora de escopo

- Expor `pattern` dinamicamente no schema MCP via FastMCP custom schema hooks
  (não suportado pela versão atual do FastMCP)
- Aplicar a mesma validação lazy a outros tipos `Annotated` com `Field(pattern=...)`
  no codebase (não existem atualmente)
