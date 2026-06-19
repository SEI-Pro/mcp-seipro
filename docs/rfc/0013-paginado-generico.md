# RFC 0013 — PaginadoGenerico: colapsar subclasses Paginado em modelo único

**Status:** ✅ Concluída (Opção C implementada)
**Data:** 2026-06-19
**Relacionado:** RFC 0008 (saída estruturada, Fase 2)

---

## Contexto

RFC 0008 Fase 2 adicionou 12 subclasses de `Paginado` a `responses.py`, todas com a mesma estrutura:

```python
class ResultadoXxx(Paginado):
    """Resposta de sei_pesquisar_xxx."""
    campo: list[dict[str, object]] = Field(default_factory=list)
```

A única diferença entre elas é o nome do campo de dados (`hipoteses`, `tipos`, `assuntos`, etc.). O valor concreto em termos de schema publicado é mínimo — cada campo é `list[dict[str, object]]`, equivalente a `list[Any]` do ponto de vista do contrato estrutural.

**Por que foi deferido em RFC 0008:** mudar os nomes de campo publicados no `outputSchema` constitui uma quebra de compatibilidade para clientes MCP que já introspectam o schema. A migração exige versionamento cuidadoso.

---

## Problema

### 1. Superfície de manutenção desnecessária

Cada nova tool paginada exige:
- Uma nova classe em `responses.py`
- Uma nova entrada na lista de importações do módulo de tool
- Documentação do nome de campo no docstring

Se quisermos adicionar um campo comum a todas as respostas paginadas de lista (ex.: `fonte: str | None`), precisamos editar 12 classes manualmente — e esquecemos uma, o schema diverge silenciosamente.

**Evidência:** `ResultadoListaProcessos` e `ProcessosBloco` foram criados como duplicatas estruturais idênticas no mesmo PR — o padrão já produziu divergência imediata.

### 2. Campo nome ≠ contrato estrutural

O nome do campo (`hipoteses` vs. `contatos`) não adiciona contrato de tipo — o conteúdo é `list[dict[str, object]]` em ambos. A diferença só existe no `outputSchema` JSON publicado para clientes MCP. Mas clientes que parseiam o schema por campo-nome-específico já estão codificados contra a API de cada tool individualmente; um único campo genérico `itens` não seria pior para eles.

---

## Proposta

### Opção A — `PaginadoGenerico` (campo único `itens`)

```python
class PaginadoGenerico(Paginado):
    """Resposta paginada genérica para tools de catálogo."""
    itens: list[dict[str, object]] = Field(default_factory=list)
```

Todas as 12 tools usariam `PaginadoGenerico`. `ResultadoPesquisaProcessos` permanece separado (tem campos extras `fonte`, `aviso`). `ResultadoListaProcessos` também permanece separado (tem `total_filtrados`, `pagina_atual`, `layout`, `hints`).

**Vantagem:** 12 classes → 1. Manutenção trivial.

**Desvantagem:** quebra de schema para clientes existentes que lêem `result.hipoteses` em vez de `result.itens`.

### Opção B — `PaginadoGenerico` com `alias` por tool

```python
class PaginadoGenerico(Paginado):
    itens: list[dict[str, object]] = Field(default_factory=list)

# Em cada tool, documentar o alias no docstring:
# O campo `itens` contém as hipóteses legais encontradas.
```

Idêntica à A, mas com documentação explícita do significado de `itens` por tool.

### Opção C — Typing genérico `Paginado[T]` (futuro)

```python
from typing import Generic, TypeVar
T = TypeVar("T")

class Paginado(BaseModel, Generic[T]):
    ...
    itens: list[T] = Field(default_factory=list)

class HipoteseLegal(BaseModel):
    id: str
    nome: str
    ...
```

Permite tipagem real por item. Exige modelar cada tipo de item individualmente — muito trabalho, mas entrega o contrato estrutural real que justifica usar Pydantic.

**Custo:** modelar ~10 tipos de item SEI. Payoff: outputSchema com propriedades por campo de item.

---

## Recomendação

**Fase imediata (baixo custo):** Opção A — colapsar as 12 classes genéricas em `PaginadoGenerico` com campo `itens`. Aceitar a quebra de `outputSchema` como uma mudança semântica menor (as tools de catálogo têm pouco consumo direto de schema).

**Fase futura:** Opção C para as 3-4 tools mais usadas (`sei_pesquisar_tipos_processo`, `sei_pesquisar_contatos`, `sei_pesquisar_unidades`) — quando os tipos de item SEI estiverem estabilizados.

---

## Critérios de conclusão

| Critério | Verificação |
|---|---|
| 12 classes substituídas por `PaginadoGenerico` | grep `class Resultado` em responses.py retorna ≤ 5 hits |
| `outputSchema` de tools de catálogo publica campo `itens` | inspeção manual `list_tools()` |
| 578+ testes verdes | CI |
| Nenhum cliente MCP no repositório lê campo por nome específico | grep `\.hipoteses\|\.tipos\|\.assuntos` em testes |
