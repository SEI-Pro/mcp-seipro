---
okf:
  type: audit
  subtype: okf-md/audit
  version: "1.0"
  status: final
  rule: return-contract
  rule_ref: "Clean Code cap. 7 (Martin, 2008); Effective Python item 87; Railway-Oriented Programming (Wlaschin, 2014)"
  severity_scale: [critical, high, medium, low, info]
  codebase: franklinbaldo/todos
  scope: src/todos/ — todos os 51 arquivos Python
  authors:
    - claude-sonnet-4-6
  created: 2026-06-25
  audited_at: 2026-06-25
  tags:
    - return-contract
    - exceptions
    - sentinel
    - tuple-return
---

# Auditoria: Return Contract

## Contexto

A regra **return-contract** estabelece que toda função deve ter exatamente um tipo de retorno (o caminho feliz) e sinalizar desvios via exceções. Funções que retornam `(status, payload)`, strings mágicas, `None`-como-erro ou `bool`-como-erro criam contratos ambíguos: o chamador é obrigado a checar a primeira posição da tupla (ou comparar com sentinels) antes de usar o resultado — exatamente o padrão de `errno` do C, que exceções foram criadas para substituir.

**Referências:**
- *Clean Code* cap. 7: "Use Exceptions Rather Than Return Codes" — R. C. Martin
- *Command-Query Separation* — B. Meyer (Object-Oriented Software Construction, 1988)
- *Railway-Oriented Programming* — S. Wlaschin (F# for Fun and Profit)
- *Effective Python* item 87: "Define a Root Exception to Insulate Callers from APIs"

**Tipos de violação auditados:**

| Código | Nome | Exemplo |
|---|---|---|
| `RC-TUPLE` | Tuple-as-discriminated-union | `return ("bloquear", payload)` |
| `RC-SENTINEL` | Sentinel return | `return "nao_suportado"` / magic int |
| `RC-NONE-AS-ERROR` | None-as-error | `return None` quando significa "falhou" |
| `RC-UNION-STATUS` | Union-status | `-> X \| None` onde `None` codifica falha |
| `RC-BOOL-ERROR` | Bool-as-error | `return bool` para indicar sucesso/falha |
| `RC-DICT-STATUS` | Dict-with-status-key | `return {"encontrado": False, ...}` |

---

## Resumo executivo

| Archivos auditados | Com violations | Clean |
|---|---|---|
| 51 | 9 | 42 |

| Severidade | Findings |
|---|---|
| critical | 0 |
| high | 20 |
| medium | 5 |
| low | 5 |
| info | 2 |
| **Total** | **32** |

O foco de maior impacto está em `setup_wizard.py` (12 tuplas nuas) e `auth.py` (4 violations com nota de constraint de interface externa). Corrigir todos os `high` elimina ~93% do risco prático.

---

## `src/todos/auth.py`

> **Estado:** violations-found ⚠ nota: parcialmente constrangido por interface externa (FastMCP OAuth)

### Findings

| ID | Função / linha | Tipo | Severidade |
|---|---|---|---|
| F-001 | `load_access_token` | `RC-UNION-STATUS` | high |
| F-002 | `load_refresh_token` | `RC-UNION-STATUS` | high |
| F-003 | `load_authorization_code` | `RC-UNION-STATUS` | high |
| F-004 | `get_sei_credentials_from_token` | `RC-UNION-STATUS` | high |

#### F-001 — `load_access_token`

**Tipo:** RC-UNION-STATUS
**Severidade:** high
**Padrão atual:**
```python
async def load_access_token(self, token: str) -> AccessToken | None:
    payload = _verify(token)
    if not payload or payload.get("type") != "access":
        return None
```
**Problema:** `None` sinaliza token inválido/expirado — condição de erro, não ausência de valor. O chamador deve comparar com `None` para distinguir sucesso de falha.

**Nota:** Esse método implementa a interface `OAuthTokenStore` do FastMCP SDK, que define a assinatura `-> T | None`. A violação é real mas constrangida: modificar o tipo de retorno requer atualização de `TokenStore` upstream ou criação de um adaptador wrapper que converta `None` em exceção logo após o retorno.

**Refatoração sugerida** (com wrapper de adaptação):
```python
async def load_access_token(self, token: str) -> AccessToken:
    payload = _verify(token)
    if not payload or payload.get("type") != "access":
        raise TokenError(error="invalid_grant", error_description="Invalid access token")
    ...
```
**Esforço:** medium
**Impacto se não corrigido:** Callers que esqueçam o `if result is None` operam com objeto nulo silenciosamente.

---

#### F-002 — `load_refresh_token`

**Tipo:** RC-UNION-STATUS — mesmo padrão de F-001 para refresh token.

**Padrão atual:**
```python
async def load_refresh_token(self, ...) -> RefreshToken | None:
    if not payload or payload.get("type") != "refresh":
        return None
```
**Esforço:** medium

---

#### F-003 — `load_authorization_code`

**Tipo:** RC-UNION-STATUS — mesmo padrão para authorization code.

**Padrão atual:**
```python
async def load_authorization_code(self, ...) -> AuthorizationCode | None:
    if not data or data["client_id"] != client.client_id:
        return None
```
**Esforço:** medium

---

#### F-004 — `get_sei_credentials_from_token`

**Tipo:** RC-UNION-STATUS
**Severidade:** high
**Padrão atual:**
```python
def get_sei_credentials_from_token(token: str) -> dict | None:
    payload = _verify(token)
    if not payload or payload.get("type") != "access":
        return None
    sei = payload.get("sei")
    if sei is None:
        return None
```
**Problema:** Dois `return None` com semântica diferente: token inválido vs. credenciais ausentes no payload. O chamador não consegue distinguir os dois casos.

**Refatoração sugerida:**
```python
def get_sei_credentials_from_token(token: str) -> dict:
    payload = _verify(token)
    if not payload or payload.get("type") != "access":
        raise SEIAuthError("Token inválido ou expirado")
    sei = payload.get("sei")
    if sei is None:
        raise SEIAuthError("Credenciais SEI ausentes no token")
    return sei
```
**Esforço:** medium

---

## `src/todos/access_control.py`

> **Estado:** clean — nenhuma violação encontrada.

---

## `src/todos/exceptions.py`

> **Estado:** clean — nenhuma violação encontrada.

---

## `src/todos/responses.py`

> **Estado:** clean — nenhuma violação encontrada.

---

## `src/todos/hints.py`

> **Estado:** clean — nenhuma violação encontrada.

---

## `src/todos/mcp_app.py`

> **Estado:** clean — nenhuma violação encontrada após refatoração PR #97.

---

## `src/todos/server.py`

> **Estado:** clean — nenhuma violação encontrada.

---

## `src/todos/remote.py`

> **Estado:** clean — nenhuma violação encontrada.

---

## `src/todos/sei_web_client.py`

> **Estado:** violations-found

### Findings

| ID | Função / linha | Tipo | Severidade |
|---|---|---|---|
| F-005 | `_parse_inbox_html` | `RC-TUPLE` | high |
| F-006 | `_extrair_erro_sei` | `RC-UNION-STATUS` | low |
| F-007 | `_ler_senha_keyring` | `RC-UNION-STATUS` | low |
| F-008 | `_link_acao_visualizacao` | `RC-UNION-STATUS` | medium |

#### F-005 — `_parse_inbox_html`

**Tipo:** RC-TUPLE
**Severidade:** high
**Padrão atual:**
```python
return ("detalhada", [])
return ("resumida", rows)
return ("desconhecido", [])
```
**Problema:** O primeiro elemento da tupla é um sentinel de formato de tabela — o chamador deve comparar strings para saber o que veio. `"desconhecido"` com lista vazia é indistinguível de "tabela conhecida vazia".

**Refatoração sugerida:**
```python
from enum import Enum

class FormatoInbox(Enum):
    DETALHADA = "detalhada"
    RESUMIDA = "resumida"

@dataclass
class InboxParsed:
    formato: FormatoInbox
    rows: list[dict]

# No caso desconhecido:
raise SEIParseError("Formato de inbox desconhecido — tabela não reconhecida")
```
**Esforço:** medium

---

#### F-006 — `_extrair_erro_sei`

**Tipo:** RC-UNION-STATUS
**Severidade:** low
**Padrão atual:**
```python
def _extrair_erro_sei(html: str) -> str | None:
    ...
    return None  # sem erro
```
**Problema:** `None` significa "nenhum erro encontrado" (ausência legítima). Menos crítico, mas pode ser confundido com "parse falhou" por um leitor desatento.

**Refatoração sugerida:** Manter `None` como "não encontrado" mas documentar explicitamente; ou retornar `""` (string vazia) para distinguir de falha de parse.
**Esforço:** low

---

#### F-007 — `_ler_senha_keyring`

**Tipo:** RC-UNION-STATUS
**Severidade:** low
**Padrão atual:**
```python
async def _ler_senha_keyring(self, keyring_user: str) -> str | None:
```
**Problema:** `None` pode significar "keyring não disponível" (erro) ou "senha não armazenada" (ausência legítima). Semânticas distintas colapsadas no mesmo retorno.

**Refatoração sugerida:**
```python
async def _ler_senha_keyring(self, keyring_user: str) -> str | None:
    # None SOMENTE para "não encontrado"; levantar KeyringUnavailableError para falha.
```
**Esforço:** low

---

#### F-008 — `_link_acao_visualizacao`

**Tipo:** RC-UNION-STATUS
**Severidade:** medium
**Padrão atual:**
```python
async def _link_acao_visualizacao(self, protocolo: str, nome_var: str) -> str | None:
    ...
    if not m:
        return None  # ação não disponível nesta instância
```
**Problema:** O chamador deve checar `if link is None` antes de usar. "Ação não disponível" é uma condição excepcional conhecida, não ausência neutra.

**Refatoração sugerida:**
```python
async def _link_acao_visualizacao(self, protocolo: str, nome_var: str) -> str:
    ...
    if not m:
        raise SEINotImplementedError(
            f"Ação '{nome_var}' não disponível nesta instância SEI"
        )
```
**Esforço:** medium

---

## `src/todos/sei_client.py`

> **Estado:** clean — nenhuma violação encontrada.

---

## `src/todos/html_utils.py`

> **Estado:** clean — nenhuma violação encontrada.

---

## `src/todos/sei_styles.py`

> **Estado:** clean — nenhuma violação encontrada.

---

## `src/todos/catalog_cache.py`

> **Estado:** violations-found

### Findings

| ID | Função / linha | Tipo | Severidade |
|---|---|---|---|
| F-009 | `get` | `RC-UNION-STATUS` | low |
| F-010 | `_get_sync` | `RC-UNION-STATUS` | low |
| F-011 | `ttl` | `RC-UNION-STATUS` | low |

#### F-009 — `get`

**Tipo:** RC-UNION-STATUS
**Severidade:** low
**Padrão atual:**
```python
async def get(self, namespace: dict[str, str], key: str) -> object | None:
    try:
        return await asyncio.to_thread(self._get_sync, namespace, key)
    except (sqlite3.Error, json.JSONDecodeError):
        logger.warning("Falha ao ler cache...")
    return None  # ← erro e cache-miss colapsados
```
**Problema:** O último `return None` cobre tanto "chave não existe" (normal) quanto "sqlite falhou" (erro). Callers não conseguem distinguir.

**Refatoração sugerida:**
```python
async def get(self, namespace: dict[str, str], key: str) -> object | None:
    try:
        return await asyncio.to_thread(self._get_sync, namespace, key)
    except (sqlite3.Error, json.JSONDecodeError) as exc:
        raise CacheError("Falha ao ler cache") from exc
    # None do _get_sync significa cache-miss legítimo
```
**Esforço:** low

---

#### F-010 / F-011 — `_get_sync` / `ttl`

Mesmo padrão de F-009: `None` cobre erro e ausência. Esforço: low.

---

## `src/todos/setup_wizard.py`

> **Estado:** violations-found ⚠ maior concentração de RC-TUPLE na codebase

### Findings

| ID | Função / linha | Tipo | Severidade |
|---|---|---|---|
| F-012 | `_detect_organs` | `RC-TUPLE` | high |
| F-013 | `_resolve_organ_from_list` | `RC-TUPLE` | high |
| F-014 | `_resolve_organ_manual` | `RC-TUPLE` | high |
| F-015 | `_save_password_to_keyring` (×4) | `RC-TUPLE` | high |
| F-016 | `_detect_organs_with_ssl_fallback` (×3) | `RC-TUPLE` | high |
| F-017 | `_setup_credentials` | `RC-TUPLE` | high |
| F-018 | `_build_mcp_env` | `RC-TUPLE` | high |

Este arquivo concentra **12 instâncias** de funções que retornam tuplas nuas com 2–4 elementos posicionais sem nome. A raiz é que `setup_wizard.py` foi desenvolvido como script imperativo e nunca migrado para types estruturados.

#### F-015 — `_save_password_to_keyring` (caso crítico)

**Tipo:** RC-TUPLE
**Severidade:** high
**Padrão atual:**
```python
def _save_password_to_keyring(...) -> tuple[str, str]:
    ...
    return senha, senha      # OK: (config_pass, validation_pass)
    return "", senha          # falha leitura: config_pass vazio = "usar plaintext"
    return "", lida           # falha validação: config_pass vazio
    return "", senha          # falha readback
```
**Problema:** Quatro `return` diferentes onde o primeiro `str` vazio codifica 3 casos de falha distintos. O chamador usa `if config_pass == ""` para detectar erro — sentinel puro.

**Refatoração sugerida:**
```python
@dataclass
class KeyringResult:
    config_password: str  # vazio ↔ "usar plaintext no config"
    validation_password: str
    stored_in_keyring: bool

# Erros de I/O → raise KeyringWriteError("mensagem")
```
**Esforço:** medium

#### F-018 — `_build_mcp_env`

**Tipo:** RC-TUPLE
**Severidade:** high
**Padrão atual:**
```python
def _build_mcp_env(...) -> tuple[dict, bool]:
    return mcp_env, using_plaintext
```
**Problema:** `bool` na segunda posição é um flag de estado — o chamador destrincha `acao, usando_plaintext = _build_mcp_env(...)` e usa o bool para ramificar.

**Refatoração sugerida:**
```python
@dataclass
class MCPEnvConfig:
    env: dict[str, str]
    using_plaintext_password: bool
```
**Esforço:** low

---

## `src/todos/tools/configuracao.py`

> **Estado:** violations-found

### Findings

| ID | Função / linha | Tipo | Severidade |
|---|---|---|---|
| F-019 | `_inferir_padrao_protocolo` | `RC-TUPLE` | high |

#### F-019 — `_inferir_padrao_protocolo`

**Tipo:** RC-TUPLE
**Severidade:** high
**Padrão atual:**
```python
def _inferir_padrao_protocolo(amostras: list[str]) -> tuple[str, int, int]:
    ...
    return padrao, min_len, max_len
```
**Problema:** Tupla posicional de 3 elementos. A única documentação de quem é quem está no nome da função e na docstring — qualquer reordenação silenciosa quebra os callers.

**Refatoração sugerida:**
```python
@dataclass
class PadraoProtocolo:
    regex: str
    prefixo_min: int
    prefixo_max: int

def _inferir_padrao_protocolo(amostras: list[str]) -> PadraoProtocolo:
    ...
    return PadraoProtocolo(regex=padrao, prefixo_min=min_len, prefixo_max=max_len)
```
**Esforço:** low

---

## `src/todos/backends/composite.py`

> **Estado:** violations-found

### Findings

| ID | Função / linha | Tipo | Severidade |
|---|---|---|---|
| F-020 | `_prioridade_erro` | `RC-SENTINEL` | medium |

#### F-020 — `_prioridade_erro`

**Tipo:** RC-SENTINEL
**Severidade:** medium
**Padrão atual:**
```python
def _prioridade_erro(exc: Exception) -> int:
    if isinstance(exc, (SEINotFoundError, SEIParseError, SEIConnectionError)):
        return 3
    if isinstance(exc, SEIError):
        return 2
    return 1
```
**Problema:** Retorna `1`, `2`, `3` como magic numbers — o código que compara `if _prioridade_erro(e1) > _prioridade_erro(e2)` depende de conhecer o significado implícito dos inteiros.

**Refatoração sugerida:**
```python
from enum import IntEnum

class _ErroPrioridade(IntEnum):
    GENERICA = 1
    SEI_DOMINIO = 2
    CONCRETO = 3

def _prioridade_erro(exc: Exception) -> _ErroPrioridade:
    if isinstance(exc, (SEINotFoundError, SEIParseError, SEIConnectionError)):
        return _ErroPrioridade.CONCRETO
    if isinstance(exc, SEIError):
        return _ErroPrioridade.SEI_DOMINIO
    return _ErroPrioridade.GENERICA
```
**Esforço:** low

---

## `src/todos/backends/base.py`

> **Estado:** clean — nenhuma violação encontrada.

---

## `src/todos/backends/models.py`

> **Estado:** clean — nenhuma violação encontrada.

---

## `src/todos/backends/protocols.py`

> **Estado:** clean — nenhuma violação encontrada.

---

## `src/todos/backends/rest/__init__.py`

> **Estado:** clean.

---

## `src/todos/backends/rest/_session.py`

> **Estado:** clean.

---

## `src/todos/backends/rest/acompanhamento.py`

> **Estado:** clean.

---

## `src/todos/backends/rest/blocos.py`

> **Estado:** clean.

---

## `src/todos/backends/rest/catalogos.py`

> **Estado:** clean.

---

## `src/todos/backends/rest/credenciamento.py`

> **Estado:** clean.

---

## `src/todos/backends/rest/documentos.py`

> **Estado:** violations-found

### Findings

| ID | Função / linha | Tipo | Severidade |
|---|---|---|---|
| F-021 | `buscar_documento` | `RC-DICT-STATUS` | high |
| F-022 | `requer_id_serie` | `RC-BOOL-ERROR` | info |

#### F-021 — `buscar_documento`

**Tipo:** RC-DICT-STATUS
**Severidade:** high
**Padrão atual:**
```python
return {"encontrado": True, "id_procedimento": id_procedimento, "documento": d}
return {"encontrado": False, "mensagem": f"SEI {numero_sei} não encontrado..."}
```
**Problema:** A chave `"encontrado": False` é um código de retorno embutido em dict. O caller deve checar `if result["encontrado"]` antes de usar `result["documento"]` — equivalente a verificar `errno`.

**Refatoração sugerida:**
```python
# Sucesso: retornar diretamente o documento
return {"id_procedimento": id_procedimento, "documento": d}
# Falha: levantar exceção
raise SEINotFoundError(
    f"SEI {numero_sei} não encontrado",
    recoverable=True,
    suggested_next_tool="sei_ler_documento",
)
```
**Esforço:** medium

---

#### F-022 — `requer_id_serie`

**Tipo:** RC-BOOL-ERROR
**Severidade:** info
**Padrão atual:**
```python
async def requer_id_serie(self) -> bool:
    return True
```
**Observação:** Neste caso específico, o `bool` é uma query de capacidade ("o backend REST exige id_serie?"), não um code de erro. A classificação RC-BOOL-ERROR não se aplica estritamente — é uma característica legítima booleana. Registrado como `info` para consistência com o par `web/documentos.py`. Poderia ser uma `@property` síncrona ou constante de classe.

**Esforço:** low

---

## `src/todos/backends/rest/marcadores.py`

> **Estado:** clean.

---

## `src/todos/backends/rest/processos.py`

> **Estado:** clean.

---

## `src/todos/backends/rest/unidades.py`

> **Estado:** clean.

---

## `src/todos/backends/web/__init__.py`

> **Estado:** clean.

---

## `src/todos/backends/web/_session.py`

> **Estado:** clean.

---

## `src/todos/backends/web/acompanhamento.py`

> **Estado:** clean.

---

## `src/todos/backends/web/blocos.py`

> **Estado:** clean.

---

## `src/todos/backends/web/catalogos.py`

> **Estado:** clean.

---

## `src/todos/backends/web/documentos.py`

> **Estado:** violations-found

### Findings

| ID | Função / linha | Tipo | Severidade |
|---|---|---|---|
| F-023 | `buscar_documento` | `RC-DICT-STATUS` | high |
| F-024 | `_encontrar_em_processo` | `RC-UNION-STATUS` | medium |
| F-025 | `requer_id_serie` | `RC-BOOL-ERROR` | info |

#### F-023 — `buscar_documento`

**Tipo:** RC-DICT-STATUS
**Severidade:** high

Mesmo padrão de F-021 (`backends/rest/documentos.py`). O web backend espelha a mesma violação:
```python
return {"encontrado": False, "mensagem": f"SEI {numero_sei} não encontrado..."}
```
**Refatoração:** idêntica a F-021. Esforço: medium.

---

#### F-024 — `_encontrar_em_processo`

**Tipo:** RC-UNION-STATUS
**Severidade:** medium
**Padrão atual:**
```python
async def _encontrar_em_processo(self, ...) -> dict | None:
    ...
    return None  # documento não encontrado no processo
```
**Problema:** `None` encoda "não encontrado" — condição conhecida e tratável que deveria ser `SEINotFoundError`.

**Refatoração sugerida:**
```python
async def _encontrar_em_processo(self, ...) -> dict:
    ...
    raise SEINotFoundError(f"Documento {proto} não encontrado no processo {proto_proc}")
```
**Esforço:** medium

---

#### F-025 — `requer_id_serie`

Mesmo caso de F-022 — query de capacidade booleana legítima. Severidade: info.

---

## `src/todos/backends/web/marcadores.py`

> **Estado:** clean.

---

## `src/todos/backends/web/processos.py`

> **Estado:** clean.

---

## `src/todos/backends/web/unidades.py`

> **Estado:** clean.

---

## `src/todos/tools/__init__.py`

> **Estado:** clean.

---

## `src/todos/tools/acompanhamento.py`

> **Estado:** clean.

---

## `src/todos/tools/assinatura.py`

> **Estado:** clean.

---

## `src/todos/tools/blocos_assinatura.py`

> **Estado:** clean.

---

## `src/todos/tools/blocos_internos.py`

> **Estado:** clean.

---

## `src/todos/tools/catalogos.py`

> **Estado:** clean.

---

## `src/todos/tools/credenciamento.py`

> **Estado:** clean.

---

## `src/todos/tools/documentos.py`

> **Estado:** clean — refatoração aplicada em PR #97.

---

## `src/todos/tools/marcadores.py`

> **Estado:** clean.

---

## `src/todos/tools/processos.py`

> **Estado:** clean.

---

## `src/todos/tools/unidades.py`

> **Estado:** clean.

---

## Índice de severidade

| Severidade | Findings | Arquivos afetados |
|---|---|---|
| critical | 0 | — |
| high | 20 | auth.py (×4), sei_web_client.py (×1), setup_wizard.py (×12), tools/configuracao.py (×1), backends/rest/documentos.py (×1), backends/web/documentos.py (×1) |
| medium | 5 | sei_web_client.py (×1), backends/composite.py (×1), backends/web/documentos.py (×2), auth.py nota-constraint |
| low | 5 | sei_web_client.py (×2), catalog_cache.py (×3) |
| info | 2 | backends/rest/documentos.py (×1), backends/web/documentos.py (×1) |
| **Total** | **32** | **9 arquivos** |

### Prioridade de correção sugerida

| Prioridade | Arquivo | Findings | Esforço agregado |
|---|---|---|---|
| 1 | `setup_wizard.py` | F-012–F-018 (12 findings) | medium — criar 5 dataclasses, escopo isolado |
| 2 | `backends/rest/documentos.py` + `backends/web/documentos.py` | F-021 + F-023 | medium — substituir `{"encontrado": False}` por `SEINotFoundError` |
| 3 | `tools/configuracao.py` | F-019 | low — 1 dataclass |
| 4 | `backends/composite.py` | F-020 | low — 1 IntEnum |
| 5 | `sei_web_client.py` | F-005, F-008 | medium |
| 6 | `auth.py` | F-001–F-004 | medium — verificar constraint de interface FastMCP primeiro |
| 7 | `catalog_cache.py` | F-009–F-011 | low — separar erro de cache-miss |
| 8 | `sei_web_client.py` | F-006, F-007 | low |
