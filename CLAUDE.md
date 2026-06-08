# mcp-seipro — Contexto para Claude Code

## O que é

MCP Server para o SEI (Sistema Eletrônico de Informações) via API REST mod-wssei v2 + scraper HTTP do frontend web (modo híbrido).
~116 tools cobrindo processos, documentos, tramitação, assinatura, blocos, marcadores, acompanhamento, credenciamento, modelos e mais.
Funciona com qualquer instância SEI que tenha o módulo mod-wssei v2 instalado.

## Stack

- Python 3.11+, FastMCP (mcp SDK 1.12+), httpx, BeautifulSoup, markdownify, pdfplumber, pytesseract
- Transport: stdio (local) ou Streamable HTTP + OAuth (remoto/Railway)
- Configuração: variáveis de ambiente (SEI_URL, SEI_USUARIO, SEI_SENHA, SEI_ORGAO)

## Arquivos principais

- `src/mcp_seipro/server.py` — FastMCP server com ~116 tools + helpers (_resolver_documento, _resolver_processo)
- `src/mcp_seipro/sei_client.py` — Cliente REST assíncrono para mod-wssei v2 (auth automática, auto-reauth 401/403, cache de metadados TTL 1h)
- `src/mcp_seipro/sei_web_client.py` — Cliente HTTP scraper do frontend web do SEI (login SIP, sessão persistente, parser de inbox/árvore/histórico)
- `src/mcp_seipro/html_utils.py` — html_to_text, html_to_markdown, pdf_to_text, pdf_to_markdown (com OCR fallback), sanitize_iso8859
- `src/mcp_seipro/sei_styles.py` — Dicionário de 39 estilos CSS do SEI + helpers (html_referencia_sei, html_destinatario)

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

### Compatibilidade de versão do mod-wssei
- **Todos os ~116 endpoints existem desde o mod-wssei 2.0.0** (SEI 4.0.x)
- Única exceção: `sei_listar_relacionamentos` (`GET /processo/{id}/relacionamentos`) requer **mod-wssei 3.0.2+** (SEI 5.0.x)
- Tabela de compatibilidade SEI ↔ mod-wssei:
  - SEI 4.0.x → mod-wssei 2.0.x (131 endpoints)
  - SEI 4.1.1 → mod-wssei 2.2.0 (131 endpoints, correções de bugs)
  - SEI 5.0.x → mod-wssei 3.0.1 (131 endpoints, compat PHP 8.2)
  - SEI 5.0.x → mod-wssei 3.0.2 (132 endpoints, +relacionamentos)
- Diferenças entre versões são majoritariamente correções de bugs e encoding, não endpoints novos
- v3.0.x corrigiu `iconv()` → `mb_convert_encoding()` para compatibilidade PHP 8.2
- v3.0.2 adicionou campo `dataHora` na resposta de `listar_assinaturas`
- Se um endpoint falhar com erro inesperado, usar `sei_versao` para verificar a versão instalada
- Funcionalidades que dependem do core SEI (ex: credenciamento) podem não funcionar se o órgão não habilitou processos sigilosos

### Arquitetura híbrida REST + Web scraper
- **SEIWebClient** (sei_web_client.py) faz login via formulário SIP, captura `infra_hash` da cadeia de redirects e mantém sessão persistente
- Login web requer enviar `sbmLogin=Acessar` (par name=value do botão submit) — sem ele o backend PHP ignora o POST silenciosamente
- O token CSRF é dinâmico (`hdnToken<hash>`) e precisa ser capturado do GET inicial da página de login
- `infra_hash` é `sha256(params + sessionSecret)` — válido enquanto a sessão SIP viver, reaproveitado entre chamadas
- Visualização Detalhada forçada via POST `hdnTipoVisualizacao=D` no form de procedimento_controlar
- Especificação extraída do `onmouseover` do link do processo (`infraTooltipMostrar('Especificação','Tipo')`) — disponível INDEPENDENTE da configuração de colunas do painel
- Labels de documentos parseados via regex: "Despacho GPF 2874369" → tipo=Despacho, sigla=GPF, numero=2874369
- Tools migradas para web: `sei_listar_processos` (23×), `sei_arvore_processo` (10×), `sei_listar_documentos` (10×), `sei_listar_atividades` (2×)
- Tools híbridas REST+web: `sei_consultar_processo` (REST rich + web documentos[] em paralelo via asyncio.gather)
- `sei_resumo_processos` mantém REST direto (precisa dos flags estruturados de status para agrupamento)
- Cache in-memory TTL 1h no SEIClient para: `pesquisar_tipos_processo`, `listar_unidades_usuario`, `pesquisar_marcadores`

### WAF / Cloudflare (sei.antaq.gov.br) — bloqueio de borda
- Desde a migração SEI 3.1 → 5 (jun/2026), **todo o domínio sei.antaq.gov.br está atrás de um Cloudflare Managed Challenge** (header `cf-mitigated: challenge`, página "Just a moment...", `server: cloudflare`)
- Afeta REST (`/sei/modulos/wssei/...`) E o frontend web (`/sip/login.php`, `/sei/`) — as DUAS metades da arquitetura híbrida
- O 403 vem da BORDA, antes de chegar ao wssei → ocorre com QUALQUER credencial. **Não é erro de login, de versão do mod-wssei nem do MCP** — nenhuma mudança de código resolve
- Browsers passam automaticamente (executam o JS challenge e obtêm cookie `cf_clearance`); clientes httpx/sem-JS levam 403
- Diagnóstico rápido: `.venv/bin/python scripts/diag_conectividade.py`
- Código já detecta e levanta `SEICloudflareBlocked` (REST) / `RuntimeError` claro (web) em vez de 403 opaco
- **Correção definitiva (lado ANTAQ/infra)**: regra de bypass no Cloudflare para `/sei/modulos/wssei/` (e `/sip/login.php` se usar scraper), idealmente com header secreto; depois configurar `SEI_EXTRA_HEADERS`
- Escape hatch temporário/frágil: `SEI_CF_CLEARANCE` (cookie do browser, expira e é atrelado a IP+UA)
- **Contingência — transporte via browser** (`SEI_TRANSPORT=browser`): `src/mcp_seipro/browser_transport.py` roteia o REST por um Chromium real (Playwright), que resolve o desafio sozinho. Provado contornar o CF da ANTAQ (headless). Opt-in, pesado, serializa chamadas. Extra `playwright` + `playwright install chromium`; no Railway, build com `--build-arg INSTALL_BROWSER=true`. Validação: `scripts/diag_browser_transport.py`. Cobre só o SEIClient REST — o SEIWebClient (scraper) ainda cai no CF nesse modo.

### Bug de instalação do wssei no SEI 5 da ANTAQ (lado servidor)
- Pós-migração SEI 5 (jun/2026), `POST /autenticar` retorna **500** mesmo passando o Cloudflare: `Class "ConfiguracaoMdWSSEI" not found` em `MdWsSeiUsuarioRN.php:88` (`getTokenSecret()`)
- Causa: o arquivo de config do módulo (`<raiz>/sei/config/mod-wssei/ConfiguracaoMdWSSEI.php`) não foi recriado na migração — não é versionado (`.gitignore` do pengovbr/mod-wssei), é criado copiando `ConfiguracaoMdWSSEI.exemplo.php` (passo 6 do `docs/INSTALACAO.md`)
- Ocorre ANTES de validar credenciais → "falha com qualquer credencial". **Correção é da TI da ANTAQ** (recriar o arquivo em `/opt/sei/config/mod-wssei/`, preencher o array incl. secret do token, permissão de leitura). Quebra também integrações oficiais

### Limitações conhecidas
- Cancelar assinatura: a função `DocumentoRN::cancelarAssinaturaInternoControlado` existe no core SEI (linha 4026) mas NÃO está exposta na API REST
- `sei_marcar_nao_lido` usa workaround de enviar processo para a própria unidade
- Upload de doc externo: multipart/form-data com campo `anexo`, requer `dataElaboracao`
- Web scraper aborta se detectar CAPTCHA ou 2FA na página de login
- Colunas da Detalhada dependem da configuração do painel do usuário (mas especificação sempre vem do tooltip)
- `sei_listar_documentos` e `sei_arvore_processo` via web não retornam flags de status (assinado, cancelado, etc.) — para isso usar `sei_consultar_documento_externo` ou `sei_consultar_documento_interno` (REST) por documento

## Ambientes testados

- Produção: https://sei.antaq.gov.br/sei/modulos/wssei/controlador_ws.php/api/v2
- Treinamento: https://treinamentosei.antaq.gov.br/sei/modulos/wssei/controlador_ws.php/api/v2 (instável)

## Plano futuro

Ver `.claude/plans/roadmap.md` para o plano completo de ecossistema (interface web, mobile, SaaS, plugins).
