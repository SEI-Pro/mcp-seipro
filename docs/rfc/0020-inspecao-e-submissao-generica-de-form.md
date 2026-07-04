# RFC 0020 — Inspeção e submissão genérica de formulários (backend web)

**Status**: Proposta (protótipo implementado) · **Data**: 2026-07-04
**Autores**: Claude (com Franklin Baldo)
**RFCs relacionados**: nenhum RFC específico ainda cobre a arquitetura de
scraping do backend web — este é o primeiro a propor uma alternativa ao
padrão atual (um método Python por ação, hardcoded).

## 1. Contexto

Esta sessão gastou uma quantidade significativa de esforço fazendo
engenharia reversa de ações específicas do SEI contra uma instância real
(sei.sistemas.ro.gov.br): criar/incluir/listar/retirar/anotar documento em
bloco de assinatura, editar conteúdo de documento, assinar documento,
disponibilizar/excluir/concluir/retornar bloco. Cada uma exigiu o mesmo
processo manual, repetido ~15 vezes:

1. Buscar a página onde a ação aparece (às vezes a página atual, às vezes
   uma página diferente — ex.: a ação "Novo bloco" só existe no toolbar de
   `bloco_assinatura_listar`, não na inbox).
2. Buscar `acao=\w+` na página — como `<a href>` literal OU como string
   dentro do corpo de uma função JS (`function acaoExcluir(id){...
   action='controlador.php?acao=bloco_excluir&...'...}`).
3. Cruzar o ícone/botão clicado com a função JS pelo nome do `onclick` pra
   achar a URL literal embutida (quando não é um link direto).
4. Achar o `<form>` associado (convenção de nomes: `frmXxxYyy`).
5. Coletar o estado atual do form, sobrescrever campos específicos,
   submeter — para o destino certo, que às vezes é o próprio `action` do
   form (quando ele já está na página certa) e às vezes precisa ser
   sobrescrito explicitamente (quando o padrão JS é "sobrescreve
   `form.action` antes de submeter o mesmo form auto-referente" — bug real
   encontrado e corrigido nesta mesma sessão, PR #129).

Esse processo é **mecânico o suficiente para generalizar em uma tool**, ao
invés de escrever um método Python novo — com toda a investigação HTML/JS
repetida — para cada ação nova do SEI que o `todos` ainda não cobre.

## 2. Proposta

Duas tools novas, complementares às ~127 tools específicas já existentes
(que continuam sendo o caminho recomendado quando já existem — mais
seguras, com contrato tipado e semântica clara):

### 2.1 `sei_inspecionar_pagina_web(url)` — leitura, sem risco

Busca uma URL (absoluta, já assinada com `infra_hash` — obtida de outra
tool ou de uma resposta anterior) e devolve:

- `formularios`: lista de forms na página (`id`, `action`, campos visíveis
  com valor atual, campos ocultos, botões — incluindo os que não são
  `type=submit`, já que vários forms do SEI usam `<button type="button"
  onclick="...">` acionado por JS em vez de um submit nativo).
- `acoes_descobertas`: toda ocorrência de `acao=\w+` na página, classificada
  por origem — `href` (link direto), `js_function` (dentro do corpo de uma
  função JS, com o nome da função e, quando possível, o botão/ícone que a
  aciona via `onclick`), ou `js_variable` (atribuição direta tipo
  `linkEditarConteudo = "controlador.php?..."`).

Sem POST nenhum — mesmo nível de risco de qualquer GET já feito hoje.

### 2.2 `sei_submeter_form_web(url_pagina, form_id, overrides, url_destino=None)` — escrita

1. Rebusca `url_pagina` (form/hidden fields/hashes são frequentemente de
   uso único ou específicos da sessão — uma cópia obtida antes pode estar
   stale).
2. Localiza o form por `form_id`.
3. Coleta o estado atual (`_coletar_estado_form`, já existente), aplica
   `overrides`.
4. Submete: se `url_destino` for informado, POSTa lá ignorando o `action`
   próprio do form (`_post_form_com_acao_override`, já existente, criado
   pra corrigir o bug do PR #129); senão, usa o `action` do próprio form
   (`_post_form_preservando`, já existente).
5. Devolve `status_code`, detecção de erro SEI (`_extrair_erro_sei`), um
   trecho do corpo de resposta, e os **formulários presentes na resposta**
   (reaproveitando a mesma extração do passo de leitura) — para o
   chamador poder decidir se a ação teve o efeito esperado, encadear uma
   próxima submissão, ou comparar contra um estado "antes" que ele mesmo
   capturou via `sei_inspecionar_pagina_web`.

**Importante — não tenta verificar sucesso sozinha.** O bug do PR #129
(`_post_form_preservando` postando pro lugar errado, retornando 200 sem
erro, mas sem executar nada) mostrou que "sem erro" não significa "deu
certo". Uma tool genérica não tem conhecimento semântico da ação pra saber
o que checar — cabe ao agente chamador comparar o estado antes/depois
(ex.: usar `sei_inspecionar_pagina_web` na mesma página antes e depois, ou
comparar contra uma lista de ids/documentos esperada).

## 3. O que já foi confirmado (não é hipótese)

Testado nesta sessão contra a instância real, em ~15 casos distintos:

- **Nenhuma ação encontrada precisou de execução de JS de verdade** — tudo
  era texto estático (HTML/JS) parseável por regex/BeautifulSoup. Mantém a
  filosofia "sem Playwright/browser" já estabelecida neste repo.
- O padrão de descoberta (passos 1-5 da seção 1) se repetiu de forma
  consistente entre bloco de assinatura, documento (assinar, editar
  conteúdo), e as ações JS-form-driven de processo já corrigidas antes
  desta sessão (`remover_sobrestamento_web`).

## 4. Limitações conhecidas (não são bloqueadores, são o preço da
generalidade)

- **Nem toda ação aparece na página atual.** Ex.: "Reabrir bloco de
  assinatura" só existe no toolbar quando há pelo menos um bloco no estado
  "Concluído" na visão filtrada — não foi possível confirmar seu mecanismo
  nesta sessão por falta de um bloco concluído disponível. Descoberta
  genérica não contorna isso sozinha; o agente pode precisar navegar (ex.:
  mudar o filtro de estado) antes de inspecionar.
- **Algumas ações exigem resolução em 2+ hops** (ex.: `editor_montar`
  exige primeiro buscar `arvore_visualizar`, extrair uma variável JS de lá,
  depois buscar o link real). `sei_inspecionar_pagina_web` descobre o que
  está NA página atual — encadear múltiplos hops ainda é trabalho do
  agente chamador (mas cada hop individual usa a mesma tool).
- **Nomes de campo continuam exigindo alguma inferência.** A tool devolve
  o nome do campo (`txtAnotacao`, `txaDescricao`, etc.) — decidir qual
  campo corresponde à intenção do usuário ("isso aqui é o texto da
  anotação") é trabalho de leitura/raciocínio do agente, não algo que a
  tool resolve magicamente. Na prática, LLMs lidam bem com isso a partir de
  labels/contexto — é o mesmo tipo de inferência que guiou toda a
  descoberta manual desta sessão.
- **O risco de falha silenciosa não desaparece — só passa a acontecer em
  toda chamada ao vivo, não só uma vez no desenvolvimento.** Ver seção 2.2.

## 5. Não-objetivos

- Substituir as ~127 tools específicas existentes — elas continuam sendo o
  caminho recomendado quando já cobrem a ação desejada (contrato tipado,
  parâmetros claros, sem exigir que o agente entenda HTML do SEI).
- Executar JavaScript de verdade (Playwright/browser) — mantém a
  arquitetura pure-HTTP já estabelecida.
- Resolver descoberta multi-hop automaticamente — fica a critério do
  agente encadear chamadas.

## 6. Reaproveitamento interno pelos métodos específicos — PEDIDO DE COMENTÁRIOS

**Problema.** Surgiu a pergunta de se os ~15 métodos específicos escritos
manualmente nesta sessão (`anotar_documento_bloco_assinatura_web`,
`_executar_acao_bloco_form`, `retirar_documento_bloco_assinatura_web`,
etc.) poderiam trocar seu corpo por uma chamada a `submeter_form_web`, pra
não manter duas implementações do mesmo padrão fetch→achar-form→
sobrescrever→POSTar.

A resposta não é um "sim" direto, porque `submeter_form_web` **sempre
rebusca `url_pagina`** antes de submeter (de propósito — RFC 0020 §2.2,
staleness de hash/estado de sessão). Vários métodos específicos chegam ao
POST já com o `Tag` do form em mãos, obtido de um fetch anterior feito
por outro motivo (ex.: `retirar_documento_bloco_assinatura_web` já buscou
`rel_bloco_protocolo_listar` pra fazer o regex que acha a URL exata da
ação — refazer isso via `submeter_form_web` adicionaria um segundo GET
inteiramente redundante).

**Por que isso não é resolvível só recebendo um form já pronto como
parâmetro:** `sei_submeter_form` é uma tool MCP — atravessa a fronteira
processo-a-processo entre agente e servidor via JSON. Um `bs4.Tag` não é
serializável nessa fronteira; o agente só pode passar de volta uma `url`,
nunca um objeto Python vivo. Então, para o uso VIA TOOL (agente chamando),
rebuscar é inerente — não tem como contornar. O caso que fica sem solução
boa é só o uso INTERNO (mixin Python chamando outro mixin Python), que não
atravessa fronteira nenhuma e por isso não deveria pagar esse custo.

**Proposta preliminar (não decidida — ver pedido de comentários abaixo):**
separar em duas camadas —

1. **Camada baixa, já existe**: `_post_form_preservando`/
   `_post_form_com_acao_override` recebem um `Tag` já em mãos + overrides,
   fazem só o POST. Métodos específicos que já têm o form continuam
   usando isso diretamente — sem rebusca redundante.
2. **Camada alta, é `submeter_form_web`**: rebusca por `url_pagina`, acha o
   form pelo `form_id`, delega pra camada baixa, e monta o dict de
   resultado (`status_code`/`erro`/`formularios_apos`/`raw_html`) —
   exclusiva de quem só tem uma URL (a tool MCP, essencialmente).

Nessa proposta, o reaproveitamento fica limitado a extrair a **montagem do
resultado da resposta** (parsear `erro`/`formularios_apos`/`raw_html` de
um `httpx.Response`) pra uma função `_parse_resultado_submissao(response,
*, incluir_raw)` compartilhada pelas duas camadas — isso sim seria
reaproveitável pelos métodos específicos sem round-trip extra, mesmo que
eles continuem chamando a camada baixa diretamente pro POST em si.

**Pedido de comentários — decisões ainda em aberto:**

1. Vale a pena esse refactor (extrair `_parse_resultado_submissao`) agora,
   ou é otimização prematura antes de ter uso real acumulado da tool
   genérica? A RFC original (§ Plano de implementação) já previa esperar
   uso real antes de generalizar mais.
2. Existe algum método específico onde o round-trip extra de rebuscar via
   `submeter_form_web` seria aceitável (ex.: ação pouco frequente, sem
   sensibilidade de latência) e o refactor direto (trocar corpo inteiro)
   valeria a simplicidade de código sobre o custo de uma requisição a
   mais? Se sim, quais?
3. A camada baixa (`_post_form_preservando`/`_post_form_com_acao_override`)
   deveria ganhar uma variante que também devolve o dict de resultado
   parseado (não só o `httpx.Response` cru), pra reduzir ainda mais
   duplicação nos call sites que hoje fazem esse parsing manualmente
   (`_extrair_erro_sei`, `BeautifulSoup(...).find_all("form")`)? Ou isso
   mistura demais as duas camadas e é melhor deixar cada uma com uma
   responsabilidade só (POST puro vs. GET+POST+parse)?

Sem consenso sobre essas 3 perguntas, **nenhum refactor dos métodos
específicos foi feito** — esta seção documenta a análise e fica aberta
pra decisão numa revisão futura desta RFC.

## 7. Plano de implementação

1. **Protótipo (esta PR)**: `sei_inspecionar_pagina_web`/
   `sei_submeter_form_web` no backend web, com testes cobrindo os 3 tipos
   de origem de ação (`href`, `js_function`, `js_variable`) e a
   re-submissão de form preservando estado.
2. Uso real acumulado decide se vale promover uma descoberta genérica
   recorrente pra uma tool específica nova (o caminho inverso do usual:
   hoje cada tool específica nasce de investigação manual; com a tool
   genérica, ela pode nascer de um padrão de uso repetido observado).
3. Seção 6 (reaproveitamento interno) fica pendente de decisão — não faz
   parte do escopo desta PR.
