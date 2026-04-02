"""Dicionário de estilos CSS do SEI para formatação de documentos.

O SEI utiliza classes CSS padronizadas para manter uniformidade nos
documentos governamentais. Este módulo cataloga todos os estilos
disponíveis para uso nas seções editáveis dos documentos.
"""

# Estilos organizados por categoria com descrição de uso
SEI_STYLES = {
    # === TEXTO BÁSICO ===
    "Texto_Justificado": {
        "descricao": "Parágrafo justificado (uso geral, mais comum)",
        "exemplo": '<p class="Texto_Justificado">Texto do parágrafo.</p>',
        "alinhamento": "justify",
        "recuo": False,
    },
    "Texto_Justificado_Recuo_Primeira_Linha": {
        "descricao": "Parágrafo justificado com recuo na primeira linha (25mm)",
        "exemplo": '<p class="Texto_Justificado_Recuo_Primeira_Linha">Texto com recuo.</p>',
        "alinhamento": "justify",
        "recuo": True,
    },
    "Texto_Justificado_Maiusculas": {
        "descricao": "Parágrafo justificado em maiúsculas",
        "exemplo": '<p class="Texto_Justificado_Maiusculas">texto vira maiúscula.</p>',
        "alinhamento": "justify",
        "recuo": False,
    },
    "Texto_Alinhado_Esquerda": {
        "descricao": "Texto alinhado à esquerda. "
                     "Para destinatário de Despachos, usar com âncora SEI para vincular à unidade.",
        "exemplo": (
            '<p class="Texto_Alinhado_Esquerda">'
            '&Agrave; <span contenteditable="false" style="text-indent:0px;" '
            'class="ancoraSei interessadoSeiPro" data-id="ID_UNIDADE">'
            'SIGLA - Nome da Unidade</span></p>'
        ),
        "exemplo_simples": '<p class="Texto_Alinhado_Esquerda">Texto à esquerda.</p>',
        "alinhamento": "left",
        "recuo": False,
    },
    "Texto_Alinhado_Esquerda_Espacamento_Simples": {
        "descricao": "Texto à esquerda com espaçamento simples (menos margem)",
        "exemplo": '<p class="Texto_Alinhado_Esquerda_Espacamento_Simples">Texto.</p>',
        "alinhamento": "left",
        "recuo": False,
    },
    "Texto_Alinhado_Esquerda_Maiusc": {
        "descricao": "Texto à esquerda em maiúsculas",
        "exemplo": '<p class="Texto_Alinhado_Esquerda_Maiusc">texto vira maiúscula.</p>',
        "alinhamento": "left",
        "recuo": False,
    },
    "Texto_Alinhado_Esquerda_Maiusc_Negrito": {
        "descricao": "Texto à esquerda, maiúsculas, negrito (13pt)",
        "exemplo": '<p class="Texto_Alinhado_Esquerda_Maiusc_Negrito">título.</p>',
        "alinhamento": "left",
        "recuo": False,
    },
    "Texto_Alinhado_Direita": {
        "descricao": "Texto alinhado à direita",
        "exemplo": '<p class="Texto_Alinhado_Direita">Local e data.</p>',
        "alinhamento": "right",
        "recuo": False,
    },
    "Texto_Alinhado_Direita_Maiusc": {
        "descricao": "Texto à direita em maiúsculas",
        "exemplo": '<p class="Texto_Alinhado_Direita_Maiusc">texto.</p>',
        "alinhamento": "right",
        "recuo": False,
    },
    "Texto_Centralizado": {
        "descricao": "Texto centralizado (cabeçalhos, assinaturas)",
        "exemplo": '<p class="Texto_Centralizado">Cargo do signatário</p>',
        "alinhamento": "center",
        "recuo": False,
    },
    "Texto_Centralizado_Maiusculas": {
        "descricao": "Texto centralizado em maiúsculas (13pt, nome do signatário)",
        "exemplo": '<p class="Texto_Centralizado_Maiusculas">nome do servidor</p>',
        "alinhamento": "center",
        "recuo": False,
    },
    "Texto_Centralizado_Maiusculas_Negrito": {
        "descricao": "Texto centralizado, maiúsculas, negrito (títulos de seção)",
        "exemplo": '<p class="Texto_Centralizado_Maiusculas_Negrito">Despacho</p>',
        "alinhamento": "center",
        "recuo": False,
    },

    # === DESTAQUES ===
    "Texto_Fundo_Cinza_Negrito": {
        "descricao": "Texto com fundo cinza e negrito (destaque de seção)",
        "exemplo": '<p class="Texto_Fundo_Cinza_Negrito">Seção importante</p>',
        "alinhamento": "justify",
        "recuo": False,
    },
    "Texto_Fundo_Cinza_Maiusculas_Negrito": {
        "descricao": "Texto com fundo cinza, maiúsculas e negrito",
        "exemplo": '<p class="Texto_Fundo_Cinza_Maiusculas_Negrito">título de seção</p>',
        "alinhamento": "justify",
        "recuo": False,
    },
    "Texto_Espaco_Duplo_Recuo_Primeira_Linha": {
        "descricao": "Negrito com espaçamento duplo entre letras (ênfase especial)",
        "exemplo": '<p class="Texto_Espaco_Duplo_Recuo_Primeira_Linha">Resolvo:</p>',
        "alinhamento": "justify",
        "recuo": True,
    },
    "Citacao": {
        "descricao": "Citação recuada (10pt, margem esquerda 160px). "
                     "Usar para reproduzir trechos de leis, normas, acórdãos, "
                     "pareceres ou outros documentos citados no texto.",
        "exemplo": '<p class="Citacao">"Art. 5º Todos são iguais perante a lei..."</p>',
        "alinhamento": "justify",
        "recuo": True,
        "uso": "Citação de legislação, jurisprudência, doutrina ou documentos",
    },
    "Tachado": {
        "descricao": "Texto tachado (riscado, para indicar exclusão)",
        "exemplo": '<p class="Tachado">Texto removido.</p>',
        "alinhamento": "justify",
        "recuo": True,
    },

    # === PARÁGRAFOS NUMERADOS (autonumeração) ===
    "Paragrafo_Numerado_Nivel1": {
        "descricao": "Parágrafo numerado nível 1 (1. 2. 3.)",
        "exemplo": '<p class="Paragrafo_Numerado_Nivel1">Primeiro item.</p>',
        "alinhamento": "justify",
        "autonumeracao": "1.",
    },
    "Paragrafo_Numerado_Nivel2": {
        "descricao": "Parágrafo numerado nível 2 (1.1. 1.2.)",
        "exemplo": '<p class="Paragrafo_Numerado_Nivel2">Sub-item.</p>',
        "alinhamento": "justify",
        "autonumeracao": "1.1.",
    },
    "Paragrafo_Numerado_Nivel3": {
        "descricao": "Parágrafo numerado nível 3 (1.1.1.)",
        "exemplo": '<p class="Paragrafo_Numerado_Nivel3">Sub-sub-item.</p>',
        "alinhamento": "justify",
        "autonumeracao": "1.1.1.",
    },
    "Paragrafo_Numerado_Nivel4": {
        "descricao": "Parágrafo numerado nível 4 (1.1.1.1.)",
        "exemplo": '<p class="Paragrafo_Numerado_Nivel4">Detalhe.</p>',
        "alinhamento": "justify",
        "autonumeracao": "1.1.1.1.",
    },

    # === TÍTULOS DE SEÇÃO / HEADINGS (autonumeração hierárquica) ===
    # Equivalentes a H1, H2, H3, H4 no HTML ou #, ##, ###, #### no Markdown.
    # Usados em Notas Técnicas, Pareceres e documentos estruturados para
    # organizar capítulos e seções. NÃO usar no corpo do texto corrido —
    # para isso usar Paragrafo_Numerado_Nivel*.
    "Item_Nivel1": {
        "descricao": "Título de seção nível 1 (≈ H1 / #) — maiúsculas, negrito, fundo cinza. "
                     "Ex: '1. INTRODUÇÃO', '2. FUNDAMENTAÇÃO'",
        "exemplo": '<p class="Item_Nivel1">Introdução</p>',
        "alinhamento": "justify",
        "autonumeracao": "1.",
        "equivalente_md": "#",
        "equivalente_html": "h1",
        "uso": "Notas Técnicas, Pareceres — títulos de capítulo",
    },
    "Item_Nivel2": {
        "descricao": "Título de seção nível 2 (≈ H2 / ##). Ex: '1.1. Do objeto'",
        "exemplo": '<p class="Item_Nivel2">Do objeto</p>',
        "alinhamento": "justify",
        "autonumeracao": "1.1.",
        "equivalente_md": "##",
        "equivalente_html": "h2",
        "uso": "Notas Técnicas, Pareceres — subseções",
    },
    "Item_Nivel3": {
        "descricao": "Título de seção nível 3 (≈ H3 / ###). Ex: '1.1.1. Da competência'",
        "exemplo": '<p class="Item_Nivel3">Da competência</p>',
        "alinhamento": "justify",
        "autonumeracao": "1.1.1.",
        "equivalente_md": "###",
        "equivalente_html": "h3",
        "uso": "Notas Técnicas, Pareceres — sub-subseções",
    },
    "Item_Nivel4": {
        "descricao": "Título de seção nível 4 (≈ H4 / ####). Ex: '1.1.1.1.'",
        "exemplo": '<p class="Item_Nivel4">Detalhamento</p>',
        "alinhamento": "justify",
        "autonumeracao": "1.1.1.1.",
        "equivalente_md": "####",
        "equivalente_html": "h4",
        "uso": "Notas Técnicas, Pareceres — detalhamento",
    },

    # === ALÍNEAS E INCISOS (autonumeração) ===
    # IMPORTANTE: a numeração (a, b, c / I, II, III) é gerada automaticamente
    # pelo CSS via counter. NUNCA escrever "a)", "b)", "I -", "II -" no texto.
    # Basta colocar o conteúdo — o SEI insere a letra/número automaticamente.
    "Item_Alinea_Letra": {
        "descricao": "Alínea com letra minúscula — autonumera a) b) c). "
                     "NÃO escrever a letra no texto, o SEI gera automaticamente.",
        "exemplo": '<p class="Item_Alinea_Letra">conteúdo da primeira alínea;</p>\n'
                   '<p class="Item_Alinea_Letra">conteúdo da segunda alínea.</p>',
        "alinhamento": "justify",
        "autonumeracao": "a) b) c)",
    },
    "Item_Inciso_Romano": {
        "descricao": "Inciso com numeral romano — autonumera I - II - III -. "
                     "NÃO escrever o numeral no texto, o SEI gera automaticamente. "
                     "Recuo 120px.",
        "exemplo": '<p class="Item_Inciso_Romano">conteúdo do primeiro inciso;</p>\n'
                   '<p class="Item_Inciso_Romano">conteúdo do segundo inciso.</p>',
        "alinhamento": "justify",
        "autonumeracao": "I - II - III -",
    },
    "Item_Inciso_Romano_Recuo": {
        "descricao": "Inciso romano com recuo menor (margem 6pt). "
                     "Autonumera I - II - III -.",
        "exemplo": '<p class="Item_Inciso_Romano_Recuo">conteúdo do inciso.</p>',
        "alinhamento": "justify",
        "autonumeracao": "I - II - III -",
    },
    "Item_Inciso_Romano_Esquerda_Recuo_Justif": {
        "descricao": "Inciso romano justificado com recuo à esquerda. "
                     "Autonumera I - II - III -.",
        "exemplo": '<p class="Item_Inciso_Romano_Esquerda_Recuo_Justif">conteúdo.</p>',
        "alinhamento": "justify",
        "autonumeracao": "I - II - III -",
    },

    # === TABELAS ===
    "Tabela_Texto_Justificado": {
        "descricao": "Texto justificado dentro de tabela (11pt)",
        "exemplo": '<p class="Tabela_Texto_Justificado">Conteúdo da célula.</p>',
        "alinhamento": "justify",
        "contexto": "tabela",
    },
    "Tabela_Texto_Centralizado": {
        "descricao": "Texto centralizado dentro de tabela (11pt). "
                     "Também usado para legendas de tabela em tamanho normal.",
        "exemplo": '<p class="Tabela_Texto_Centralizado">Tabela 1 - Valores apurados</p>',
        "alinhamento": "center",
        "contexto": "tabela",
        "uso": "Conteúdo de célula centralizado ou legenda de tabela",
    },
    "Tabela_Texto_Alinhado_Esquerda": {
        "descricao": "Texto à esquerda dentro de tabela (11pt)",
        "exemplo": '<p class="Tabela_Texto_Alinhado_Esquerda">Nome.</p>',
        "alinhamento": "left",
        "contexto": "tabela",
    },
    "Tabela_Texto_Alinhado_Direita": {
        "descricao": "Texto à direita dentro de tabela (11pt, valores)",
        "exemplo": '<p class="Tabela_Texto_Alinhado_Direita">R$ 1.000,00</p>',
        "alinhamento": "right",
        "contexto": "tabela",
    },
    "Tabela_Texto_8": {
        "descricao": "Texto pequeno em tabela (8pt, à esquerda)",
        "exemplo": '<p class="Tabela_Texto_8">Nota de rodapé.</p>',
        "alinhamento": "left",
        "contexto": "tabela",
    },
    "Tabela_Texto_8_Centralizado": {
        "descricao": "Texto pequeno centralizado em tabela (8pt)",
        "exemplo": '<p class="Tabela_Texto_8_Centralizado">Nº</p>',
        "alinhamento": "center",
        "contexto": "tabela",
    },
    "Tabela_Fonte_9_Centralizado": {
        "descricao": "Texto menor (9pt) centralizado em tabela. "
                     "Usado para legendas de tabela em fonte reduzida.",
        "exemplo": '<p class="Tabela_Fonte_9_Centralizado">Tabela 1 - Valores apurados (em R$ mil)</p>',
        "alinhamento": "center",
        "contexto": "tabela",
        "uso": "Legenda de tabela em fonte menor ou cabeçalhos compactos",
    },
    "Tabela_Justificado_Recuo_Primeira_Linha": {
        "descricao": "Texto justificado com recuo em tabela (11pt)",
        "exemplo": '<p class="Tabela_Justificado_Recuo_Primeira_Linha">Parágrafo.</p>',
        "alinhamento": "justify",
        "contexto": "tabela",
    },

    # === ESPECIAL ===
    "Texto_Mono_Espacado": {
        "descricao": "Texto monoespaçado (8pt, pré-formatado, código/dados)",
        "exemplo": '<p class="Texto_Mono_Espacado">Dados tabulados</p>',
        "alinhamento": "left",
        "recuo": False,
    },
}


# Mapeamento rápido: intenção → classe CSS recomendada
STYLE_SHORTCUTS = {
    # Destinatário com âncora SEI
    "destinatario_ancora": "Texto_Alinhado_Esquerda",  # usar com html_destinatario()

    # Corpo do texto
    "paragrafo": "Texto_Justificado",
    "paragrafo_recuo": "Texto_Justificado_Recuo_Primeira_Linha",
    "texto": "Texto_Justificado",

    # Destinatário
    "destinatario": "Texto_Alinhado_Esquerda",

    # Assinatura
    "nome_signatario": "Texto_Centralizado_Maiusculas",
    "cargo_signatario": "Texto_Centralizado",
    "fecho": "Texto_Justificado_Recuo_Primeira_Linha",

    # Títulos de documento (Despacho, Ofício)
    "titulo": "Texto_Centralizado_Maiusculas_Negrito",
    "subtitulo": "Texto_Fundo_Cinza_Negrito",

    # Headings de seção (Nota Técnica, Parecer) — equivalentes a # ## ### ####
    "h1": "Item_Nivel1",
    "h2": "Item_Nivel2",
    "h3": "Item_Nivel3",
    "h4": "Item_Nivel4",
    "capitulo": "Item_Nivel1",
    "secao": "Item_Nivel2",
    "subsecao": "Item_Nivel3",

    # Citação de legislação/norma/documento
    "citacao": "Citacao",

    # Listas
    "item_1": "Paragrafo_Numerado_Nivel1",
    "item_2": "Paragrafo_Numerado_Nivel2",
    "item_3": "Paragrafo_Numerado_Nivel3",
    "alinea": "Item_Alinea_Letra",
    "inciso": "Item_Inciso_Romano",

    # Local e data
    "local_data": "Texto_Alinhado_Direita",
    "data": "Texto_Alinhado_Direita",

    # Tabela
    "celula": "Tabela_Texto_Justificado",
    "celula_centro": "Tabela_Texto_Centralizado",
    "celula_direita": "Tabela_Texto_Alinhado_Direita",
    "legenda_tabela": "Tabela_Texto_Centralizado",
    "legenda_tabela_pequena": "Tabela_Fonte_9_Centralizado",
}


def html_referencia_sei(id_documento: str, numero_sei: str) -> str:
    """Gera o HTML de referência (hiperlink dinâmico) para um documento SEI.

    Ao citar um documento no texto, usar este helper para gerar o link
    que o SEI renderiza como hiperlink clicável na interface web.

    Parâmetros:
    - id_documento: ID interno do documento (obtido via _resolver_documento)
    - numero_sei: protocoloFormatado (o número que o usuário vê)

    Exemplo:
        html_referencia_sei("683594", "0627920")
        → '<span contenteditable="false" style="text-indent:0;">
            <a class="ancoraSei" id="lnkSei683594" style="text-indent:0;">0627920</a></span>'
    """
    return (
        f'<span contenteditable="false" style="text-indent:0;">'
        f'<a class="ancoraSei" id="lnkSei{id_documento}" '
        f'style="text-indent:0;">{numero_sei}</a></span>'
    )


def html_destinatario(id_unidade: str, sigla: str, nome: str) -> str:
    """Gera o HTML de destinatário com âncora SEI.

    O span com class="ancoraSei interessadoSeiPro" e data-id vincula
    o destinatário à unidade no SEI, permitindo que a interface web
    sugira automaticamente a unidade ao tramitar o processo.

    Exemplo de uso:
        html_destinatario("110000061", "SFC",
            "Superintendência de Fiscalização e Coordenação das Unidades Regionais")
        → '<p class="Texto_Alinhado_Esquerda">&Agrave; <span ...>SFC - ...</span></p>'
    """
    return (
        f'<p class="Texto_Alinhado_Esquerda">'
        f'&Agrave; <span contenteditable="false" style="text-indent:0px;" '
        f'class="ancoraSei interessadoSeiPro" data-id="{id_unidade}">'
        f'{sigla} - {nome}</span></p>'
    )
