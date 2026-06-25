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
  scope: src/todos/ — todos os 52 arquivos Python
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

---

## Regra auditada

**return-contract** (ver Contexto). Tipos de violação e critérios de classificação:

| Código | Nome | Exemplo |
|---|---|---|
| `RC-TUPLE` | Tuple-as-discriminated-union | `return ("bloquear", payload)` |
| `RC-SENTINEL` | Sentinel return | `return "nao_suportado"` / magic int |
| `RC-NONE-AS-ERROR` | None-as-error | `return None` quando significa "falhou" |
| `RC-UNION-STATUS` | Union-status | `-> X \| None` onde `None` codifica falha |
| `RC-BOOL-ERROR` | Bool-as-error | `return bool` para indicar sucesso/falha |
| `RC-DICT-STATUS` | Dict-with-status-key | `return {"encontrado": False, ...}` |

**Critério de `RC-TUPLE` e severidade** (fixado para convergência — todo retorno de tupla posicional desempacotado por ordem é finding):
- **high** — algum elemento é sentinel de status/formato (ex.: `parse_inbox` → `"detalhada"`/`"desconhecido"`), **ou** a tupla está em path crítico de segurança/credencial (ex.: `setup_wizard`).
- **low** — tupla de dados puros sem sentinel (ex.: `(html, url)`, `(nivel, hipotese)`), mesmo com `≥3` elementos.

**Critério de `None`/`RC-UNION-STATUS`** (RFC 0015 D3): `-> X | None` **só** é finding quando o `None` colapsa erro com ausência. Quando o `None` é ausência legítima que o caller trata sem `try/except` (cache-miss, lookup opcional, "não encontrado no parse"), **não** é finding.

> **Tipo de retorno canônico da refatoração:** os sketches abaixo mostram `@dataclass` por brevidade, mas o tipo de destino segue a **RFC 0015 D2** — **Pydantic `BaseModel` frozen por default**, dataclass só para objetos que carregam tipos não-Pydantic (`bs4.Tag`).

---

## Resumo executivo

| Arquivos auditados | Com violations | Clean |
|---|---|---|
| 52 | 11 | 41 |

| Severidade | Findings |
|---|---|
| critical | 0 |
| high | 14 |
| medium | 6 |
| low | 8 |
| info | 2 |
| **Total** | **30** |

> **Convenção de contagem:** 1 finding = 1 linha de tabela. Findings que agrupam múltiplas instâncias do mesmo padrão na mesma função/arquivo trazem a multiplicidade no rótulo (`×N`) mas contam como **1**. Instâncias agrupadas: F-009 (×21, 20 métodos), F-015 (×4), F-016 (×4), F-026 (×3). Total de instâncias individuais ≈ 60.

O foco de maior impacto está em `setup_wizard.py` (7 findings RC-TUPLE, 13 instâncias) e `auth.py` (4 findings com nota de constraint de interface externa). Corrigir todos os `high` elimina ~85% do risco prático. `mcp_app.py` tem 2 findings medium (RC-NONE-AS-ERROR em helpers de busca de documento) corrigíveis com esforço baixo.

---

## Capítulos (um por arquivo)

Um capítulo `##` por arquivo auditado, identificado pelo caminho relativo. Arquivos sem violação trazem `> **Estado:** clean`.

## `src/todos/__init__.py`

> **Estado:** clean — nenhuma violação encontrada.

---

## `src/todos/__main__.py`

> **Estado:** clean — nenhuma violação encontrada.

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

> **Estado:** violations-found

### Findings

| ID | Função / linha | Tipo | Severidade |
|---|---|---|---|
| F-027 | `extrair_nivel:306` | `RC-TUPLE` | low |

#### F-027 — `extrair_nivel` (linha 306)

**Tipo:** RC-TUPLE
**Severidade:** low
**Padrão atual:**
```python
def extrair_nivel(metadata: dict) -> tuple[str | None, str | None]:
    ...
    return None, None
    return normalizar_nivel(nivel), hipotese
```
**Problema:** Retorna `(nivel_acesso, hipotese_legal)` — dois valores posicionais que o caller desempacota por ordem (`nivel, hl = extrair_nivel(...)`). Mesmo acoplamento posicional dos tuples do `setup_wizard`.
**Nuance:** **nenhum** dos dois elementos é sentinel de status/erro — são dois campos de dado legitimamente opcionais, e `(None, None)` significa "ambos ausentes", não falha. Por isso é **low** (ergonomia/acoplamento posicional, não error-hiding): a refatoração é cosmética, não corrige bug.
**Refatoração sugerida:**
```python
@dataclass(frozen=True, slots=True)
class NivelExtraido:
    nivel: str | None
    hipotese: str | None
```
**Esforço:** low
**Impacto se não corrigido:** Baixo — caller troca a ordem dos dois campos sem o type checker avisar (ambos `str | None`).

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

> **Estado:** violations-found

### Findings

| ID | Função / linha | Tipo | Severidade | Descrição |
|---|---|---|---|---|
| F-001 | `_buscar_documento_em_processo:636` | `RC-NONE-AS-ERROR` | medium | Exceção capturada → `None`, colapsando erro com "não encontrado" |
| F-002 | `_buscar_documento_via_solr:659` | `RC-NONE-AS-ERROR` | medium | Mesma confusão: erro de rede retorna `None` igual a "não encontrado" |

#### F-001 — `_buscar_documento_em_processo` (linha 636)

**Tipo:** RC-NONE-AS-ERROR
**Severidade:** medium
**Padrão atual:**
```python
async def _buscar_documento_em_processo(
    client: SEIClient, id_proc: str, referencia: str
) -> tuple[str, str] | None:
    try:
        docs = await client.listar_documentos(id_proc, limit=200)
        ...
    except (SEIError, httpx.RequestError) as exc:
        logger.warning(...)
    return None  # ← colapsa "erro" com "não encontrado"
```
**Problema:** `None` aqui significa duas coisas distintas: (a) documento não existe no processo; (b) houve falha de rede ou SEI. O chamador (`_resolver_documento`) trata ambos como "não encontrado" e tenta a estratégia 2 (id direto), o que pode mascarar erros de conectividade como ausências legítimas.
**Refatoração sugerida:**
```python
async def _buscar_documento_em_processo(
    client: SEIClient, id_proc: str, referencia: str
) -> tuple[str, str] | None:
    """Retorna (id, tipo) ou None se não encontrado. Propaga erros."""
    docs = await client.listar_documentos(id_proc, limit=200)
    ref_norm = referencia.lstrip("0")
    for d in docs:
        proto = d.get("atributos", {}).get("protocoloFormatado", "")
        if proto == referencia or proto.lstrip("0") == ref_norm:
            doc_id = str(d.get("id", ""))
            if doc_id:
                tipo = d.get("atributos", {}).get("tipoDocumento", "I")
                return doc_id, tipo
    return None
# Caller captura SEIError / httpx.RequestError se necessário
```
**Esforço:** low
**Impacto se não corrigido:** Erros de rede no `listar_documentos` são silenciados → `_resolver_documento` tenta o ID diretamente e pode retornar resultado errado ou levantar `SEINotFoundError` em vez do erro original.

#### F-002 — `_buscar_documento_via_solr` (linha 659)

**Tipo:** RC-NONE-AS-ERROR
**Severidade:** medium
**Padrão atual:**
```python
async def _buscar_documento_via_solr(client: SEIClient, referencia: str) -> tuple[str, str] | None:
    try:
        result = await client.pesquisar_processos(...)
        ...
    except (SEIError, httpx.RequestError) as exc:
        logger.warning(...)
    return None  # ← colapsa "não encontrado no Solr" com "Solr falhou"
```
**Problema:** Idêntico ao F-001. Falha do Solr produz o mesmo `None` que "nenhum resultado", então `_resolver_documento` silenciosamente degrada para busca por id direto sem que o chamador saiba que o Solr está fora.
**Refatoração sugerida:** Propagar a exceção; deixar `_resolver_documento` decidir se o fallback é seguro na presença de erros.
```python
async def _buscar_documento_via_solr(client: SEIClient, referencia: str) -> tuple[str, str] | None:
    """Retorna (id, tipo) ou None se não encontrado. Propaga erros de rede/SEI."""
    result = await client.pesquisar_processos(
        FiltrosPesquisaProcessos(palavras_chave=referencia, limit=20)
    )
    for p in result.get("processos", []):
        id_proc = str(p.get("idProcedimento", ""))
        if not id_proc:
            continue
        found = await _buscar_documento_em_processo(client, id_proc, referencia)
        if found is not None:
            return found
    return None
```
**Esforço:** low
**Impacto se não corrigido:** Indisponibilidade temporária do Solr degrada silenciosamente para busca por id direto, podendo retornar documento errado em vez de propagar o erro de serviço.

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
| F-005 | `parse_inbox:4875` | `RC-TUPLE` | high |
| F-007 | `_ler_senha_keyring` | `RC-UNION-STATUS` | low |
| F-008 | `_link_acao_visualizacao` | `RC-UNION-STATUS` | medium |
| F-009 | write methods (×21, 20 métodos) | `RC-DICT-STATUS` | medium |
| F-028 | tuplas posicionais data-only (×11) | `RC-TUPLE` | low |

#### F-005 — `parse_inbox` (linha 4875)

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

#### F-009 — write methods (×21, 20 métodos)

**Tipo:** RC-DICT-STATUS
**Severidade:** medium
**Funções afetadas** (busca por `"ok": True` / `"status": "ok"`, incluindo `return {` multi-linha):
```
executar_acao_processo:1460 (×2)          remover_sobrestamento_web:1616
reabrir_processo_web:1700                 desmarcar_processo_web:1763
remover_anotacao_web:1846                 alterar_secoes_web:2018
alterar_documento_interno_web:2075        enviar_processo_web:2587
criar_bloco_assinatura_web:2855           disponibilizar_bloco_assinatura_web:2910
cancelar_disponibilizacao_bloco_assinatura_web:2923   _executar_acao_bloco:2945
alterar_bloco_assinatura_web:3026         criar_processo_web:3537
alterar_processo_web:3629                 criar_documento_interno_web:3715
alterar_acompanhamento_web:4402           remover_acompanhamento_web:4418
retirar_documento_bloco_assinatura_web:4579   anotar_documento_bloco_assinatura_web:4613
```
**Padrão atual:**
```python
return {"ok": True, "mensagem": "Sobrestamento removido.", "protocolo": protocolo}
return {"status": "ok", "id_documento": id_documento}
```
**Problema:** O backend web retorna diretamente o dict de resposta MCP (`"ok"`, `"status"`) em vez de um domínio tipado. Isso acopla a camada de backend ao formato de resposta MCP — o backend não deveria saber que existe um `"ok"`. Bugs de validação de campos do dict são silenciosos (nenhum type error no retorno `-> dict`).
**Refatoração sugerida:** Conforme RFC 0015 D1/D4 — backend retorna modelo de domínio Pydantic (`ProcessoAlterado`, `DocumentoCriado`, etc.) e levanta `SEIError`; a tool retorna o modelo direto (FastMCP serializa via RFC 0008). NÃO construir envelope manual.
```python
class ProcessoAlterado(BaseModel):
    protocolo: str
    mensagem: str | None = None
```
**Esforço:** medium — requer modelos por domínio + atualizar callers nas tools
**Impacto se não corrigido:** Mudança no formato de resposta exige varrer todos os 20 métodos manualmente; nenhum type error avisa de campos faltantes.

---

#### F-028 — tuplas posicionais data-only (×11)

**Tipo:** RC-TUPLE
**Severidade:** low
**Funções afetadas** (retornam tupla posicional sem nomes, desempacotada por ordem no call site):
```
_extrair_submit_btn:195          tuple[str, str] | None   (name, value)
_fetch_unit_switch_form:761      tuple[str, Tag]
_arvore_do_processo:1319         tuple[str, str]          (html, url)
_pagina_visualizacao_processo:1666  tuple[str, str]       (html, url)
_pagina_marcador:1724            tuple[str, BeautifulSoup, str]
_split_marcador_desc:1746        tuple[str, str]
_navegar_historico:2441          tuple[str, str, str]     (hist_url, id_proc, referer)
_abrir_form_cadastro_processo:3438  tuple[Tag, str]
_renumerar_nos_chunk:4682        tuple[str, int]
fetch_inbox:879                  tuple[int, str]
_get_doc_signed_url:1858         tuple[str, str]
```
**Problema:** Acoplamento posicional — o caller precisa conhecer a ordem dos elementos. Mesmo padrão de F-027 (`extrair_nivel`).
**Distinção de severidade vs F-005:** **nenhuma** destas codifica sentinel de status no 1º elemento (são pares/triplas de dados puros); por isso **low**, não high. `parse_inbox` (F-005) é a única tupla deste arquivo com sentinel de formato (`"detalhada"`/`"desconhecido"`) → high. Onde há `| None`, o `None` é ausência legítima e permanece.
**Refatoração sugerida:** frozen dataclass nomeada por função (≥3 elementos primeiro; 2-tuplas são opcionais — Pythonic mas menos explícitas).
**Esforço:** low (mecânico, escopo por função)

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
| F-011 | `ttl` | `RC-UNION-STATUS` | low |

> Os helpers **síncronos** `_get_sync` / `_ttl_sync` **não** são findings: não capturam exceção alguma (sqlite/JSON propagam ao wrapper async), e o `None` deles é exclusivamente cache-miss/entrada expirada — ausência legítima, não erro.

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

#### F-011 — `ttl`

Mesmo padrão de F-009: o wrapper async `ttl` captura `sqlite3.Error`, loga warning e retorna `None`, colapsando erro com "entrada inexistente". Esforço: low.

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
| F-016 | `_detect_organs_with_ssl_fallback` (×4) | `RC-TUPLE` | high |
| F-017 | `_setup_credentials` | `RC-TUPLE` | high |
| F-018 | `_build_mcp_env` | `RC-TUPLE` | high |
| F-026 | `_mcp_add_via_cli`, `_mcp_add_via_json`, `_update_codex_via_cli` (×3) | `RC-BOOL-ERROR` | low |
| F-029 | `_read_existing_todos_env` | `RC-UNION-STATUS` | low |

Este arquivo concentra **13 instâncias** de funções que retornam tuplas nuas com 2–4 elementos posicionais sem nome, mais **3** helpers que retornam `bool` de sucesso/falha. A raiz é que `setup_wizard.py` foi desenvolvido como script imperativo e nunca migrado para types estruturados.

#### F-012 — `_detect_organs`

**Tipo:** RC-TUPLE
**Severidade:** high
**Padrão atual:**
```python
def _detect_organs(...) -> tuple[list[tuple[str, str]], str, str]:
    ...
    return organs, sigla_orgao_sistema, sigla_sistema
```
**Problema:** Retorna 3-tupla onde os dois últimos strings são parâmetros de URL extraídos da resposta — callers fazem `organs, sigla, sistema = _detect_organs(...)` sem nomes. Adicionalmente, `organs` é `list[tuple[str, str]]` — nested RC-TUPLE.
**Refatoração sugerida:**
```python
@dataclass
class OrgaoSEI:
    id: str
    nome: str

@dataclass
class DetectOrgansResult:
    organs: list[OrgaoSEI]
    sigla_orgao_sistema: str
    sigla_sistema: str
```
**Esforço:** medium

---

#### F-013 — `_resolve_organ_from_list`

**Tipo:** RC-TUPLE
**Severidade:** high
**Padrão atual:**
```python
def _resolve_organ_from_list(organs: list[tuple[str, str]]) -> tuple[str, str]:
    ...
    return orgao_id, sigla_orgao
```
**Problema:** `(orgao_id, sigla_orgao)` — dois strings posicionais sem nome. Callers fazem `orgao_id, sigla = _resolve_organ_from_list(...)`.
**Refatoração sugerida:**
```python
@dataclass
class OrganSelection:
    orgao_id: str
    sigla_orgao: str
```
**Esforço:** low

---

#### F-014 — `_resolve_organ_manual`

**Tipo:** RC-TUPLE
**Severidade:** high
**Padrão atual:**
```python
def _resolve_organ_manual(sigla_orgao_sistema: str) -> tuple[str, str, str, str]:
    ...
    return sigla_orgao, sigla_orgao_sistema, orgao_id, default_sigla_sistema
```
**Problema:** 4-tupla de strings totalmente posicionais — callers precisam conhecer a ordem exata de 4 campos. `default_sigla_sistema` retornado junto com config mutável torna o contrato opaco.
**Refatoração sugerida:**
```python
@dataclass
class ManualOrganConfig:
    sigla_orgao: str
    sigla_orgao_sistema: str
    orgao_id: str
    default_sigla_sistema: str
```
**Esforço:** low

---

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

---

#### F-016 — `_detect_organs_with_ssl_fallback` (×4)

**Tipo:** RC-TUPLE
**Severidade:** high
**Padrão atual:**
```python
def _detect_organs_with_ssl_fallback(...) -> tuple[list[tuple[str, str]], str, str, bool]:
    ...
    return [], sigla_orgao_sistema, sigla_sistema, False   # user recusou SSL
    return [], sigla_orgao_sistema, sigla_sistema, False   # HTTP error
    return organs, sigla_orgao_sistema, sigla_sistema, False  # sucesso
    return organs, sigla_orgao_sistema, sigla_sistema, True   # sucesso sem SSL
```
**Problema:** `bool` na posição 4 é sentinel de estado ("SSL desabilitado?"). Há 4 `return` distintos, dois deles indistinguíveis pelo caller (`([], ..., False)` cobre tanto recusa do usuário quanto erro HTTP).
**Refatoração sugerida:**
```python
@dataclass
class OrgansDetectionResult:
    organs: list[OrgaoSEI]
    sigla_orgao_sistema: str
    sigla_sistema: str
    ssl_disabled: bool

# recusa e HTTP error → raise OrgansDetectionError("mensagem") com campo reason
```
**Esforço:** medium

---

#### F-017 — `_setup_credentials`

**Tipo:** RC-TUPLE
**Severidade:** high
**Padrão atual:**
```python
def _setup_credentials(sei_root: str) -> tuple[str, str, str]:
    ...
    return usuario, senha_config, senha_validacao
```
**Problema:** 3-tuple de strings; callers fazem `usuario, senha_config, senha_val = _setup_credentials(...)`. A distinção entre `senha_config` (pode ser vazia para keyring) e `senha_validacao` é posicional.
**Refatoração sugerida:**
```python
@dataclass
class CredentialsResult:
    usuario: str
    senha_config: str   # vazia = usar keyring
    senha_validacao: str
```
**Esforço:** low

---

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

#### F-026 — `_mcp_add_via_cli` / `_mcp_add_via_json` / `_update_codex_via_cli` (×3)

**Tipo:** RC-BOOL-ERROR
**Severidade:** low
**Padrão atual:**
```python
def _mcp_add_via_cli(...) -> bool:
    try:
        _sp.run(cmd, check=True, ...)
    except _sp.CalledProcessError:
        return False        # engole o erro, vira bool
    except OSError:
        return False
    else:
        return True
```
**Problema:** `bool` codifica sucesso/falha e o erro real (`CalledProcessError`, `OSError`) é engolido — o caller não distingue "claude CLI ausente" de "mcp add falhou por outro motivo". `_update_codex_via_cli` ainda loga a re-tentativa com `logger.debug` (invisível em produção, conforme CLAUDE.md).
**Nuance (RFC 0015 D3):** o `bool` aqui é consumido como sinal de *fallback* (`if not _mcp_add_via_cli(...): _mcp_add_via_json(...)`) — control-flow legítimo de cadeia CLI→JSON, não erro propagável. Por isso **low**, não medium. A correção mínima é não engolir o motivo: logar com `warning` e/ou retornar um resultado tipado que carregue a razão da falha, mantendo o contrato de fallback.
**Refatoração sugerida:**
```python
@dataclass(frozen=True, slots=True)
class MCPRegisterResult:
    sucesso: bool
    motivo: str | None = None   # preenchido em falha, para o caller logar/decidir
```
**Esforço:** low
**Impacto se não corrigido:** Falha de registro do servidor MCP no setup fica sem diagnóstico — o usuário vê só "não funcionou" sem a causa.

---

#### F-029 — `_read_existing_todos_env`

**Tipo:** RC-UNION-STATUS / None-as-error
**Severidade:** low
**Padrão atual:**
```python
def _read_existing_todos_env() -> dict[str, str] | None:
    if not config_path.exists():
        return None                       # ausência legítima: nunca configurado
    try:
        data = json.loads(config_path.read_text(...))
    except (OSError, json.JSONDecodeError) as exc:
        _logger_setup.warning("Não foi possível ler ~/.claude.json: %s", exc)
        return None                       # ← falha de leitura/parse, MESMO None
    ...
```
**Problema:** `None` cobre dois casos: "config não existe" (ausência legítima) e "config existe mas corrompido/ilegível" (erro). `run_set_password` trata ambos como "nunca configurado" — um `~/.claude.json` corrompido vira "setup fresco" silenciosamente (mesmo logando warning).
**Refatoração sugerida:** manter `None` só para "não configurado"; `raise` (ou retornar resultado tipado) quando o arquivo existe mas falha o parse, para o caller decidir conscientemente.
**Esforço:** low

---

## `src/todos/tools/configuracao.py`

> **Estado:** violations-found

### Findings

| ID | Função / linha | Tipo | Severidade |
|---|---|---|---|
| F-019 | `_inferir_padrao_protocolo` | `RC-TUPLE` | low |

#### F-019 — `_inferir_padrao_protocolo`

**Tipo:** RC-TUPLE
**Severidade:** low
**Padrão atual:**
```python
def _inferir_padrao_protocolo(amostras: list[str]) -> tuple[str, int, int]:
    ...
    return padrao, min_len, max_len
```
**Problema:** Tupla posicional de 3 elementos `(padrao, min_len, max_len)`. A única documentação de quem é quem está no nome da função e na docstring — qualquer reordenação silenciosa quebra os callers.
**Severidade (critério §Regra auditada):** **low** — dados puros, **nenhum** elemento é sentinel de status (amostras inválidas levantam `SEIValidationError`), e não é path de credencial/segurança.

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

## `src/todos/backends/__init__.py`

> **Estado:** clean — nenhuma violação encontrada.

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

Contagem por finding (linha de tabela), não por instância — ver convenção no Resumo executivo.

| Severidade | Findings | Arquivos afetados |
|---|---|---|
| critical | 0 | — |
| high | 14 | auth.py (4), sei_web_client.py (1), setup_wizard.py (7), backends/rest/documentos.py (1), backends/web/documentos.py (1) |
| medium | 6 | mcp_app.py (2), sei_web_client.py (2), backends/composite.py (1), backends/web/documentos.py (1) |
| low | 8 | sei_web_client.py (2), catalog_cache.py (2), setup_wizard.py (2), access_control.py (1), tools/configuracao.py (1) |
| info | 2 | backends/rest/documentos.py (1), backends/web/documentos.py (1) |
| **Total** | **30** | **11 arquivos** |

### Prioridade de correção sugerida

| Prioridade | Arquivo | Findings | Esforço agregado |
|---|---|---|---|
| 1 | `setup_wizard.py` | F-012–F-018 (13 instâncias) + F-026 (×3 bool) | medium — criar 6 dataclasses, escopo isolado |
| 2 | `backends/rest/documentos.py` + `backends/web/documentos.py` | F-021 + F-023 | medium — substituir `{"encontrado": False}` por `SEINotFoundError` |
| 3 | `tools/configuracao.py` | F-019 | low — 1 dataclass |
| 4 | `backends/composite.py` | F-020 | low — 1 IntEnum |
| 5 | `sei_web_client.py` | F-005, F-008, F-009 (×21, 20 métodos) | medium — F-009 requer modelos de domínio |
| 6 | `auth.py` | F-001–F-004 | medium — verificar constraint de interface FastMCP primeiro |
| 7 | `mcp_app.py` | F-001–F-002 | low — propagar exceção em vez de swallow→None |
| 8 | `catalog_cache.py` | F-009, F-011 | low — separar erro de cache-miss |
| 9 | `sei_web_client.py` | F-007, F-028 | low |
