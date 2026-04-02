# mcp-seipro — Contexto para Claude Code

## O que é

MCP Server genérico para o SEI (Sistema Eletrônico de Informações) via API REST mod-wssei v2.
64 tools cobrindo processos, documentos, tramitação, assinatura, blocos, marcadores, acompanhamento.
Funciona com qualquer instância SEI que tenha o módulo mod-wssei v2 instalado.

## Stack

- Python 3.11+, FastMCP (mcp SDK 1.12+), httpx, BeautifulSoup, markdownify, pdfplumber, pytesseract
- Transport: stdio (local)
- Configuração: variáveis de ambiente (SEI_URL, SEI_USUARIO, SEI_SENHA, SEI_ORGAO)

## Arquivos principais

- `src/mcp_seipro/server.py` — FastMCP server com 64 tools + helpers (_resolver_documento, _resolver_processo)
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

### Limitações conhecidas
- Cancelar assinatura: a função `DocumentoRN::cancelarAssinaturaInternoControlado` existe no core SEI (linha 4026) mas NÃO está exposta na API REST
- `sei_marcar_nao_lido` usa workaround de enviar processo para a própria unidade
- Upload de doc externo: multipart/form-data com campo `anexo`, requer `dataElaboracao`

## Ambientes testados

- Produção: https://sei.antaq.gov.br/sei/modulos/wssei/controlador_ws.php/api/v2
- Treinamento: https://treinamentosei.antaq.gov.br/sei/modulos/wssei/controlador_ws.php/api/v2 (instável)

## Plano futuro

Ver `.claude/plans/roadmap.md` para o plano completo de ecossistema (interface web, mobile, SaaS, plugins).
