# RFC 0026 — `marcar_processo_web` postava para a tela errada; `criar_marcador_web` via scraper

**Status**: ✅ Implementada · **Data**: 2026-07-05
**Autores**: Claude (com Franklin Baldo)

## 1. Contexto

`sei_marcar_processo` (backend web, `MarcadoresWeb.marcar_processo` →
`SEIWebClient`) reportava `{"ok": true}` mas **não aplicava o marcador de
verdade**. Confirmado ao vivo contra `sei.sistemas.ro.gov.br`, em duas
tentativas seguidas contra processos reais (números omitidos de propósito —
não são necessários para registrar o bug): depois da chamada,
`sei_consultar_marcador_processo` mostrava que o marcador **não estava lá**.

## 2. Causa raiz

A implementação original montava o POST assim (`backends/web/marcadores.py`,
antes desta correção):

```python
campos = {"selMarcador": marcador, "hdnIdMarcador": marcador}
await self._web.executar_acao_processo(processo, "andamento_marcador_gerenciar", campos)
```

`andamento_marcador_gerenciar` é a tela de **gerenciar/remover** marcadores
**já aplicados** a um processo — o form real ali (`frmGerenciarMarcador`) só
tem um checkbox por marcador já aplicado (`chkInfraItem0`, `chkInfraItem1`,
...); **não existe campo `selMarcador` nessa tela**. O `<select>` real com
todos os marcadores da unidade só existe num form **separado**,
`frmAndamentoMarcadorCadastro`, alcançado pelo botão "Adicionar" daquela
tela — a ação certa é `andamento_marcador_cadastrar`.

Como `executar_acao_processo` faz POST de qualquer campo extra que receba,
sem validar se o campo existe de fato no form da página carregada, o SEI
recebeu um POST com um campo (`selMarcador`) que não pertencia a nenhum
`<select>` real do form `frmGerenciarMarcador` — e o ignorou silenciosamente,
sem retornar erro. Esse é o mesmo padrão de falha silenciosa já catalogado
nesta base de código (RFC 0023, `criar_bloco_assinatura_web`): o SEI aceita
um POST malformado com HTTP 200 e nenhuma mensagem de erro, mas não executa
a ação — só uma reconfirmação por releitura pega esse caso.

## 3. Solução implementada

### 3.1 `marcar_processo_web`

Fluxo (`SEIWebClient.marcar_processo_web`, `src/todos/sei_web_client.py`):

1. Reaproveita `_pagina_marcador(protocolo)` — já existente, ponto de
   partida comum de `desmarcar_processo_web`/`consultar_marcador_processo_web`.
2. Extrai a URL do botão "Adicionar" do HTML retornado via regex no
   `onclick="location.href='...'"`.
3. `GET` nessa URL — carrega o form `frmAndamentoMarcadorCadastro`.
4. `POST` nesse form (`_post_form_preservando`, que preserva o restante do
   estado do form e sobrescreve só os campos indicados) com `selMarcador` e
   `hdnIdMarcador` ambos setados para o id do marcador (o JS do form real
   espelha um campo no outro; montando o POST direto, basta setar os dois),
   `txaTexto` opcional, e **o par name=value do botão submit incluído
   explicitamente** — `_post_form_preservando`/`_coletar_estado_form`
   excluem o botão submit de propósito (documentado desde
   `criar_bloco_assinatura_web`), e o PHP do SEI ignora o POST em silêncio
   sem ele.
5. Reconfirma: relê os marcadores aplicados via
   `consultar_marcador_processo_web` e só retorna sucesso se o id aplicado
   aparecer de fato na lista — senão levanta `SEIConnectionError` em vez de
   reportar sucesso falso.

`MarcadoresWeb.marcar_processo` passou a delegar direto a esse método, sem
montar campos manualmente.

### 3.2 `criar_marcador_web` (nova capacidade)

`sei_criar_marcador` usava `_rest_backend(ctx)` diretamente — em instâncias
sem mod-wssei (REST), falhava com "Backend REST não está configurado para
esta instância do SEI", mesmo a criação de marcador sendo replicável via
scraping puro. Testado ao vivo: **um marcador novo foi criado com sucesso**
contra `sei.sistemas.ro.gov.br` via scraping direto, sem tocar REST.

Fluxo (`SEIWebClient.criar_marcador_web`):

1. `_obter_link_toolbar("marcador_listar")` — helper genérico já existente,
   usado por outras ações de toolbar da inbox (ex.:
   `bloco_assinatura_listar`, `acompanhamento_listar`).
2. Na página de listagem, extrai a URL do botão "Novo" (`btnNovo`) via
   regex no `onclick`.
3. `GET` nessa URL — form `frmMarcadorCadastro`: `selStaIcone`/
   `hdnStaIcone` (cor — id numérico + nome em português; **extraído ao vivo
   do próprio `<select>`, nunca hardcoded**, já que as opções podem variar
   por instância), `txtNome`, `txaDescricao` (opcional), `hdnIdMarcador`
   (vazio para criar novo, não editar).
4. `POST` (`_post_form_preservando`, com o par name=value do botão submit
   incluído explicitamente — mesmo cuidado do item 3.1).
5. Reconfirma relendo `marcador_listar` e conferindo que o **nome** aparece
   na listagem antes de reportar sucesso — o SEI não devolve o id do
   marcador recém-criado em nenhum lugar explícito (mesma limitação já
   documentada para blocos de assinatura em `criar_bloco_assinatura_web`).

`listar_cores_marcador_web()` extrai as cores disponíveis independentemente
(mesmo `<select selStaIcone>`), reutilizado tanto por `sei_criar_marcador`
(fallback de erro quando `id_cor` vem vazio) quanto por quem quiser listar as
cores antes de escolher.

`sei_criar_marcador` (tool MCP, `src/todos/tools/marcadores.py`) passou a
usar o dispatcher composto `_backend`/`@requires_backend` (mesmo padrão já
usado por `sei_marcar_processo`) em vez de `_rest_backend` fixo — funciona
tanto em instâncias com mod-wssei (`backend="rest"`) quanto sem
(`backend="web"`).

## 4. Onde está o código

- `SEIWebClient.marcar_processo_web`, `_pagina_marcador` (docstring
  atualizada), `criar_marcador_web`, `listar_cores_marcador_web`,
  `_pagina_marcador_listar`, `_pagina_marcador_cadastro` —
  `src/todos/sei_web_client.py`.
- `MarcadoresWeb.marcar_processo`/`criar_marcador`/`listar_cores_marcador` —
  `src/todos/backends/web/marcadores.py`.
- Tool MCP `sei_criar_marcador` — `src/todos/tools/marcadores.py` (agora com
  `@requires_backend` + `_backend(ctx)`).
- Testes: `tests/test_sei_web_client_marcar_processo.py` (sucesso com
  reconfirmação, falha silenciosa detectada, erro explícito do SEI, botão
  "Adicionar" ausente) e `tests/test_sei_web_client_criar_marcador.py`
  (sucesso, extração de cores, `id_cor` omitido, falha silenciosa
  detectada, botão "Novo" ausente).

## 5. Não-objetivos / gaps conhecidos

`sei_excluir_marcador`/`sei_desativar_marcador`/`sei_reativar_marcador`
permanecem REST-only. Não foi testado ao vivo se essas ações seguem o mesmo
padrão de tela (`marcador_listar`/edição) — replicar o mesmo tipo de bug
desta RFC (adivinhar a estrutura de um form sem confirmar contra uma
instância real) é exatamente o risco que esta correção existe para evitar.
Fica documentado como gap conhecido em vez de implementado sem confirmação.
