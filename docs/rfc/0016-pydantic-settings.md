# RFC 0016 — Configuração centralizada com pydantic-settings

**Status:** ✅ Implementado (Fases 1, 2 e 3 concluídas); ⚠️ ponto revertido — ver nota
**Data:** 2026-06-25
**Autor:** avaliação + revisão manual

> [!IMPORTANT]
> **Nota de atualização (2026-06-30):** o item "`.env` nativo" do §4 (Prós) e
> a afirmação de §6 ("`.env.example` permanece válido") **não valem mais**.
> `TodosSettings.model_config` tinha `env_file=".env"`; isso foi removido para
> que o `todos` nunca leia um arquivo `.env` automaticamente, alinhando com o
> princípio de que credenciais devem vir preferencialmente do **keyring**
> (RFC 0002) e, como segunda opção, de uma env var já presente no processo —
> nunca de um arquivo silenciosamente lido do diretório de trabalho. O
> `.env.example` continua existindo só como lista de referência dos nomes de
> variável, não como algo que o `todos` carrega sozinho. O restante desta RFC
> (Fases 1–3, motivação, tipagem) continua valendo.

---

## 1. Contexto

A configuração do `todos` é lida via `os.environ.get(...)` espalhado por ~15
módulos (~40 call-sites). O padrão dominante é:

```python
self.base_url = (cfg.sei_url or os.environ.get("SEI_URL", "")).rstrip("/")
self._usuario = cfg.sei_usuario or os.environ.get("SEI_USUARIO", "")
self._senha = cfg.sei_senha or os.environ.get("SEI_SENHA", "")
```

Esse padrão é repetido campo a campo **e** duplicado entre `SEIClient`
(`sei_client.py`) e `SEIWebClient` (`sei_web_client.py`). Os dois dataclasses
de configuração (`SEIClientConfig`, `SEIWebClientConfig` em
`backends/models.py`) declaram os campos mas não sabem ler o ambiente — a ponte
env→config vive no construtor de cada cliente.

Além da duplicação, há **parsing manual e repetido** de tipos não-string:

| Tipo | Onde | Como hoje |
|------|------|-----------|
| bool | `SEI_VERIFY_SSL` | `.strip().lower() in ("false","0","no")` repetido em 4 lugares |
| int  | `SEI_MAX_SESSIONS`, `SEI_MAX_OCR_PAGES` | `int(...)`, `_safe_int`, `_parse_max_ocr_pages` |
| float| `SEI_ELICIT_TIMEOUT_S` | `float(os.environ.get(...))` |
| list | `SEI_RISCOS_EXTRA`, `SEI_HINTS` | `split` manual |

Vários desses parsers caem em `except ValueError: return default` — que viola a
regra do `CLAUDE.md` ("`return` default silencioso é erro"), porque o chamador
não distingue "não configurado" de "valor inválido".

Por fim, alguns módulos leem o ambiente em **import-time**
(`auth._JWT_SECRET`, `html_utils.MAX_OCR_PAGES`, `access_control`), o que
dificulta o teste: exige `monkeypatch.setenv` + reimport do módulo.

---

## 2. Decisão

Adotar [`pydantic-settings`](https://github.com/pydantic/pydantic-settings) como
fonte única de configuração **de processo**, com um objeto `TodosSettings`
tipado em `src/todos/settings.py`.

`pydantic` v2 e `pydantic-settings` **já estão na árvore de dependências**
(puxados por `mcp[cli]`/`fastmcp`); o custo de adoção é código, não peso novo.
A dependência passa a ser declarada explicitamente em `pyproject.toml`.

### 2.1 Escopo — o que entra

Configuração **de processo**, lida do ambiente uma vez por processo:

- Conexão SEI: `SEI_URL`, `SEI_WEB_URL`, `SEI_USUARIO`, `SEI_SENHA`,
  `SEI_ORGAO`, `SEI_CONTEXTO`, `SEI_CA_BUNDLE`, `SEI_VERIFY_SSL`.
- Identificadores web: `SEI_SIGLA_ORGAO`, `SEI_SIGLA_SISTEMA`,
  `SEI_SIGLA_ORGAO_SISTEMA`.
- Limites/operação (fases seguintes): `SEI_MAX_SESSIONS`, `SEI_MAX_OCR_PAGES`,
  `SEI_OCR_LANG`, `SEI_ELICIT_TIMEOUT_S`, `SEI_CACHE_TTL_SECONDS`,
  `TODOS_CACHE_DIR`, `SEI_PERMITIR_RESTRITOS`, `SEI_RISCOS_EXTRA`.

### 2.2 Escopo — o que **não** entra

- **Credenciais OAuth por-request.** No modo HTTP, as credenciais vêm do token
  por requisição (`get_sei_credentials_from_token`), não de variáveis globais.
  `pydantic-settings` cobre só a config de processo (ex.: `JWT_SECRET`,
  `SEI_SENHA`, limites) — **não substitui** o fluxo OAuth de `auth.py`.
- **Dataclasses de parâmetros de operação** (`NovoProcesso`, `EnvioProcesso`,
  `FiltrosPesquisaProcessos`...). Continuam dataclasses `frozen`; descrevem
  argumentos de chamada, não configuração de ambiente.

### 2.3 Princípio de merge preservado

O merge **field-level** (config explícita tem precedência sobre o ambiente) é
mantido. O padrão

```python
cfg.sei_url or os.environ.get("SEI_URL", "")
```

vira

```python
cfg.sei_url or settings.sei_url
```

onde `settings` é o `TodosSettings` tipado. Em modo stdio (`config=None`) tudo
vem de `settings`; em modo HTTP, o token preenche os campos e `settings` só
cobre o que faltar. A semântica observável **não muda**.

---

## 3. Implementação por fases

### Fase 1 — núcleo + clientes (esta RFC)

1. Novo módulo `src/todos/settings.py` com `TodosSettings(BaseSettings)`
   (`env_prefix="SEI_"`, lê `.env`) e acessor `get_settings()` cacheado.
2. `SEIClient` e `SEIWebClient` passam a resolver os campos via
   `cfg.X or settings.X`, removendo todas as chamadas `os.environ.get` dos dois
   construtores e o parsing manual de `SEI_VERIFY_SSL`.
3. `pydantic-settings` declarado em `pyproject.toml`.

### Fase 2 — parsers tipados (próxima)

Mover `SEI_MAX_SESSIONS`, `SEI_MAX_OCR_PAGES`, `SEI_ELICIT_TIMEOUT_S`,
`SEI_CACHE_TTL_SECONDS`, `SEI_RISCOS_EXTRA`, `SEI_HINTS` para campos tipados do
`TodosSettings`, eliminando `_safe_int`/`_parse_max_ocr_pages` e os
`except ValueError: return default`.

### Fase 3 — import-time → lazy

Converter as leituras em import-time (`auth`, `html_utils`, `access_control`)
para `get_settings()`, recuperando testabilidade sem reimport de módulo.

---

## 4. Prós

- **Elimina** o `cfg.X or os.environ.get("X", default)` campo a campo e a
  duplicação entre os dois clientes.
- **Tipagem real e validada** uma vez, no lugar de ~6 parsers ad-hoc.
- **Fail-fast** com mensagem clara via validators (substitui
  `validate_jwt_secret()` manual na Fase 3).
- **Testabilidade**: `TodosSettings(sei_url=...)` num teste, sem
  `monkeypatch.setenv` + reimport.
- **`.env` nativo** — já existe `.env.example`; sem precisar de `python-dotenv`.
- **Documentação num só lugar** — os campos viram a fonte de verdade das env vars.

## 5. Contras / riscos

- Dependência passa a ser **direta** (peso real ~0, já era transitiva).
- Leituras import-time precisam virar lazy (Fase 3) para o ganho de
  testabilidade valer.
- O modelo multi-credencial do OAuth **não** encaixa — manter a fronteira do
  §2.2 explícita para não tentar mover credenciais por-usuário para cá.
- Refator amplo se feito de uma vez — daí o faseamento. A Fase 1 é contida aos
  dois construtores de cliente, onde a lógica sutil de `SEI_VERIFY_SSL`
  (`bool | str` = caminho do CA bundle) é centralizada com cuidado.

---

## 6. Compatibilidade

- Sem mudança de comportamento observável: as mesmas variáveis de ambiente, os
  mesmos defaults, a mesma precedência config→env.
- `.env.example` permanece válido; os nomes das variáveis não mudam.
</content>
</invoke>
