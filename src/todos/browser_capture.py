"""Captura de screenshot real do SEI via Playwright — exceção deliberada e escopada.

Todo o resto deste projeto (`sei_web_client.py`, RFC 0020) evita
deliberadamente um browser/Playwright: scraping usa httpx puro + BeautifulSoup,
mais leve e mais rápido, e isso já se provou suficiente para toda ação
descoberta até aqui (RFC 0020 §3: "nenhuma ação encontrada precisou de
execução de JS de verdade"). Screenshot visual é a exceção: não há como obter
o mesmo resultado renderizando HTML puro (CSS/JS client-side, layout real do
browser, fontes) — a própria natureza do pedido ("como a tela aparece de
verdade") exige um motor de renderização real.

Esta é uma exceção ESCOPADA a este módulo (RFC 0021,
`docs/rfc/0021-captura-de-tela-via-browser.md`) — não é precedente para
reescrever o resto do scraper em Playwright. `playwright` é uma dependência
opcional (extra `screenshot` em `pyproject.toml`), importada sob guarda de
`try/except ImportError` como o extra `llm` já existente em
`tools/analise.py`, para não derrubar as demais tools do servidor quando o
extra não está instalado.

Autenticação — NUNCA loga de novo no browser. A única fonte de autenticação
continua sendo `SEIWebClient.ensure_authenticated()` (sessão httpx); os
cookies dessa sessão já autenticada são transplantados para o
`BrowserContext` do Playwright via `_httpx_cookies_to_playwright` antes de
qualquer navegação. Se a página carregada acabar sendo a tela de login —
detectado via `is_login_page` (`todos.html_utils`), mesmo marcador usado pelo scraper HTTP — uma
`SEIAuthError` clara é levantada em vez de silenciosamente devolver um PNG da
tela de login, ou pior, inventar um fluxo de login alternativo com senha.

CAUSA CONFIRMADA (teste ao vivo, RFC 0021 §2.4) para essa `SEIAuthError`: NÃO
é o transplante de cookies em si (ele funciona — validado ao vivo contra
sei.sistemas.ro.gov.br) nem fingerprint/User-Agent do browser (também
descartado ao vivo: alinhar o `user_agent` do `BrowserContext` ao UA da
sessão httpx não mudou o resultado). A causa real é a mesma staleness já
documentada em RFC 0020 para `submeter_form_web`: o `infra_hash` embutido em
toda URL do SEI é amarrado à SESSÃO SIP específica que a gerou — uma URL
resolvida por uma sessão A (outro login, outro `SEIWebClient`) dá "Hash
inválido" (que o SEI resolve redirecionando para a MESMA tela de login) se
usada por uma sessão B, mesmo autenticada como o mesmo usuário, e mesmo via
puro httpx sem browser nenhum envolvido. Na prática: sempre resolva `url`
usando a MESMA sessão/`SEIWebClient` que vai chamar `capturar_tela` (no
servidor MCP em stdio, isso é automático — há um único `SEIWebClient`
compartilhado por processo; em HTTP multi-sessão, é por `ctx.session_id`) —
não cacheie uma URL de uma chamada anterior distante no tempo ou de outra
sessão.
"""

from __future__ import annotations

import logging
import re
import tempfile
import time
from pathlib import Path
from typing import TYPE_CHECKING, cast

from todos.exceptions import SEIAuthError, SEIConnectionError, SEIError, SEIValidationError
from todos.html_utils import action_name, is_login_page, is_read_action

if TYPE_CHECKING:
    import httpx
    from playwright.async_api import SetCookieParam

    from todos.sei_web_client import SEIWebClient

logger = logging.getLogger(__name__)

try:
    from playwright.async_api import Error as _PlaywrightError
    from playwright.async_api import async_playwright

    _PLAYWRIGHT_AVAILABLE = True
except ImportError:
    _PLAYWRIGHT_AVAILABLE = False

_NAV_TIMEOUT_MS = 30_000  # tempo máximo de navegação (page.goto)
_SCREENSHOT_TIMEOUT_MS = 15_000  # tempo máximo para localizar/capturar `selector`
_SLUG_MAX_LEN = 80  # trunca a URL usada no nome do arquivo


_CookiePlaywright = dict[str, "str | float | bool"]


def _httpx_cookies_to_playwright(cookies: httpx.Cookies) -> list[_CookiePlaywright]:
    """Converta o cookie jar httpx para o formato que `BrowserContext.add_cookies()` espera.

    Esta é a ÚNICA ponte de autenticação entre a sessão httpx (mantida por
    `SEIWebClient.ensure_authenticated()`) e o browser — não há login
    independente no Playwright. O chamador é responsável por garantir que
    `ensure_authenticated()` já rodou antes de chamar esta função (senão o
    jar está vazio/expirado e o resultado é uma lista de cookies inúteis).

    `httpx.Cookies.jar` expõe o `http.cookiejar.CookieJar` subjacente —
    iterar sobre ele dá os objetos `http.cookiejar.Cookie` de onde extraímos
    nome/valor/domínio/path/expiração/secure/httpOnly. Devolve dicts simples
    (não o TypedDict `SetCookieParam` do Playwright) DE PROPÓSITO — esta
    função não deve exigir playwright instalado para ser chamada (ela não
    sabe nem precisa saber que o consumidor é Playwright); `capturar_tela`
    faz o cast estrutural na fronteira, onde playwright já está garantido
    disponível.
    """
    convertidos: list[_CookiePlaywright] = []
    for cookie in cookies.jar:
        item: _CookiePlaywright = {
            "name": cookie.name,
            "value": cookie.value or "",
            "domain": cookie.domain,
            "path": cookie.path or "/",
        }
        if cookie.expires:
            item["expires"] = float(cookie.expires)
        if cookie.secure:
            item["secure"] = True
        # http.cookiejar não modela httpOnly como atributo de primeira classe;
        # ele sobrevive (quando presente) no dict `_rest` de atributos não
        # padronizados do Set-Cookie original.
        rest = getattr(cookie, "_rest", None) or {}
        if any(chave.lower() == "httponly" for chave in rest):
            item["httpOnly"] = True
        convertidos.append(item)
    return convertidos


_NAO_ALFANUMERICO = re.compile(r"[^\w\-]")
# Mesmas capacidades assinadas que `html_utils.redact_signed_capabilities`
# remove de saída diagnóstica — aqui removidas ANTES de derivar o nome do
# arquivo, já que `tempfile.gettempdir()` é um diretório compartilhado
# (frequentemente legível por outros processos/usuários do mesmo host) e o
# slug pega os últimos caracteres da URL, onde `infra_hash`/tokens
# tipicamente aparecem.
_CAPACIDADE_ASSINADA = re.compile(
    r"(?i)[?&](?:infra_hash|hdnToken\w*|token|csrf(?:_token)?)=[^&#]*"
)


def _caminho_screenshot(url: str, selector: str | None) -> Path:
    """Monta um caminho previsível em disco pro PNG.

    Mesma convenção de `_salvar_arquivo_temp` (tools/processos.py):
    `tempfile.gettempdir()` + prefixo `SEI_`. Inclui timestamp em
    milissegundos porque, ao contrário de PDF/ZIP de processo (uma versão
    "atual" por protocolo), a mesma URL pode ser capturada várias vezes em
    sequência (ex.: antes/depois de uma ação) e cada captura deve gerar um
    arquivo novo, não sobrescrever a anterior.
    """
    url_sem_capacidades = _CAPACIDADE_ASSINADA.sub("", url)
    slug = _NAO_ALFANUMERICO.sub("_", url_sem_capacidades)[-_SLUG_MAX_LEN:]
    sufixo_selector = f"_{_NAO_ALFANUMERICO.sub('_', selector)}" if selector else ""
    ts_ms = int(time.time() * 1000)
    nome = f"SEI_tela_{slug}{sufixo_selector}_{ts_ms}.png"
    return Path(tempfile.gettempdir()) / nome


async def capturar_tela(
    client: SEIWebClient,
    url: str,
    *,
    selector: str | None = None,
    aguardar_segundos: float = 1.0,
) -> Path:
    """Navega até `url` num Chromium headless real e salva um screenshot PNG.

    Reaproveita a sessão SIP já autenticada de `client` (RFC 0021) — nunca
    loga de novo no browser. `url` é validada como sendo da mesma origem do
    SEI configurado (mesmo mecanismo de `_validar_mesma_origem` usado por
    `inspecionar_pagina_web`/`submeter_form_web`, RFC 0020) ANTES de
    qualquer navegação, prevenindo SSRF via a sessão autenticada transplantada.

    Parâmetros:
    - selector: seletor CSS opcional — recorta só esse elemento da página em
      vez da tela inteira
    - aguardar_segundos: espera após o carregamento da página, antes de
      capturar — dá tempo a JS/CSS assíncrono de terminar de renderizar

    Levanta:
    - `SEIError` se o extra `playwright` não estiver instalado
    - `SEIValidationError` (via `_validar_mesma_origem`) se `url` for de fora
      da instância SEI configurada, ou se `acao` de `url` não for classificada
      como leitura (GET não é sinônimo de seguro no SEI — algumas ações
      mutantes, ex. `linkReabrirProcesso`, também usam GET; ver
      `todos.html_utils.is_read_action`, mesma restrição de
      `sei_action_plans._fetch_read_page`, RFC 0025). Um browser real executa
      a navegação de verdade — diferente de só inspecionar HTML, capturar
      tela de uma URL mutante executaria a mutação.
    - `SEIConnectionError` se a navegação ou a captura falharem
    - `SEIAuthError` se a página carregada for a tela de login do SEI — causa
      mais provável (confirmada em teste ao vivo): `url` foi resolvida por
      uma sessão diferente da de `client` (infra_hash é específico da sessão
      que o gerou), não necessariamente falha real de autenticação; resolva
      `url` de novo usando este mesmo `client` e tente outra vez antes de
      concluir que a sessão está inválida
    """
    # Validação de origem primeiro: barata, e não deve depender de playwright
    # estar instalado nem de rede alguma ter sido tocada.
    url_validada = client.validar_mesma_origem(url)

    acao = action_name(url_validada)
    if not is_read_action(acao):
        msg = (
            f"sei_capturar_tela só navega para rotas de leitura conhecidas; "
            f"{acao or 'rota sem acao'} não foi classificada como leitura. Um "
            "browser real executaria a ação em vez de só fotografá-la."
        )
        raise SEIValidationError(msg)

    if not _PLAYWRIGHT_AVAILABLE:
        msg = (
            "playwright não está instalado (dependência opcional deste projeto, "
            "extra `screenshot` — ver RFC 0021). Instale com "
            "`uv sync --extra screenshot` e depois baixe o browser com "
            "`uv run playwright install chromium` antes de usar sei_capturar_tela."
        )
        raise SEIError(msg)

    await client.ensure_authenticated()
    cookies = _httpx_cookies_to_playwright(client.cookies)

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            context = await browser.new_context()
            # cast: `cookies` são dicts simples estruturalmente compatíveis com o
            # TypedDict `SetCookieParam` (ver docstring de _httpx_cookies_to_playwright) —
            # playwright já está confirmado disponível neste ponto (_PLAYWRIGHT_AVAILABLE).
            # A anotação real (não-string) em `cookies_pw` mantém `SetCookieParam`
            # visivelmente referenciado para o vulture (ele não enxerga o uso dentro
            # da string passada a `cast`, e reportaria o import de TYPE_CHECKING
            # como morto sem esta linha).
            cookies_pw: list[SetCookieParam] = cast("list[SetCookieParam]", cookies)
            await context.add_cookies(cookies_pw)
            page = await context.new_page()
            try:
                await page.goto(url_validada, wait_until="load", timeout=_NAV_TIMEOUT_MS)
            except _PlaywrightError as exc:
                msg = f"Falha ao navegar até {url_validada} no browser: {exc}"
                raise SEIConnectionError(msg) from exc

            # `_validar_mesma_origem` acima só cobriu a URL de ENTRADA — diferente
            # de `_enviar_mesma_origem` (httpx), o browser segue redirects sozinho,
            # sem validação por salto. Um open-redirect same-origin no próprio SEI
            # levaria a sessão autenticada transplantada (cookies) para fora da
            # instância configurada sem que percebêssemos. Revalida a origem FINAL
            # (pós-redirects) antes de prosseguir — não elimina o vazamento do
            # cookie no salto do redirect em si, mas impede que o conteúdo da
            # página fora de origem seja fotografado e devolvido ao chamador.
            client.validar_mesma_origem(page.url)

            if aguardar_segundos > 0:
                await page.wait_for_timeout(aguardar_segundos * 1000)

            corpo = await page.content()
            if is_login_page(corpo):
                msg = (
                    f"A página carregada em {url_validada} é a tela de login do "
                    "SEI, não o conteúdo esperado. Causa mais provável (confirmada "
                    "em teste ao vivo — ver docstring do módulo): o infra_hash "
                    "embutido nesta URL foi assinado por uma sessão SEI diferente "
                    "da sessão cujos cookies foram transplantados para o browser — "
                    "'Hash inválido' redireciona para esta mesma tela de login, "
                    "mesmo autenticado como o mesmo usuário. Resolva `url` de novo "
                    "usando a MESMA sessão/SEIWebClient que vai chamar esta tool, "
                    "imediatamente antes da chamada, e tente novamente. NÃO tente "
                    "reautenticar digitando usuário/senha no browser — se o erro "
                    "persistir mesmo com uma URL recém-resolvida na mesma sessão, "
                    "pare e reporte."
                )
                raise SEIAuthError(msg)

            caminho = _caminho_screenshot(url_validada, selector)
            try:
                if selector:
                    elemento = page.locator(selector).first
                    await elemento.screenshot(path=str(caminho), timeout=_SCREENSHOT_TIMEOUT_MS)
                else:
                    await page.screenshot(path=str(caminho), full_page=True)
            except _PlaywrightError as exc:
                msg = (
                    f"Falha ao capturar screenshot de {url_validada} (selector={selector!r}): {exc}"
                )
                raise SEIConnectionError(msg) from exc
        finally:
            await browser.close()

    return caminho
