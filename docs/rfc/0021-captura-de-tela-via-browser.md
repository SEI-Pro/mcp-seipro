# RFC 0021 — Captura de tela via browser real (`sei_capturar_tela`)

- **Status:** Implementada
- **Autor:** Claude (com Franklin Baldo)
- **Data:** 2026-07-04
- **RFCs relacionados:** RFC 0020 (inspeção/submissão genérica de formulário —
  documenta explicitamente a arquitetura pure-HTTP deste projeto e lista
  "executar JS de verdade (Playwright/browser)" como não-objetivo)

## 1. Contexto

Este projeto (`todos`, MCP server para o SEI) é deliberadamente pure-HTTP:
todo scraping usa `httpx` + BeautifulSoup, sem browser/Playwright — mais leve,
mais rápido, e suficiente para tudo que já foi investigado (RFC 0020 §3:
"nenhuma ação encontrada precisou de execução de JS de verdade"). O projeto
irmão `pink` (Kanoê) já usa Playwright para SSO — mas isso nunca foi trazido
para cá.

Surgiu a necessidade de uma tool que devolva **como uma tela do SEI aparece
de verdade** — um screenshot fiel (CSS/JS client-side, layout renderizado,
fontes), não uma reconstrução aproximada a partir do HTML bruto. Isso não é
obtível com o scraper existente: `inspecionar_pagina_web` devolve estrutura
(forms/campos/ações), não uma imagem do que o navegador efetivamente
desenha.

## 2. Proposta

Uma tool nova, `sei_capturar_tela(url, selector=None, aguardar_segundos=1.0)`,
que abre um **Chromium headless via Playwright** só para esse caso de uso
pontual (captura visual), navega até `url`, espera `aguardar_segundos`, e
salva um PNG em disco (tela inteira, ou recortado por `selector` CSS se
informado).

### 2.1 Escopo da exceção — o que ISSO NÃO é

- **Não é** uma reescrita do scraper existente. `sei_web_client.py` continua
  100% httpx + BeautifulSoup; nenhuma ação de escrita (POST/form) passa a
  usar o browser.
- **Não é** precedente para novas tools nascerem em Playwright por padrão —
  a régua continua sendo RFC 0020 (httpx puro é a primeira escolha; browser
  só quando o resultado exigido é intrinsecamente visual/renderizado, como
  aqui).
- **É** uma dependência opcional (`pyproject.toml`, extra `screenshot`),
  guardada por `try/except ImportError` (mesmo padrão do extra `llm` em
  `tools/analise.py`) — ausência do pacote não derruba as ~130 tools
  existentes, só faz `sei_capturar_tela` falhar com mensagem clara.

### 2.2 Autenticação — sem login duplicado no browser

`SEIWebClient` já mantém uma sessão `httpx.AsyncClient` autenticada via
`ensure_authenticated()` (cookies de sessão SIP). Logar de novo dentro do
Playwright exigiria digitar usuário/senha uma segunda vez — isso é
**proibido**: introduziria um segundo caminho de credencial, fora do fluxo
único hoje auditável em `sei_web_client.py`.

Em vez disso, os cookies da sessão httpx já autenticada são **transplantados**
para o `BrowserContext` do Playwright antes de qualquer navegação:

1. `client._http.cookies.jar` (o `http.cookiejar.CookieJar` subjacente da
   sessão httpx) é iterado.
2. Cada `http.cookiejar.Cookie` é convertido para o dict que
   `BrowserContext.add_cookies()` espera (`name`, `value`, `domain`, `path`,
   e opcionalmente `expires`/`secure`/`httpOnly`).
3. `context.add_cookies([...])` roda ANTES de `page.goto(url)`.

Se a página carregada acabar sendo a tela de login, a tool detecta isso
(mesmo marcador `_is_login_page` já usado pelo scraper HTTP) e levanta
`SEIAuthError` — **não** tenta nenhum fluxo de login alternativo com senha.

**Achado confirmado em teste ao vivo (contra sei.sistemas.ro.gov.br,
2026-07-04):** a primeira tentativa de teste ao vivo caiu na tela de login
mesmo com os cookies corretamente transplantados. Duas hipóteses foram
descartadas por teste direto: (1) o transplante de cookies em si estava
errado — descartado, os cookies chegavam certos no `BrowserContext`; (2)
alguma checagem de User-Agent/fingerprint do lado do SEI — descartado,
alinhar o `user_agent` do `BrowserContext` ao UA usado pela sessão httpx não
mudou o resultado. A causa real, confirmada pela mensagem de erro do próprio
SEI no redirect (`login.php?...&msg=Hash+inválido...`): o `infra_hash`
embutido na URL de teste tinha sido resolvido por uma sessão SIP diferente
da que gerou os cookies transplantados (dois processos/logins distintos no
script de diagnóstico) — o SEI rejeita esse descasamento independentemente
de browser estar envolvido (reproduzido também via puro httpx, sem
Playwright). Isso é a MESMA staleness de `infra_hash` já documentada em RFC
0020 §2.2 para `submeter_form_web` ("campos ocultos/hashes do SEI costumam
ser de uso único ou específicos da sessão"), só que descoberta aqui por vir
de sessões inteiramente diferentes, não de uma cópia desatualizada da mesma
sessão. Corrigido resolvendo `url` com `client.consultar_processo(...)` na
MESMA sessão que depois chama `capturar_tela` — a captura funcionou de
primeira. Na prática, isso não é uma preocupação nova para quem já usa
`sei_capturar_tela` através do servidor MCP: dentro de uma mesma sessão de
agente, `_get_web_client`/`_web_backend` devolvem o mesmo `SEIWebClient`
compartilhado (por processo em stdio, por `ctx.session_id` em HTTP) — só
importa se alguém cachear uma URL de uma chamada anterior distante no tempo
ou tentar reusá-la contra uma sessão diferente.

### 2.3 Validação de mesma origem (SSRF)

Reaproveita o mecanismo já existente de `_validar_mesma_origem`
(`sei_web_client.py`, RFC 0020) sobre `url` antes de qualquer navegação —
mesmo hardening que protege `inspecionar_pagina_web`/`submeter_form_web`:
uma URL fora da instância SEI configurada (mesmo scheme+host) é rejeitada
sem tocar rede, o que importa em dobro aqui porque a sessão autenticada
transplantada para o browser não pode vazar cookies para um host externo.

### 2.4 Localização no código

- `src/todos/browser_capture.py` — módulo novo e isolado: import guardado de
  Playwright, conversão de cookies, e a função `capturar_tela(client, url,
  *, selector, aguardar_segundos) -> Path`.
- `src/todos/backends/web/generico.py` (`GenericoWeb.capturar_tela`) — mixin
  do backend web que delega para `browser_capture`, ao lado de
  `inspecionar_pagina`/`submeter_form` (RFC 0020) — sem equivalente REST
  (mod-wssei não expõe uma tela renderizada para fotografar).
- `src/todos/tools/generico.py` (`sei_capturar_tela`) — tool MCP, mesmo
  padrão de docstring/anotações (`_READ`) das outras tools genéricas.
- Caminho do PNG salvo segue a mesma convenção de
  `sei_gerar_pdf_processo`/`sei_gerar_zip_processo`
  (`Path(tempfile.gettempdir()) / f"SEI_..."`), com timestamp em ms no nome
  (diferente de PDF/ZIP consolidado, a mesma URL pode ser capturada várias
  vezes em sequência — cada captura deve gerar um arquivo novo).

## 3. Não-objetivos

- Migrar qualquer ação de leitura/escrita existente para Playwright.
- Suportar 2FA/CAPTCHA no browser (mesma limitação do login httpx existente
  — se a sessão httpx não consegue autenticar, o browser também não vai
  conseguir, já que ele só herda os cookies dela).
- Um pool de browsers/contexts de longa duração — cada chamada de
  `sei_capturar_tela` sobe e derruba seu próprio Chromium (via
  `async with async_playwright()` + `browser.close()` em `finally`). Se o
  volume de chamadas justificar reuso futuro, isso é uma otimização
  separada, não parte deste RFC.

## 4. Testes

`tests/test_browser_capture.py` cobre, sem subir um browser real:

- Conversão de cookies httpx → formato Playwright (`_httpx_cookies_to_playwright`),
  incluindo `expires`/`secure`/`httpOnly` quando presentes no cookie original.
- Rejeição de URL fora da instância SEI configurada (SSRF) antes de qualquer
  chamada de rede ou tentativa de importar/usar Playwright.

Teste de integração ao vivo (fora do pytest, contra `sei.sistemas.ro.gov.br`)
descrito no changelog da versão que introduziu esta tool.
