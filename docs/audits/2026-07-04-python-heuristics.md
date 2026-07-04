# Auditoria de Heurísticas Python — 2026-07-04

**Commit auditado:** `5495383` (branch `claude/python-heuristics-franklin-hbixtx`,
`main` em `3547e2c8`). Achados citam arquivo + linha desse commit específico —
o código já pode ter mudado desde então; confira a linha antes de agir.

Relatório de achados da codebase `todos` contra as heurísticas de design/estilo
de Franklin (skill `python-heuristics`): tipos e estrutura, onde a lógica deve
morar, exceção vs. retorno tipado, funções e fluxo, TDD vs. BDD, e ferramental
(`ruff`, logging).

> **Como usar este documento.** Isto é um backlog de hipóteses priorizadas
> para investigação, não uma lista de violações que devem virar PR
> automaticamente. Cada achado foi verificado por leitura direta do código no
> commit acima, mas a heurística que o motivou pode não se aplicar
> integralmente ao caso concreto — use julgamento de engenharia antes de
> corrigir, principalmente nos "Padrões transversais" abaixo: alguns descrevem
> um princípio geral (ex. "prefira retorno tipado a dict solto") cuja aplicação
> mecânica item-por-item, sem avaliar o trade-off local, pode piorar a
> simplicidade do código em vez de melhorá-la (ver ressalvas inline nos itens
> 3 e 4).

## Metodologia

- 55 arquivos em `src/todos/` (~23.000 linhas) e 30 arquivos em `tests/`
  (~8.100 linhas) foram lidos integralmente, um a um, por agentes de
  auditoria dedicados (14 lotes, sem sobreposição de arquivos).
- Cada achado abaixo cita arquivo e número de linha e foi verificado por
  leitura direta do código — não é inferido a partir de nomes de arquivo ou
  suposições.
- Achados são reportados por desvio das heurísticas, não por severidade de
  segurança (para isso já existe `docs/audits/2026-06-25-security-review.md`).
  Alguns achados aqui coincidem com bugs reais e são sinalizados como **[bug]**
  no resumo.
- "Nenhuma violação relevante encontrada" significa que o arquivo foi lido
  por completo e não apresentou desvio das heurísticas — não que não foi
  auditado.

---

## Resumo executivo

### Padrões transversais (aparecem em dezenas de arquivos — não repetidos por
arquivo no resumo, só nos capítulos)

1. **Dict solto cruzando fronteira de módulo.** É a violação mais frequente
   da codebase, de longe. Praticamente todo método público dos backends
   (`backends/rest/*.py`, `backends/web/*.py`), do `SEIClient`
   (`sei_client.py`) e do `SEIWebClient` (`sei_web_client.py`) retorna
   `dict`/`list[dict]` cru vindo direto do JSON da API ou do parse de HTML,
   sem `pydantic`/`dataclass`. Os inputs de escrita já são tipados
   (`NovoProcesso`, `EnvioProcesso`, `FiltrosPesquisaProcessos` em
   `backends/models.py`) — a assimetria está nos retornos.
2. **Herança de mixins em vez de composição.** Todos os backends de domínio
   (`MarcadoresWeb`, `ProcessosRest`, `UnidadesWeb`, etc.) e as classes-fachada
   (`SEIRestBackend`, `SEIWebBackend`) usam herança múltipla de 8–9 mixins
   apenas para compartilhar `self._rest`/`self._web` via MRO — não há relação
   "é-um" genuína entre nenhum desses mixins. `backends/base.py` e
   `backends/protocols.py` reforçam o mesmo padrão (e `protocols.py` parece
   ser código morto — ver capítulo dedicado).
3. **Exceção customizada sem atributos estruturados.** A esmagadora maioria
   dos `raise SEIValidationError(msg)`/`SEINotFoundError(msg)` na codebase
   carrega só uma string interpolada, mesmo `SEIError` já suportando
   `error_code`/`recoverable`/`suggested_next_tool`/`suggested_args`
   (usado corretamente em poucos pontos, ex. `sei_criar_processo` em
   `tools/processos.py`, e via `erro_do_sei(msg, data.get("mensagem"))` em
   ~10 pontos de `sei_client.py`).
4. **`logger.warning("...", exc)` em vez de `logger.exception(...)`.**
   Passar o objeto da exceção manualmente como argumento de formatação
   aparece em `hints.py`, `catalog_cache.py`, `backends/rest/_session.py`,
   `backends/rest/blocos.py`, `sei_client.py`, `auth.py`.
   **Ressalva:** `logger.exception(...)` força nível `ERROR` com traceback —
   não é um substituto mecânico 1:1 de `logger.warning(..., exc)` em todo
   ponto listado. Onde o nível `WARNING` já é a escolha deliberada (evento
   recuperável, não uma falha do sistema), o fix correto é
   `logger.warning(msg, exc_info=True)` (mantém o nível, ganha o traceback);
   `logger.exception(...)` só é o fix certo nos pontos que já mereceriam ser
   `ERROR`. Avaliar caso a caso antes de trocar em massa.
5. **Comentários "essa parte faz X" sinalizando função grande demais.**
   Recorrente em `sei_web_client.py` (`criar_documento_interno_web`,
   `incluir_documento_externo`, `_arvore_do_processo`, `_login_impl`),
   `sei_client.py` (`__init__`, `consultar_processo_completo`),
   `server.py` (`sei_resumo_processos`, `sei_pesquisar_processos`),
   `mcp_app.py` (`_resolver_documento`) e `setup_wizard.py`.
6. **Exceção usada como fluxo de controle para decidir entre estratégias
   equivalentes** (ex.: qual dialeto do SEI está em uso, qual fallback de
   scraping tentar) em vez de retorno tipado — muito comum em
   `sei_web_client.py` e em `mcp_app.py` (elicit/consentimento).

### Achados que são bugs reais, não só desvio de estilo

- **`ConsentRecusadoError` nunca é capturada** — quando o usuário recusa
  explicitamente o consentimento de acesso a um documento restrito, a
  exceção escapa como `ToolError` cru em vez de virar um payload estruturado
  (só `GateBloqueadoError` é tratada). Ver capítulos de `mcp_app.py` e
  `tools/documentos.py`.
- **`auth.py` — guard de SSRF falha aberto em erro de DNS**, logado apenas em
  `debug`: se a resolução de hostname falhar, a função devolve
  `blocked=None`, que é interpretado como "não bloqueado" (`_resolvido_bloqueado`,
  `_validar_url_sei`).
- **`sei_web_client.py::gerar_pdf_processo`** monta uma mensagem de erro que
  promete relatar o Content-Type recebido, mas usa uma string hardcoded
  `"(desconhecido)"` em vez do valor real — diagnóstico sempre inútil.
- **`backends/protocols.py` parece ser código morto**: nenhuma classe do
  arquivo é importada em nenhum outro lugar do repositório; o contrato
  realmente usado é `backends/base.py`.

---

# Parte 1 — Código-fonte (`src/todos/`)

## Núcleo (`__init__.py`, `__main__.py`, utilitários de topo)

### `src/todos/__init__.py`

Nenhuma violação relevante encontrada. Arquivo é apenas a declaração de versão do pacote.

### `src/todos/__main__.py`

Nenhuma violação relevante encontrada. Entry-point mínimo e direto.

### `src/todos/hints.py`

- **[logger.exception vs logging manual]** linha 38: `logger.warning("SEI_HINTS inválido — usando hints padrão: %s", exc)` passa o objeto da exceção manualmente como argumento de formatação em vez de usar `logger.exception("mensagem")` dentro do bloco `except`. → Trocar para `logger.exception("SEI_HINTS inválido — usando hints padrão")` (sem interpolar `exc` manualmente).

Fora isso, o arquivo é um bom exemplo de "retorna default logando antes" e trata JSON malformado de env var como exceção de parse de terceiro — categoria permitida.

### `src/todos/remote.py`

- **[side effect em argumento mutável sem indicar no nome]** linha 50: `build_remote_app(mcp: FastMCP, *, base_url: str)` muta o parâmetro recebido (`mcp.auth = SEIProOAuthProvider(base_url)`) sem que o nome da função sinalize a mutação do argumento. → Renomear para algo como `configure_and_build_remote_app`, ou não mutar `mcp` diretamente (retornar a config de auth e deixar o chamador aplicar).

Fora isso, uso correto de `TYPE_CHECKING`, logging (sem `print`), constante nomeada (`_MAX_ICON_BYTES`) e guard clauses em `_icon_bytes`.

### `src/todos/cli_call.py`

- **[exceção sem atributo estruturado]** linhas 34-35: `msg = f"Nome de tool inválido: {tool_name!r} — esperado 'todos <tool> chave=valor'"; raise CliArgumentError(msg)` — só string, sem atributo (`e.tool_name`).
- **[exceção sem atributo estruturado]** linhas 44-45: `msg = f"Argumento inválido (esperado chave=valor): {pair!r}"; raise CliArgumentError(msg)` — mesmo problema, sem atributo (`e.argumento`).

Fora isso, o arquivo é um bom exemplo de tipos modernos, docstrings completas, guard clauses (`validate_tool_name`) e uso de `sys.stdout.write` em vez de `print`.

### `src/todos/exceptions.py`

Nenhuma violação relevante encontrada. O arquivo é um bom exemplo do padrão pedido: hierarquia de exceções é o único caso de herança genuinamente "é-um" (LSP: `SEICredenciaisError` é-um `SEIAuthError` é-um `SEIError`), `SEIError` já carrega atributos estruturados (`error_code`, `recoverable`, `suggested_next_tool`, `suggested_args`) em vez de depender só de string, e `erro_do_sei` centraliza a classificação na origem sem re-tradução a jusante.

### `src/todos/settings.py`

Nenhuma violação relevante encontrada. `TodosSettings` é `pydantic_settings.BaseSettings` (objeto tipado na fronteira de configuração), usa sintaxe moderna (`int | None`, sem `Optional`), validadores com guard clause e mensagens de erro específicas por campo — nenhum acesso a atributo privado, nenhuma exceção genérica, nenhum valor mágico repetido.

### `src/todos/catalog_cache.py`

- **[dict cruzando boundary]** linhas 61, 69, 87, 94, 111, 118, 123, 131: `namespace: dict[str, str]` cruza a fronteira pública da classe (`get`/`set`/`delete`/`ttl`) como dict solto. → Substituir por um dataclass `CacheNamespace`-like tipado na assinatura pública.
- **[logger.exception não usado para erro real]** linhas 66, 92, 116, 128: `logger.warning("Falha ao ...", exc_info=True)` é usado em vez de `logger.exception(...)` nos quatro métodos de acesso ao SQLite. → Trocar para `logger.exception("Falha ao ler cache de catalogos")` (mesmo efeito de traceback, seguindo a convenção pedida).

Fora isso, o arquivo trata bem os erros (nunca engole silenciosamente sem logar, usa tipos estreitos de exceção — `sqlite3.Error`, `json.JSONDecodeError` — e não há `except Exception` genérico).

### `src/todos/access_control.py`

- **[dict solto na fronteira]** linhas 136-219 e 281-329 (`_bloco_base`, `construir_aviso_bloqueio`, `construir_disclaimer_acompanhante`, `construir_aviso_recusado`, `extrair_nivel`, `extrair_nivel_web`): todas essas funções recebem/retornam `dict` cru com um schema fixo e conhecido (`nivel_acesso`, `rotulo_nivel`, `hipotese_legal`, `alvo`, `riscos`, `tipo_resposta`, ...) em vez de um pydantic model/dataclass — esse dict é embutido em `GateBloqueadoError.payload` (linha 36-39) e depois serializado em outras camadas, então divergência de schema não é pega por type checking.
- Ponto positivo: `GateBloqueadoError`/`ConsentRecusadoError` (linhas 33-44) são uma relação de herança "é-um" genuína (exceção especializando exceção) e já carregam atributo estruturado (`payload`) em vez de string solta.

### `src/todos/html_utils.py`

- **[clareza / função grande demais]** linhas 179-240 (`_SEIMarkdownConverter.convert_tr`): cascata de booleanos derivados (`is_first_row`, `is_tag_parent`, `is_headrow`, `is_tag_grandparent`, `is_head_row_missing`) para decidir se uma linha é cabeçalho de tabela — exige "confiar" no rastreamento manual da lógica para entender o resultado. → Extrair a classificação de linha de cabeçalho para uma função nomeada isolada (ex. `_is_header_row(el, parent, grandparent) -> bool`).
- **[except mistura categorias de erro]** linha 267: `except (AttributeError, TypeError, ValueError, UnicodeDecodeError)` em `html_to_markdown` agrupa `AttributeError` (tipicamente bug/uso indevido de API) junto com falhas legítimas de parsing de HTML de terceiros — um bug real em `convert_tr` seria silenciosamente convertido em fallback para `html_to_text` em vez de propagar. → Remover `AttributeError` deste except.
- **[exceção como fluxo de controle]** linhas 406-411 (`sanitize_iso8859`): usa `try/except UnicodeEncodeError` por caractere para testar se ele está em ISO-8859-1 — condição perfeitamente previsível (ISO-8859-1 cobre `ord(char) <= 0xFF`), não bug/infra. → Substituir por `if ord(char) <= 0xFF: ... else: ...`, sem exceção.

### `src/todos/responses.py`

- **[dict solto dentro de modelo tipado]** linhas 375-378 (`ResultadoPesquisaProcessos.processos: list[dict[str, object]]`) e linha 389 (`ResultadoListaProcessos.processos: list[dict[str, object]]`): campos de lista de dicts crus dentro de modelos Pydantic que, no resto do arquivo, tipam explicitamente cada item de catálogo (`UnidadeSEI`, `UsuarioSEI`, `BlocoAssinatura`, etc.). → Definir um modelo (`ProcessoResumo`) com os campos normalizados + `model_config = ConfigDict(extra="allow")` para os extras, como já é feito em `UnidadeSEI`/`UsuarioSEI`.
- **[dict solto dentro de modelo tipado]** linha 412 (`ProcessoDetalhe.aviso_acesso: dict | None`): mesmo padrão. → Modelar como `AvisoAcesso(BaseModel)` com os campos conhecidos.

O uso de `ConfigDict(extra="allow")` como padrão em quase todos os modelos de catálogo é uma versão mais branda do mesmo problema, mas é um trade-off documentado e consistente — não listado como violação isolada.

### `src/todos/sei_styles.py`

- **[dict solto cruzando módulo]** linhas 9-284 (`SEI_STYLES`) e 288-329 (`STYLE_SHORTCUTS`): dois catálogos de configuração expostos como dicts soltos no nível de módulo, importados por outras partes do sistema — dado de configuração atravessando fronteira de módulo sem `dataclass`/`pydantic`. O dict é heterogêneo (alguns itens têm `recuo`, outros `autonumeracao`, outros `contexto`/`uso`). → Definir `class SeiStyle(BaseModel|dataclass)` com campos opcionais e um `dict[str, SeiStyle]`/lista de instâncias no lugar do dict cru.
  **Ressalva:** diferente dos dicts de retorno de API/scraper (item 1 do
  resumo), `SEI_STYLES`/`STYLE_SHORTCUTS` são dados estáticos autorados no
  próprio código, não parseados de input externo/não confiável — não há
  fronteira real de validação a ganhar aqui (o guard de drift na linha
  335-341 já cobre a única invariante que importa). Converter os 39 itens
  para instâncias de classe é mais cerimônia sem uma correspondente redução
  de risco; tratar como item de prioridade baixa, não como o mesmo tipo de
  achado que os dicts de retorno de API/scraper.

O guard de import-time (linhas 335-341) que valida drift entre os dois catálogos é um bom uso de exceção para invariante violado — não é uma violação.

### `src/todos/auth.py`

- **[fail-open / logger.debug para erro real — BUG]** linhas 94–113 (`_resolvido_bloqueado`): quando a resolução DNS falha (`except (socket.gaierror, OSError) as exc`, linha 111), o erro é logado em `logger.debug` (linha 112) e a função retorna `blocked=None` — que em `_validar_url_sei` (linhas 130–134) é interpretado como "não bloqueado", deixando a validação de SSRF passar. Uma falha de DNS num guard de segurança sendo tratada como "seguro por padrão" e logada apenas em debug é o "return default silencioso" + "logger.debug para erro real" que a heurística proíbe, com efeito colateral de segurança (bypass de SSRF). → Logar em `warning`/`logger.exception` e decidir explicitamente se falha de resolução deve bloquear (fail-closed).
- **[logger-exception-manual]** linhas 111–112 e 254–256: nos dois blocos `except ... as exc: logger.debug("...", exc)` a exceção é formatada manualmente em vez de `logger.exception(...)`. → Trocar para `logger.exception(...)` (ou `exc_info=True`) nos dois pontos.
- **[retorno-tipado/clareza]** linhas 82–91 (`_is_ip_bloqueado`): o retorno `str | None` sobrecarrega três significados — `None` = "não é IP literal", `""` = "IP válido, não bloqueado", string não vazia = "IP bloqueado" — e a própria linha 91 precisa de um comentário para explicar a convenção. → Substituir por um enum/dataclass de resultado (ex. `IPCheckResult` com variantes `NAO_E_IP` / `PERMITIDO` / `BLOQUEADO(endereco)`).
- **[dict-boundary]** linhas 190, 201, 270, 367–395, 417–442, 612–618, 697–723: payload JWT, credenciais SEI e auth code trafegam como `dict`/`dict[str, dict]` crus (`_auth_codes: dict[str, dict]`; `sei_creds = {...}` acessado depois via `sei_creds["sei_usuario"]`). Chaves string literais repetidas em múltiplas funções sem modelo. → Introduzir dataclasses/pydantic (`SeiCredentials`, `AuthCodeEntry`, `TokenPayload`).
- **[custom-exception-sem-atributos]** linhas 127, 134, 137 (`_validar_url_sei`): `ValueError(msg)` com string interpolada, sem atributo estruturado (campo, motivo, host). → Criar exceção própria (`URLSeiInvalidaError`) com atributos.
- **[duplicação]** linhas 179–180, 231–232, 240–241: o guard `if len(get_settings().jwt_secret) < _JWT_SECRET_MIN_LEN: raise RuntimeError(_JWT_CONFIG_ERR)` é repetido idêntico em três funções. → Extrair função auxiliar única (`_require_jwt_secret()`).
- **[magic-value]** linhas 154 e 160: truncamento `sig[:64]` repetido sem constante nomeada. → Extrair `_REVOCATION_KEY_LEN = 64`.

---

## `src/todos/backends/`

### `src/todos/backends/__init__.py`

- **[exceção como fluxo de controle]** linhas 3-5: o docstring documenta que `SEIBackend` usa `NotImplementedError` como mecanismo padrão para "operação não suportada por este backend" — uma situação de negócio esperada e recorrente (confirmado em `web/__init__.py:15-17` e `rest/__init__.py:14-16`), não bug/infra/parse externo. → Modelar como retorno tipado (`OperacaoNaoSuportada` ou similar) em vez de `raise NotImplementedError` no contrato.

### `src/todos/backends/choice.py`

- **[exceção sem atributo estruturado]** linhas 47-48 e 51-55: `SEIError(msg)` levantado duas vezes só com string interpolada, sem atributo estruturado (ex. nome da tool ou estado esperado). → Adicionar atributos à exceção.

Fora isso, o arquivo é um bom exemplo de mecanismo request-scoped bem documentado e tipado (`BackendChoice`, `Literal`).

### `src/todos/backends/models.py`

Nenhuma violação relevante encontrada. O arquivo é o exemplo correto do padrão pedido: todo parâmetro de alta aridade é `@dataclass(frozen=True)` tipado, sintaxe moderna (`str | None`, `list[str] | None`), sem herança, `senha: str = field(repr=False)` evita vazamento de segredo em repr, e a validação de invariantes (`__post_init__`) é o caso legítimo de exceção por invariante violado.

### `src/todos/backends/protocols.py` — código morto?

- **[dead-code]** arquivo inteiro (693 linhas): nenhuma classe deste arquivo (`UnidadesProtocol`, `ProcessosLeituraProtocol`, `ProcessosEscritaProtocol`, `DocumentosProtocol`, `CatalogosProtocol`, `MarcadoresProtocol`, `AcompanhamentoProtocol`, `BlocoInternoProtocol`, `BlocoAssinaturaProtocol`, `CredenciamentoProtocol`) é importada em nenhum outro lugar do repositório. O docstring (linhas 3–10) descreve um propósito ("backends declaram o que implementam herdando do Protocol relevante") nunca adotado: quem é herdado de fato por `SEIRestBackend`/`SEIWebBackend` é `SEIBackend` (`base.py`), não estes Protocols. → Remover o arquivo, ou migrar os backends para implementá-los de fato e eliminar a duplicação de `base.py`.
- **[dict-boundary]** linhas 30, 34, 38, 96, 108, 156, 189, 485 (representativo de ~127 métodos): todo retorno é `dict`, `list[dict]` ou `dict | list[dict]` cru, apesar de entradas de escrita já usarem modelos tipados (`NovoProcesso`, `EnvioProcesso`, etc.).
- **[dict-boundary]** linha 322 (`alterar_secoes`): `secoes: list[dict]` é a única entrada de escrita do arquivo sem modelo tipado. → Criar `SecaoEdicao` (dataclass/pydantic).
- **[clareza]** linhas 156, 189, 485: retorno `dict | list[dict]` documentado via docstring ("retorna `list[dict]` no REST e `dict` no web") força o chamador a checar `isinstance` em runtime — exatamente o "confia em mim" que a regra de clareza proíbe.

### `src/todos/backends/base.py`

- **[exceção-como-fluxo]** linhas 43–50 e todos os ~127 stubs (`raise NotImplementedError`): "operação não suportada por este backend" é condição de negócio esperada e recorrente, documentada por método — confirmado que `mcp_app.py:548` faz `except (NotImplementedError, TypeError)` e `tools/assinatura.py:62` faz `except NotImplementedError:` para decidir a resposta ao usuário, tratando a exceção como fluxo normal. → Substituir por retorno tipado de suporte (ex. `suporta(op) -> bool` por backend).
- **[herança]** linha 43 (`class SEIBackend:`): existe só para fornecer um stub padrão herdado junto com ~8 mixins de domínio em cada backend concreto (herança múltipla de 9 classes em `rest/__init__.py`/`web/__init__.py`) — não há relação "é-um" genuína. → Composição: objeto de capacidades registrado explicitamente por backend.
- **[clareza/doc-desatualizada]** linhas 9–11: o docstring afirma que "o roteador (composite) trata a queda para o outro backend", mas não existe mais `composite.py` no pacote, e `backends/__init__.py` (linhas 9–12) afirma o oposto ("não há roteamento automático REST/web"). → Atualizar o docstring.
- **[dict-boundary]** linhas 58, 62, 110, 142, 208, 358: retornos `dict`/`list[dict]` crus em praticamente todos os métodos; `secoes: list[dict]` sem tipo (linha 358).
- **[duplicação]** arquivo inteiro vs. `protocols.py`: as mesmas ~127 assinaturas são declaradas de forma independente em dois arquivos, sem nenhum import/teste que force sincronia.

### `src/todos/backends/web/__init__.py`

- **[herança vs composição]** linhas 38-49: `SEIWebBackend` é composto por herança múltipla de 8 classes (`_WebBase, UnidadesWeb, ProcessosWeb, DocumentosWeb, CatalogosWeb, MarcadoresWeb, AcompanhamentoWeb, BlocosWeb, SEIBackend`). Só a relação com `SEIBackend` é genuinamente "é-um"; as demais 7 são mixins de domínio sem relação de especialização. → Substituir por objetos compostos como atributos, delegando explicitamente.
- **[exceção como fluxo de controle]** linhas 15-17: reafirma o stub `NotImplementedError` para operações sem suporte web — mesmo ponto sistêmico de `backends/__init__.py`.

### `src/todos/backends/web/_session.py`

- **[herança vs composição]** linha 15 e 26: `_WebMixin`/`_WebBase` existem só para injetar `self._web` compartilhado via MRO. Não há relação "é-um" entre `MarcadoresWeb`/`AcompanhamentoWeb`/etc. e `_WebMixin`. → Composição explícita (cada mixin de domínio recebe um `SEIWebClient` por injeção).

### `src/todos/backends/web/marcadores.py`

- **[herança vs composição]** linha 12: `MarcadoresWeb(_WebMixin)` — mesma observação de `_session.py`.
- **[dict solto cruzando módulo]** linhas 15, 26, 30, 34: todos os métodos públicos retornam `dict` cru. → Definir modelos de resposta (`MarcadorAplicado`, `ListaMarcadores`).

### `src/todos/backends/web/acompanhamento.py`

- **[herança vs composição]** linha 21: `AcompanhamentoWeb(_WebMixin)`.
- **[dict solto cruzando módulo]** linhas 29, 49, 53, 59, 68, 77: retornos `dict` crus.
- **[envelopamento inconsistente de exceção externa]** apenas `acompanhar_processo` (linhas 41-47) envelopa `httpx.HTTPError` em `SEIConnectionError`; os demais métodos do mesmo mixin (linhas 49, 53, 59, 68, 77) deixam `httpx.HTTPError`/`SEIError` vazar sem tradução. → Envelopar uniformemente em todos os métodos.
- **[exceção sem atributo estruturado]** linhas 46-47: usa corretamente `from exc`, mas a exceção carrega só string, sem atributo `processo` estruturado.

### `src/todos/backends/web/unidades.py`

- **[herança vs composição]** linha 13: `UnidadesWeb(_WebMixin)`.
- **[dict solto cruzando módulo]** linhas 16, 20, 24, 28, 61, 73, 77, 84.
- **[if aninhado além de um nível]** linhas 52-58: `if sigla and (...)` contém `if not any(...)` — mais de um nível de aninhamento. → Extrair para função auxiliar com guard clauses (`_deve_incluir_unidade_atual(...)`).
- **[lógica no lugar errado]** linhas 44-56: lê múltiplos campos do mesmo objeto `atual` (`sigla`, `nome`, `id_unidade`) para decidir se deve reinserir a unidade atual nos resultados — deveria ser um método do objeto de unidade (ex. `atual.combina_com_filtro(filtro)`).
- **[função grande demais / comentário "essa parte faz X"]** linhas 33-35 e 41-43: comentários explicando a reinserção da unidade atual indicam que `pesquisar_unidades` (linhas 28-59) acumula responsabilidades demais. → Extrair para função auxiliar nomeada.

### `src/todos/backends/web/catalogos.py`

- **[herança vs composição]** linha 12: `CatalogosWeb(_WebMixin)`.
- **[dict cruzando boundary]** linhas 32-39, 55-64, 74-83, 93-102, 112-121, 129, 134, 144, 152-159, 169-176: retornos `dict` crus montados a partir de `result.get("tipos", [])` etc. → Modelar retorno como dataclass (`ListaCatalogoResult`).
- **[magic value repetido]** linhas 24, 46, 67, 86, 105, 123, 131, 137, 146, 162: `limit: int = 50` repetido em 10 assinaturas sem constante nomeada.

### `src/todos/backends/web/blocos.py`

- **[herança vs composição]** linha 14: `BlocosWeb(_WebMixin)`.
- **[magic value repetido]** linhas 54, 90 e 118: string `"Lista vazia — forneça pelo menos um id."` repetida em três métodos sem constante nomeada.
- **[logger manual do objeto de exceção]** linhas 66-68, 100 e 128: `logger.warning(..., outcome)` passa o objeto de exceção manualmente em vez de `logger.exception`/`exc_info`.
- **[dict cruzando boundary]** linhas 17-159: todos os métodos retornam `dict`/`list[dict]` cru.
- **[exceção sem atributo estruturado]** linhas 73-74, 105-106 e 133-134: `SEIError(msg)` construída só com `'; '.join(erros)` embutido na string, sem carregar a lista de IDs que falharam como atributo.

### `src/todos/backends/web/documentos.py`

- **[herança vs composição]** linha 27: `DocumentosWeb(_WebMixin)`.
- **[magic value duplicado / exceção sem atributos]** o padrão `if processo is None: raise SEINotImplementedError(msg)` (mensagem "Em instâncias sem mod-wssei, forneça o parâmetro 'processo' ...") se repete quase idêntico em 8 pontos (linhas 87-91, 98-103, 108-110, 118-122, 155-159, 168-173, 194-199, 211-215), e a variante "requer mod-wssei (REST)" se repete em 5 pontos (261-265, 270-275, 279-284, 291-295, 297-299) — sem constante/helper nomeado, e nenhuma dessas exceções carrega atributo estruturado. → Extrair `_exigir_processo(operacao: str)`.
- **[duplicação de lógica]** linhas 82-92 e 94-104: `consultar_documento_externo` e `consultar_documento_interno` têm corpo idêntico.

### `src/todos/backends/web/processos.py`

- **[herança vs composição]** linha 27: `ProcessosWeb(_WebMixin)`.
- **[exceção sem atributos estruturados / inconsistência]** linha 175-179 (`enviar_processo`): string simples, sem `error_code`/`suggested_next_tool`, para a mesma falha ("unidade não encontrada") que em `rest/processos.py:140-146` carrega esses atributos.
- **[if/else aninhado além de um nível]** linhas 220-252 (`atribuir_processo`): `if exatos: ... else: parciais=[...]; if len(parciais)==1: ... elif not parciais: ... else: ...` aninha um segundo nível dentro do `else` externo. → Extrair helper `_resolver_usuario_por_texto(opcoes, alvo)`.
- **[função exige comentário / extrair]** linha 218-219: comentário descreve o que o bloco seguinte faz — sinal de que deveria ser extraído.
- **[exceção sem atributos estruturados]** linhas 211 e 215 não carregam atributos, enquanto as exceções mais abaixo na mesma função (233-239, 246-252) carregam `error_code`/`recoverable`/`suggested_next_tool`/`suggested_args` — inconsistência dentro do mesmo método.

---

## `src/todos/backends/rest/`

### `src/todos/backends/rest/__init__.py`

- **[herança vs composição]** linhas 38-49: `SEIRestBackend` combina 9 classes por herança múltipla (`_RestBase, UnidadesRest, ProcessosRest, DocumentosRest, CatalogosRest, MarcadoresRest, AcompanhamentoRest, BlocosRest, CredenciamentoRest, SEIBackend`). → Composição explícita em vez de MRO.
- **[exceção como fluxo de controle]** linhas 14-16: mesmo stub `NotImplementedError` para operações sem equivalente REST.

### `src/todos/backends/rest/_session.py`

- **[herança]** linha 56: `_RestBase(_RestMixin)` — composição disfarçada de herança.
- **[dict cruzando boundary]** linhas 71-76 e 88-111: `proc.get("IdProcedimento", "")`, `d.get("atributos", {}).get(...)` — respostas do `SEIClient` atravessam o módulo como dict solto. → Modelar como dataclass/pydantic (`ProcessoREST`, `DocumentoREST`).
- **[logger manual do objeto de exceção]** linhas 94-101, 112-117 e 123-128: `logger.warning("... %s", ..., exc)` em vez de `logger.exception(...)`.
- **[exceção sem atributo estruturado]** linhas 74-75 e 130-136: `SEINotFoundError(msg)` só com string, sem `e.referencia`.
- **[exceção como fluxo de controle]** linhas 83-128: resolução de documento usa duas tentativas (Solr → `visualizar_documento_interno`) via `try/except SEIError` capturando erro de negócio como sinal de "tente a próxima estratégia". → Retorno tipado (`Found | NotFound`) em vez de exceção.

### `src/todos/backends/rest/catalogos.py`

- **[herança]** linha 21: `CatalogosRest(_RestMixin)`.
- **[dict cruzando boundary]** linhas 26, 43, 59, 71, 83, 91, 99, 108, 118, 126, 138.
- **[magic value repetido]** linhas 25, 42, 58, 70, 82, 91, 99, 111, 120, 129: `limit: int = 50` repetido 10 vezes sem constante nomeada (diferente de `tools/catalogos.py`, que já usa `_DEFAULT_LIMIT`).
- **[exceção sem atributo estruturado]** linhas 17-18: `SEIValidationError(msg)` só com string interpolada de `pagina`.

### `src/todos/backends/rest/marcadores.py`

- **[herança]** linha 8: `MarcadoresRest(_RestMixin)`.
- **[dict cruzando boundary]** linhas 11, 15, 19, 23, 27, 32, 36, 45, 50; o docstring de `consultar_marcador_processo` admite `dict | list[dict]` — union solto em vez de tipo modelado.

### `src/todos/backends/rest/acompanhamento.py`

- **[herança]** linha 9: `AcompanhamentoRest(_RestMixin)`.
- **[dict cruzando boundary]** linhas 12, 19, 31, 38, 42, 46, 50, 54.
- **[lógica no lugar errado]** linha 25: `id_acomp = str(acomp.get("idAcompanhamento") or acomp.get("id", "")) if acomp else ""` lê dois campos alternativos do mesmo dict para decidir o id efetivo — decisão de negócio embutida na função em vez de método/propriedade de um objeto de resposta.
- **[exceção sem atributo estruturado]** linhas 27-28: `SEINotFoundError(msg)` sem atributo (`processo`).

### `src/todos/backends/rest/credenciamento.py`

- **[herança]** linha 28: `CredenciamentoRest(_RestMixin)`.
- **[dict cruzando boundary]** linhas 35, 41, 48, 54.
- **[exceção sem atributo estruturado]** linhas 17-18 (`_exigir_processo`) e 24-25 (`_exigir_id_usuario`): `SEIValidationError(msg)` com string interpolada, sem atributo (`campo`).

### `src/todos/backends/rest/unidades.py`

- **[herança]** linha 8: `UnidadesRest(_RestMixin)`.
- **[dict cruzando boundary]** linhas 11, 15, 22, 29, 33, 41, 45, 49, 53, 57, 61: retornos `dict`/`list[dict]` crus em praticamente todos os métodos.

### `src/todos/backends/rest/blocos.py`

- **[herança]** linha 16: `BlocosRest(_RestMixin)`.
- **[dict cruzando boundary]** linhas 23-201: todos os métodos retornam `dict`/`list[dict]` cru.
- **[logger manual do objeto de exceção]** linhas 171-173: `logger.warning("...: %s", login, exc)` em vez de `logger.exception(...)`.
- **[exceção sem atributo estruturado]** linhas 174-179: `SEIValidationError(msg)` só com string interpolando `login`, sem atributo (`e.login`).

### `src/todos/backends/rest/documentos.py`

- **[herança para composição]** linha 24: `DocumentosRest(_RestMixin)`, combinado por herança múltipla junto com outros 8 mixins em `SEIRestBackend`.
- **[dict solto na fronteira]** linhas 27-75 (`buscar_documento`): formas ad hoc de dict (`{"encontrado": True, ...}` vs `{"encontrado": False, ...}`) para representar um resultado de negócio esperado. → Union/dataclass tipado (`DocumentoEncontrado | DocumentoNaoEncontrado`).
- **[função grande demais / exceção sem atributos]** linhas 171-213 (`assinar_documento`): um único método resolve id do documento, login, autenticação, fallback de busca de usuário, validação e construção de credenciais. → Extrair `_resolver_id_usuario(login)`. Linhas 202-206 levantam `SEIValidationError(msg)` só com string, ao contrário de outros pontos do código (ex. `rest/processos.py:140-146`).

### `src/todos/backends/rest/processos.py`

- **[herança para composição]** linha 27: `ProcessosRest(_RestMixin)`.
- **[exceção sem atributos estruturados / inconsistência]** linha 200-204: `atribuir_processo` carrega só string, sem `error_code`/`suggested_next_tool`, enquanto `enviar_processo` (140-146, mesmo arquivo) usa `error_code="UNIDADE_NAO_ENCONTRADA"`, `recoverable=True`, `suggested_next_tool`, `suggested_args`.
- **[exceção sem atributos estruturados]** linhas 56-61 e 121-122: idem, só string.
- **[dict solto na fronteira]** pervasivo: praticamente todo método público retorna `dict`/`list[dict]` (linhas 30, 34, 65, 70, 75, 80, 85, 90, 95, 160, 164, 169, 206, 211, 216, 221, 235, 240, 245, 250) apesar de `backends.models` já ser usado para *inputs*.

---

## `src/todos/tools/`

### `src/todos/tools/__init__.py`

Nenhuma violação relevante encontrada. Apenas docstring de pacote, sem código executável.

### `src/todos/tools/credenciamento.py`

- **[acesso a símbolo privado de outro módulo]** linha 13: `from todos.mcp_app import _DEST, _READ, _json, _rest_backend, mcp` importa símbolos com prefixo `_` de outro módulo (padrão repetido em todo `todos.tools.*`, não isolado deste arquivo). → Expor API pública explícita entre `tools` e `mcp_app`.

Fora esse ponto, o arquivo usa bem `pydantic` (`CredenciamentoSEI`, `PaginadoGenerico`, `NextAction`) e guard clauses simples.

### `src/todos/tools/marcadores.py`

- **[dict cruzando boundary / inconsistência]** linhas 51, 67, 95, 114, 131, 143, 159, 174, 185: todas as tools serializam `dict` cru via `_json(result)`, diferente do padrão já estabelecido em `tools/catalogos.py`. → Criar modelos de resposta em `todos/responses.py`.
- **[magic value / sentinela não nomeada]** linha 90: `if marcador == "?":` sentinela sem constante nomeada. → `_SENTINELA_DESCOBERTA = "?"`.
- **[exceção sem atributo estruturado]** linhas 48-49: `SEIValidationError(msg)` só com string (lista de cores embutida no texto).

### `src/todos/tools/catalogos.py`

Nenhuma violação relevante encontrada. Ao contrário dos backends REST/web equivalentes, já modela corretamente as fronteiras (retornos `PaginadoGenerico[HipoteseLegal|TipoCatalogo|AssuntoSEI|ContatoSEI|TextoPadrao|GrupoModelos|ModeloDocumento]`), constante `_DEFAULT_LIMIT` e guard clauses simples.

### `src/todos/tools/blocos_internos.py`

- **[dict solto na fronteira]** linhas 104-108: `result = {"processos": ..., "tem_proxima": ..., "itens_pagina": ...}` monta manualmente um dict ad hoc que espelha `ResultadoListaProcessos`, só validado no fim (linha 129). → Construir/validar o modelo pydantic diretamente.
- **[lógica de decisão em dict solto]** linha 116-120: `primeiro_protocolo = (page_items[0].get("protocoloFormatado", "") or page_items[0].get("protocolo", "") ...)` lê dois campos do mesmo item para decidir fallback dentro do handler. → Extrair helper (`_protocolo_de(item)`).

### `src/todos/tools/acompanhamento.py`

- **[magic value]** linha 52: `if grupo == "?":` sentinela sem constante nomeada. → `_SENTINELA_DESCOBERTA = "?"`.
- **[dict solto / reshape ad hoc]** linhas 148 e 187: `result["itens"] = result.pop("acompanhamentos", [])` renomeia chave de dict cru diretamente no handler, duplicado em dois lugares. → Mover normalização de schema para o backend/adaptador único.

### `src/todos/tools/assinatura.py`

- **[exceção sem atributos estruturados]** linhas 40-48 (`_exigir_cargo`): `SEIValidationError` construída só com string interpolando a lista de cargos, sem atributo estruturado (`cargos_disponiveis=itens`).
- **[função grande demais / comentários de fase]** linhas 78-142 (`sei_cancelar_assinatura`): quatro fases distintas, cada uma introduzida por comentário ("Resolver número SEI → id interno", "Verificar se está assinado...", "Montar payload...", "Editar derruba a assinatura..."). → Extrair helpers (`_resolver_doc_id`, `_montar_payload_secoes`).

### `src/todos/tools/blocos_assinatura.py`

- **[dict solto na fronteira]** linha 49 (recorrente em 65, 81, 97, 206, 224, 241, 258, 275, 292, 311, 330): toda tool de escrita faz `result = await backend.xxx(...)` seguido de `return _json(result)` sem validação/tipo, ao contrário das tools de leitura paginadas do mesmo arquivo (108, 153) que já usam `PaginadoGenerico[...]`. → Modelar com `RespostaEscrita` (já existe em `responses.py`).
- **[magic value]** linhas 104 e 150: `limit: int = 50` hard-coded, sem constante nomeada (compare `tools/unidades.py:29`, que define `_DEFAULT_LIMIT = 50`).

### `src/todos/tools/configuracao.py`

- **[lógica no lugar errado]** linhas 54-58: `_sei_host()` lê `settings.sei_web_url` e `settings.sei_url` para decidir qual usar. → Mover para método/propriedade em `TodosSettings` (ex. `settings.effective_sei_host()`).
- **[logger.debug para erro real]** linhas 96-98, 257-259 e 319-321: o mesmo bloco trata `TimeoutError` (falha real de infraestrutura) no mesmo nível de log que "feature não suportada". → Separar `TimeoutError`/`_KeyringError` para `logger.warning`.
- **[exceção como fluxo de controle]** linhas 163, 185, 238, 299 e 308: `SEIValidationError` para cenários de negócio esperados (protocolo fora do padrão, amostras insuficientes, host não configurado, keyring ausente). → Retorno tipado (`ResultadoValidacao(ok, motivo)`).
- **[exceção sem atributos estruturados]** mesmas linhas.

### `src/todos/tools/unidades.py`

- **[dict solto na fronteira]** linhas 42, 60, 75, 155, 168, 185, 197, 313, 331, 348: `result = await backend.xxx(...)` seguido de `_json(result)` sem validação pydantic; a necessidade de `isinstance(result, dict)` (linhas 150, 180, 308, 326, 343) antes de `setdefault` confirma que o tipo não é garantido pelo chamador. → Tipar retornos com dataclasses/Pydantic, usando os modelos já existentes (`UnidadeSEI`, `UsuarioSEI`) de forma consistente também nessas tools.

Fora isso, o arquivo segue bem as convenções (constante `_DEFAULT_LIMIT` nomeada, keyword-only forçado corretamente, guard clauses simples).

### `src/todos/tools/analise.py`

- **[except+pass sem logar]** linhas 231-232 e 237-238: `except json.JSONDecodeError: pass` (duas vezes) engole o erro real de parse do JSON do LLM sem log. → Logar antes do `pass`, ou capturar a última exceção para incluir no `raise` final.
- **[raise bare descarta causa]** linha 293: `raise SEIError(msg)` depois de formatar `msg` com `last_exc` — a exceção original não é encadeada. → `raise SEIError(msg) from last_exc`.
- **[envelopamento incompleto de exceção externa]** linhas 286-291: só `litellm.RateLimitError`/`litellm.APIError` são capturadas; outras exceções de `litellm` (`AuthenticationError`, `Timeout`, `BadRequestError`) escapam sem virar `SEIError` na fronteira. → Capturar a exceção-base de `litellm` num único ponto.
- **[dict/list solto como resultado de parse]** linha 226 (`_extract_json(raw: str) -> dict`) e linha 390 (`**parsed` mesclado direto na resposta): dict do LLM atravessa a fronteira da tool sem validação de schema. → Validar contra modelo Pydantic (`AnaliseProcessoResposta`).

### `src/todos/tools/documentos.py`

- **[exceção como fluxo de controle — tratamento assimétrico, BUG]** linhas 213-214 (`_ler_documento_via_backend`) e 361-362 (`sei_baixar_anexo`): capturam `access_control.GateBloqueadoError` e convertem em payload JSON, mas **não capturam `access_control.ConsentRecusadoError`** — a recusa explícita do usuário escapa como `ToolError` cru em vez de receber o mesmo tratamento estruturado.
- **[wrapping de exceção externa duplicado em múltiplas tools]** linhas 293-296, 379-381, 487-489, 547-549 e 892-894: o mesmo bloco `except httpx.RequestError as e: raise SEIConnectionError(msg) from e` aparece idêntico em cinco tools. → Envelopar uma única vez na borda (ex. decorator `@requires_backend` ou helper compartilhado).
- **[exceção customizada sem atributo estruturado]** linhas 137-142, 301-306 e 365-370: o tamanho em bytes (`len(content)`) é embutido só na string de `SEIValidationError`.
- **[dict cru cruzando módulo]** linha 120: `_aplicar_disclaimer(conteudo, disclaimer: dict | None, formato)` recebe `disclaimer` como dict solto. → Dataclass/pydantic `Disclaimer`.
- **[lógica duplicada entre módulos]** linhas 682-684 (`sei_consultar_documento_externo`): repete exatamente a lógica de fallback de nível de acesso já presente em `mcp_app.py:609-611`.

### `src/todos/tools/processos.py`

- **[boundary-dict]** linhas 99, 137, 184: `_shape_lista_documentos`, `_shape_atividades`, `_shape_consultar_processo` recebem payload bruto do backend como `dict` solto em vez de modelo intermediário.
- **[boundary-dict]** linhas 447, 465, 481, 709, 723, 738, 753, 769, 787, 808, 824, 842, 874-879, 906-920, 1021-1023, 1058-1060: a maioria das tools de escrita/consulta devolve `str` via `_json(result)` embrulhando um dict cru, em vez do modelo tipado já usado em `_shape_resposta_escrita` (linhas 656/689) no mesmo arquivo.
- **[boundary-dict]** linhas 861-867: `sei_marcar_nao_lido` trata `backend.unidade_atual()` como dict cru apesar de `UnidadeSEI` (pydantic) já existir e ser usado em `sei_listar_unidades_processo` (linha 366) no mesmo arquivo.
- **[reuse]** linhas 325-344: `sei_listar_documentos` repete `d.get("atributos", {})` 5 vezes na mesma list-comprehension. → Extrair uma vez.
- **[exception-as-control-flow]** linhas 516-520, 862-869, 950-954, 971-978: `SEIValidationError` repetidamente só com string, embora a classe suporte atributos estruturados (usado corretamente em `sei_criar_processo`, linhas 635-643).
- **[magic-value]** linha 597: `limit=500` inline, enquanto o arquivo já nomeia limites semelhantes (`_LISTA_DOCS_LIMIT`, `_ATIVIDADES_LIMIT`).
- **[type-hint]** linha 79: `_to_str_list(items: list)` sem parâmetro genérico.

---

## `src/todos/server.py`, `mcp_app.py`

### `src/todos/server.py`

- **[decisão lê 2+ atributos do mesmo objeto]** linhas 176-185 e 186-195: extratores `anotacao`/`retorno` em `_CAMPOS_AGRUPAMENTO` leem múltiplos campos do dict `s` (status) para decidir o rótulo. → Mova a lógica para um modelo `StatusProcesso` (pydantic/dataclass).
- **[dict cru cruzando módulo]** linhas 258-274: `_agrupar_processos(todos: list[dict], ...) -> dict[str, dict]` declara `dict` explicitamente sem modelo.
- **[função grande / comentário de seção]** linha 336 dentro de `sei_resumo_processos` (289-370): mistura validação, paginação completa e agrupamento no mesmo bloco. → Extrair `_buscar_todos_processos`.
- **[função grande / comentário de seção]** linha 582 dentro de `sei_pesquisar_processos` (486-632, ~146 linhas): tentativa REST e fallback web deveriam ser funções próprias (`_pesquisar_via_rest`, `_pesquisar_via_web`).
- **[exceção re-envelopada perde subtipo]** linhas 630-632: `except (SEIError, httpx.HTTPError) as e2: raise SEIError(msg) from e2` recaptura um `SEIError` que já pode ser subtipo específico (`SEIAuthError`, `SEIConnectionError`) e o rebaixa para genérico — quem trata por tipo mais adiante deixa de funcionar.
- **[wrapping de exceção externa duplicado entre camadas]** linhas 368-370 e 578-580: bloco `except httpx.RequestError as e: raise SEIConnectionError(msg) from e` repetido idêntico (e mais 5 vezes em `documentos.py`).
- **[exceção customizada sem atributo estruturado]** linhas 124-131 (`_validar_filtro`) e 234-241 (`_validar_campo`): valor inválido só embutido na string.
- **[clareza — variável morta]** linha 549 (`_rest_unavailable = False`): setada mas nunca lida depois na função `sei_pesquisar_processos`.

### `src/todos/mcp_app.py`

- **[exceção usada como fluxo de controle para resultado de negócio esperado]** linhas 482-487 (`_ElicitNaoSuportadoError`, `_ElicitRecusadoError`) e uso em 624-634: "cliente não suporta elicit", "usuário recusou" e "usuário aceitou" são três ramos de negócio esperados, modelados inteiramente com `try/except` de exceções privadas. → Retorno tipado (`enum ConsentimentoResultado`) devolvido por `_solicitar_consentimento_via_elicit`.
- **[mesma família de exceção-como-controle, tratamento assimétrico — BUG]** linhas 626-629 vs 630-633: `_ElicitRecusadoError` vira `access_control.ConsentRecusadoError` e `_ElicitNaoSuportadoError` vira `access_control.GateBloqueadoError` — mas só `GateBloqueadoError` é tratada no nível da tool (`documentos.py`); **`ConsentRecusadoError` não é capturada em nenhum dos dois arquivos** e escapa como `ToolError` cru.
- **[dict cru cruzando módulo]** linha 569 `_consultar_meta_documento(...) -> dict` e uso em `_aplicar_gate_documento` (605-616): metadados do documento atravessam a fronteira backend→gate como dict solto. → `DocumentoMeta` (pydantic/dataclass) com propriedade `nivel_acesso`/`hipotese`.
- **[função com comentários de seção "Estratégia N"]** linhas 724 e 729-731 dentro de `_resolver_documento` (704-761): a Estratégia 2 ("tentar como id direto") ficou inline com comentário descritivo, ao contrário da Estratégia 1 já extraída (`_buscar_documento_via_solr`). → Extrair `_tentar_id_direto`.
- **[lógica duplicada entre módulos]** linhas 609-611 e as mesmas duas linhas em `documentos.py:682-684`: `nivel, hipotese = access_control.extrair_nivel(meta); if nivel is None: nivel = access_control.extrair_nivel_web(meta)` copiado idêntico. → Extrair helper único `access_control.resolver_nivel(meta)`.

---

## `src/todos/setup_wizard.py`

- **[log-level]** linha 423: `_logger_setup.debug("mcp add re-tentativa falhou: %s", re_exc)` registra em `debug` uma falha real antes de `return False` (contraste com a mesma função, linha 428, que usa `warning` corretamente).
- **[log-level]** linha 565: mesmo padrão em `_update_codex_via_cli`.
- **[boundary-dict]** linha 258 (`_read_existing_todos_env() -> dict[str, str] | None`), usado em `run_setup_wizard`/`run_set_password`: dado de I/O atravessa módulo como dict solto. → Dataclass (`TodosEnvConfig`).
- **[boundary-dict]** linhas 158-190: `_detect_organs(...)` devolve resultado de scraping como tupla de tuplas soltas, enquanto `_detect_modsei_url` (659-691) já usa a dataclass `_ModseiDetection` para resultado equivalente. → Envolver em dataclass (`_OrganDetection`).
- **[decision-in-wrong-place]** linhas 786, 788, 796: `_resolve_modsei_url` lê `detection.url`/`detection.confirmed` várias vezes para decidir mensagem/fluxo. → Método de `_ModseiDetection` (ex. `detection.describe()`).
- **[clareza/boundary]** linhas 276-318 e 865-884: retornam tuplas posicionais quase idênticas contendo senhas — alto risco de inversão silenciosa na chamada. → `NamedTuple`/dataclass com campos nomeados.
- **[needs-comment→extract]** linha 768: comentário documenta fluxo de controle implícito (fallthrough de try/except/else) dentro de `_detect_organs_with_ssl_fallback` (726-781). → Quebrar em passos nomeados.
- **[magic-value]** linha 293: `future.result(timeout=10)` sem constante, enquanto o arquivo já nomeia timeouts equivalentes (`_WSSEI_PROBE_TIMEOUT`).
- **[magic-value]** linhas 238-244: literais `"PGE"`, `"RO"`, `"9"`, `"0"` como defaults hardcoded sem constantes nomeadas.

---

## `src/todos/sei_client.py` (cliente REST assíncrono, 2333 linhas)

- **[dict solto cruzando boundary]** ~110 pontos ao longo do arquivo (representativo: linhas 343, 352, 361, 370, 391, 439, 457, ..., 2313): praticamente todos os métodos públicos fazem `data = resp.json()` e retornam esse dict/list cru direto para o chamador, sem validação nem schema. → Introduzir `pydantic`/`dataclass` por shape de resposta (processo, documento, marcador, bloco, etc.).
- **[exceção sem atributo estruturado / string interpolada]** ~110 pontos (representativo: linhas 349, 358, 367, 388, ..., 2328): `msg = f"Erro ao X: {data.get('mensagem')}"; raise SEIError(msg)` joga fora a mensagem do servidor como string interpolada. O próprio arquivo já tem o padrão correto em 10 lugares (`raise erro_do_sei(msg, data.get("mensagem"))` — linhas 466, 478, 505, 543, 631, 642, 722, 963, 1083, 1185). → Padronizar todos os `SEIError(msg)` para usar `erro_do_sei(msg, data.get("mensagem"))`.
- **[exceção sem atributo estruturado]** linhas 261, 309: `raise SEIError(str(e)) from e` ao envelopar `httpx.HTTPStatusError` descarta `e.response.status_code`/corpo da resposta.
- **[exceção de biblioteca externa não envelopada na fronteira]** linhas 535, 1914: `alterar_documento_externo`/`criar_documento_externo` chamam `resp.raise_for_status()` fora do `try/except` que em `_request` (256-261) envelopa `httpx.HTTPStatusError`/`httpx.TransportError` — erro HTTP do upload propaga cru. → Mover tratamento para dentro de `_post_with_file_reopen`.
- **[decisão de negócio no lugar errado]** linhas 934-957 (`alterar_processo`): lê múltiplas chaves do mesmo dict `proc` (`nivelAcesso`, `hipoteseLegal`, `tipoProcesso`, `especificacao`, `grauSigilo`, `assuntos`) para decidir se zera `hipotese_legal` e como montar `assuntos_ids`. → Método de domínio no objeto Processo.
- **[decisão de negócio no lugar errado]** linhas 1023-1031 (`criar_processo`): lê `dados.assuntos`/`dados.interessados` para decidir normalização de formato JSON. → Método/property em `NovoProcesso` (ex. `dados.assuntos_json`).
- **[if/else aninhado além de um nível]** linhas 91-106 (`__init__`): `elif self.base_url:` contém if/else interno para decidir `self.sei_root`. → Extrair `_resolver_sei_root(sei_web_url, base_url) -> str`.
- **[função grande demais / comentários "essa parte faz X"]** linhas 81-149 (`__init__`): comentários demarcam resolução de `sei_root`, cálculo de chave de keyring, montagem de namespace de cache, cálculo de `verify` SSL, construção do `httpx.AsyncClient` no mesmo construtor. → Extrair helpers privados.
- **[função grande demais / comentários]** linhas 391-437 (`consultar_processo_completo`): "call 1"/"call 2"/"merge" demarcam 3 etapas na mesma função. → Extrair `_consultar_basico`/`_consultar_rico`/merge.
- **[magic value repetido]** linhas 234, 245, 1858, 1869: tupla `(401, 403)` para detectar sessão expirada repetida 4 vezes. → `_HTTP_STATUS_REAUTH = (401, 403)`.
- **[logger.exception vs passar exceção manualmente]** linha 285: `logger.warning("Não foi possível obter a senha do keyring: %s", e)` em vez de `logger.exception(...)`.
- **[return default silencioso sem distinguir causa]** linhas 44-51 (`_safe_int`): `except (TypeError, ValueError): return default` engole a diferença entre "valor ausente" e "não numérico" sem logar.

---

## `src/todos/sei_web_client.py` (scraper HTTP, 5679 linhas)

### Parte 1 (linhas 1–~2950)

- **[dict solto cruzando boundary]** virtualmente todo método público (representativo: linhas 224, 986, 1025, 1279, 1439, 1495, 1697, 1788, 1853, 1937, 1990, 2000, 2083, 2172, 2188, 2193, 2393, 2420, 2475, 2564, 2773, 2924, 2997, 3053): resultado de scrape retornado como `dict`/`list[dict]` cru. → Modelos tipados (`ProcessoWeb`, `DocumentoWeb`, `AndamentoWeb`, `MarcadorWeb`).
- **[exceção customizada sem atributos estruturados]** virtualmente todo `raise SEIxxxError(msg)` (dezenas de pontos, ex. linhas 152/154/155, 263, 739, 743, 880, 901, 994, ..., 2940): `SEIError` já suporta `error_code`/`recoverable`/`suggested_next_tool`/`suggested_args`, mas nenhum destes call sites os preenche.
- **[exceção usada como fluxo de controle, sem log]** linhas 2246–2253 (`baixar_documento_externo_web`): `except SEIParseError:` decide entre duas ações alternativas (fallback de download) sem logar — falha real do primeiro caminho fica indistinguível de "use o fallback". Mesmo padrão em linha 3177 (`pesquisar_tipos_processo_web`).
- **[return default silencioso sem log]** linhas 183–188 (`_safe_int`): captura `ValueError` e retorna `default` sem `logger.warning`/`debug`.
- **[função grande demais / comentários "essa parte faz X"]** linhas 1556–1691 (`_arvore_do_processo`, ~135 linhas, comentários "1. Parse initial nodes", "2. Expand folders via POST", "3. Resolve AGUARDE nodes via BFS"); linhas 676–867 (`_login_impl`, ~190 linhas cobrindo keyring, CAPTCHA/2FA, parsing de form, POST, validação de redirect, populamento de caches); linhas 2773–2886 (`listar_atividades`): os branches `tipo_historico == "R"` e o `else` duplicam quase integralmente a mesma lógica de paginação.
- **[duplicação / magic string repetida]** `sei_base = f"{self.sei_root}/sei/"` reconstruído inline em pelo menos 10 métodos (linhas 1246, 1717, 1806, 1976, 2104, 2282, 2425, 2485, 2572, 2689, 2738, 3014, 3072, 3160). → Propriedade `self.sei_base` calculada uma vez.
- **[clareza / bug de mensagem enganosa — BUG]** linhas 2758–2761 (`gerar_pdf_processo`): `ct = "(desconhecido)"` é uma string hardcoded, não o Content-Type real da resposta — a mensagem de erro promete informar o Content-Type mas sempre imprime o mesmo placeholder. → Capturar o content-type real em `_gerar_arquivo_processo`.

### Parte 2 (linhas ~2950–5679)

- **[dict solto cruzando módulo]** dezenas de pontos (representativo: linhas 2952–2991, 2997, 3053–3058, 3163, 3230, 3260, 3282, ..., 5179): retornos crus com chaves inconsistentes entre métodos (compare `"idBloco"` em 3311 com `"id"`/`"nome"` em 3908). → Dataclasses de resultado por família de tool.
- **[exceção como fluxo de controle — feature detection entre versões do SEI]** linhas 3175–3178, 3376–3379, 3442–3447, 3973–3976, 5055–5060, 5096–5101: `try/except SEINotFoundError` usado repetidamente para decidir qual dialeto/versão do SEI está em uso. → `_obter_link_toolbar` deveria devolver `str | None` em vez de forçar try/except.
- **[logger.debug para erro real, com `continue`]** linhas 3818–3820, 3890–3894, 3926–3930 (`pesquisar_marcadores_web`, `pesquisar_tipos_documento_web`, `pesquisar_tipos_conferencia_web`): falhas reais de parsing/HTTP logadas em `debug` e descartadas com `continue`.
- **[except...continue sem log algum]** linha 4955–4956 (`listar_grupos_acompanhamento_web`): `except (SEIParseError, SEINotFoundError): continue` sem log — se todos os protocolos falharem, retorna `{"grupos": [], "total_itens": 0}` sem rastro.
- **[return default silencioso ao capturar exceção]** linhas 4869–4875 (`verificar_acesso_web`): captura `(SEIError, httpx.HTTPError)` amplo e retorna `{"temAcesso": False, ...}` sem logar.
- **[if/else aninhado além de um nível + função grande demais]** linhas 3758–3830 (`pesquisar_marcadores_web`): try aninhado dentro de try dentro de for, mais de quatro níveis de aninhamento.
- **["essa parte faz X" — funções grandes]** linhas 4241–4439 (`criar_documento_interno_web`, ~200 linhas, `# --- Step 1/2/4 ---`) e 4443–4767 (`incluir_documento_externo`, ~325 linhas, `# --- Step 1 a 7 ---`): os próprios comentários numerados são o sintoma da heurística.
- **[magic value repetido — status HTTP aceitável]** linhas 3124, 3410, 3431, 3449, 3466, 3584, 4114, 4409, 5165, 5222 usam a tupla `(200, 302)` repetida onze vezes; linha 4222 repete a mesma checagem como `{200, 302}` (set) — duas representações do mesmo conceito.
- **[lógica de decisão que lê múltiplos atributos do mesmo objeto]** linhas 4080–4103 (`criar_processo_web`) e 4465–4471 (`incluir_documento_externo`): decisão de serialização baseada em múltiplos campos do mesmo objeto deveria ser método do próprio dataclass.
- **[dict[str, Any] em fronteira de parser]** linhas 5473, 5513, 5583, 5625: `row: dict[str, Any]`/`result: dict[str, object]` como retorno de parser — tipo essencialmente não tipado por chave.

---

# Parte 2 — Testes (`tests/`)

*Nota: `pyproject.toml` já relaxa `ANN`/`SLF001`/`PLR2004`/`D` para `tests/**`, então ausência de type hints em funções `test_*` e acesso a atributos `_privados` (whitebox testing) não são tratados como violação nesta seção, salvo quando citados explicitamente abaixo.*

### `tests/__init__.py`

Arquivo vazio — nada a auditar.

### `tests/conftest.py`

Nenhuma violação relevante encontrada.

### `tests/helpers.py`

- **[type hints]** linha 6: `def aconst(v: object):` não anota o tipo de retorno — inconsistente com a função interna `_f(_ctx: object) -> object` do mesmo arquivo.

### `tests/test_keyring_reread.py`, `test_next_format.py`, `test_setup_wizard.py`, `test_tool_count.py`, `test_sei_styles.py`, `test_error_boundaries.py`, `test_gate_documento.py`, `test_exceptions.py`, `test_tool_annotations.py`

Nenhuma violação relevante encontrada em nenhum destes arquivos.

### `tests/test_backends_choice.py`

- **[duplicação/reuso]** linhas 27–45: `_FakeContext` reimplementa campo a campo a mesma fake já definida em `tests/helpers.py:FakeCtx` (15-32). → Importar e usar `helpers.FakeCtx`.

### `tests/test_gerar_arquivo_incluir_base64.py`

- **[type hints]** linha 38: `_aconst(v: object)` sem anotação de retorno (mesmo problema de `helpers.aconst`).
- **[duplicação/reuso]** linhas 38–42: `_aconst` é cópia literal de `helpers.aconst`. → Importar em vez de redefinir.

### `tests/test_cli_call.py`

- **[type hints]** linhas 102, 109, 116, 122: `monkeypatch`/`capsys` sem anotação de tipo — inconsistente com `test_setup_wizard.py`, que sempre anota `monkeypatch: pytest.MonkeyPatch`.

### `tests/test_cli_dispatch.py`

- **[type hints]** linhas 42, 65, 79, 105, 120, 134: `monkeypatch`/`capsys` sem anotação de tipo (em 105 só `exc: Exception` está anotado).

### `tests/test_catalog_cache.py`

- **[type hints]** linhas 105, 116, 139: `monkeypatch` sem anotação, enquanto `tmp_path: Path` no mesmo parameter list está corretamente anotado — inconsistência dentro do próprio arquivo.

### `tests/test_settings.py`, `test_detect_modsei.py`, `test_sei_web_client_binary_doc_detection.py`, `test_html_utils.py`

Nenhuma violação relevante encontrada em nenhum destes arquivos.

### `tests/test_rest_invariants.py`

- **[teste-frágil-implementação]** linhas 60-62 e 123-125: `_make_rest_base`/`_make_rest_base_for_doc` constroem `_RestBase` via `__new__` e atribuem diretamente `base._rest = mock_client`, contornando o construtor público. Se o nome do atributo interno mudar numa refatoração sem alterar comportamento observável, os testes quebram. → Expor construtor de teste ou injeção via parâmetro.

### `tests/test_sei_web_client_reauth.py`

- **[supressão sem justificativa]** linhas 122-123, 160-161, 164, 183-184, 215-217, 263-265, 284: `# type: ignore[method-assign]` repetido sem comentário de justificativa ao lado — a regra do projeto veda supressão sem correção do padrão subjacente. → Tipar `_http`/`login` como atributos substituíveis, ou usar `monkeypatch.setattr`.
- **[teste-frágil-implementação]** linhas 85-86, 94-98, 240-243, 269: escrita direta em atributos privados do cliente (`client._inbox_url`, `client._trabalhar_links[...]`) e, na linha 269, asserção sobre a estrutura interna do cache (`assert stale_key not in client._trabalhar_links`) em vez do efeito observável já presente (`content == _R5_OK`). → Verificar só o efeito observável.

### `tests/test_backends.py`

- **[supressão sem justificativa]** linha 265: `return SEIWebBackend(client)  # type: ignore[arg-type]` sem comentário de justificativa. → Tipar `_FakeWebClient` contra um `Protocol` compartilhado com `SEIWebClient`.

### `tests/test_sei_web_client_transport_reauth.py`

- **[teste frágil / detalhe de implementação]** linhas 250–255 e 259–261 (`TestReauthTransportPreservesTLSVerification`): acessam `client._http._transport`, `reauth_transport._wrapped` e `inner._pool._ssl_context.check_hostname` — atributos privados do próprio código e internos de `httpx`. Um refactor interno do httpx (sem mudança de comportamento observável) já quebraria o teste. → Testar comportamento observável (requisição real contra servidor TLS inválido) ou isolar atrás de propriedade pública testável.
- **[teste frágil / detalhe de implementação]** linha 33 e linha 405: mesmo padrão de acoplamento a atributos privados (`_http`, `_transport`, `_reauth_transport`, `_wrapped`).
- **[regra de projeto: `# type: ignore` proibido]** linhas 52, 82, 116, 149, 182, 221, 309, 352: todo `_ReauthTransport(wrapped, _FakeClient())` seguido de `# type: ignore[arg-type]` (8 ocorrências). → Definir `Protocol` mínimo e tipar `_FakeClient` contra ele.

### `tests/test_documentos_tools.py`, `tests/test_access_control.py`, `tests/test_parsers.py`

Nenhuma violação relevante encontrada em nenhum destes arquivos. (`test_access_control.py` trata explicitamente o risco de estado mutável compartilhado: `_ALVO` é constante e a fixture `alvo()` devolve `_ALVO.copy()`.)

### `tests/test_tool_routing.py`

- **[reuse/duplication]** linhas 82–100: `_FakeCtx` duplica byte-a-byte `FakeCtx` já existente em `tests/helpers.py:15-32` (que já é importado neste mesmo arquivo para `aconst`). → Substituir por `from helpers import FakeCtx`.

Fora esse ponto, o arquivo está bem desenhado: nomes de teste descritivos, sem BDD, sem `except` genérico, sem `print()`, sem bool posicional. A herança em `_ReadBackend(SEIBackend)` (linha 762) é LSP genuíno — uso legítimo.

### `tests/test_parsers_extra.py`

- **[teste frágil / implementação vazada para o teste]** linhas 975–993 (`test_id_unidade_from_inbox_url`): o teste recalcula o valor esperado chamando a mesma lógica de produção usada para extrair o id, em vez de comparar com um valor literal (`"99"`, já presente na URL construída na linha 985) — torna o teste tautológico. → Trocar a asserção final por um valor literal.

Fora esse ponto, não há violações adicionais: os acessos a atributos privados do `SEIWebClient` são whitebox testing legítimo (o arquivo é dedicado a testar helpers internos de parsing, sem API pública alternativa).

---

## Priorização sugerida

1. **Corrigir o bug de `ConsentRecusadoError` não capturada** (`mcp_app.py`, `tools/documentos.py`) — usuário recusando consentimento hoje vira erro cru, não um payload estruturado.
2. **Revisar o fail-open de SSRF em `auth.py`** quando a resolução DNS falha — hoje é tratado como "não bloqueado" e logado só em debug.
3. **Padronizar exceções com atributos estruturados** — maior volume de ocorrências (`sei_client.py`, `sei_web_client.py`, praticamente todos os backends `rest/`/`web/`), mas mecânico e paralelizável arquivo a arquivo.
4. **Introduzir modelos tipados nas fronteiras de retorno dos backends** (`backends/rest/*.py`, `backends/web/*.py`, `sei_client.py`, `sei_web_client.py`) — é o padrão mais disseminado da codebase; convém tratar por família de domínio (processos, documentos, marcadores, blocos) em vez de arquivo a arquivo, já que o mesmo shape de dado atravessa REST e web.
5. **Decidir o destino de `backends/protocols.py`** (remover ou adotar de fato) antes de investir na correção 4, já que ele duplica as assinaturas de `base.py` sem uso real.
6. **Migrar backends de herança múltipla de mixins para composição** — mudança estrutural maior; fazer depois dos itens tipados acima, pois toca as mesmas classes.
