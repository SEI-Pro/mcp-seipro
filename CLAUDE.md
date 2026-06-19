# todos — Contexto para Claude Code

**TOdos Domina O Sei** — MCP Server para o SEI (Sistema Eletrônico de Informações) com arquitetura web-first.
126 tools cobrindo processos, documentos, tramitação, assinatura, blocos, marcadores, acompanhamento, credenciamento, modelos e mais.
Opera via scraper HTTP do frontend web + REST mod-wssei v2 quando disponível. Funciona em qualquer instância SEI 4.0+ — inclusive sem mod-wssei instalado.

## Stack

- Python 3.11+, FastMCP (mcp SDK 1.12+), httpx, BeautifulSoup, markdownify, pdfplumber, pytesseract
- Transport: stdio (local) ou Streamable HTTP + OAuth (remoto/Railway)
- Configuração: variáveis de ambiente (SEI_URL opcional, SEI_USUARIO, SEI_SENHA, SEI_ORGAO)

## Surfaces de tool

### MCP server (`mcp-seipro` / `todos`)

Start: `uv run mcp-seipro` (stdio) ou `PORT=8000 uv run mcp-seipro` (HTTP).

Tools principais — lista parcial das 126:

| Tool | Tipo | Quando usar |
|------|------|-------------|
| `sei_listar_processos` | read | Inbox da unidade — ponto de partida da triagem |
| `sei_resumo_processos` | read | Agrupamento por status; REST-only |
| `sei_consultar_processo` | read | Dados completos + árvore de documentos de um processo |
| `sei_arvore_processo` | read | Apenas a árvore (mais rápido que consultar_processo) |
| `sei_ler_documento` | read | Conteúdo HTML/Markdown de um documento interno |
| `sei_listar_documentos` | read | Lista de documentos de um processo |
| `sei_buscar_documento` | read | Resolve número SEI → id interno via Solr |
| `sei_pesquisar_processos` | read | Pesquisa full-text Solr por palavras-chave |
| `sei_criar_processo` | write | Abre novo processo |
| `sei_criar_documento` | write | Cria documento interno (HTML) |
| `sei_criar_documento_externo` | write | Importa PDF/DOCX para um processo |
| `sei_enviar_processo` | **destructive** | Tramita processo para outra unidade |
| `sei_assinar_documento` | **destructive** | Assina documento — requer certificado PKI |
| `sei_concluir_processo` | **destructive** | Conclui processo na unidade |
| `sei_registrar_andamento` | write | Adiciona entrada no histórico |
| `sei_atribuir_processo` | write | Atribui processo a um usuário |
| `sei_marcar_processo` | write | Aplica marcador colorido |
| `sei_unidade_atual` | read | Informa unidade ativa da sessão |
| `sei_trocar_unidade` | write | Muda a unidade ativa |
| `sei_versao` | read | Versão do mod-wssei instalado; REST-only |

Read tools retornam `_next` com sugestão de próxima chamada.
Write tools retornam `{"ok": True, "_next": [...]}` — execute `_next[0]` para verificar; não assuma sucesso apenas pelo `ok: True`.

### Integração com o pink (Kanoê/Caipora) — repo irmão

O pink é o MCP do sistema de gestão processual. Para usar ambos no mesmo host MCP, adicione as duas entradas no `.mcp.json`:

```json
{
  "mcpServers": {
    "seipro": { "command": "uv", "args": ["run", "--directory", "/caminho/para/todos", "mcp-seipro"], "env": { "SEI_USUARIO": "...", "SEI_SENHA": "..." } },
    "pink":   { "command": "uv", "args": ["run", "--directory", "/caminho/para/pink",  "pink-mcp"],  "env": { "METABASE_USUARIO": "...", "METABASE_SENHA": "..." } }
  }
}
```

Quando ambos estão ativos, é possível ler um expediente do Kanoê que referencia um NUP do SEI, ler o processo no SEI e comentar de volta no expediente com a análise — tudo em uma conversa.

## Fluxo de triagem recomendado

```
sei_listar_processos()             # inbox — o que chegou
  → sei_consultar_processo(nup)    # contexto completo do processo
    → sei_ler_documento(doc_id)    # lê documentos relevantes
    → sei_registrar_andamento(nup) # documenta ação tomada
    → pink: expediente_comentar(id, "<análise>")  # posta no Kanoê
    → sei_enviar_processo(nup, destino)            # tramita se necessário
```

Quando o expediente do Kanoê referencia um NUP (`\d{4}\.\d{6}/\d{4}-\d{2}`), use `sei_consultar_processo(<nup>)` para resolver sem pedir ao usuário.

## Credenciais

| Var de ambiente | Obrigatória | Para quê |
|-----------------|-------------|----------|
| `SEI_USUARIO` | sim | Login do usuário no SEI |
| `SEI_SENHA` | sim | Senha do SEI |
| `SEI_ORGAO` | sim | Sigla do órgão (ex.: `RO`) |
| `SEI_URL` | não | URL do mod-wssei v2; sem ela → modo web-only |
| `SEI_WEB_URL` | não | URL base do SEI web (padrão: inferido de SEI_URL) |
| `TODOS_LOG_LEVEL` | não | Nível de log `todos.*` (padrão: `INFO`) |

Secrets nunca são persistidos pelo servidor. Configure no shell, `.env` ou direnv.

## Operações de escrita — confirmação humana

Tools com `destructiveHint=true` (`sei_enviar_processo`, `sei_concluir_processo`, `sei_assinar_documento`, `sei_assinar_documentos_bloco`, `sei_excluir_bloco_*`) devem solicitar confirmação do usuário antes de executar — os efeitos são imediatos e visíveis para outros usuários da unidade.

Tools de escrita sem `destructiveHint` (`sei_criar_documento`, `sei_registrar_andamento`, `sei_marcar_processo`) criam registros novos sem sobrescrever — menos críticas, mas ainda assim permanentes.

## Exit codes (CLI)

Hosts MCP recebem `ToolError` com a mensagem equivalente — nunca exit codes.

| Código | Significado |
|--------|-------------|
| 0 | Sucesso |
| 1 | Erro inesperado / não classificado |
| 2 | Não encontrado (`SEINotFoundError`) |
| 3 | Erro de autenticação (`SEIAuthError`) |
| 4 | Erro de validação (`SEIValidationError`) |
| 5 | Sem conectividade (`SEIConnectionError`) |

## Arquivos principais

- `src/todos/server.py` — FastMCP server com 126 tools + helpers (`_resolver_documento`, `_resolver_processo`)
- `src/todos/mcp_app.py` — lifespan, pool de sessões, gate de acesso a documentos restritos
- `src/todos/backends/composite.py` — router REST-first com fallback web; ponto central de despacho
- `src/todos/sei_client.py` — Cliente REST assíncrono para mod-wssei v2 (auth automática, auto-reauth 401/403, cache TTL 1h)
- `src/todos/sei_web_client.py` — Scraper HTTP do frontend SEI (login SIP, sessão persistente, parser de inbox/árvore/histórico)
- `src/todos/html_utils.py` — `html_to_text`, `html_to_markdown`, `pdf_to_text`, `pdf_to_markdown` (com OCR fallback)
- `src/todos/sei_styles.py` — 39 estilos CSS do SEI + helpers (`html_referencia_sei`, `html_destinatario`)
- `tests/test_parsers.py` — Testes unitários dos parsers HTML (sem servidor SEI)

## Convenções importantes

### API do SEI
- O `protocoloFormatado` (número SEI que o usuário vê) é DIFERENTE do `id` interno do documento
- A pesquisa Solr (`/processo/pesquisar?palavrasChave=`) funciona em produção para resolver número SEI → processo → id
- Documentos recém-criados podem não estar indexados no Solr ainda
- Paginação usa `start` como número de PÁGINA (0-indexed), não offset
- `listar_usuarios` filtra por unidade com parâmetro `unidade={id}` (a API ignora `filter` para nomes)
- `assuntos` no `criar_processo` precisa ser JSON: `[{"id":"876"}]`
- `alterar_processo` exige TODOS os campos (busca dados atuais primeiro)
- Hipóteses legais com sufixo (S) = sigiloso, sem = restrito

### Estilos CSS do SEI para documentos
- Corpo de Despachos: `Paragrafo_Numerado_Nivel1` (autonumera 1. 2. 3.)
- Títulos de Notas Técnicas: `Item_Nivel1/2/3/4` (≈ H1/H2/H3/H4)
- Alíneas: `Item_Alinea_Letra` — NUNCA escrever a) b) no texto
- Incisos: `Item_Inciso_Romano` — NUNCA escrever I - II - no texto
- Destinatário: `Texto_Alinhado_Esquerda` com span `ancoraSei interessadoSeiPro data-id`
- Referências SEI: span `ancoraSei` com `id="lnkSei{id_documento}"`
- `sei_editar_secao` preenche seções somenteLeitura automaticamente

### Arquitetura web-first
- **SEIWebClient** é o backend primário — faz login via formulário SIP, captura `infra_hash` da cadeia de redirects e mantém sessão persistente
- Login web requer `sbmLogin=Acessar` (par name=value do botão submit) — sem ele o backend PHP ignora o POST silenciosamente
- O token CSRF é dinâmico (`hdnToken<hash>`) e precisa ser capturado do GET inicial da página de login
- `infra_hash` é `sha256(params + sessionSecret)` — válido enquanto a sessão SIP viver, reaproveitado entre chamadas
- Labels de documentos parseados via regex: "Despacho GPF 2874369" → tipo=Despacho, sigla=GPF, numero=2874369
- **`hdnAnexos` encoding**: separador é `±` (U+00B1), encoding ISO-8859-1 como `%B1` — NÃO usar `#`. Construir POST manual (`content=body.encode("ascii")`) para evitar double-encoding pelo httpx
- **`hdnFlagDocumentoCadastro`**: JS `submeter()` muda `'1'→'2'` antes do submit; obrigatório ser `'2'` no POST
- **`hdnFlagProcedimentoCadastro`**: idem para criar/alterar processo — com `'1'` o servidor só re-exibe o form e **não salva** (retorna 200 sem erro, no-op silencioso)
- **Criar/alterar processo**: assuntos vão em `hdnAssuntos` no formato `id±texto` (itens separados por `¥` U+00A5); nível de acesso em `rdoNivelAcesso` (0=público/1=restrito/2=sigiloso)
- Ações acionadas por JS (reabrir, assinar, dar ciência): URLs assinadas declaradas como `var link<Acao>` no HEAD da **página de visualização do nó raiz** (`acao=arvore_visualizar`), NÃO na árvore. Fluxo: `_pagina_visualizacao_processo` → `_link_acao_visualizacao(protocolo, "linkReabrirProcesso")` → GET

### Limitações conhecidas
- Cancelar assinatura: a função `DocumentoRN::cancelarAssinaturaInternoControlado` existe no core SEI mas NÃO está exposta na API REST
- `sei_marcar_nao_lido` usa workaround de enviar processo para a própria unidade
- Web scraper aborta se detectar CAPTCHA ou 2FA na página de login
- `sei_listar_documentos` e `sei_arvore_processo` via web não retornam flags de status (assinado, cancelado) — use `sei_consultar_documento_*` (REST) por documento
- Atribuições NÃO aparecem no histórico resumido — usar `historico_atribuicoes` (POST `hdnTipoHistorico='P'`)

### Compatibilidade mod-wssei
- Todos os 126 tools funcionam em qualquer SEI 4.0+ sem mod-wssei (web-only)
- Única exceção REST-only: `sei_listar_relacionamentos` requer mod-wssei 3.0.2+ (SEI 5.0.x)
- Se um endpoint falhar com erro inesperado, use `sei_versao` para verificar a versão instalada

## Integração com o pink (Kanoê/Caipora)

| Tool pink | Uso típico junto ao todos |
|-----------|--------------------------|
| `pasta_show` | Lê resumo estratégico antes de consultar o SEI (`sei_enrich=True` adiciona `_sei_refs`) |
| `expediente_comentar` | Posta análise/ação após `sei_consultar_processo` |
| `expediente_criar` | Cria controle de prazo (ED: 5 dias úteis Juizado/TR, 10 demais) |
| `inbox` | Lista processos não-recebidos; `full=True` traz teor dos expedientes |
| `hoje` | Triagem diária — expedientes e tarefas com prazo no horizonte |
| `pasta_mover` | Move pasta para caixa após triagem |
| `tarefa_criar` | Cria tarefa de acompanhamento |

**Convenções do pink:**
- `_next` após escrita — execute `_next[0]` para verificar; não assuma sucesso só pelo `ok: True`
- `fresh=True` — bypass de cache após escrita imediata
- `raw_html=True` — `texto`/`teor` já contém HTML; sem o flag o texto é escapado
- `mark=False` — suprime marcador visual rosa (RFC 0012)
- `cursor`/`proximo_cursor` — paginação
- Prazo: formato `YYYY-MM-DD` obrigatório; sessão virtual → criar expediente de controle de destaque/sustentação oral (até 48h antes)

**Tipos de expediente pink** (`tipo` em `expediente_criar`): 0=OUTROS, 1=INTIMACAO, 2=CITACAO, 3=RETORNO_PROGRAMADO, 4=NOTIFICACAO

## Qualidade de código

### Regras absolutas — paridade CLI ↔ MCP
- **Toda operação disponível no CLI deve ter uma tool MCP equivalente.** Se algo só é possível via CLI, a tool MCP está faltando — adicione-a.
- O inverso não é obrigatório: tools MCP de leitura interna (status, sessão) não precisam de comando CLI.
- **A implementação fica na camada de serviço/backend**, não no handler CLI ou na tool MCP. Ambos chamam a mesma função — sem lógica duplicada.

### Regras absolutas — ruff
- **Proibido `# noqa`** — nunca suprima uma violação com `# noqa` ou `# type: ignore`. Se o ruff sinalizar, corrija o padrão.
- **Proibido descartar** — nunca classifique uma violação como "puramente estilística" ou "não se aplica a CLIs". Toda violação é um code smell.
- **Verificação obrigatória** — após qualquer edição em Python, rode `uv run ruff check .` e `uv run ruff format --check .` antes de encerrar o turno.
- **Formatação** — rode `uv run ruff format .` se houver divergência; nunca ajuste manualmente o estilo.

### Regras absolutas — tratamento de erros
- **Proibido engolir erros** — nunca `except ... pass`, `except ... continue` ou `suppress(Exception)` sem logar. Erros devem propagar ou ser logados com `logger.warning`/`logger.error`.
- **`suppress` só com tipos estreitos** — `suppress(httpx.TransportError, OSError)` é aceitável para cleanup; `suppress(Exception)` nunca.
- **`return` default silencioso é erro** — funções que retornam `None`/`[]`/`{}` ao capturar exceção devem logar antes; o chamador não consegue distinguir "não encontrado" de "falhou".
- **`logger.debug` para erros reais é invisível em produção** — use `warning` para falhas reais (parsing, HTTP error, estado inesperado); `debug` só para "feature não suportada" ou fluxo normal.

### Logging
- Logger `todos.*` configurado via `fastmcp.utilities.logging.configure_logging` em `mcp_app.py` — mesmo RichHandler do FastMCP.
- Nível: `TODOS_LOG_LEVEL` → `FASTMCP_LOG_LEVEL` → `INFO`.
- Use `logging.getLogger(__name__)` em todos os módulos. Nunca `print()` para diagnóstico.

### Receitas para violações comuns

| Violação | Solução correta |
|---|---|
| `BLE001` blind `except Exception` | Use exceções específicas: `httpx.HTTPError`, `OSError`, `RuntimeError`, `ValueError`, etc. |
| `T201` `print()` | `sys.stdout.write(...)` para saída normal; use logger para diagnóstico |
| `FBT001/002` bool posicional | Adicione `*` antes do parâmetro bool para forçar keyword-only |
| `C901/PLR0912/PLR0915` complexidade | Extraia funções auxiliares; responsabilidade única |
| `SLF001` acesso a `_privado` | Adicione propriedade pública ao objeto |
| `PLR2004` magic value | Declare constante nomeada no topo do módulo |
| `ANN` annotation faltando | Anote todos os parâmetros e retornos; use `X \| None` em vez de `Optional[X]` |
| `UP` sintaxe legada | `list[str]` não `List[str]`; `X \| None` não `Optional[X]`; `datetime.UTC` não `timezone.utc` |
| `TC001/TC002` import de tipo | Mova para bloco `if TYPE_CHECKING:` |
| `D1xx` docstring faltando | Toda função/classe pública precisa de docstring (uma linha basta) |
| `S110/S112` except+pass | `suppress(ExcType)` com tipo **estreito**; logar se for erro real |
| `PERF401` loop com append | List comprehension ou `list.extend(...)` |
| `ERA001` código comentado | Delete — histórico fica no git |

## Ambientes testados

- Produção ANTAQ: `https://sei.antaq.gov.br/sei/modulos/wssei/controlador_ws.php/api/v2`
- SEI-RO (sem mod-wssei): `https://sei.sistemas.ro.gov.br` — web-only, funciona com SEIWebClient

## Paridade web — implementado

Fases 1–11 concluídas. Ver `docs/rfc/0001-web-first.md` para histórico e lista de tools permanentemente REST-only (assinatura PKI, credenciamento, `sei_versao`, `sei_resumo_processos`, `sei_listar_relacionamentos`).
