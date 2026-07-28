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
- Tools que tinham caminho web: `sei_listar_processos`, `sei_arvore_processo`, `sei_listar_documentos`, `sei_listar_atividades`, `sei_consultar_processo` (híbrida)
- **Desde jun/2026 (SSO Microsoft da ANTAQ) o scraper web não loga mais → essas tools rodam por REST por padrão.** O caminho web virou opt-in via `SEI_WEB_SCRAPER=1` (gate `_web_scraper_enabled()` no server.py). Bug corrigido nesse processo: `listar_atividades` REST exige o param `procedimento` (não `protocolo`)

### List view enxuta de `sei_listar_processos` (para consumo por agente)
- `src/mcp_seipro/shaping.py` (`shape_processo_resumido`) converte o payload bruto da wssei (payload de tela de detalhe, ~28 flags "S"/"N", ciências = 29% dos bytes) numa list view tipada. Reduz ~82% o payload. Spec: `docs/spec_listar_processos.md`, testes: `tests/test_shaping.py`
- **D-1 (crítico):** `usuarioAtribuido`/`unidade` de topo da wssei NÃO é a atribuição na unidade consultada. `atribuido_unidade_atual` é derivado resolvendo por id contra `dadosAbertura.unidades[i]`↔`lista[i]` (paralelos) + regra "aberto só na unidade" + `atributos.unidade`. **Exige `sei_trocar_unidade` antes**, senão vem `null`
- Flags viram boolean; textos passam por `html.unescape`; `prazo` extraído em ISO; hex removido do nome do marcador. `incluir_detalhe=true` reanexa ciências/anotações; `apenas_contar=true` dá contagem barata
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
- **Transporte auto-detectável (multi-órgão), `SEI_TRANSPORT`**:
  - `auto` (PADRÃO) — começa em httpx; ao detectar o desafio do Cloudflare (`_handle_cloudflare` → `_is_cloudflare_challenge`), **escala sozinho** para o browser (Playwright) via `_escalate_to_browser`, re-autentica e refaz a chamada. **Órgãos sem WAF nunca escalam — funciona como sempre funcionou.** Se o Playwright não estiver instalado, levanta `SEICloudflareBlocked` (genérico, sem hardcode de órgão).
  - `httpx` — força httpx (nunca escala). `browser` — força o Chromium desde o início (pula a 1ª tentativa httpx; útil em deploy dedicado a um órgão sabidamente atrás de CF).
  - `browser_transport.py` roteia o REST por um Chromium real que resolve o desafio; serializa chamadas, pesado. Extra `playwright` + `playwright install chromium`; no Railway `--build-arg INSTALL_BROWSER=true`. Testes: `tests/test_transport.py`. Cobre só o SEIClient REST — o SEIWebClient (scraper, opt-in) ainda cai no CF.

### WAF do Cloudflare bloqueia ESCRITA por conteúdo (distinto do desafio)
- Além do Managed Challenge, o Cloudflare da ANTAQ tem **managed rules (WAF) que inspecionam o CORPO do POST**. O `POST /documento/secao/alterar` leva **403 "Attention Required"** quando o HTML das seções casa um padrão do ruleset — ex.: o conteúdo real de um template de Despacho. Payload benigno (mesmo grande, 22KB) passa; por isso "alguns docs editam, outros dão 403"
- É **bloqueio DURO** (`server: cloudflare`, 403, sem `cf-mitigated: challenge`) → o **browser-transport NÃO contorna** (ele passa o *challenge*, não o *managed-rule block*). Detectado por `_is_cloudflare_waf_block` → levanta `SEICloudflareBlocked` com mensagem específica de WAF; não re-autentica à toa
- **Contorno automático** (`sei_editar_secao`): isolado ao vivo que o gatilho é a seção de cabeçalho (brasão em base64) que o SEI **regenera** ao salvar. No 403-WAF, a tool reenvia essa(s) seção(ões) read-only com base64 **vazias** (`_secao_cabecalho_base64` → placeholder), o SEI reconstrói o cabeçalho, e **verifica** relendo (aborta se não regenerou, sem corromper). Resposta traz `_waf_contornado`. Só cobre gatilho em seção regenerável; se estiver no conteúdo do usuário, propaga o erro claro
- **Correção definitiva (lado ANTAQ/infra):** exceção de WAF para `/sei/modulos/wssei/` (ou desabilitar as managed rules nesse path). Verificado ao vivo 01/07/2026 no proc de teste 50300.018905/2018-67
- `_request` agora inclui o **corpo da resposta** no erro HTTP (`_raise_http_with_body`) — antes o `raise_for_status` engolia o `mensagem`/`exception` do wssei (ex.: "Conteúdo do documento incompleto")

### Normalização de entidades + escada de tentativas em `sei_editar_secao`
- **Entidades HTML são desescapadas antes do POST** (`normalizar_entidades_html` em html_utils.py) em TODAS as seções — inclusive nas relidas do próprio SEI, que é onde elas nascem e se acumulam a cada ciclo ler→reenviar. O SEI aceita UTF-8 literal. `&nbsp;` → ` ` (sobrevive ao ISO-8859-1). As **estruturais** (`&lt; &gt; &amp; &quot; &apos;` + numéricas equivalentes) são preservadas — desescapá-las viraria texto em marcação. Validado ao vivo: payload do doc 2953648 saiu com **zero** entidades
- **Escada de tentativas** no 403-WAF: (1) envio normalizado → (2) cabeçalho base64 regenerável neutralizado + verificação de regeneração → (3) falha relatando o que foi tentado. A mensagem antiga afirmava causa raiz não medida ("o gatilho está em conteúdo não-regenerável", "só a exceção de WAF resolve") e induzia a desistir da API; agora `_msg_waf_esgotado` lista as tentativas e os caminhos (dry_run, gravar por partes, simplificar HTML, interface web, exceção de WAF) sem prescrever causa
- **`dry_run=True`** devolve o payload exato (todas as seções normalizadas + bytes por seção + total) sem POST — isola o gatilho numa chamada só
- **`validar_referencias=True`** (padrão) detecta âncora `id="lnkSeiNNNN"` que usa nº SEI em vez do id interno (link morto que só aparece depois). Usa a rota formatada; avisos vão em `_avisos`, nunca bloqueiam a edição
- **`idSecaoModelo` inexistente não vira mais falso sucesso**: antes o POST gravava as seções atuais (no-op) e retornava sucesso. Agora: erro se NENHUM modelo informado existe (com a lista dos disponíveis); aviso em `_avisos` se só alguns

### Resolver nº SEI → id interno sem Solr
- `GET /documento/interno/formatado/consultar/{protocoloFormatado}` (ProtocoloRN::consultarRN0186) resolve **direto no banco** → funciona para documento **recém-criado**, que o Solr ainda não indexou. É a estratégia 1 de `_resolver_documento` (Solr virou a 2, id direto a 3)
- Retorna `idDocumento` (id interno), `nomeDocumento`, `protocolo` (= idProcedimento). NÃO devolve o discriminador I/X — o tipo vem da listagem do processo
- Validado ao vivo em SEI 5.0.4 / wssei 3.0.2: `2953648` → `3242105`

### `sei_listar_documentos`: ordem, paginação e list view
- A listagem do wssei é sempre ASC por sequência e **sem parâmetro de ordem** (`setOrdNumSequencia(ASC)`); `start` é PÁGINA. Para `ordem='desc'` ou offset alto, o cliente pagina o processo inteiro (`listar_documentos_todos`, teto de 25 páginas) e ordena client-side
- A resposta do wssei traz `total` (`getNumTotalRegistros`) — exposto como `total`, com `retornados`/`truncado`
- `resumido=True` (padrão) → id, protocoloFormatado, tipo, tipo_documento, unidade, nome, assinado, cancelado, acesso. O bruto (`resumido=False`) estoura a janela em processos grandes. **Não há data na listagem** — o wssei não a devolve nesse endpoint; use `sei_consultar_documento_interno` (campo `dataElaboracao`)

### Upload de documento externo por base64
- `sei_criar_documento_externo` aceita `arquivo_base64` + `nome_arquivo` (aceita data URI) além de `arquivo_path` — que só serve para arquivos no disco do SERVIDOR onde o MCP roda. `alterar_documento_externo` idem, via `arquivo_bytes`
- `_post_multipart` no cliente dá ao upload o mesmo tratamento de borda/re-auth do `_request` (antes o upload usava `_client.post` cru e engolia Cloudflare/403)
- Teto: `SEI_MAX_UPLOAD_BASE64_MB` (padrão 50 MB). O limite do SEI **não** serve de teto prático — na ANTAQ `SEI_TAM_MB_DOC_EXTERNO` = **5124 MB**

### 403 do WAF ≠ 403 de autenticação
- Os dois chegam como 403 e pedem ações OPOSTAS. `SEIAcessoNegado` (nova exceção) é levantada quando o 403/401 sobrevive à re-autenticação e o corpo é JSON do wssei → é o SEI recusando (unidade/permissão). Se o corpo não é JSON e `server: cloudflare`, vira `SEICloudflareBlocked` (borda)
- As respostas de erro das tools agora trazem `erro_origem` (`cloudflare_waf` | `cloudflare_borda` | `sei_acesso`) e `erro_acao` (`_classificar_erro` no server.py)

### Resolução de id nas tools de seção (número SEI ≠ id interno)
- `sei_listar_secoes` e `sei_editar_secao` agora resolvem via `_resolver_documento` (igual `sei_ler_documento`) — antes tratavam o argumento como id interno cru → passar um `protocoloFormatado` retornava **outro documento silenciosamente** (colisão de namespace: ambos são inteiros de magnitude parecida)
- Ambas ecoam `_documento_resolvido` ({id_documento, nome, protocoloFormatado, idProcedimento}) — `nomeDocumento` (ex.: "Despacho 2949729") é o rótulo mais inequívoco. No `consultar_documento_interno`, o campo `protocolo` é o **idProcedimento** (não o nº do doc), e o nº SEI do doc sai do fim de `nomeDocumento`

### Tool annotations e o gate de aprovação do cliente ("No approval received")
- O "No approval received" é o **gate de aprovação por-ferramenta do CLIENTE** (ex.: Claude.ai), NÃO do código nem do SEI. O servidor não tem flag de "requires confirmation" — só sinaliza via **tool annotations** (hints MCP). Claude.ai **não suporta elicitation** → confirmações têm que ser parâmetro (ex.: `confirmar_acesso_restrito`), nunca prompt interativo
- `_aplicar_tool_annotations()` (fim do server.py) aplica `ToolAnnotations` às ~116 tools por **convenção de nome** (`sei_<verbo>_...`), sem anotar decorators à mão: leitura (`listar/pesquisar/consultar/buscar/ler/arvore/gerar/estilos/resumo/versao/parametros/verificar/historico/sugestao`) → `readOnlyHint=true` (cliente tende a auto-aprovar); `excluir` → `destructiveHint=true`; `alterar/editar` → `idempotentHint=true`; demais escritas → `readOnlyHint=false, destructiveHint=false`. Todas com `openWorldHint=true`. Respeita annotation já declarada no decorator. Teste: `tests/test_annotations.py`
- Ressalva: annotations são *hints* — muitos clientes pedem aprovação em escrita de qualquer forma. Correção garantida do prompt é do lado do usuário: marcar o conector como "Permitir sempre" no Claude.ai

### Estado verificado (atualização) — SEI 5.0.4 / wssei 3.0.2
- **REST login/senha via `/autenticar` VOLTOU A FUNCIONAR.** O bug `ConfiguracaoMdWSSEI not found` (abaixo) foi corrigido pela TI da ANTAQ. No transporte `auto` (padrão) o MCP detecta o CF e escala p/ browser sozinho — validado: token + `versao` {sei:5.0.4, wssei:3.0.2} + 258 unidades
- **O Cloudflare CONTINUA ativo na borda** → httpx puro leva 403, e o modo `auto` escala automaticamente (ou regra de bypass no WAF, ainda pendente)
- **Login do frontend web virou SSO Microsoft (Entra ID).** A página `/sip/login.php` só expõe "Entrar com Microsoft" (`acaoLogin(11,'Microsoft')` → `login_sso.php`); NÃO há mais campos usuário/senha. POST local é rejeitado. → O **scraper web (SEIWebClient) ficou inoperante**; usar REST. O REST `/autenticar` NÃO é afetado (caminho de auth separado, ainda aceita login/senha local)
- Automatizar o login Microsoft via Playwright esbarra em MFA/2FA + Conditional Access (IP de datacenter) — frágil e desnecessário pro core. Se o scraper for mesmo necessário, padrão viável é persistir sessão (`storage_state`) após login manual, não automação headless de senha

### Bug de instalação do wssei no SEI 5 da ANTAQ (lado servidor) — RESOLVIDO
- (Histórico) Pós-migração SEI 5 (jun/2026), `POST /autenticar` retornava **500** mesmo passando o Cloudflare: `Class "ConfiguracaoMdWSSEI" not found` em `MdWsSeiUsuarioRN.php:88` (`getTokenSecret()`)
- Causa: o arquivo de config do módulo (`<raiz>/sei/config/mod-wssei/ConfiguracaoMdWSSEI.php`) não foi recriado na migração — não é versionado (`.gitignore` do pengovbr/mod-wssei), é criado copiando `ConfiguracaoMdWSSEI.exemplo.php` (passo 6 do `docs/INSTALACAO.md`)
- Ocorria ANTES de validar credenciais → "falha com qualquer credencial". Corrigido pela TI da ANTAQ (arquivo recriado). Mantido aqui como referência caso reincida após updates do módulo

### Limitações conhecidas
- **Cancelar/excluir DOCUMENTO não existe na API**: o mod-wssei (conferido no master, 3.x) não tem rota nem RN para isso — as únicas rotas "cancelar" são `/bloco/assinatura/{id}/disponibilizacao/cancelar` e `/processo/{protocolo}/cancelar/sobrestamento`. Uma minuta indesejada só sai pela interface web (ou pelo scraper, hoje inoperante por SSO). Não há como fechar esse ciclo de erro do agente sem mudança no módulo
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
