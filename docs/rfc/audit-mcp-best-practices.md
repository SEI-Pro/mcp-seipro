# MCP Best Practices Audit — `todos`

Codebase audit of every Python source file in `src/todos/` against MCP best practices, project
coding conventions (CLAUDE.md), and Python 3.11+ style rules.

Ruff static analysis passes clean across the entire codebase; all issues below are
**semantic/architectural** violations not caught by the linter.

---

## Table of Contents

- [Executive Summary](#executive-summary)
- [Critical Issues (cross-cutting)](#critical-issues-cross-cutting)
- [Core Server](#core-server)
  - [server.py](#srctodosserverpy)
  - [mcp_app.py](#srctodosmcp_apppy)
- [Backend Architecture](#backend-architecture)
  - [backends/composite.py](#srctodosbackendscompositepy)
  - [backends/base.py](#srctodosbackendsbasepy)
  - [backends/models.py](#srctodosbackendsmodelspy)
  - [backends/protocols.py](#srctodosbackendsprotocolspy)
- [SEI Clients](#sei-clients)
  - [sei_client.py](#srctodossei_clientpy)
  - [sei_web_client.py](#srctodossei_web_clientpy)
- [Tools Layer](#tools-layer)
  - [tools/\_\_init\_\_.py](#srctodostoolsinitpy)
  - [tools/documentos.py](#srctodostoolsdocumentospy)
  - [tools/credenciamento.py](#srctodostoolscredenciamentopy)
  - [tools/assinatura.py](#srctodostoolsassinaturapy)
  - [tools/configuracao.py](#srctodostoolsconfiguracaopy)
  - [tools/blocos_assinatura.py](#srctodostoolsblocos_assinaturapy)
  - [tools/catalogos.py](#srctodostoolscatalogospy)
  - [tools/acompanhamento.py](#srctodostoolsacompanhamentopy)
  - [tools/blocos_internos.py](#srctodostoolsblocos_internospy)
  - [tools/marcadores.py](#srctodostoolsmarcadorespy)
  - [tools/processos.py](#srctodostoolsprocessospy)
  - [tools/unidades.py](#srctodostoolsunidadespy)
- [REST Backends](#rest-backends)
  - [backends/rest/\_\_init\_\_.py](#srctodosbackendsrestinitpy)
  - [backends/rest/\_session.py](#srctodosbackendsrest_sessionpy)
  - [backends/rest/documentos.py](#srctodosbackendsrestdocumentospy)
  - [backends/rest/credenciamento.py](#srctodosbackendsrestcredenciamentopy)
  - [backends/rest/marcadores.py](#srctodosbackendsrestmarcadorespy)
  - [backends/rest/processos.py](#srctodosbackendsrestprocessospy)
  - [backends/rest/blocos.py](#srctodosbackendsrestblocospy)
  - [backends/rest/catalogos.py](#srctodosbackendsrestcatalogospy)
  - [backends/rest/unidades.py](#srctodosbackendsrestunidadespy)
  - [backends/rest/acompanhamento.py](#srctodosbackendsrestacompanhamentopy)
- [Web Backends](#web-backends)
  - [backends/web/\_\_init\_\_.py](#srctodosbackendswebinitpy)
  - [backends/web/\_session.py](#srctodosbackendsweb_sessionpy)
  - [backends/web/documentos.py](#srctodosbackendswebdocumentospy)
  - [backends/web/marcadores.py](#srctodosbackendswebmarcadorespy)
  - [backends/web/processos.py](#srctodosbackendswebprocessospy)
  - [backends/web/blocos.py](#srctodosbackendswebblocospy)
  - [backends/web/catalogos.py](#srctodosbackendswebcatalogospy)
  - [backends/web/acompanhamento.py](#srctodosbackendswemacompanhamentopy)
  - [backends/web/unidades.py](#srctodosbackendswebunidadespy)
- [Utility & Support Files](#utility--support-files)
  - [html_utils.py](#srctodoshtml_utilspy)
  - [sei_styles.py](#srctodossei_stylespy)
  - [responses.py](#srctodosresponsespy)
  - [hints.py](#srctodoshintspy)
  - [exceptions.py](#srctodosexceptionspy)
  - [access_control.py](#srctodosaccess_controlpy)
  - [auth.py](#srctodosauthpy)
  - [catalog_cache.py](#srctodoscatalog_cachepy)
  - [remote.py](#srctodosremotepy)
  - [setup_wizard.py](#srctodossetup_wizardpy)

---

## Executive Summary

| Severity | Count | Top areas |
|---|---|---|
| **Critical** | 4 | `_WRITE`/`_IDEM` annotation profiles missing `destructiveHint: False`; `blocos.py` REST signing silently passes empty user ID; `auth.py` XSS in success page; `composite.py` `_FALLBACK_EXCS` is a `frozenset` (runtime `TypeError`) |
| **High** | 18 | ~25 read tools missing `_next` hints; `ValueError` raised instead of `ToolError`; write tools returning raw JSON instead of `RespostaEscrita`; wrong `_IDEM` annotation on ~15 non-idempotent tools |
| **Medium** | 31 | Silent `except` blocks without logging; missing loggers in web backends; `total_itens` reports post-truncation count; `models.py` missing `from __future__ import annotations`; `protocols.py` missing `resolver_documento` |
| **Low** | 26 | Bare `dict` annotations; magic literals; missing docstrings on private helpers; `sys.stderr.write` instead of logger; `__all__` placement |

---

## Critical Issues (cross-cutting)

### C1 — `_WRITE` and `_IDEM` profiles missing `destructiveHint: False`

**File:** `src/todos/mcp_app.py` (lines 825–826)

The MCP spec defaults `destructiveHint` to `True` when absent. Both `_WRITE` and `_IDEM` profiles
omit `"destructiveHint": False`, so every write and idempotent tool appears destructive to MCP
clients. Affected tools include `sei_criar_processo`, `sei_registrar_andamento`,
`sei_criar_documento`, `sei_atribuir_processo`, `sei_trocar_unidade`, and ~30 more.

```python
# Current (dangerous):
_WRITE = {"readOnlyHint": False, "idempotentHint": False}
_IDEM  = {"readOnlyHint": False, "idempotentHint": True}

# Fix:
_WRITE = {"readOnlyHint": False, "idempotentHint": False, "destructiveHint": False}
_IDEM  = {"readOnlyHint": False, "idempotentHint": True,  "destructiveHint": False}
```

### C2 — `_FALLBACK_EXCS` is a `frozenset`, not a `tuple`

**File:** `src/todos/backends/composite.py` (line ~248)

Python's `except` clause requires a type or a `tuple` of types. Using a `frozenset` raises
`TypeError: catching classes that do not inherit from BaseException is not allowed` at runtime.

```python
# Fix: change frozenset to tuple
_FALLBACK_EXCS = (NotImplementedError, SEINotImplementedError, ...)
```

### C3 — XSS in `auth.py` success page

**File:** `src/todos/auth.py` (line 489)

`{usuario}` is inserted directly from user-controlled form input into the HTML success page without
`html.escape()`. This is a stored XSS vulnerability.

```python
# Fix:
import html
# ...
_SUCCESS_HTML.format(usuario=html.escape(usuario))
```

### C4 — `blocos.py` REST: silent empty `id_usuario` passed to signing

**File:** `src/todos/backends/rest/blocos.py` (lines 153, 163)

`assinar_bloco` and `assinar_documentos_bloco` use `id_usuario or ""` when
`garantir_autenticacao()` returns `None`. The empty string is then passed as the signer ID,
producing a cryptic REST 400/500 instead of a clear `SEIValidationError`. The fix used in
`documentos.py:assinar_documento` (lines 183–200) — resolve via `listar_usuarios` with a
fallback `SEIValidationError` — should be applied here too.

---

## Core Server

### `src/todos/server.py`

- **No logger defined.** The module has no `import logging` and no `logger = logging.getLogger(__name__)`. Helper functions (`_agrupar_processos`, `_ordenar_resumo`, etc.) cannot emit warnings. **Fix:** add both at the top of the file.

- **`sei_buscar_documento` missing `_next` hint.** Returns `_json(result)` with no `_next`. After resolving a document the natural next step is `sei_ler_documento`. **Fix:** append `"_next": [{"tool": "sei_ler_documento", "args": {"documento": result.get("id", "")}}]` to the result.

- **`sei_enviar_processo`, `sei_atribuir_processo`, `sei_sobrestar_processo` return raw JSON, no `_next`.** Per CLAUDE.md write tools must return `{"ok": True, "_next": [...]}`. None of these three use `_shape_resposta_escrita` or include a verification step. **Fix:** use `_shape_resposta_escrita` and add `_next` pointing to `sei_consultar_processo`.

- **`sei_resumo_processos` uses magic literal `200` for page size.** `limit=200` is an inline literal. A named constant `_RESUMO_PAGE_SIZE = 200` should be defined and used. Similarly, `_DEFAULT_PESQUISA_LIMIT = 50` is defined but the `sei_pesquisar_processos` signature still defaults to the literal `50` instead of the constant.

- **`sei_sobrestar_processo` annotated `_IDEM` (idempotent) but it is not idempotent.** Repeated calls with the same motivo may fail or duplicate entries. **Fix:** change to `_WRITE`.

- **`sei_marcar_nao_lido` annotated `_IDEM` but internally calls `sei_enviar_processo` (`_DEST`).** This is incorrect; it generates a history entry visible to other users. **Fix:** change to `_WRITE` or `_DEST` and document the side effect.

- **`sei_pesquisar_processos` REST fallback swallows original error without logging.** When REST is unavailable the code sets `_rest_unavailable = True` with no `logger.debug`. If the SEI server itself is down, the web fallback will also fail but the surfaced error won't mention the original network failure. **Fix:** add `logger.debug("REST inacessível (%s: %s) — tentando fallback web", type(e).__name__, e)`.

- **`_agrupar_processos`, `_ordenar_resumo`, `_wrap_pesquisa`, `_pesquisa_cursor_args` missing docstrings.** CLAUDE.md requires docstrings on all exported helpers. **Fix:** add one-liners.

- **Bare `dict` type parameters.** `grupos: dict[str, dict]`, `item: dict`, `cursor_extra: dict` should be `dict[str, dict[str, object]]` etc. (UP006).

- **Magic literal `"X"` for external document type** in `mcp_app.py:_consultar_meta_documento`. Should be `_TIPO_DOCUMENTO_EXTERNO = "X"`.

---

### `src/todos/mcp_app.py`

- **`ValueError` raised instead of `ToolError`/`SEIError` in `_get_client` and `_get_web_client`.** Lines 126, 132, 136, 160, 171, 177, 181, 205: authentication and configuration guard branches raise plain `ValueError`. FastMCP surfaces these as internal server errors rather than clean user-facing tool errors. **Fix:** replace with `raise SEIAuthError(msg)` or `raise SEIError(msg)`.

- **Silent `except ... pass` without logging in `_resolver_documento` (line 687–691).** The fallback attempt to resolve a `referencia` as a direct document ID swallows `(SEIError, httpx.HTTPError)` with a bare `pass`. **Fix:** add `logger.debug("_resolver_documento: tentativa de id direto falhou (%s: %s)", type(exc).__name__, exc)`.

- **Silent `except ValueError` in `_backend` without logging (line 224).** The fallback `SEIWebClient()` is created silently when `_get_web_client` raises `ValueError` in stdio mode. **Fix:** add `logger.debug("_backend: usando fallback SEIWebClient (stdio) — %s", exc)`.

- **`sei_status_resource` swallows `AttributeError` broadly (line 385–386).** `except (SEIError, httpx.HTTPError, AttributeError)` logs nothing and returns an error string. `AttributeError` is too broad — it likely masks programming bugs. **Fix:** add `logger.warning(...)` before return; remove `AttributeError` or handle it separately.

- **`logger.debug` for eviction close failures (lines 153, 198).** These are real cleanup failures (network/OS errors) and should be `logger.warning`, not `logger.debug`.

- **`_WRITE` and `_IDEM` profiles missing `"destructiveHint": False`.** See Critical Issue C1 above.

- **Bare `dict` type parameters** in `_evict_oldest` (line 112), `_solicitar_consentimento_via_elicit` (line 455), `_shape_resposta_escrita` (line 713), `_add_cursor` (lines 767, 772). All should be `dict[str, object]`.

- **`_json` missing docstring (line 709).** Add `"""Serializa para JSON compacto sem escape de caracteres Unicode."""`.

---

## Backend Architecture

### `src/todos/backends/composite.py`

- **`_FALLBACK_EXCS` is a `frozenset`, not a `tuple` (line ~248).** See Critical Issue C2. This is a runtime `TypeError`.

- **Partial backend failures in `consultar_processo` are not logged (lines 151–162).** When one of REST/web fails but the other succeeds, the exception is stored in `rest_result`/`web_result` but never emitted to the server log. Only both-fail triggers a raised exception. **Fix:** add `logger.warning("REST falhou para %s: %s", processo, rest_result)` when either source fails.

- **`dados: object` type in `criar_documento_externo` (line 172).** Should be `dados: NovoDocumentoExterno`.

---

### `src/todos/backends/base.py`

- **`listar_documentos`, `listar_relacionamentos`, `consultar_marcador_processo` return `dict | list[dict]`.** The union return type burdens all callers with type narrowing and prevents the composite layer from merging results cleanly. Consider normalising to `dict` with a `"documentos"` key in each backend.

- **`requer_id_serie` concrete default undocumented.** Unlike every other method, this one has a concrete default `return False`. The docstring should note: "Default returns `False`; REST-backed subclasses override to return `True`."

---

### `src/todos/backends/models.py`

- **Missing `from __future__ import annotations` (line 9).** The file uses `list[str] | None` and `str | bool | None` — these require Python 3.10+ without the future import. All other files in the package have it.

- **Magic string `"0"` for `nivel_acesso` repeated six times** (lines 41, 73, 88, 98, 183, and others). Define `NIVEL_ACESSO_PUBLICO = "0"`, `NIVEL_ACESSO_RESTRITO = "1"`, `NIVEL_ACESSO_SIGILOSO = "2"` at module level.

- **`CredenciaisAssinatura.senha` exposed in `__repr__`** (line 117). `@dataclass` generates `__repr__` by default, which logs the password. **Fix:** `senha: str = field(repr=False)`.

- **`NovoProcessoWeb` and `DocumentoExternoInclusaoWeb` are mutable** (lines 153, 176). All other dataclasses use `frozen=True`; these two don't. **Fix:** add `frozen=True` — `None` defaults are safe to freeze.

- **`"S"`/`"N"` string booleans undocumented in `EnvioProcesso`** (lines 54–64). At minimum, a class docstring should note that `"S"` = sim (yes) and `"N"` = não (no).

- **`FiltrosPesquisaProcessos.__post_init__` validation duplicated in `FiltroListagemProcessos`.** Extract `_validate_paginacao(limit, pagina)` and call it from both.

---

### `src/todos/backends/protocols.py`

- **`resolver_documento` absent from all Protocols.** `SEIBackend.resolver_documento` (base.py) is not present in any domain protocol; type checkers cannot verify backend completeness.

- **`requer_id_serie` absent from all Protocols.** Same issue.

- **No `@runtime_checkable` on any Protocol.** `isinstance(backend, DocumentosProtocol)` will raise `TypeError`. Add `@runtime_checkable` if runtime checks are needed, or document that Protocols are static-only.

- **`__all__` placed at bottom of file (line 675).** By convention (and ruff's `RUF022`) it belongs after imports. **Fix:** move to the top.

---

## SEI Clients

### `src/todos/sei_client.py`

*The following issues were identified; many were already fixed in commit `f434548`.*

- **`autenticar()` network errors were not mapped to typed exceptions.** Now fixed: `httpx.TimeoutException`/`httpx.ConnectError` → `SEIConnectionError`; 401/403 → `SEIAuthError`.

- **`_post_with_file_reopen` used manual `{"token": self._token or ""}` after re-auth.** Now fixed: uses `await self._get_headers()`.

- **`alterar_documento_externo` bypassed re-auth logic for file uploads.** Now fixed: routes through `_post_with_file_reopen`.

- **`consultar_processo_completo` second call failure was not logged.** Now fixed: `logger.warning(...)` emitted.

- **Duplicate `_CAMPOS_PESQUISA_PROCESSO` constant.** Now fixed: second definition removed.

---

### `src/todos/sei_web_client.py`

*The following issues were identified; many were already fixed in commit `f434548`.*

- **`_ler_senha_keyring` swallowed all errors silently.** Now fixed: `TimeoutError` → `logger.warning`; others → `logger.debug`.

- **`pesquisar_processos_web` used `raise_for_status()` instead of `_check()`.** Now fixed.

- **`criar_bloco_assinatura_web` and `cancelar_disponibilizacao_bloco_assinatura_web` caught `RuntimeError` instead of `SEINotFoundError` for toolbar lookups.** Now fixed.

- **`listar_documentos_bloco_assinatura_web` returned `[]` silently when block edit link not found.** Now fixed: logs `logger.warning`.

- **`_autocomplete_ajax` returned `[]` silently on HTTP failure and JSON decode error.** Now fixed: both paths emit `logger.warning`.

- **`alterar_secoes_web`, `alterar_documento_interno_web`, `enviar_processo_web` used UTF-8 encoding in POST bodies.** Now fixed: changed to `urlencode(..., encoding="iso-8859-1", errors="replace").encode("ascii")`.

- **Dead `if "pdf" not in ...: pass` block in `gerar_pdf_processo`.** Now fixed: removed.

---

## Tools Layer

### `src/todos/tools/__init__.py`

No violations. Module docstring is accurate and correct.

---

### `src/todos/tools/documentos.py`

- **`sei_baixar_anexo`, `sei_listar_secoes`, `sei_gerar_referencia`, `sei_listar_blocos_documento`, `sei_sugestao_assuntos_documento` missing `_next` hints.** All return raw JSON. Natural next steps: `sei_ler_documento`, `sei_editar_secao`, `sei_assinar_documento`, etc.

- **`sei_editar_secao`, `sei_alterar_documento_interno` return `str` instead of `RespostaEscrita`.** Both are write/idempotent tools and should return `_shape_resposta_escrita(result, ...)`.

- **`_ler_documento_via_backend` catches `SEIConnectionError` in the interno → externo fallback (lines 138–143).** If the network is down, the interno attempt fails with `SEIConnectionError` and the code falls back to externo, which will also fail. `SEIConnectionError` should not trigger the fallback. **Fix:** remove it from the caught exception set.

- **`sei_gerar_referencia` docstring missing usage guidance.** Does not explain when to use this vs. citing a URL directly. **Fix:** add "Use ao citar documentos SEI dentro do corpo de outros documentos no editor interno."

- **`sei_consultar_documento_externo` docstring missing field list.** Forces the LLM to guess the output schema. **Fix:** add a "Campos retornados" section.

---

### `src/todos/tools/credenciamento.py`

- **All 4 tools missing `_next` hints.** Credential operations have natural chains (list → grant/revoke → verify). None include `_next`.

- **`sei_conceder_credenciamento`, `sei_cassar_credenciamento`, `sei_renunciar_credenciamento` return raw JSON instead of `RespostaEscrita`.**

- **`sei_listar_credenciamentos`, `sei_conceder_credenciamento`, `sei_cassar_credenciamento` docstrings too sparse.** No domain explanation (what is credenciamento? when is it needed?), no field list, no parameter format guidance (what is `id_usuario`?).

---

### `src/todos/tools/assinatura.py`

- **`sei_cancelar_assinatura` annotated `_IDEM` but is not idempotent.** A second call on an already-cancelled document will fail. **Fix:** change to `_WRITE`.

- **`sei_dar_ciencia` returns raw JSON instead of `RespostaEscrita`.** Should add `_next` suggesting `sei_listar_ciencias` for verification.

- **`sei_listar_assinaturas`, `sei_listar_ciencias` missing `_next` hints.**

- **`_validar_cargo` (line ~53) swallows `SEIConnectionError` in the `(SEIError, httpx.HTTPError)` catch.** If the network is down during `listar_assinantes()`, the LLM gets "Cargo/Função não informado — opções: (nenhum retornado)" instead of "SEI inacessível". **Fix:** re-raise `SEIConnectionError` separately before swallowing `SEIError`.

- **`sei_assinar_documento`: `orgao` parameter undescribed.** An LLM doesn't know when to supply it. **Fix:** add "orgao: código do órgão assinante (use `sei_listar_orgaos_assinante` para ver opções; omita para usar o padrão da sessão)."

---

### `src/todos/tools/configuracao.py`

- **`_read_keyring_pattern_sync` swallows `(TimeoutError, OSError, RuntimeError, AttributeError, ValueError, _KeyringError)` with no logging (lines 83–91).** A corrupted or locked keyring is silently ignored. **Fix:** add `logger.debug("keyring read failed: %s", exc)`.

- **`sei_detectar_formato_protocolo` and `sei_redefinir_formato_protocolo` missing `_next` hints.**

- **`sys.stderr.write` used for diagnostics** (lines 38, 107). CLAUDE.md mandates `logging.getLogger(__name__)`. **Fix:** replace with `logger.info`/`logger.warning`.

- **Keyring write failure (`persistido=False`) not logged in `sei_detectar_formato_protocolo` (line 193).** The pattern is detected but not persisted; the tool should log `logger.warning(...)` before returning.

---

### `src/todos/tools/blocos_assinatura.py`

- **Multiple write tools return `str` instead of `RespostaEscrita`:** `sei_criar_bloco_assinatura`, `sei_incluir_documento_bloco_assinatura`, `sei_disponibilizar_bloco_assinatura`, `sei_cancelar_disponibilizacao_bloco`, `sei_anotar_documento_bloco_assinatura`, `sei_alterar_anotacao_bloco_assinatura`, `sei_alterar_bloco_assinatura`, `sei_excluir_bloco_assinatura`, `sei_concluir_bloco_assinatura`, `sei_reabrir_bloco_assinatura`, `sei_retornar_bloco_assinatura`.

- **`sei_retirar_documentos_bloco_assinatura` annotated `_DEST` (destructive) but operation is reversible** via `sei_incluir_documento_bloco_assinatura`. **Fix:** change to `_WRITE`.

- **`sei_disponibilizar_bloco_assinatura`, `sei_cancelar_disponibilizacao_bloco`, `sei_concluir_bloco_assinatura`, `sei_reabrir_bloco_assinatura`, `sei_retornar_bloco_assinatura` annotated `_IDEM` but are not idempotent.** Repeated calls will error if block is already in the target state. **Fix:** change to `_WRITE`.

- **`sei_pesquisar_blocos_assinatura`, `sei_listar_documentos_bloco_assinatura` missing domain-level `_next`** (beyond pagination cursor).

- **`sei_disponibilizar_bloco_assinatura` docstring missing pre-conditions.** Does not mention the block must have at least one document, or what "disponibilizar" means for assinantes.

- **`sei_cancelar_disponibilizacao_bloco` docstring missing state-transition guard.** Should note: "Só é possível cancelar quando o bloco está no estado 'Disponibilizado'."

---

### `src/todos/tools/catalogos.py`

- **`sei_criar_contato` returns `str` instead of `RespostaEscrita`.**

- **All paginated read tools missing domain-level `_next`** pointing to how to use discovered IDs (e.g., `sei_pesquisar_hipoteses_legais` → use in `sei_criar_processo(hipotese_legal=...)`).

- **`sei_pesquisar_contatos` `filtro` parameter undescribed.** Does not mention it is required in web-only mode, or what fields it searches.

- **`sei_sugestao_assuntos_processo` docstring too thin.** Does not explain how to use returned IDs in `sei_criar_processo(assuntos=...)`.

---

### `src/todos/tools/acompanhamento.py`

- **`sei_acompanhar_processo`, `sei_remover_acompanhamento`, `sei_criar_grupo_acompanhamento`, `sei_excluir_grupo_acompanhamento`, `sei_alterar_acompanhamento` return raw `str` instead of `RespostaEscrita`.**

- **`sei_listar_grupos_acompanhamento`, `sei_listar_meus_acompanhamentos`, `sei_listar_acompanhamentos_unidade` missing `_next` hints.**

- **`sei_excluir_grupo_acompanhamento` docstring missing impact warning.** Does not warn that processes in the group lose their grouping. **Fix:** add "ATENÇÃO: exclui o grupo permanentemente. Processos acompanhados perdem o agrupamento (os acompanhamentos individuais permanecem)."

- **`sei_acompanhar_processo` annotated `_IDEM` but repeated calls may create duplicates.** **Fix:** change to `_WRITE`.

- **`sei_remover_acompanhamento` parameter `processo` undescribed.**

---

### `src/todos/tools/blocos_internos.py`

- **`sei_criar_bloco_interno`, `sei_incluir_processo_bloco_interno`, `sei_alterar_bloco_interno`, `sei_anotar_processo_bloco_interno`, `sei_alterar_anotacao_bloco_interno` return raw `str` instead of `RespostaEscrita`.**

- **`sei_retirar_processo_bloco_interno` annotated `_DEST` but reversible** via `sei_incluir_processo_bloco_interno`. **Fix:** change to `_WRITE`.

- **`sei_concluir_bloco_interno` annotated `_IDEM` but is not idempotent.** Concluding an already-concluded block will error. **Fix:** change to `_WRITE`.

- **`sei_listar_processos_bloco_interno` missing `_next` hint.**

- **`sei_incluir_processo_bloco_interno`, `sei_retirar_processo_bloco_interno` accept only `IdProcedimento`.** Every other tool accepts "protocolo formatado ou IdProcedimento." Inconsistent; update docstrings and accept both formats.

- **`sei_alterar_bloco_interno` docstring has no parameter table.** `descricao` is required but callers get a generic validation error with no guidance.

---

### `src/todos/tools/marcadores.py`

- **All write/idempotent tools return raw `str` instead of `RespostaEscrita`:** `sei_criar_marcador`, `sei_excluir_marcador`, `sei_marcar_processo`, `sei_desmarcar_processo`, `sei_desativar_marcador`, `sei_reativar_marcador`.

- **`sei_consultar_marcador_processo`, `sei_historico_marcador_processo` missing `_next` hints.**

- **`sei_pesquisar_marcadores` returns `str` while other catalogue tools return `PaginadoGenerico`.** Inconsistent return shape.

- **`sei_criar_marcador` references `sei_listar_cores_marcador` which does not exist as an MCP tool.** Either expose `sei_listar_cores_marcador` or update the docstring reference.

- **`sei_consultar_marcador_processo` docstring is a one-liner.** Missing parameter description for `processo` and field list for the return value.

---

### `src/todos/tools/processos.py`

- **`sei_concluir_processo`, `sei_reabrir_processo`, `sei_receber_processo`, `sei_remover_atribuicao`, `sei_remover_sobrestamento`, `sei_registrar_andamento`, `sei_criar_anotacao`, `sei_remover_anotacao`, `sei_criar_observacao`, `sei_marcar_nao_lido` all return raw `str`** instead of `RespostaEscrita`.

- **`sei_marcar_nao_lido` annotated `_IDEM` but internally calls the destructive `enviar_processo`.** Repeated calls create duplicate tramitação history entries. **Fix:** change to `_WRITE` or `_DEST`; document side effect.

- **`sei_cancelar_assinatura` annotated `_IDEM` but is not idempotent.** **Fix:** change to `_WRITE`.

- **`sei_executar_acao` annotated `_WRITE` but can execute destructive actions when `confirmar=True`.** Should be `_DEST` since the tool has destructive potential.

- **`sei_receber_processo`, `sei_remover_atribuicao`, `sei_remover_sobrestamento`, `sei_reabrir_processo`, `sei_disponibilizar_bloco_assinatura`, `sei_cancelar_disponibilizacao_bloco` annotated `_IDEM` but not idempotent.** All will error on repeated calls. **Fix:** change to `_WRITE`.

- **`sei_arvore_processo` and `sei_listar_documentos` overlapping purpose without disambiguation.** Docstrings don't clearly state when to prefer one over the other. **Fix:** add "Prefira `sei_arvore_processo` — é mais rápido. Use `sei_listar_documentos` apenas quando compatibilidade REST explícita for necessária."

- **`sei_gerar_pdf_processo` and `sei_gerar_zip_processo` return both base64 AND file path.** For large PDFs this bloats the MCP response potentially beyond context limits. Consider returning only the path, or making base64 opt-in.

- **`sei_executar_acao` `acao` parameter undescribed.** An LLM has no way to discover valid action strings. **Fix:** add a list of common valid values (e.g., `procedimento_concluir`, `procedimento_visualizar`) with a note that they are PHP controller action names.

- **`ctx: Context | None = None` vs `ctx: Context` inconsistency.** `sei_consultar_processo` uses non-optional `ctx: Context` while all other tools use `ctx: Context | None = None`. Standardise on one form.

---

### `src/todos/tools/unidades.py`

- **`sei_trocar_unidade` returns raw `str` instead of `RespostaEscrita`.** Should include `_next: [{"tool": "sei_unidade_atual"}]` to confirm the switch.

- **`sei_unidade_atual`, `sei_listar_unidades`, `sei_listar_usuarios`, `sei_versao`, `sei_listar_orgaos`, `sei_listar_contextos`, `sei_listar_assinantes`, `sei_listar_orgaos_assinante`, `sei_parametros_upload` missing `_next` hints.**

- **`sei_versao` docstring circular.** "Se falhar com erro inesperado, use `sei_versao` para verificar" instructs the LLM to call itself on failure. **Fix:** "Disponível apenas com mod-wssei instalado. Se falhar, a instância não tem o módulo REST."

- **`sei_unidade_atual` docstring has typos.** "sessao" and "operacoes" missing diacritics (should be "sessão" and "operações").

- **`sei_listar_contextos` `id_orgao` parameter undescribed.** No hint on how to obtain `id_orgao`. **Fix:** add "id_orgao: ID do órgão (use `sei_listar_orgaos` para descobrir os IDs)."

- **`sei_pesquisar_usuarios` vs `sei_listar_usuarios` ambiguity undocumented.** Docstring should state when to prefer each.

---

## REST Backends

### `src/todos/backends/rest/__init__.py`

No violations.

---

### `src/todos/backends/rest/_session.py`

- **`_resolver_processo` returns `""` silently when `IdProcedimento` absent (line 72).** Downstream REST calls then use `""` as a path segment, producing a cryptic 404. **Fix:**
  ```python
  id_proc = str(proc.get("IdProcedimento", ""))
  if not id_proc:
      msg = f"Processo '{referencia}' não retornou IdProcedimento."
      raise SEINotFoundError(msg)
  return id_proc
  ```

- **Inconsistent exception type in `_resolver_documento` (line 108 vs 119).** Outer `except` uses `httpx.RequestError`; inner uses `httpx.HTTPError`. **Fix:** change line 108 to `except (SEIError, httpx.HTTPError)` for symmetry.

---

### `src/todos/backends/rest/documentos.py`

- **`buscar_documento` no debug log when document not found (lines 40–43).** Returns `{"encontrado": False}` silently with no `logger.debug`. Makes diagnosing Solr indexing gaps harder. **Fix:** add `logger.debug("sei %s not found via pesquisa (%d candidatos)", numero_sei, len(candidatos))`.

---

### `src/todos/backends/rest/credenciamento.py`

No violations.

---

### `src/todos/backends/rest/marcadores.py`

No violations.

---

### `src/todos/backends/rest/processos.py`

- **No `import logging` / no logger.** Per-candidate errors inside `atribuir_processo` retry loop are never logged to the server-side logger. **Fix:** add logger and `logger.warning("Falha ao atribuir processo a %s: %s", id_usuario, e)` inside the `except` block before `erros.append(...)`.

- **`concluir_processo` passes unresolved protocol directly (line 150–152).** This is correct (endpoint accepts `protocoloFormatado`) but differs from `reabrir_processo` which resolves to internal ID first. **Fix:** add a comment explaining the intentional inconsistency.

---

### `src/todos/backends/rest/blocos.py`

- **`assinar_bloco` and `assinar_documentos_bloco` silently pass `id_usuario=""` (lines 153, 163).** See Critical Issue C4. Apply the resolver pattern from `documentos.py:assinar_documento`.

---

### `src/todos/backends/rest/catalogos.py`

- **`_validar_pagina` raises `ValueError` instead of `SEIValidationError` (line 17).** FastMCP surfaces `ToolError` (base of `SEIError`) cleanly; plain `ValueError` becomes an opaque internal error. **Fix:** import and raise `SEIValidationError`.

---

### `src/todos/backends/rest/unidades.py`

No violations.

---

### `src/todos/backends/rest/acompanhamento.py`

No violations.

---

## Web Backends

### `src/todos/backends/web/__init__.py`

No violations.

---

### `src/todos/backends/web/_session.py`

No violations.

---

### `src/todos/backends/web/documentos.py`

- **`buscar_documento`: empty `numero_sei` not validated (line 44–50).** After `.strip()`, `numero_sei` can be `""`. The match closure then succeeds on every document with an empty number. **Fix:** add `if not numero_sei: raise SEIValidationError("numero_sei não pode ser vazio")`.

- **Unguarded `doc["id"]` access in `resolver_documento` (line 206–219).** If the scraper response ever lacks the `"id"` key, this raises an unhandled `KeyError`. **Fix:** use `doc.get("id")` with a `SEINotFoundError` guard.

- **Silent data loss in `alterar_secoes` when `"conteudo"` key missing (line 173).** `s.get("conteudo", "")` silently blanks a section if the caller omits the key. **Fix:** add `logger.warning(...)` when `"conteudo"` is `None` and expected.

---

### `src/todos/backends/web/marcadores.py`

- **No `logging` import and no logger.** The module has no way to emit warnings for unexpected scraper behavior.

- **`pesquisar_marcadores`: silent `[]` return when response structure changes (line 37–40).** `result.get("marcadores", [])` returns `[]` silently if the scraper key is renamed. No warning is emitted. **Fix:** check whether the key is present; log at `WARNING` if absent.

---

### `src/todos/backends/web/processos.py`

- **`listar_unidades_processo`, `listar_interessados`, `listar_sobrestamentos` return `[]` silently without logging (lines 81–92).** All use `.get(key, [])` without checking if the key should have been present. **Fix:** add `logger.warning` when the expected key is absent from the scraper result.

- **Magic literals `"2"` (priority) and `"S"` (boolean flag) without named constants** (lines 59, 292). Define `_SEI_SIM = "S"`, `_PRIORIDADE_ALTA = "2"` at module level.

- **`enviar_processo`: no local warning when `autocomplete_unidades` returns `[]` due to HTTP failure** (lines 158–170). The `SEIValidationError` message will misleadingly say "Candidatos: " as if the endpoint was reachable but found nothing.

- **`atribuir_processo` raises `SEIConnectionError` for a missing form field (line 200–201).** A missing `"selAtribuicao"` in the scraper response is a parsing failure, not a connectivity error. **Fix:** raise `SEIError` or a `SEIParseError` instead.

---

### `src/todos/backends/web/blocos.py`

- **Batch methods (`retirar_documentos_bloco_assinatura`, `excluir_blocos_assinatura`, `concluir_blocos_assinatura`) don't validate empty input** (lines 52–54, 83–84, 106–107). An empty `ids` list causes `asyncio.gather(*[])` to return `[]`, which is returned silently — indistinguishable from success. **Fix:** add `if not ids: raise SEIValidationError("Lista vazia")`.

- **`isinstance(outcome, BaseException)` in gather results is over-broad (lines 60, 89, 112).** Includes `KeyboardInterrupt` and `SystemExit`. **Fix:** use `isinstance(outcome, Exception)` and re-raise any non-`Exception` `BaseException`.

- **`alterar_anotacao_bloco_assinatura` is an undocumented alias for `anotar_documento_bloco_assinatura`.** Both call the same web method. **Fix:** add a comment documenting the alias relationship.

---

### `src/todos/backends/web/catalogos.py`

- **No `logging` import and no logger.** Multiple silent empty-list returns with no diagnostic capability.

- **`total_itens` reports post-truncation count, not total available** (lines 29–143). Every method sets `total_itens = len(items_after_truncation)` instead of `len(items_before_truncation)`. Callers cannot determine whether more items exist. **Fix:** capture `total_before = len(items)` before slicing with `limit`.

- **Silent `[]` returns when scraper response keys absent** throughout. No `logger.warning` is possible without a logger.

---

### `src/todos/backends/web/acompanhamento.py`

- **No `logging` import and no logger.**

- **`pagina` parameter silently ignored (lines 57, 62).** Callers passing `pagina > 0` receive page 0 with no warning. Docstrings should state `pagina` is not supported in web mode.

---

### `src/todos/backends/web/unidades.py`

- **No `logging` import and no logger.**

- **`pesquisar_unidades`: silent exclusion of current unit when `sigla` absent from scraper result (lines 33–41).** If `unidade_atual()` returns a dict without `"sigla"`, the current unit is silently excluded and no warning is logged.

- **`id_atual` can be `""` when `"id_unidade"` missing (line 37–40).** The duplicate-check logic becomes fragile when `id_atual` is empty.

---

## Utility & Support Files

### `src/todos/html_utils.py`

- **Silent `except ImportError: pass` for `pytesseract`/`pdf2image` imports (lines 28–29).** If OCR is unavailable, the failure is not logged. **Fix:** add `logger.debug("pytesseract/pdf2image not available — OCR disabled")`.

- **Silent `except ImportError: pass` for `pdfplumber` (lines 41–42).** Same issue. **Fix:** add `logger.debug("pdfplumber not available — PDF text extraction disabled")`.

---

### `src/todos/sei_styles.py`

No violations. Well-structured constants, named helpers, import-time consistency guard, full type annotations and docstrings.

---

### `src/todos/responses.py`

- **`RespostaEscrita` missing `next_actions` / `_next` field (lines 84–94).** Per CLAUDE.md: "Write tools retornam `{'ok': True, '_next': [...]}` — execute `_next[0]` para verificar." `RespostaEscrita` has no such field; agents cannot chain actions from write responses systematically.

- **`aviso_acesso: dict | None` uses bare `dict` (line 316).** Should be `dict[str, object] | None` (UP006).

- **Several `ProcessoDetalhe` fields lack `Field(description=...)` (lines 308–315).** Inconsistent with the rest of the file; weakens the MCP `outputSchema`.

---

### `src/todos/hints.py`

- **Silent `json.JSONDecodeError`/`ValueError` on bad `SEI_HINTS` env var (lines 27–29).** Falls back to defaults with no log. **Fix:** add `logger.warning("SEI_HINTS inválido — usando hints padrão: %s", exc)`.

- **No `logger = logging.getLogger(__name__)` defined.** The module has no logger, making the fix above impossible without adding the import.

---

### `src/todos/exceptions.py`

- **`SEINotFoundError`, `SEIParseError` docstrings too sparse.** Do not guide callers on what context to include (which field failed, which URL, etc.).

- **`SEIPermissionError` and `SEIParseError` have no corresponding exit codes** documented in the class hierarchy or module docstring. Minor gap in the exit-code table.

---

### `src/todos/access_control.py`

- **`_bloco_base`, `construir_aviso_bloqueio`, `construir_disclaimer_acompanhante`, `construir_aviso_recusado`, `avaliar_acesso`, `prefixar_markdown`, `prefixar_texto`, `envelopar_html`, `extrair_nivel`, `extrair_nivel_web` all use bare `dict` parameter types** (UP006). Should be `dict[str, object]`.

- **`_nfkd` missing docstring** (private helper without documentation).

---

### `src/todos/auth.py`

- **XSS: `{usuario}` inserted unescaped into HTML success page (line 489).** See Critical Issue C3.

- **Password stored in plaintext in SQLite for up to 5 minutes in `_auth_codes` (line 254).** If the SQLite file is compromised within the auth-code TTL window (300 seconds), the password is exposed. Documented limitation; should be noted as a known risk with mitigation guidance.

- **Token revocation is a no-op with 30-day TTL (line 366–367).** Stolen tokens remain valid for up to 30 days. The module docstring should document that `JWT_SECRET` rotation is the only revocation mechanism.

---

### `src/todos/catalog_cache.py`

- **`close` method is a no-op with no explanation (lines 170–171).** Maintainers may assume it's an unimplemented stub. **Fix:** add a comment: `# SQLite connections are opened/closed per-call — nothing persistent to close`.

- **No `logger.debug` when stale cache entry is deleted (line 99).** **Fix:** add `logger.debug("cache entry expired: %s/%s", namespace, key)`.

- **`_rng = secrets.SystemRandom()` used for a probabilistic sweep (line 39).** `random.Random()` would suffice and is faster for this non-security use case.

---

### `src/todos/remote.py`

- **`favicon` returns HTTP 200 with empty body when no icon found (lines 54–59).** An empty body with `image/png` content type is an invalid PNG. **Fix:** return `Response(b"", status_code=404)` when `icon` is empty.

- **`build_remote_app` mutates the passed `mcp` object as a side effect (line 48).** The name "build" implies construction without mutation. Minor design smell.

---

### `src/todos/setup_wizard.py`

- **Silent `except (OSError, json.JSONDecodeError): return None` in `_read_existing_todos_env` (line 265–266).** A malformed `~/.claude.json` silently causes the "already configured" check to be skipped, potentially overwriting an existing configuration. **Fix:** add `logger.warning("Não foi possível ler ~/.claude.json: %s", exc)`.

- **Silent `except (OSError, json.JSONDecodeError): return False` in `_mcp_add_via_json` (line 447–448).** Caller cannot determine why the add failed. **Fix:** add `logger.warning(...)` before returning.

- **`_logger_setup = logging.getLogger(__name__)` defined at line 630 but used from line 356.** Works at runtime (name resolved at call time) but is non-idiomatic. **Fix:** move to the top of the module after imports.

- **`logging.getLogger("httpx").setLevel(logging.WARNING)` mutates the global `httpx` logger permanently (line 327–328).** May suppress useful debug logs elsewhere in the process. **Fix:** save and restore the original level, or use a `contextmanager`.

- **`_do_login` return type uses bare `dict` (line 343).** Should be `dict[str, object]` (UP006).
