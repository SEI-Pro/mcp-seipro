# mcp-seipro — Contexto para Claude Code

## O que é

MCP Server genérico para o SEI (Sistema Eletrônico de Informações) via API REST mod-wssei v2.
~115 tools cobrindo processos, documentos, tramitação, assinatura, blocos, marcadores, acompanhamento, credenciamento, modelos e mais.
Funciona com qualquer instância SEI que tenha o módulo mod-wssei v2 instalado.

## Stack

- Python 3.11+, FastMCP (mcp SDK 1.12+), httpx, BeautifulSoup, markdownify, pdfplumber, pytesseract
- Transport: stdio (local)
- Configuração: variáveis de ambiente (SEI_URL, SEI_USUARIO, SEI_SENHA, SEI_ORGAO)

## Arquivos principais

- `src/mcp_seipro/server.py` — FastMCP server com ~115 tools + helpers (_resolver_documento, _resolver_processo)
- `src/mcp_seipro/sei_client.py` — Cliente REST assíncrono para mod-wssei v2 (auth automática, auto-reauth 401/403)
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

### Limitações conhecidas
- Cancelar assinatura: a função `DocumentoRN::cancelarAssinaturaInternoControlado` existe no core SEI (linha 4026) mas NÃO está exposta na API REST
- `sei_marcar_nao_lido` usa workaround de enviar processo para a própria unidade
- Upload de doc externo: multipart/form-data com campo `anexo`, requer `dataElaboracao`

## Ambientes testados

- Produção: https://sei.antaq.gov.br/sei/modulos/wssei/controlador_ws.php/api/v2
- Treinamento: https://treinamentosei.antaq.gov.br/sei/modulos/wssei/controlador_ws.php/api/v2 (instável)

## Plano futuro

Ver `.claude/plans/roadmap.md` para o plano completo de ecossistema (interface web, mobile, SaaS, plugins).
