"""Tools genéricas de inspeção e submissão de formulário (RFC 0020).

Complementam as tools específicas já existentes — que continuam sendo o
caminho recomendado quando já cobrem a ação desejada (contrato tipado,
parâmetros claros, sem exigir que o agente entenda HTML do SEI). Estas
tools servem pra explorar/operar ações do SEI que o `todos` ainda não
cobre com uma tool dedicada.

Sem `from __future__ import annotations`: o FastMCP introspecta os type hints em
tempo de execução para montar o schema de cada tool, então as anotações precisam
ser objetos reais (não strings adiadas).
"""

from fastmcp import Context

from todos.mcp_app import _DEST, _READ, _json, _web_backend, mcp


@mcp.tool(annotations=_READ)
async def sei_inspecionar_pagina(
    url: str,
    ctx: Context | None = None,
    *,
    incluir_raw: bool = False,
) -> str:
    """Busca uma URL do SEI e devolve os formulários e ações descobertas na página.

    Leitura pura — nenhum POST é feito. Complementa as tools específicas
    existentes quando a ação desejada ainda não tem uma tool dedicada:
    inspecione a página onde a ação deveria aparecer (ex: obtida de outra
    tool, ou por navegação manual) e veja o que está disponível.

    `url` precisa ser da mesma instância SEI configurada (mesmo
    scheme+host) — URLs externas são rejeitadas antes de qualquer request,
    assim como qualquer redirect que tente sair da instância.

    Parâmetros:
    - url: URL absoluta do SEI, já assinada com infra_hash (obtida de outra
      tool ou de uma resposta anterior desta mesma tool/sei_submeter_form)
    - incluir_raw: se True, inclui também o HTML/JS bruto da página — útil
      quando o parsing automático não captura algo que você precisa ver
      diretamente (ex: um padrão de ação novo, não reconhecido pelos três
      formatos que `acoes_descobertas` cobre hoje: href, js_variable,
      js_function)

    Retorna:
    - formularios: cada form da página com id, action, campos (nome, tipo,
      valor atual, opções se for select), campos ocultos, e botões
      (incluindo os que disparam ação via JS onclick, não só type=submit)
    - acoes_descobertas: toda ocorrência de acao=X na página, classificada
      por origem (href = link direto; js_variable = variável JS tipo
      linkEditarConteudo; js_function = dentro do corpo de uma função JS,
      geralmente disparada por um botão — ver onclick_funcao nos botões)

    Use sei_submeter_form pra agir sobre um form encontrado aqui.
    """
    backend = await _web_backend(ctx)
    result = await backend.inspecionar_pagina(url, incluir_raw=incluir_raw)
    return _json(result)


@mcp.tool(annotations=_DEST)
async def sei_submeter_form(
    url_pagina: str,
    form_id: str,
    overrides: dict[str, str],
    url_destino: str = "",
    ctx: Context | None = None,
    *,
    incluir_raw: bool = False,
) -> str:
    """Submete um formulário do SEI, com campos sobrescritos e destino opcional.

    Complementa as tools específicas existentes — prefira uma tool dedicada
    quando ela já cobrir a ação desejada. Use esta quando a ação ainda não
    tem tool própria: inspecione a página com sei_inspecionar_pagina
    primeiro pra descobrir o form_id e os nomes de campo corretos.

    IMPORTANTE — esta tool NÃO verifica se a ação teve efeito. "Sem erro"
    não significa "deu certo": o SEI pode responder 200 sem erro mesmo
    quando o POST não executou nada (ex: foi pro form/action errado). Após
    chamar esta tool, confirme o resultado com sei_inspecionar_pagina (ou
    outra tool de leitura) comparando o estado antes/depois — não confie
    só no status_code/erro devolvidos aqui.

    `url_pagina`, `url_destino` e o action resolvido do form precisam ser
    da mesma instância SEI configurada (mesmo scheme+host) — URLs externas
    são rejeitadas antes de qualquer request, assim como qualquer redirect
    que tente sair da instância.

    Parâmetros:
    - url_pagina: URL da página que contém o form (será rebuscada, não
      reusa uma cópia antiga — campos ocultos/hashes do SEI costumam ser de
      uso único ou específicos da sessão)
    - form_id: id do <form> a submeter (obtido de sei_inspecionar_pagina)
    - overrides: campos a sobrescrever, {nome_campo: valor} — os demais
      campos do form são preservados com o valor atual
    - url_destino: se informado, POSTa aqui IGNORANDO o action próprio do
      form — necessário quando a ação real sobrescreve form.action via JS
      antes de submeter o mesmo form auto-referente (comum em listagens:
      excluir/disponibilizar item). Sem isso, usa o action do próprio form
      — correto quando o form já está na página certa da ação desejada.
    - incluir_raw: inclui o HTML/JS bruto da resposta

    Retorna: status_code, erro detectado (se houver), e os formulários
    presentes na resposta (pra decidir se deu certo, encadear a próxima
    submissão, ou comparar contra o estado capturado antes).
    """
    backend = await _web_backend(ctx)
    result = await backend.submeter_form(
        url_pagina,
        form_id,
        overrides,
        url_destino or None,
        incluir_raw=incluir_raw,
    )
    return _json(result)


@mcp.tool(annotations=_READ)
async def sei_capturar_tela(
    url: str,
    ctx: Context | None = None,
    *,
    selector: str | None = None,
    aguardar_segundos: float = 1.0,
) -> str:
    """Captura um screenshot PNG real de uma tela do SEI, renderizado por um browser Chromium.

    EXCEÇÃO ARQUITETURAL DELIBERADA (RFC 0021): esta é a única tool deste
    servidor que abre um browser de verdade (Playwright) — todo o resto usa
    httpx puro + BeautifulSoup (RFC 0020). Só existe porque uma captura
    visual fiel (CSS/JS client-side, layout real) não é obtível renderizando
    HTML puro. Ver `todos.browser_capture` para detalhes; não é precedente
    para reescrever outras tools em Playwright.

    Autenticação: reaproveita a sessão httpx já autenticada por
    `SEIWebClient.ensure_authenticated()`, transplantando os cookies dela
    para o browser. NUNCA loga de novo digitando usuário/senha no browser —
    se a sessão não colar no browser, a tool levanta erro em vez de continuar.

    IMPORTANTE — resolva `url` pouco antes de chamar esta tool, na mesma
    sessão: o `infra_hash` embutido em toda URL do SEI é específico da sessão
    SIP que a gerou (confirmado em teste ao vivo — RFC 0021 §2.2); uma URL
    obtida há muito tempo, ou por uma sessão diferente da que está ativa
    agora, causa "Hash inválido" no SEI — que aparenta ser (e a tool reporta
    como) falha de autenticação, mesmo a sessão atual estando válida. Ex.:
    chame sei_consultar_processo/sei_arvore_processo e passe a URL retornada
    imediatamente para sei_capturar_tela, no mesmo turno.

    `url` precisa ser da mesma instância SEI configurada (mesmo
    scheme+host) — mesma validação de mesma origem (SSRF) de
    sei_inspecionar_pagina/sei_submeter_form: URLs externas são rejeitadas
    antes de qualquer navegação.

    Parâmetros:
    - url: URL absoluta do SEI, já assinada com infra_hash (mesma convenção
      de sei_inspecionar_pagina — obtida de outra tool ou de uma resposta
      anterior)
    - selector: seletor CSS opcional — se informado, recorta só esse
      elemento da página em vez de capturar a tela inteira
    - aguardar_segundos: tempo de espera após o carregamento da página, antes
      de capturar (padrão 1.0s) — dá tempo a JS/CSS assíncrono terminar de
      renderizar

    Requer a dependência opcional `playwright` (extra `screenshot`) instalada
    e os browsers baixados (`uv run playwright install chromium`) — levanta
    erro claro e acionável se ausente, em vez de falhar de forma obscura.

    Retorna: caminho absoluto do PNG salvo em disco (mesma convenção de
    sei_gerar_pdf_processo/sei_gerar_zip_processo — arquivo em disco, não
    payload inline) e o tamanho em bytes.
    """
    backend = await _web_backend(ctx)
    result = await backend.capturar_tela(
        url, selector=selector, aguardar_segundos=aguardar_segundos
    )
    return _json(result)
