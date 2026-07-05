"""Generic inspection and declarative action-plan tools for the SEI frontend.

Typed tools remain the preferred public surface. These tools are for discovery
and for the small, statically verifiable subset of SEI flows that does not yet
have a typed wrapper.

There is intentionally no callback or JavaScript input: callbacks are parsed
from an inspected SEI page into opaque, declarative plans before execution.

No ``from __future__ import annotations``: FastMCP introspects type hints at
runtime to build the tool schema.
"""

from fastmcp import Context

from todos.mcp_app import _DEST, _READ, _get_web_client, _json, _web_backend, mcp
from todos.sei_action_plans import execute_page_plan, inspect_page


@mcp.tool(annotations=_READ)
async def sei_inspecionar_pagina(
    url: str,
    ctx: Context | None = None,
    *,
    incluir_raw: bool = False,
) -> str:
    """Inspeciona uma página de leitura do SEI e devolve planos de ação opacos.

    A ferramenta só abre rotas conhecidas como leitura. No SEI, uma URL GET
    pode alterar estado; links mutantes são descobertos e descritos, mas nunca
    seguidos durante a inspeção.

    O resultado contém:
    - ``page_ref``: referência efêmera, vinculada à sessão, para executar um
      plano já descoberto sem transportar URLs assinadas ou ``infra_hash``;
    - ``forms``: campos, controles repetidos/multivalorados, e botões;
    - ``actions``: gatilhos ``trigger_id`` de links, variáveis ``linkX`` e
      callbacks estáticos, com nível de risco e suporte de execução.

    Não passe callbacks, JavaScript ou URLs de destino para a execução. Use
    ``sei_executar_plano_sei(page_ref, trigger_id, ...)``. Para operações
    frequentes ou de risco, prefira sempre a ferramenta tipada correspondente.

    ``incluir_raw=True`` inclui HTML/JavaScript para diagnóstico, com hashes e
    tokens comuns redigidos.
    """
    web = await _get_web_client(ctx)
    return _json(await inspect_page(web, url, incluir_raw=incluir_raw))


@mcp.tool(annotations=_DEST)
async def sei_executar_plano_sei(
    page_ref: str,
    trigger_id: str,
    ctx: Context | None = None,
    *,
    overrides: list[dict[str, str]] | None = None,
    submit_button: dict[str, str] | None = None,
    confirmar: bool = False,
    expect: dict[str, str] | None = None,
) -> str:
    """Executa um plano previamente descoberto por ``sei_inspecionar_pagina``.

    A página é relida antes da execução; se a estrutura tiver mudado, a ação é
    recusada como stale. A ferramenta nunca avalia JavaScript de entrada e não
    aceita uma URL de destino arbitrária.

    Ações ``write`` e ``destructive`` exigem ``confirmar=True``. Em formulários
    com mais de um submit, informe ``submit_button`` com o ``button_key``
    devolvido pela inspeção. ``overrides`` é uma lista para preservar campos
    HTML repetidos, por exemplo:

    ``[{"name": "selUnidades", "value": "123"}, {"name": "selUnidades", "value": "456"}]``

    ``expect`` é opcional e pode verificar ``text_present``, ``text_absent``,
    ``selector_present`` ou ``selector_absent`` após a ação. Sem uma
    pós-condição, a resposta informa explicitamente que a alteração material
    não foi verificada.
    """
    web = await _get_web_client(ctx)
    return _json(
        await execute_page_plan(
            web,
            page_ref,
            trigger_id,
            overrides=overrides,
            submit_button=submit_button,
            confirmar=confirmar,
            expect=expect,
        )
    )


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
    """Submete um formulário pelo mecanismo legado da RFC 0020.

    Mantida por compatibilidade. Para novos fluxos use
    ``sei_inspecionar_pagina`` seguido de ``sei_executar_plano_sei``: o novo
    fluxo preserva campos repetidos, não expõe URLs assinadas como contrato,
    exige escolha explícita de botão quando ambígua e aceita pós-condição.
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
    antes de qualquer navegação. `url` também precisa ter uma `acao`
    classificada como leitura (mesma restrição de sei_inspecionar_pagina,
    RFC 0025) — GET não é sinônimo de seguro no SEI (algumas ações mutantes
    também usam GET, ex. linkReabrirProcesso), e um browser real EXECUTA a
    navegação de verdade, então uma URL mutante seria executada, não só
    fotografada.

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
