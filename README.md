# mcp-seipro

[![PyPI](https://img.shields.io/pypi/v/mcp-seipro)](https://pypi.org/project/mcp-seipro/)
[![Python](https://img.shields.io/pypi/pyversions/mcp-seipro)](https://pypi.org/project/mcp-seipro/)
[![License](https://img.shields.io/pypi/l/mcp-seipro)](https://pypi.org/project/mcp-seipro/)

MCP Server do **[SEI Pro](https://sei-pro.github.io/sei-pro/)** para o SEI (Sistema Eletrônico de Informações) via API REST mod-wssei v2 + scraper do frontend web (modo híbrido).

**116 tools** para gerenciar processos, documentos, tramitação, assinatura, blocos, marcadores, acompanhamento, credenciamento, modelos e mais em qualquer instância do SEI. Cobertura completa da API mod-wssei v2 oficial ([pengovbr/mod-wssei](https://github.com/pengovbr/mod-wssei)) **mais um scraper HTTP do frontend web** que dá ganhos de até **23×** em operações de listagem (`sei_listar_processos` cai de ~14 s para ~600 ms warm).

## Instalação

### Opção 1: Claude Desktop (extensão com um clique)

Baixe o arquivo [`seipro.mcpb`](https://github.com/sei-pro/mcp-seipro/releases/latest) e abra com duplo-clique. O Claude Desktop instala automaticamente e pede suas credenciais.

### Opção 2: PyPI (pip)

```bash
pip install mcp-seipro
```

### Opção 3: Instalador interativo

```bash
git clone https://github.com/sei-pro/mcp-seipro.git
cd mcp-seipro
python3 setup_claude.py
```

O script pergunta suas credenciais, instala o pacote e configura o Claude Desktop automaticamente.

## Configuração

### Variáveis de ambiente

| Variável | Obrigatória | Descrição |
|----------|-------------|-----------|
| `SEI_URL` | Sim | URL base da API mod-wssei v2 |
| `SEI_USUARIO` | Sim | Usuário para autenticação |
| `SEI_SENHA` | Sim | Senha para autenticação |
| `SEI_ORGAO` | Sim | Código do órgão |
| `SEI_CONTEXTO` | Não | Contexto opcional |
| `SEI_VERIFY_SSL` | Não | `true` (padrão) ou `false` |
| `SEI_OCR_LANG` | Não | Idioma do OCR (padrão: `por`) |
| `SEI_PERMITIR_RESTRITOS` | Não | `false` (padrão) ou `true`. Ver "Privacidade e dados restritos" |

> **Dica: como obter `SEI_URL` e `SEI_ORGAO` direto pelo SEI**
>
> Na barra lateral do SEI (menu à esquerda), role até o final — você verá um QR Code para o aplicativo móvel. Esse QR Code contém um link com todas as informações necessárias:
>
> ```
> https://sei.orgao.gov.br/sei/modulos/wssei/controlador_ws.php/api/v2;siglaorgao: ORGAO;orgao: 0;contexto:
> ```
>
> - **`SEI_URL`** — a URL antes do `;` (ex: `https://sei.orgao.gov.br/sei/modulos/wssei/controlador_ws.php/api/v2`)
> - **`SEI_ORGAO`** — o valor após `orgao:` (ex: `0`)
>
> Você pode escanear o QR Code com a câmera do celular para copiar o link, ou simplesmente anotar os dados a partir do menu.

### Registro no Claude Code

Adicione ao `.mcp.json` do projeto ou `~/.claude.json` (global):

```json
{
  "mcpServers": {
    "seipro": {
      "command": "mcp-seipro",
      "env": {
        "SEI_URL": "https://sei.orgao.gov.br/sei/modulos/wssei/controlador_ws.php/api/v2",
        "SEI_USUARIO": "seu.usuario",
        "SEI_SENHA": "sua-senha",
        "SEI_ORGAO": "0"
      }
    }
  }
}
```

### Registro no Claude Desktop (manual)

Edite `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS):

```json
{
  "mcpServers": {
    "seipro": {
      "command": "mcp-seipro",
      "env": {
        "SEI_URL": "https://sei.orgao.gov.br/sei/modulos/wssei/controlador_ws.php/api/v2",
        "SEI_USUARIO": "seu.usuario",
        "SEI_SENHA": "sua-senha",
        "SEI_ORGAO": "0"
      }
    }
  }
}
```

## Exemplos de uso

Com o MCP SEI Pro configurado, basta conversar com o Claude em linguagem natural:

### Consultas

- *"O que diz o processo 50300.018905/2018-67?"*
- *"Leia o documento SEI 2843449 e me faça um resumo"*
- *"Qual foi o último andamento do processo de Auditoria TCU que está na unidade GPF?"*
- *"Liste para mim os processos da caixa GPF no SEI"*
- *"Quais processos estão atribuídos a mim na unidade SFC?"*

### Ações

- *"Crie um despacho no processo 50300.001234/2024-01 aprovando o pedido"*
- *"Tramite o processo 50300.005678/2024-02 para a unidade SFC com prazo de 5 dias"*
- *"Assine todos os documentos do bloco de assinatura 'Contratos Março'"*
- *"Marque o processo como acompanhamento especial com o grupo 'Urgentes'"*
- *"Crie um marcador vermelho chamado 'Pendente Resposta' e aplique no processo"*

### Análise

- *"Me dê um resumo dos processos da minha caixa agrupados por tipo"*
- *"Quais processos da unidade GPF estão sem movimentação há mais de 30 dias?"*
- *"Compare o conteúdo dos documentos 2843449 e 2843450"*

## Tools disponíveis (116)

### Sistema e metadados (3)

| Tool | Descrição |
|------|-----------|
| `sei_versao` | Retorna versão do SEI e do mod-wssei instalado |
| `sei_listar_orgaos` | Lista órgãos da instalação do SEI |
| `sei_listar_contextos` | Lista contextos disponíveis para um órgão |

### Navegação e contexto (7)

| Tool | Descrição |
|------|-----------|
| `sei_listar_unidades` | Lista unidades acessíveis pelo usuário |
| `sei_trocar_unidade` | Troca a unidade ativa |
| `sei_pesquisar_unidades` | Pesquisa unidades por nome/sigla |
| `sei_pesquisar_outras_unidades` | Pesquisa unidades excluindo a atual |
| `sei_pesquisar_textos_padrao` | Pesquisa textos padrão internos da unidade |
| `sei_listar_usuarios` | Lista usuários (filtra por unidade ativa e nome) |
| `sei_pesquisar_usuarios` | Busca usuários por palavra-chave no órgão |

### Processos — consulta (11)

| Tool | Descrição |
|------|-----------|
| `sei_listar_processos` | Lista caixa da unidade via scraper web (~23× mais rápido que REST). Suporta `apenas_meus`, `tipo`, `filtro` |
| `sei_pesquisar_processos` | Pesquisa por texto, descrição, datas, unidade geradora, assunto ou grupo de acompanhamento |
| `sei_consultar_processo` | **Híbrido**: REST (especificacao, assuntos, interessados, observacoes) + Web (lista de documentos da árvore) em paralelo |
| `sei_resumo_processos` | Resumo agrupado por 17 campos (usa REST direto para flags estruturadas) |
| `sei_listar_unidades_processo` | Lista unidades onde o processo está aberto |
| `sei_consultar_atribuicao` | Consulta quem é responsável pelo processo |
| `sei_verificar_acesso` | Verifica se o usuário tem acesso ao processo |
| `sei_listar_relacionamentos` | Lista processos relacionados (mod-wssei 3.0.2+) |
| `sei_listar_atividades` | Histórico de atividades/andamentos via scraper web (~2× mais rápido) |
| `sei_listar_interessados` | Lista interessados do processo |
| `sei_listar_sobrestamentos` | Lista histórico de sobrestamentos |

### Processos — gestão (13)

| Tool | Descrição |
|------|-----------|
| `sei_criar_processo` | Cria novo processo (público ou restrito) |
| `sei_alterar_processo` | Altera metadados (nível de acesso, especificação) |
| `sei_enviar_processo` | Tramita para outra(s) unidade(s) — aceita sigla |
| `sei_concluir_processo` | Conclui na unidade atual |
| `sei_reabrir_processo` | Reabre processo concluído |
| `sei_receber_processo` | Confirma recebimento na unidade |
| `sei_atribuir_processo` | Atribui a um usuário (aceita nome) |
| `sei_remover_atribuicao` | Remove atribuição de processo |
| `sei_marcar_nao_lido` | Marca processo como não lido na unidade |
| `sei_sobrestar_processo` | Sobresta processo (motivo obrigatório) |
| `sei_remover_sobrestamento` | Remove sobrestamento |
| `sei_pesquisar_tipos_processo` | Pesquisa tipos de processo |
| `sei_pesquisar_hipoteses_legais` | Pesquisa hipóteses legais (restrito/sigiloso) |

### Processos — assuntos (2)

| Tool | Descrição |
|------|-----------|
| `sei_pesquisar_assuntos` | Pesquisa assuntos disponíveis |
| `sei_sugestao_assuntos_processo` | Sugestões de assunto para um tipo de processo |

### Processos sigilosos — credenciamento (4)

| Tool | Descrição |
|------|-----------|
| `sei_listar_credenciamentos` | Lista credenciamentos de acesso ao processo |
| `sei_conceder_credenciamento` | Concede acesso a um usuário |
| `sei_renunciar_credenciamento` | Renuncia ao próprio acesso |
| `sei_cassar_credenciamento` | Revoga acesso de um usuário |

### Documentos — leitura (8)

| Tool | Descrição |
|------|-----------|
| `sei_arvore_processo` | Árvore completa via scraper web (~10× mais rápido que REST). Aceita protocolo formatado |
| `sei_buscar_documento` | Busca documento pelo número SEI (via Solr) |
| `sei_listar_documentos` | Lista documentos via scraper web (~10× mais rápido). Aceita protocolo formatado |
| `sei_ler_documento` | Lê documento (HTML ou PDF/OCR) em Markdown |
| `sei_baixar_anexo` | Baixa documento externo em base64 (max 10MB) |
| `sei_consultar_documento_externo` | Consulta metadados de documento externo |
| `sei_listar_assinaturas` | Lista assinaturas de um documento |
| `sei_listar_blocos_documento` | Lista blocos de assinatura do documento |

### Documentos — escrita (10)

| Tool | Descrição |
|------|-----------|
| `sei_criar_documento` | Cria documento interno vazio |
| `sei_criar_documento_externo` | Cria documento externo com upload de arquivo |
| `sei_alterar_documento_interno` | Altera metadados de documento interno |
| `sei_alterar_documento_externo` | Altera metadados/arquivo de documento externo |
| `sei_listar_secoes` | Lista seções editáveis de um documento |
| `sei_editar_secao` | Altera conteúdo HTML (preenche somenteLeitura auto) |
| `sei_assinar_documento` | Assinatura eletrônica |
| `sei_cancelar_assinatura` | Tenta cancelar assinatura via edição |
| `sei_gerar_referencia` | Gera hiperlink dinâmico para documento citado |
| `sei_estilos` | Consulta dicionário de 39 estilos CSS do SEI |

### Documentos — tipos e modelos (7)

| Tool | Descrição |
|------|-----------|
| `sei_pesquisar_tipos_documento` | Pesquisa tipos de documento (séries) |
| `sei_pesquisar_tipos_documento_externo` | Tipos aplicáveis a documentos externos |
| `sei_pesquisar_tipos_conferencia` | Tipos de conferência (cópia, original, autenticada) |
| `sei_sugestao_assuntos_documento` | Sugestões de assunto para um tipo de documento |
| `sei_listar_grupos_modelos` | Lista grupos de modelos de documento |
| `sei_listar_modelos` | Lista modelos de documento disponíveis |
| `sei_parametros_upload` | Extensões/tamanhos permitidos para upload |

### Assinantes (2)

| Tool | Descrição |
|------|-----------|
| `sei_listar_assinantes` | Lista cargos/funções para assinatura |
| `sei_listar_orgaos_assinante` | Lista órgãos disponíveis para assinatura |

### Ciência e andamento (3)

| Tool | Descrição |
|------|-----------|
| `sei_dar_ciencia` | Dá ciência em documento ou processo |
| `sei_listar_ciencias` | Lista ciências registradas |
| `sei_registrar_andamento` | Registra andamento/atividade no processo |

### Anotação e observação (2)

| Tool | Descrição |
|------|-----------|
| `sei_criar_anotacao` | Cria anotação (post-it) individual no processo |
| `sei_criar_observacao` | Cria observação da unidade no processo |

### Contatos (2)

| Tool | Descrição |
|------|-----------|
| `sei_pesquisar_contatos` | Pesquisa contatos cadastrados |
| `sei_criar_contato` | Cria novo contato |

### Marcador (8)

| Tool | Descrição |
|------|-----------|
| `sei_criar_marcador` | Cria marcador (lista cores se omitida) |
| `sei_excluir_marcador` | Exclui marcador(es) |
| `sei_desativar_marcador` | Desativa marcador(es) sem excluir |
| `sei_reativar_marcador` | Reativa marcador(es) desativados |
| `sei_marcar_processo` | Adiciona marcador a um processo |
| `sei_pesquisar_marcadores` | Lista marcadores disponíveis |
| `sei_consultar_marcador_processo` | Consulta marcadores ativos de um processo |
| `sei_historico_marcador_processo` | Histórico de marcadores do processo |

### Acompanhamento especial (8)

| Tool | Descrição |
|------|-----------|
| `sei_acompanhar_processo` | Adiciona acompanhamento especial |
| `sei_alterar_acompanhamento` | Altera acompanhamento existente |
| `sei_remover_acompanhamento` | Remove acompanhamento |
| `sei_listar_meus_acompanhamentos` | Lista processos acompanhados pelo usuário |
| `sei_listar_acompanhamentos_unidade` | Lista acompanhamentos da unidade |
| `sei_listar_grupos_acompanhamento` | Lista grupos de acompanhamento |
| `sei_criar_grupo_acompanhamento` | Cria grupo de acompanhamento |
| `sei_excluir_grupo_acompanhamento` | Exclui grupo de acompanhamento |

### Bloco interno (10)

| Tool | Descrição |
|------|-----------|
| `sei_criar_bloco_interno` | Cria bloco interno |
| `sei_alterar_bloco_interno` | Altera descrição do bloco |
| `sei_excluir_bloco_interno` | Exclui bloco(s) |
| `sei_concluir_bloco_interno` | Conclui bloco(s) |
| `sei_reabrir_bloco_interno` | Reabre bloco concluído |
| `sei_incluir_processo_bloco_interno` | Inclui processo(s) no bloco |
| `sei_retirar_processo_bloco_interno` | Remove processo(s) do bloco |
| `sei_listar_processos_bloco_interno` | Lista processos do bloco |
| `sei_anotar_processo_bloco_interno` | Cria anotação em processo do bloco |
| `sei_alterar_anotacao_bloco_interno` | Altera anotação do bloco |

### Bloco de assinatura (16)

| Tool | Descrição |
|------|-----------|
| `sei_criar_bloco_assinatura` | Cria bloco (aceita sigla de unidades) |
| `sei_alterar_bloco_assinatura` | Altera descrição do bloco |
| `sei_excluir_bloco_assinatura` | Exclui bloco(s) |
| `sei_concluir_bloco_assinatura` | Conclui bloco(s) |
| `sei_reabrir_bloco_assinatura` | Reabre bloco concluído |
| `sei_retornar_bloco_assinatura` | Retorna bloco para unidade de origem |
| `sei_incluir_documento_bloco_assinatura` | Inclui documento(s) no bloco |
| `sei_retirar_documentos_bloco_assinatura` | Remove documento(s) do bloco |
| `sei_listar_documentos_bloco_assinatura` | Lista documentos do bloco |
| `sei_disponibilizar_bloco_assinatura` | Disponibiliza bloco para assinatura |
| `sei_cancelar_disponibilizacao_bloco` | Cancela disponibilização |
| `sei_pesquisar_blocos_assinatura` | Pesquisa blocos existentes |
| `sei_assinar_bloco` | Assina todos os documentos de um bloco |
| `sei_assinar_documentos_bloco` | Assina documentos específicos de um bloco |
| `sei_anotar_documento_bloco_assinatura` | Cria anotação em documento do bloco |
| `sei_alterar_anotacao_bloco_assinatura` | Altera anotação do bloco |

## Compatibilidade com versões do SEI

Todos os **116 endpoints funcionam desde o mod-wssei 2.0.0** (SEI 4.0.x), exceto um:

| Tool | Versão mínima |
|------|---------------|
| `sei_listar_relacionamentos` | mod-wssei **3.0.2+** (SEI 5.0.x) |

Tabela de compatibilidade SEI ↔ mod-wssei:

| Versão SEI | mod-wssei | Observações |
|---|---|---|
| 4.0.x | 2.0.x | Base completa (131 rotas) |
| 4.1.1 | 2.2.0 | Correções de bugs |
| 5.0.x | 3.0.1 | Compatibilidade PHP 8.2 |
| 5.0.x | **3.0.2** | +`relacionamentos`, +`dataHora` em assinaturas |

Se algum endpoint falhar com erro inesperado, use `sei_versao` para verificar a versão do mod-wssei instalada na sua instância do SEI.

> **Nota:** a API mod-wssei v2 não expõe endpoint para **cancelar assinatura** de documentos em nenhuma versão (verificado até v3.0.2). A função existe no core do SEI (`DocumentoRN::cancelarAssinaturaInternoControlado`) mas não está exposta via REST. O `sei_cancelar_assinatura` usa o workaround de forçar uma edição mínima no documento.

## Arquitetura híbrida REST + Web scraper

A maioria das tools usa a **REST mod-wssei v2** (estável, oficial, disponível desde SEI 4.0.x). Mas duas operações críticas para latência ganham com um caminho alternativo via **scraping HTTP do frontend web do SEI**:

| Tool | Estratégia | Ganho medido |
|---|---|---|
| `sei_listar_processos` | Scraper web puro (`procedimento_controlar.php` em modo Detalhada) | ~14.7 s → ~625 ms (**23×**) |
| `sei_consultar_processo` | Híbrido: REST `/processo/consultar/{id}` + scraper `arvore_montar.php` em paralelo | combina dados complementares |
| `sei_arvore_processo` | Scraper web (`arvore_montar.php`) | ~12 s → ~1.1 s (**10×**) |
| `sei_listar_documentos` | Scraper web (`arvore_montar.php`) | ~9.7 s → ~1.1 s (**10×**) |
| `sei_listar_atividades` | Scraper web (`procedimento_consultar_historico.php`) | ~2.5 s → ~1.2 s (**2×**) |
| `pesquisar_tipos_processo` | Cache in-memory TTL 1h | ~4.2 s → instant |
| `listar_unidades_usuario` | Cache in-memory TTL 1h | ~3.0 s → instant |
| `pesquisar_marcadores` | Cache in-memory TTL 1h | ~2.6 s → instant |

O scraper:

- Mantém uma **sessão SIP autenticada** persistente (login custa ~3 s, uma vez por conexão MCP).
- Reaproveita o `infra_hash` capturado da cadeia de redirects pós-login (válido enquanto a sessão SIP viver).
- Cacheia o action e os hidden fields do form principal de `procedimento_controlar` para POSTs subsequentes.
- Re-loga automaticamente se detectar que a sessão expirou.
- Funciona com qualquer instância SEI 4.0+/5.0+ que use o módulo `Infra` v1.5x+ (a maioria das instalações modernas).

A REST mod-wssei continua sendo o caminho **padrão** para todas as outras operações e o **fallback** se o scraper falhar (ex: CAPTCHA após muitas tentativas, 2FA habilitado, mudança de layout no SEI). O método REST de `listar_processos` permanece disponível em [`SEIClient.listar_processos`](src/mcp_seipro/sei_client.py) — não exposto como tool MCP, mas usado internamente pelo `sei_resumo_processos` (que precisa dos flags estruturados de status).

## Funcionalidades

### Resolução automática

| Parâmetro | Aceita | Exemplo |
|-----------|--------|---------|
| Documento | Número SEI ou id interno | `sei_ler_documento("2843449")` |
| Processo | Protocolo ou IdProcedimento | `sei_criar_anotacao(processo="50300.018905/2018-67")` |
| Unidade | Sigla ou ID | `sei_enviar_processo(unidades_destino="SFC")` |
| Usuário | Nome ou ID | `sei_atribuir_processo(usuario="Karina")` |

### Leitura universal de documentos

- **Internos (HTML)** → Markdown (tabelas limpas, sem colunas vazias)
- **PDFs com texto** → Markdown via pdfplumber
- **PDFs escaneados** → Markdown via OCR (tesseract, limite 20 páginas)

### Estilos CSS do SEI

**Despachos:** `Paragrafo_Numerado_Nivel1` (corpo), âncora SEI no destinatário

**Notas Técnicas:** `Item_Nivel1/2/3/4` (H1/H2/H3/H4), `Item_Alinea_Letra` (a, b, c), `Item_Inciso_Romano` (I, II, III)

**Regra:** toda numeração usa classes CSS, nunca texto manual.

## Privacidade e dados restritos

O SEI classifica processos e documentos em três níveis: público (`nivelAcesso=0`), restrito (`1`) e sigiloso (`2`). O MCP usa as credenciais do usuário, então acessa o que o usuário enxergaria no SEI — incluindo restritos. Sigilosos exigem credenciamento prévio no próprio SEI.

Como conteúdo restrito pode trafegar para um provedor LLM (que talvez logue, retenha ou treine modelos com ele), o MCP impõe um **gate de consentimento** nas duas tools que entregam conteúdo bruto:

- `sei_ler_documento` — markdown/texto/HTML do documento
- `sei_baixar_anexo` — base64 do arquivo

**Comportamento padrão (mais seguro):** se o documento tem `nivelAcesso` 1 ou 2 e a chamada **não** trouxe `confirmar_acesso_restrito=true`, o MCP responde com um JSON estruturado em pt-BR (`consentimento_necessario=true`, lista de `riscos[]` cobrindo LGPD/LAI/treinamento de modelos/sigilo funcional, e `como_liberar`). **O conteúdo bruto não é entregue.**

Existem duas formas de liberar:

| Forma | Escopo | Quando usar |
|-------|--------|-------------|
| `confirmar_acesso_restrito=true` na chamada | Per-call | Decisão pontual do usuário ao usar o LLM |
| `SEI_PERMITIR_RESTRITOS=true` (env var) | Servidor inteiro | Operador do MCP libera previamente |

Em ambos os casos, o conteúdo entregue vem com um **disclaimer prefixado** lembrando o nível de acesso, a hipótese legal e os riscos.

As demais tools (`sei_consultar_processo`, `sei_consultar_documento_externo`, etc.) **não bloqueiam metadados** — apenas anexam um campo `_aviso_acesso` quando detectam restrição, para o LLM repassar a informação ao usuário.

> O gate trata restrito e sigiloso de forma idêntica. Sigiloso já tem proteção adicional do SEI (credenciamento). Se quiser regras diferentes, abra um issue.

### Por que não há um modal nativo de autorização?

O MCP define o protocolo `elicitInput` justamente para isso — o servidor pede input estruturado e o cliente renderiza UI nativa. O servidor SEI Pro implementa esse caminho desde v0.3.7: quando o cliente declara a capability, o gate aparece como modal/formulário no cliente, fora do alcance do modelo.

Hoje, no entanto, **os clientes Anthropic conectados via Streamable HTTP** (`mcp.seipro.io` no `claude.ai`/Claude Desktop com servidor remoto) não declaram a capability nem respondem aos requests de elicit. O servidor detecta isso e cai no JSON gate textual, que continua sendo a barreira efetiva. Quando esse suporte for ativado nos clientes Anthropic, o caminho de elicit começa a funcionar automaticamente — nada precisa mudar no servidor.

O fluxo "sem elicit" (atual): modelo recebe JSON estruturado de bloqueio → traduz os riscos ao usuário em texto → usuário digita autorização explícita → modelo passa `confirmar_acesso_restrito=true` na próxima chamada. Funciona bem com modelos grandes (Opus 4.7) e, com as docstrings + `instrucao_para_modelo` + `nao_e_erro_tecnico` introduzidos nas versões 0.3.5–0.3.7, também com modelos menores (Haiku 4.5).

## Deploy remoto (Railway)

O servidor pode rodar em modo HTTP para uso via Claude no celular, na web ou em qualquer cliente MCP remoto. Cada órgão faz seu próprio deploy — as credenciais do SEI são informadas pelo usuário na tela de login OAuth e nunca ficam armazenadas no servidor.

### O que é o Railway

O [Railway](https://railway.com?referralCode=jJJ7Xz) é uma plataforma de deploy na nuvem que facilita colocar aplicações no ar. Você faz push do código e o Railway cuida de build, domínio, SSL e escalabilidade.

### 1. Criar conta no Railway

1. Acesse [railway.com](https://railway.com?referralCode=jJJ7Xz) e clique em **Sign Up**
2. Faça login com GitHub, GitLab ou e-mail
3. Confirme seu e-mail

### 2. Instalar o Railway CLI

**macOS (Homebrew):**
```bash
brew install railway
```

**npm (qualquer plataforma):**
```bash
npm install -g @railway/cli
```

**Verificar instalação:**
```bash
railway --version
```

### 3. Autenticar no terminal

```bash
railway login
```

Isso abre o navegador para você autorizar o CLI na sua conta Railway.

### 4. Clonar o repositório

```bash
git clone https://github.com/sei-pro/mcp-seipro.git
cd mcp-seipro
```

### 5. Criar o projeto no Railway

```bash
railway init -n mcp-seipro
```

Se você tiver mais de um workspace, adicione `--workspace "Nome do Workspace"`.

### 6. Criar o serviço

```bash
railway add --service mcp-seipro
```

### 7. Configurar variáveis de ambiente

O servidor precisa de duas variáveis obrigatórias:

```bash
railway variables set \
  JWT_SECRET="$(openssl rand -base64 48)" \
  BASE_URL="https://SEU-PROJETO.up.railway.app"
```

- **`JWT_SECRET`** — chave para encriptar os tokens OAuth (gerada automaticamente pelo comando acima)
- **`BASE_URL`** — URL pública do seu servidor (será definida no passo 9)

> **Nota:** as credenciais do SEI (URL, usuário, senha) **não** ficam no servidor. São informadas pelo usuário na tela de login OAuth e encriptadas dentro do token.

### 8. Gerar domínio público

```bash
railway domain
```

Isso gera uma URL como `https://mcp-seipro-production.up.railway.app`. Copie essa URL.

Agora atualize a variável `BASE_URL` com a URL gerada:

```bash
railway variables set BASE_URL="https://mcp-seipro-production.up.railway.app"
```

### 9. Fazer o deploy

```bash
railway up
```

Aguarde o build finalizar (2-3 minutos na primeira vez). Ao terminar, verifique:

```bash
# Deve retornar HTTP 401 (protegido por OAuth)
curl -s -o /dev/null -w "%{http_code}" -X POST https://SEU-PROJETO.up.railway.app/mcp
```

Se retornar `401`, o servidor está rodando com autenticação ativa.

### 10. Conectar no Claude

1. Acesse [claude.ai](https://claude.ai) → **Settings** → **Connectors**
2. Clique em **Adicionar conector personalizado**
3. Cole a URL do seu servidor: `https://SEU-PROJETO.up.railway.app/mcp`
4. O Claude vai abrir a tela de login do SEI Pro
5. Preencha a URL da API do SEI, usuário e senha do seu órgão
6. Clique em **Conectar**

Pronto! A configuração sincroniza automaticamente com o app mobile e a web.

### Como funciona

O servidor detecta automaticamente o ambiente:

| Ambiente | Variável `PORT` | Transporte | Uso |
|----------|-----------------|------------|-----|
| Local | ausente | stdio | Claude Code / Claude Desktop |
| Railway | presente (injetada) | Streamable HTTP + OAuth | Claude mobile / web / remoto |

No modo remoto, as credenciais do SEI são encriptadas dentro do token JWT e nunca armazenadas no servidor. O `Dockerfile` inclui `tesseract-ocr` para OCR de PDFs escaneados.

### Domínio customizado (opcional)

```bash
railway domain --custom mcp.seu-orgao.gov.br
```

Configure um registro CNAME no DNS do seu órgão apontando para o valor fornecido pelo Railway. O certificado SSL é provisionado automaticamente.

Lembre-se de atualizar a variável `BASE_URL`:
```bash
railway variables set BASE_URL="https://mcp.seu-orgao.gov.br"
railway up
```

### Atualizar o servidor

Para atualizar com novas versões do mcp-seipro:

```bash
git pull
railway up
```

## Requisitos de sistema

- Python >= 3.11
- Qualquer instância do SEI com módulo mod-wssei v2
- Claude Code, Claude Desktop, ou qualquer cliente MCP

**Para OCR de PDFs escaneados (opcional):**
- `tesseract-ocr` e `tesseract-ocr-por`
- `poppler-utils`

## Links

- [SEI Pro](https://sei-pro.github.io/sei-pro/) — Extensão de navegador para o SEI
- [PyPI](https://pypi.org/project/mcp-seipro/)
- [Repositório](https://github.com/sei-pro/mcp-seipro)
- [Railway](https://railway.com?referralCode=jJJ7Xz) — Plataforma de deploy na nuvem

## Licença

MIT
