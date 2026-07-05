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
