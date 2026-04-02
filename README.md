# mcp-seipro

[![PyPI](https://img.shields.io/pypi/v/mcp-seipro)](https://pypi.org/project/mcp-seipro/)
[![Python](https://img.shields.io/pypi/pyversions/mcp-seipro)](https://pypi.org/project/mcp-seipro/)
[![License](https://img.shields.io/pypi/l/mcp-seipro)](https://pypi.org/project/mcp-seipro/)

MCP Server do **[SEI Pro](https://sei-pro.github.io/sei-pro/)** para o SEI (Sistema Eletrônico de Informações) via API REST mod-wssei v2.

64 tools para gerenciar processos, documentos, tramitação, assinatura, blocos, marcadores e acompanhamento em qualquer instância do SEI.

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

## Tools disponíveis (64)

### Navegação e contexto (4)

| Tool | Descrição |
|------|-----------|
| `sei_listar_unidades` | Lista unidades acessíveis pelo usuário |
| `sei_trocar_unidade` | Troca a unidade ativa |
| `sei_pesquisar_unidades` | Pesquisa unidades por nome/sigla |
| `sei_listar_usuarios` | Lista usuários (filtra por unidade ativa e nome) |

### Processos — consulta (5)

| Tool | Descrição |
|------|-----------|
| `sei_listar_processos` | Lista caixa da unidade (`todas_paginas=true`) |
| `sei_pesquisar_processos` | Pesquisa por texto, descrição ou datas |
| `sei_consultar_processo` | Consulta processo pelo protocolo formatado |
| `sei_resumo_processos` | Resumo agrupado por 17 campos |
| `sei_listar_unidades_processo` | Lista unidades onde o processo está aberto |

### Processos — gestão (14)

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
| `sei_listar_interessados` | Lista interessados do processo |

### Documentos — leitura (7)

| Tool | Descrição |
|------|-----------|
| `sei_arvore_processo` | Árvore completa com metadados, volumes e emojis |
| `sei_buscar_documento` | Busca documento pelo número SEI (via Solr) |
| `sei_listar_documentos` | Lista documentos de um processo |
| `sei_ler_documento` | Lê documento (HTML ou PDF/OCR) em Markdown |
| `sei_baixar_anexo` | Baixa documento externo em base64 (max 10MB) |
| `sei_pesquisar_tipos_documento` | Pesquisa tipos de documento (séries) |
| `sei_listar_assinaturas` | Lista assinaturas de um documento |

### Documentos — escrita (8)

| Tool | Descrição |
|------|-----------|
| `sei_criar_documento` | Cria documento interno vazio |
| `sei_criar_documento_externo` | Cria documento externo com upload de arquivo |
| `sei_listar_secoes` | Lista seções editáveis de um documento |
| `sei_editar_secao` | Altera conteúdo HTML (preenche somenteLeitura auto) |
| `sei_assinar_documento` | Assinatura eletrônica |
| `sei_cancelar_assinatura` | Tenta cancelar assinatura via edição |
| `sei_gerar_referencia` | Gera hiperlink dinâmico para documento citado |
| `sei_estilos` | Consulta dicionário de 39 estilos CSS do SEI |

### Ciência e andamento (4)

| Tool | Descrição |
|------|-----------|
| `sei_dar_ciencia` | Dá ciência em documento ou processo |
| `sei_listar_ciencias` | Lista ciências registradas |
| `sei_registrar_andamento` | Registra andamento/atividade no processo |
| `sei_listar_sobrestamentos` | Lista histórico de sobrestamentos |

### Anotação (1)

| Tool | Descrição |
|------|-----------|
| `sei_criar_anotacao` | Cria anotação (post-it) no processo |

### Contato (1)

| Tool | Descrição |
|------|-----------|
| `sei_pesquisar_contatos` | Pesquisa contatos cadastrados |

### Marcador (5)

| Tool | Descrição |
|------|-----------|
| `sei_criar_marcador` | Cria marcador (lista cores se omitida) |
| `sei_excluir_marcador` | Exclui marcador(es) |
| `sei_marcar_processo` | Adiciona marcador a um processo |
| `sei_pesquisar_marcadores` | Lista marcadores disponíveis |
| `sei_consultar_marcador_processo` | Consulta marcadores ativos de um processo |

### Acompanhamento especial (5)

| Tool | Descrição |
|------|-----------|
| `sei_acompanhar_processo` | Adiciona acompanhamento especial |
| `sei_remover_acompanhamento` | Remove acompanhamento |
| `sei_listar_grupos_acompanhamento` | Lista grupos de acompanhamento |
| `sei_criar_grupo_acompanhamento` | Cria grupo de acompanhamento |
| `sei_excluir_grupo_acompanhamento` | Exclui grupo de acompanhamento |

### Bloco interno (3)

| Tool | Descrição |
|------|-----------|
| `sei_criar_bloco_interno` | Cria bloco interno |
| `sei_incluir_processo_bloco_interno` | Inclui processo(s) no bloco |
| `sei_retirar_processo_bloco_interno` | Remove processo(s) do bloco |

### Bloco de assinatura (7)

| Tool | Descrição |
|------|-----------|
| `sei_criar_bloco_assinatura` | Cria bloco (aceita sigla de unidades) |
| `sei_incluir_documento_bloco_assinatura` | Inclui documento(s) no bloco |
| `sei_disponibilizar_bloco_assinatura` | Disponibiliza bloco para assinatura |
| `sei_cancelar_disponibilizacao_bloco` | Cancela disponibilização |
| `sei_pesquisar_blocos_assinatura` | Pesquisa blocos existentes |
| `sei_assinar_bloco` | Assina todos os documentos de um bloco |
| `sei_assinar_documentos_bloco` | Assina documentos específicos de um bloco |

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

## Deploy remoto (Railway)

O servidor pode rodar em modo HTTP para uso via Claude mobile (celular), Claude web ou qualquer cliente MCP remoto. Cada órgão faz seu próprio deploy com suas credenciais.

### Pré-requisitos

- Conta no [Railway](https://railway.com)
- [Railway CLI](https://docs.railway.com/guides/cli) instalado (`npm install -g @railway/cli`)

### Passo a passo

**1. Clone e acesse o repositório:**

```bash
git clone https://github.com/sei-pro/mcp-seipro.git
cd mcp-seipro
```

**2. Crie o projeto no Railway:**

```bash
railway login
railway init -n mcp-seipro
railway add --service mcp-seipro
```

**3. Configure as credenciais do SEI:**

```bash
railway variables set \
  SEI_URL="https://sei.orgao.gov.br/sei/modulos/wssei/controlador_ws.php/api/v2" \
  SEI_USUARIO="seu.usuario" \
  SEI_SENHA="sua-senha" \
  SEI_ORGAO="0" \
  SEI_VERIFY_SSL="true"
```

**4. Gere um domínio e faça o deploy:**

```bash
railway domain
railway up
```

**5. Conecte no Claude:**

Vá em [claude.ai](https://claude.ai) → Settings → Connectors → Add Custom Connector e cole a URL:

```
https://seu-projeto.up.railway.app/mcp
```

A configuração sincroniza automaticamente com o app mobile e web.

### Como funciona

O servidor detecta automaticamente o ambiente:

| Ambiente | Variável `PORT` | Transporte | Uso |
|----------|-----------------|------------|-----|
| Local | ausente | stdio | Claude Code / Claude Desktop |
| Railway | presente (injetada) | Streamable HTTP | Claude mobile / web / remoto |

O `Dockerfile` inclui `tesseract-ocr` para OCR de PDFs escaneados. Se não precisar, comente as linhas de OCR no Dockerfile para reduzir o tamanho da imagem.

### Domínio customizado (opcional)

```bash
railway domain --custom seu-dominio.gov.br
```

Configure o CNAME no DNS do seu órgão apontando para o valor fornecido pelo Railway. O certificado SSL é provisionado automaticamente.

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

## Licença

MIT
