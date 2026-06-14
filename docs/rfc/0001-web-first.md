# RFC 0001 — Web-first: paridade funcional para instâncias SEI sem mod-wssei

**Status**: Concluído
**Data**: 2026-06-11 · **Atualizado**: 2026-06-13
**Autores**: Franklin Baldo (com Claude Code)

## 0. Estado atual (2026-06-13)

Implementação completa. Todas as fases (PRs #2 a #16) estão **concluídas**.

### ✅ Concluído

| Fase | Conteúdo | PR |
|---|---|---|
| Fundações | `executar_acao_processo`, `obter_form_acao`, `SEIBackend`, `smoke_web.py` | merged |
| Ações simples | `concluir`, `reabrir`, `dar_ciencia`, `receber`, `remover_atribuicao` | merged |
| Forms com campos | `registrar_andamento`, `criar_anotacao`, `marcar_processo`, `sobrestar`, `atribuir`, `acompanhar` | merged |
| Read scrapers | `consultar_documento_web`, `listar_assinaturas_web`, `listar_ciencias_web`, `visualizar_documento_interno_web`, `baixar_documento_externo_web`, `consultar_processo_detalhe` | merged |
| Forms complexos | `enviar_processo_web`, `criar_processo_web`, `criar_documento_interno_web`, `incluir_documento_externo` | merged |
| Read fallbacks | `listar_unidades_processo`, `listar_interessados`, `listar_sobrestamentos` via `consultar_processo_detalhe` | merged |
| Fase 6 | `sei_listar_usuarios`, `sei_pesquisar_tipos_processo`, `sei_pesquisar_blocos_assinatura` | #16 |
| Fase 7 | `sei_pesquisar_hipoteses_legais`, `sei_pesquisar_tipos_documento`, `sei_pesquisar_tipos_conferencia`, `sei_pesquisar_marcadores` | #16 |
| Fase 8 | `sei_pesquisar_outras_unidades`, `sei_criar_bloco_assinatura`, `sei_disponibilizar_bloco_assinatura`, `sei_cancelar_disponibilizacao_bloco` | #16 |
| Fase 9 | `sei_pesquisar_assuntos`, `sei_pesquisar_textos_padrao`, `sei_consultar_atribuicao` | #16 |
| Fase 10 | `sei_concluir_bloco_assinatura`, `sei_excluir_bloco_assinatura`, `sei_reabrir_bloco_assinatura`, `sei_retornar_bloco_assinatura`, `sei_listar_documentos_bloco_assinatura`, `sei_alterar_bloco_assinatura` | #16 |
| Fase 11 | `sei_pesquisar_usuarios`, `sei_pesquisar_tipos_documento_externo`, `sei_verificar_acesso`, `sei_listar_meus_acompanhamentos`, `sei_listar_acompanhamentos_unidade`, `sei_alterar_acompanhamento`, `sei_listar_grupos_modelos`, `sei_listar_modelos`, `sei_retirar_documentos_bloco_assinatura`, `sei_anotar_documento_bloco_assinatura`, `sei_alterar_anotacao_bloco_assinatura` | #16 |

**121 tools** no total — **~80 com fallback web** no caminho de execução.

### 🔒 Permanentemente REST-only (sem plano de web)

Ver §4.6. Em instância sem mod-wssei, essas tools retornam erro explicativo.

## 1. Problema

O todos hoje é REST-first: ~105 das 116 tools dependem da API mod-wssei v2.
Em instâncias que não têm o módulo instalado — caso real: **SEI-RO**
(`sei.sistemas.ro.gov.br`, 404 em `/modulos/wssei/.../autenticar`) — essas tools
falham na autenticação antes de qualquer operação.

O mod-wssei é um módulo opcional. A maioria dos órgãos estaduais e municipais
não o instala. O frontend web, por outro lado, **existe em 100% das instâncias**
— é o próprio SEI. Quem scrapeia o frontend atende qualquer instância.

A prova de conceito já existe no projeto: 8 tools usam o `SEIWebClient`
(listar_processos 23× mais rápido, arvore 10×, upload de documento externo
funcionando em SEI-RO onde o REST nem autentica, além de gerar_pdf_processo
e gerar_zip_processo — scraping dos forms `frmProcedimentoPdf/Zip`).

## 2. Objetivo

Tornar o web scraper um **backend completo**, não um acelerador do REST:

1. Toda tool de uso cotidiano funciona em instância sem mod-wssei.
2. O REST passa a ser otimização opcional (quando disponível), não dependência.
3. Detecção automática de capacidade: o servidor descobre no startup o que a
   instância suporta e roteia cada tool para o melhor backend.

Fora de escopo: assinatura digital (PKI no navegador), pesquisa Solr completa,
funções administrativas (órgãos, contextos).

**Objetivo atingido** — todas as tools de fluxo cotidiano têm fallback web.

## 3. Fundamento técnico — o padrão universal do SEI

Tudo que aprendemos implementando `incluir_documento_externo` (e validado
contra o código da extensão [SEI Pro](https://github.com/SEI-Pro/sei-pro), que
faz scraping client-side há anos em dezenas de instâncias) converge para um
único padrão:

```
1. GET procedimento_trabalhar?id_procedimento=X   (URL pré-assinada da inbox/pesquisa)
2. GET arvore (iframe ifrArvore) → extrair Nos[0].acoes (JS com HTML embutido)
3. Achar o link da ação desejada pelo padrão acao=<acao> no href
   (o link já vem assinado com infra_hash válido para a sessão)
4. GET a página do form
5. POST o form com hidden fields + campos preenchidos
6. Validar sucesso pela URL final do redirect (ex.: acao=arvore_visualizar)
```

A diferença entre "concluir processo" e "enviar processo" é só **qual `acao=`
buscar e quais campos preencher**. O `SEIWebClient` já implementa os passos
1–2; falta generalizar 3–6.

### Implementações de referência

Três projetos open-source documentam o comportamento real do SEI sem depender de documentação oficial:

| Repo | Linguagem | O que tem |
|---|---|---|
| [SEI-Pro/sei-pro](https://github.com/SEI-Pro/sei-pro) | JavaScript (browser ext) | `procedimento_enviar`, `procedimento_concluir`, `procedimento_ciencia`, `documento_assinar`, `editor_montar`; padrão completo de extração de `[type=hidden]` + POST ISO-8859-1 |
| [jonatasrs/sei](https://github.com/jonatasrs/sei) | JavaScript (browser ext) | `anotacao_registrar`, `documento_receber`, `bloco_*`; nomes dos prefixos de campo (`hdn*`, `txa*`, `txt*`, `sel*`, `rdo*`, `chk*`, `sbm*`) |
| [pengovbr](https://github.com/pengovbr) | PHP (SEI source) | Controladores PHP — nomes dos arquivos mapeiam diretamente para `acao=` (ex.: `ProcedimentoConcluirController.php` → `acao=procedimento_concluir`) |

**Fluxo de pesquisa por ação**: para implementar uma nova ação, buscar o `acao=` no pengovbr para entender o PHP, depois ver no SEI-Pro/SEI++ quais campos o form exige.

### Invariantes descobertos (hard-won, documentar para não redescobrir)

| Invariante | Detalhe |
|---|---|
| `hdnInfraTipoPagina` | Token de estado de página que **deve ser capturado do GET e incluído no POST** de cada form — análogo a um CSRF token de estado. Ausente → PHP trata como reload. `_extrair_submit_btn` + extração de todos os `[type=hidden]` resolve automaticamente. |
| Botão submit no login | O PHP exige o par `name=value` do botão submit no POST; sem ele ignora o form silenciosamente. O nome **varia por instância** (`sbmLogin=Acessar` na ANTAQ, `sbmAcessar=ACESSAR` no SEI-RO) — detectar dinamicamente do form |
| Checkboxes | Enviar como `"on"` / `"off"` (string), não booleano. Ausente no POST = desmarcado para o PHP. |
| Token CSRF dinâmico | `hdnToken<hash>` capturado do GET da página de login — nunca reutilizar entre sessões |
| `infra_hash` | sha256(params+secret) da sessão; links de `Nos[0].acoes` já vêm assinados; reutilizável entre chamadas da mesma sessão |
| Encoding | Backend é ISO-8859-1; decodificar com `iso-8859-1` e POSTar idem — UTF-8 corrompe acentos |
| `hdnFlagDocumentoCadastro` | JS `submeter()` muda `'1'→'2'` antes do submit; sem isso o POST é tratado como reload |
| `hdnAnexos` | Separador é `±` (U+00B1) URL-encoded como `%B1` (ISO-8859-1), **não** `#`; não pode ser duplo-codificado (o byte alto UTF-8 `%C2` quebra o split no PHP); campos: `nome_upload±nome±data_hora±tamanho±tamanho_fmt±cpf±sigla_unidade` |
| Radio buttons | `rdoNivelAcesso` e `rdoFormato` precisam ir no POST (browsers só enviam se checked) |
| Usuario/unidade p/ anexo | Extraídos do literal JS `objTabelaAnexos.adicionar([..., 'CPF', 'SIGLA'])` |
| Visualização Detalhada | Requer `hdnTipoVisualizacao=D` no POST do form `procedimento_controlar` |
| Sucesso de POST | Verificar `acao=arvore_visualizar` (ou equivalente) na URL final, não status 200 |
| Catálogos via AJAX | `controlador_ajax.php?acao_ajax=<nome>&termo=<filtro>` retorna JSON `[{id, nome, ...}]`; nomes: `usuario_auto_completar`, `assunto_auto_completar`, `texto_padrao_auto_completar`, `unidade_auto_completar` |
| Blocos: URL assinada | Ação em bloco específico requer encontrar URL com `acao=<acao>&id_bloco=<id>&infra_hash=<hash>` na página `bloco_assinatura_listar` — o hash é único por bloco/sessão |

## 4. Implementação entregue

### 4.1 Camada de roteamento por capacidade (`SEIBackend`)

> **⚠️ Superado pela [RFC 0006](0006-backend-abstrato.md).** O facade descrito
> abaixo (`backend.has_rest` + `backend.rest`/`backend.web`) foi o primeiro corte
> do roteamento e está sendo substituído pelo `CompositeBackend` (contrato único,
> decisão de roteamento em um lugar, `SEINotImplementedError` tipado). O
> *comportamento* web-first desta RFC é preservado integralmente; muda só o
> mecanismo. Veja a RFC 0006 §3 e §7.

Facade original (histórico):

```python
backend = _get_backend(ctx)   # retorna SEIBackend com .rest e .web
if backend.has_rest:
    result = await backend.rest.method(...)
else:
    result = await backend.web.method_web(...)
```

`_get_backend` detecta no startup se mod-wssei responde (via GET `/api/v2/versao`
ou `/autenticar`). 404 → `has_rest=False`; tudo roteia para web.

Novo mecanismo (RFC 0006): a tool chama `backend.<op>(protocolo)` e o
`CompositeBackend` decide REST-first com fallback web, levantando
`SEINotImplementedError` quando nenhum backend suporta a operação. A lista de
tools "permanentemente REST-only" (§4.4) corresponde às ops que o
`SEIWebBackend` herda como stub `NotImplementedError`.

### 4.2 Helpers genéricos implementados

| Helper | Localização | O que faz |
|---|---|---|
| `executar_acao_processo` | `SEIWebClient` | trabalhar → arvore → Nos[0].acoes → link acao= → GET form → POST |
| `obter_form_acao` | `SEIWebClient` | Retorna `{campos, selects, textareas}` de qualquer form de processo |
| `_obter_link_toolbar` | `SEIWebClient` | URL assinada de ação na toolbar (não dependente de processo) |
| `_autocomplete_ajax` | `SEIWebClient` | Wrapper genérico para `controlador_ajax.php` |
| `_obter_acao_bloco_url` | `SEIWebClient` | URL assinada de ação em bloco específico |
| `_executar_acao_bloco` | `SEIWebClient` | Executa ação simples de bloco via GET |
| `_extrair_erro_sei` | módulo | Extrai mensagem de erro de `alert()`/`infraMsg` no HTML |
| `_extrair_submit_btn` | módulo | Captura par `name=value` do botão submit do form |

### 4.3 Técnicas web por categoria de tool

| Categoria | Técnica | Exemplos |
|---|---|---|
| Ações de processo | `executar_acao_processo` | concluir, reabrir, enviar, dar_ciencia, atribuir |
| Catálogos de form | Scrape de `<select>` via toolbar | tipos_processo, hipoteses_legais, marcadores |
| Catálogos AJAX | `_autocomplete_ajax` | pesquisar_usuarios, pesquisar_assuntos, pesquisar_outras_unidades |
| Blocos (listagem) | Scrape de `bloco_assinatura_listar` | pesquisar_blocos_assinatura |
| Blocos (ações) | `_obter_acao_bloco_url` + GET | disponibilizar, concluir, excluir, retirar_documento |
| Blocos (forms) | GET detail → POST | criar_bloco, alterar_bloco, anotar_documento_bloco |
| Leitura de dados | Scrape de página de detalhe | consultar_processo, listar_assinaturas, arvore_processo |
| Verificação de acesso | `_garantir_link_trabalhar` | verificar_acesso |
| Acompanhamento | `executar_acao_processo` + scrape | alterar_acompanhamento, listar_meus_acompanhamentos |

### 4.4 Ferramentas permanentemente REST-only

Por restrição técnica ou escopo (PKI server-side, API admin, cross-nav complexa):

| Tool | Razão |
|---|---|
| `sei_assinar_documento`, `sei_assinar_bloco` | PKI — chave privada no browser/token do usuário |
| `sei_cancelar_assinatura` | Função `cancelarAssinaturaInternoControlado` existe no core SEI mas não está exposta via web nem REST |
| `sei_versao` | Endpoint REST puro sem equivalente web |
| `sei_listar_orgaos`, `sei_listar_contextos` | APIs de administração do SIP |
| `sei_listar_credenciamentos` e família | Processos sigilosos — funcionalidade desativável por órgão; scraping dependeria de UI que pode não existir |
| `sei_listar_relacionamentos` | Requer mod-wssei ≥ 3.0.2; sem equivalente no frontend SEI 4.x |
| `sei_sugestao_assuntos_documento` | Requer cross-navegação tipo→assuntos via API; sem page equivalente no web |
| `sei_listar_blocos_documento` | Cross-navegação: precisaria buscar em todos os blocos o documento — alto custo |
| `sei_alterar_processo` | Form com muitos campos interdependentes e validação client-side complexa |

### 4.5 Robustez

- **Smoke test por instância**: `scripts/smoke_web.py` — login + ações de cada
  categoria; critério de aceite de cada fase (variações de versão SEI 4.0/4.1/5.0).
- **Testes unitários**: `tests/test_parsers.py` — testa parsers HTML com
  amostras sem precisar de servidor SEI (ver §4.6).
- **Erro legível**: todo POST valida URL final e extrai `alert()`/`infraMsg`
  via `_extrair_erro_sei` — erro real do SEI é propagado.
- **Re-login automático**: detectado por `txtUsuario` na resposta; re-autentica
  e repete a requisição.
- **Graceful fallback**: tools AJAX com filtro obrigatório retornam
  `{"_aviso": "filtro obrigatório"}` em vez de erro.

### 4.6 Testes unitários de parsers

```bash
uv run --with pytest pytest tests/
```

Cobrindo:
- `_parse_doc_label` — formato "Tipo SIGLA NUMERO" e "Tipo (NUMERO)"
- `_parse_acompanhamento_tabela` — tabela vazia, limite, linha sem link
- `parse_inbox` — layout detalhada, resumida, HTML vazio
- `parse_arvore_nos` — JS vazio, entrada inválida

## 5. Variáveis de ambiente

| Variável | Obrigatoriedade | Descrição |
|---|---|---|
| `SEI_URL` | Opcional | URL REST mod-wssei (ex: `https://sei.org.gov.br/sei/modulos/wssei/.../api/v2`). Quando presente, `sei_root` é derivado dela |
| `SEI_WEB_URL` | Obrigatória se `SEI_URL` ausente | Raiz web do SEI (ex: `https://sei.org.gov.br`). Tem precedência sobre a derivação de `SEI_URL` |
| `SEI_USUARIO` | Obrigatória | Usuário SEI/SIP |
| `SEI_SENHA` | Obrigatória | Senha SEI/SIP |
| `SEI_ORGAO` | Opcional (padrão: `0`) | ID do órgão na API REST |
| `SEI_SIGLA_ORGAO` | Opcional (padrão: `ANTAQ`) | Sigla do órgão no `selOrgao` do SIP |
| `SEI_SIGLA_SISTEMA` | Opcional (padrão: `SEI`) | Parâmetro `sigla_sistema` na URL de login SIP |
| `SEI_SIGLA_ORGAO_SISTEMA` | Opcional (padrão: `SEI_SIGLA_ORGAO`) | Parâmetro `sigla_orgao_sistema` na URL de login SIP (ex: `RO`) |
| `SEI_VERIFY_SSL` | Opcional (padrão: `true`) | Verificação de certificado SSL |

## 6. Alternativas consideradas

1. **Pedir instalação do mod-wssei ao órgão** — fora do nosso controle; meses
   ou nunca. Rejeitada como única via.
2. **Automação por navegador (Playwright)** — resolve JS de verdade (CKEditor,
   autocompletes), mas: pesado (Chromium por sessão), frágil em headless
   server, latência 5–10× maior que httpx. Mantida como **último recurso**
   para o que o scraping puro não alcançar.
3. **Implementar o protocolo SOAP legado (SeiWS)** — existe em mais instâncias
   que o mod-wssei, mas exige `SiglaSistema` + chave de integração concedidas
   pelo administrador do órgão. Vale investigar como **terceiro backend**
   (usuários que conseguem a chave ganham operações server-side estáveis),
   mas não substitui o web para o usuário comum.

## 7. Riscos

| Risco | Mitigação |
|---|---|
| HTML muda entre versões do SEI | Gate por versão + smoke test + parsers tolerantes (regex âncora em `acao=`, não em layout) |
| CAPTCHA/2FA no login | Já detectado e abortado com erro claro; sem workaround (correto) |
| Rate limiting / bloqueio | Sessão única persistente, sem paralelismo agressivo; comportamento idêntico a um usuário no navegador |
| Ações destrutivas erradas | Validação de protocolo feita antes do POST |
| Encoding corrompe acentos | Padrão ISO-8859-1 em todo o pipeline já estabelecido |

## 8. Métricas de sucesso

- **M1** ✅: 100% das tools de fluxo cotidiano (listar, consultar, concluir,
  reabrir, andamento, ciência, anotação, marcador, upload externo, enviar)
  funcionando em SEI-RO.
- **M2** ✅: zero regressão nas instâncias com REST (ANTAQ) — `smoke_web.py`
  passa contra a ANTAQ antes e depois de cada fase.
- **M3** ✅: tempo médio por operação web ≤ 2 s (hoje: 0.6–1.5 s nas migradas).

## 9. Decisões

1. **`sei_executar_acao` como tool MCP genérica: SIM, com dry-run por padrão.**
   `confirmar=False` é o default — a tool retorna os campos do form sem POSTar;
   o POST exige `confirmar=True` explícito na chamada. Equilibra o poder de
   alcançar ações não mapeadas com proteção contra POST destrutivo acidental.
   (Decidido em 2026-06-11.)
2. **Cache de catálogos: disco** (`~/.cache/todos/`, TTL 24h). O Claude Desktop
   reinicia o servidor MCP com frequência; memória obrigaria re-scrape de
   selects com 400+ opções a cada restart. (Decidido em 2026-06-11.)
3. **SOAP SeiWS: descoberta sim, invocação não (sem chave).** Esclarecimento
   importante: a autenticação SOAP (`SiglaSistema` + `IdentificacaoServico`) é
   **sistema-a-sistema**, validada por chamada contra o cadastro do SIP — é um
   plano de autenticação separado da sessão de usuário. **Login web não
   contorna a chave.** Porém o WSDL costuma ser servido sem autenticação (a
   chave só é validada ao invocar operações), então ele serve para
   **enumerar operações e schemas de parâmetros** — útil para mapear capacidade
   da instância. **Spike realizado em 2026-06-11** — WSDL do SEI-RO confirmado
   em `https://sei.sistemas.ro.gov.br/sei/ws/SeiWS.php`. Resultado:

   | Aspecto | Detalhe |
   |---|---|
   | Operações | **67** (vs ~131 do mod-wssei REST) |
   | Protocolo | SOAP 1.1, `rpc/encoded` — estilo antigo, `zeep` deprecou suporte a `encoded`; exige plugin |
   | Auth | `SiglaSistema` + `IdentificacaoServico` em cada chamada; sem endpoint de sessão |
   | Operações únicas vs REST | Upload chunked (`adicionarArquivo` + `adicionarConteudoArquivo`), `lancarAndamento` com `IdTarefa`, `enviarEmail`, `registrarOuvidoria`, `bloquearProcesso`, controle de prazo (3 ops), catálogos geo (países/estados/cidades) |
   | Sem equivalente no SOAP | Pesquisa Solr, assinatura digital, listagem inbox/painel |

   **Conclusão do spike**: 67 operações cobrem os mesmos fluxos cotidianos que o web scraper
   almeja para Fases 1–3. Invocação como backend 3 só se/quando houver chave concedida pelo
   órgão — não substitui o web para o usuário sem chave. Ao receber chave, implementação
   exigiria client SOAP customizado (não `zeep` puro) devido ao binding `rpc/encoded`.
