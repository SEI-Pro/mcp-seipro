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
from todos.responses import NextAction


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
    if result.get("formularios"):
        result["_next"] = [
            NextAction(
                tool="sei_submeter_form",
                args={"url_pagina": url, "form_id": result["formularios"][0].get("id")},
                reason="Submeta o primeiro form encontrado, com os overrides desejados.",
            ).model_dump()
        ]
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
