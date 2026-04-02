# Plano Abrangente: Ecossistema SEI com IA

## Visão Geral

Criar um ecossistema completo em torno do SEI composto por:

1. **mcp-sei** (Open Source) — MCP Server com 64 tools (já pronto)
2. **SEI Nova Interface** — App web (React/Next.js) + Mobile (iOS/Android) com design moderno e responsivo
3. **Agente IA integrado** — Sidebar com chat IA (mcp-sei embutido) que executa ações no SEI via conversa
4. **mcp-sei Cloud** — Versão SaaS hospedada do MCP
5. **Plugins Premium** — Módulos especializados (triagem, fiscalização, etc.)

---

## Fase 1 — Publicação do mcp-sei (Semana 1-2)

### 1.1 Preparar para publicação

- Adicionar LICENSE (MIT)
- Revisar pyproject.toml com metadados completos (author, URLs, classifiers)
- Criar repositório GitHub público: `github.com/antaq/mcp-sei`
- Publicar no PyPI: `pip install mcp-sei`
- Criar releases com changelog

### 1.2 Landing Page

- Site simples (GitHub Pages ou Vercel) com:
  - Demonstração das 64 tools
  - Guia de instalação em 3 passos
  - Vídeo demo de uso no Claude Code
  - Lista de órgãos compatíveis

### 1.3 Divulgação

- Comunidade SEI (fórum, grupos Telegram/WhatsApp de TI de governo)
- Portal do Software Público Brasileiro (SPB)
- LinkedIn (posts técnicos)
- Evento de demonstração para órgãos interessados

---

## Fase 2 — SEI Nova Interface: Web (Meses 1-3)

### 2.1 Arquitetura

```
┌──────────────────────────────────────────────────────────┐
│                    SEI Nova Interface                      │
│  ┌─────────────────────────────┐  ┌───────────────────┐  │
│  │                             │  │                   │  │
│  │     Área Principal          │  │   Sidebar IA      │  │
│  │     (Processos, Docs,       │  │   (Chat Agent)    │  │
│  │      Editor, Árvore)        │  │                   │  │
│  │                             │  │  "Crie um despacho│  │
│  │  ┌─────────────────────┐   │  │   encaminhando a  │  │
│  │  │  Visualizador de    │   │  │   NT 6 para a SFC"│  │
│  │  │  Documentos         │   │  │                   │  │
│  │  │  (Markdown render)  │   │  │  → Criando...     │  │
│  │  │                     │   │  │  → Despacho SEI   │  │
│  │  │                     │   │  │    2867907 criado  │  │
│  │  └─────────────────────┘   │  │  → [Ver documento] │  │
│  │                             │  │                   │  │
│  └─────────────────────────────┘  └───────────────────┘  │
│                                                           │
│  ┌─────────────────────────────────────────────────────┐  │
│  │  Barra de Status: GPF | 1428 processos | Pedro      │  │
│  └─────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────┘
```

### 2.2 Stack Tecnológico

| Camada | Tecnologia | Justificativa |
|--------|-----------|---------------|
| Frontend Web | **Next.js 15 + React 19** | SSR, App Router, já usado no Triagem SOG |
| Estilização | **Tailwind CSS v4** | Design system consistente |
| Estado | **TanStack Query + Zustand** | Cache de API + estado global |
| Editor de documentos | **TipTap** (ProseMirror) | Editor rich-text extensível, suporta HTML do SEI |
| Markdown render | **react-markdown** | Renderização do output do agente IA |
| Chat IA | **Vercel AI SDK** | Streaming, tool calling, UI components |
| Autenticação | **NextAuth.js** ou sessão SEI direta | Login com credenciais SEI |
| API Backend | **Next.js API Routes** → **mcp-sei** | Proxy para o MCP server |

### 2.3 Módulos da Interface

#### A) Caixa de Processos (Home)
- Lista de processos da unidade (como `sei_listar_processos`)
- Filtros: tipo, atribuído, marcador, retorno, lido/não lido
- Agrupamento visual (como `sei_resumo_processos`)
- Ações rápidas: enviar, concluir, atribuir, marcar
- Busca global (como `sei_pesquisar_processos`)
- Troca de unidade (dropdown no header)

#### B) Árvore de Documentos
- Visualização em lista com emojis 📄📎
- Metadados: assinado, cancelado, visualizar, bloqueado
- Indicação de volumes
- Clique para abrir no visualizador
- Drag-and-drop para reordenar (futuro)

#### C) Visualizador de Documentos
- Documentos internos: renderização HTML com estilos SEI
- PDFs: viewer embutido (pdf.js)
- Markdown view: output do `sei_ler_documento`
- Modo comparação (2 docs lado a lado)

#### D) Editor de Documentos
- Editor TipTap com toolbar customizada para estilos SEI
- Botões para cada classe CSS (Parágrafo Numerado, Item Nível 1, Alínea, etc.)
- Inserção de referências SEI (hiperlinks dinâmicos) via dialog
- Inserção de destinatário com âncora via autocomplete de unidades
- Preview em tempo real
- Salvar via `sei_editar_secao`
- Assinatura inline (botão "Assinar" no editor)

#### E) Sidebar IA (Agente)
- Chat interface (como ChatGPT/Claude)
- Streaming de respostas
- Execução de tools do mcp-sei em background
- Resultados interativos:
  - "Criei o Despacho SEI 2867907" → botão [Abrir] que navega para o doc
  - "Processo enviado para SFC" → atualiza a caixa automaticamente
  - "Árvore do processo:" → renderiza tabela com emojis inline
- Histórico de conversas por processo
- Sugestões contextuais ("Esse processo tem docs não lidos, quer que eu resuma?")

### 2.4 Integração mcp-sei ↔ Interface

```
Usuário (chat) → Next.js API Route → Claude API (com tools) → mcp-sei → SEI REST API
                                                                  ↑
Usuário (UI)   → Next.js API Route ─────────────────────────────→┘
```

O frontend pode chamar o SEI de duas formas:
1. **Via IA** (chat) — o agente usa as tools do mcp-sei
2. **Via UI direta** — botões/ações chamam o mcp-sei como biblioteca Python ou API proxy

Para a UI direta, criar uma **API REST wrapper** sobre o mcp-sei:

```
GET  /api/sei/processos              → sei_listar_processos
GET  /api/sei/processo/:protocolo    → sei_consultar_processo
GET  /api/sei/arvore/:protocolo      → sei_arvore_processo
POST /api/sei/documento/criar        → sei_criar_documento
POST /api/sei/documento/editar       → sei_editar_secao
...
```

### 2.5 Design System

- Paleta baseada no gov.br (azul #1351B4, branco, cinza)
- Componentes acessíveis (WCAG 2.1 AA)
- Modo escuro (opcional)
- Responsivo (desktop + tablet)
- Tipografia: Inter (UI) + Calibri (documentos SEI, para fidelidade)

---

## Fase 3 — App Nativo iOS/Android (Meses 3-5)

### 3.1 Stack

| Opção | Prós | Contras |
|-------|------|---------|
| **React Native + Expo** | Compartilha lógica com web, 1 codebase | Performance nativa limitada |
| **Flutter** | Performance nativa, UI bonita | Não compartilha com web React |
| **PWA** | Sem app store, funciona offline | Menos recursos nativos |

**Recomendação:** React Native + Expo (compartilha código com Next.js via monorepo)

### 3.2 Funcionalidades Mobile

- Caixa de processos (lista simplificada)
- Leitura de documentos (Markdown render)
- Chat IA (sidebar em tela cheia no mobile)
- Notificações push (processo recebido, retorno programado)
- Assinatura rápida (biometria → senha SEI)
- Câmera: escanear documento físico → OCR → criar doc externo
- Offline: cache de processos frequentes

### 3.3 Arquitetura Mobile

```
React Native App
  ├── Telas (compartilhadas com web via packages)
  ├── Componentes nativos (câmera, biometria, push)
  └── API Client → Backend Next.js → mcp-sei → SEI
```

---

## Fase 4 — mcp-sei Cloud / SaaS (Meses 4-6)

### 4.1 Arquitetura

```
┌─────────────────────────────────────┐
│         mcp-sei Cloud               │
│                                     │
│  ┌──────────┐   ┌──────────────┐   │
│  │ Auth/API  │   │  mcp-sei     │   │
│  │ Gateway   │──→│  instances   │   │
│  │ (FastAPI) │   │  (por órgão) │   │
│  └──────────┘   └──────────────┘   │
│       ↑                             │
│  ┌──────────┐   ┌──────────────┐   │
│  │ Dashboard │   │  Billing     │   │
│  │ Admin     │   │  Stripe/PG   │   │
│  └──────────┘   └──────────────┘   │
└─────────────────────────────────────┘
```

### 4.2 Modelo de Cobrança

| Plano | Inclui | Preço sugerido |
|-------|--------|---------------|
| **Free** | 100 chamadas/mês, 5 tools básicas | Grátis |
| **Pro** | Ilimitado, todas as tools, OCR | R$ 99/mês/usuário |
| **Enterprise** | Multi-unidade, API dedicada, SLA | R$ sob consulta |

### 4.3 Segurança

- Credenciais SEI criptografadas em repouso (AES-256)
- Nunca armazenar conteúdo de documentos (proxy puro)
- Certificação de segurança (pentest, LGPD)
- Opção on-premises para órgãos sensíveis

---

## Fase 5 — Plugins Premium (Meses 6+)

### 5.1 Triagem Documental (já existe como Triagem SOG)
- Análise automática de documentos com IA
- Checklist de conformidade
- Geração de Notificações

### 5.2 Relatórios Automatizados
- Relatórios de fiscalização padronizados
- Dashboard de processos por unidade
- Métricas de produtividade

### 5.3 Gestão de Prazos
- Monitoramento de retornos programados
- Alertas de prazo vencido
- Painel de situação por servidor

### 5.4 Assistente de Redação
- Geração de minutas (despachos, ofícios, notas técnicas)
- Sugestão de texto baseado no contexto do processo
- Correção ortográfica e gramatical
- Adequação ao Manual de Redação Oficial

---

## Cronograma Resumido

| Fase | Entrega | Prazo |
|------|---------|-------|
| 1 | mcp-sei no PyPI + GitHub + divulgação | Semanas 1-2 |
| 2 | Interface Web (Next.js) com sidebar IA | Meses 1-3 |
| 3 | App Mobile (React Native) | Meses 3-5 |
| 4 | mcp-sei Cloud (SaaS) | Meses 4-6 |
| 5 | Plugins Premium | Meses 6+ |

---

## Estrutura de Repositórios

```
github.com/antaq/
├── mcp-sei/           # MCP Server (Python, MIT, PyPI)
├── sei-interface/     # Nova Interface Web (Next.js, monorepo)
│   ├── apps/
│   │   ├── web/       # Next.js app
│   │   └── mobile/    # React Native app
│   ├── packages/
│   │   ├── ui/        # Design system compartilhado
│   │   ├── sei-api/   # Client API wrapper sobre mcp-sei
│   │   └── ai-agent/  # Integração Claude + mcp-sei
│   └── turbo.json     # Turborepo config
├── sei-cloud/         # SaaS (FastAPI + Docker + Stripe)
└── sei-plugins/       # Plugins premium
```

---

## Monetização Projetada

| Fonte | Ano 1 | Ano 2 | Ano 3 |
|-------|-------|-------|-------|
| Consultoria/implantação | R$ 200K | R$ 400K | R$ 600K |
| mcp-sei Cloud (SaaS) | R$ 50K | R$ 300K | R$ 800K |
| Plugins Premium | — | R$ 100K | R$ 400K |
| Suporte/manutenção | R$ 50K | R$ 150K | R$ 300K |
| **Total** | **R$ 300K** | **R$ 950K** | **R$ 2.1M** |

*Baseado em 5 órgãos no ano 1, 20 no ano 2, 50 no ano 3 (de 200+ que usam SEI)*

---

## Verificação / Próximos Passos Imediatos

1. Publicar mcp-sei no GitHub e PyPI
2. Fazer commit de tudo que temos (mcp-sei + Triagem SOG)
3. Iniciar protótipo da interface web (Next.js) com as telas principais
4. Integrar chat IA com Vercel AI SDK + tools do mcp-sei
5. Testar com 1-2 colegas no órgão antes de divulgar
