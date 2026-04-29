"""Gate de consentimento para documentos/processos restritos do SEI.

Este módulo isola a lógica de proteção de dados sensíveis. Documentos
classificados como restritos (nivelAcesso=1) ou sigilosos (nivelAcesso=2)
podem conter dados pessoais (LGPD), informação preparatória/pessoal
protegida pela LAI, ou conteúdo sob sigilo funcional. Antes de entregar
esse conteúdo bruto ao cliente LLM, o MCP exige consentimento explícito
do usuário — via parâmetro `confirmar_acesso_restrito=true` ou via
variável de ambiente `SEI_PERMITIR_RESTRITOS=true`.
"""

from __future__ import annotations

import os
from typing import Literal

PUBLICO = "0"
RESTRITO = "1"
SIGILOSO = "2"

ROTULOS = {
    PUBLICO: "Público",
    RESTRITO: "Restrito",
    SIGILOSO: "Sigiloso",
}

Decisao = Literal["liberar", "bloquear"]


def normalizar_nivel(valor) -> str | None:
    """Converte o valor cru de nivelAcesso para uma string '0'/'1'/'2' ou None."""
    if valor is None:
        return None
    s = str(valor).strip()
    if s in (PUBLICO, RESTRITO, SIGILOSO):
        return s
    return None


def precisa_disclaimer(nivel_acesso) -> bool:
    """True quando o nível de acesso é restrito ou sigiloso."""
    return normalizar_nivel(nivel_acesso) in (RESTRITO, SIGILOSO)


def env_permite_restritos() -> bool:
    """Lê SEI_PERMITIR_RESTRITOS a cada chamada (suporta override em runtime)."""
    raw = os.getenv("SEI_PERMITIR_RESTRITOS", "false").strip().lower()
    return raw in ("1", "true", "yes", "sim")


_RISCOS = [
    "LGPD (Lei 13.709/2018): pode conter dados pessoais ou sensíveis cuja "
    "exposição exige base legal específica.",
    "LAI (Lei 12.527/2011): informação classificada como restrita não é de "
    "acesso público — o art. 31 protege informações pessoais.",
    "Treinamento e retenção de modelos: o conteúdo enviado a este LLM pode "
    "ser logado, retido ou utilizado para treinamento conforme a política "
    "do provedor — verifique antes de prosseguir.",
    "Sigilo funcional: o servidor/usuário responde administrativa, civil e "
    "penalmente pelo compartilhamento indevido de informação restrita.",
    "Cadeia de custódia: uma vez no contexto do LLM, o conteúdo pode ser "
    "citado em respostas, exportado, copiado ou compartilhado pelo cliente "
    "fora do controle do SEI.",
]

_COMO_LIBERAR = [
    "O operador do servidor pode definir a variável de ambiente "
    "SEI_PERMITIR_RESTRITOS=true para autorizar todas as chamadas — "
    "exige acesso ao deploy e é uma decisão administrativa fora do "
    "alcance da conversa.",
    "Para liberação pontual, o usuário humano deve declarar consentimento "
    "explícito em nova mensagem da conversa, após ler os riscos acima. "
    "O modelo NÃO deve presumir consentimento nem inferir autorização "
    "de pedidos genéricos como 'lê esse documento'.",
]

_NAO_E_ERRO_TECNICO = (
    "Esta resposta NÃO é falha técnica do SEI, NÃO é erro de permissão "
    "(o usuário tem acesso credenciado), NÃO é problema de unidade "
    "incorreta nem de ID errado. É uma camada de PRIVACIDADE imposta "
    "pelo MCP SEI Pro ALÉM das permissões nativas do SEI. Tentar "
    "trocar de unidade, listar documentos por outra rota ou supor ID "
    "alternativo NÃO contorna o gate — apenas aciona o mesmo aviso em "
    "outra tool e desperdiça tempo do usuário."
)

_INSTRUCAO_MODELO = (
    "ATENÇÃO, MODELO LLM: este retorno é um pedido formal de "
    "consentimento que DEVE ser apresentado ao usuário humano da "
    "conversa antes de qualquer nova tentativa. "
    "Você NÃO deve: (a) chamar esta ferramenta novamente por iniciativa "
    "própria, (b) tentar caminhos alternativos como trocar de unidade, "
    "listar documentos do processo, buscar o documento por outra rota, "
    "ou usar outra tool de leitura, (c) inferir consentimento do pedido "
    "original do usuário, (d) usar parâmetros que ativem caminhos de "
    "bypass sem que o usuário tenha lido os riscos e autorizado "
    "expressamente em nova mensagem. "
    "Encaminhe os riscos acima ao usuário em texto natural, em "
    "português, e aguarde decisão explícita. Auto-bypassar este aviso "
    "configura violação da política de privacidade do MCP SEI Pro e "
    "expõe o operador humano (titular das credenciais) a "
    "responsabilização por quebra de sigilo funcional."
)

_INSTRUCAO_MODELO_METADATA = (
    "ATENÇÃO, MODELO LLM: este campo `_aviso_acesso` é INFORMATIVO. Os "
    "metadados do documento/processo foram retornados com SUCESSO ao "
    "lado deste aviso — examine os demais campos da resposta. Esta NÃO "
    "é falha de permissão do SEI nem indica necessidade de trocar de "
    "unidade, listar por outra rota ou supor ID diferente. É apenas a "
    "notificação de que este registro está classificado como restrito "
    "ou sigiloso. Se o usuário humano pedir o CONTEÚDO BRUTO (texto, "
    "PDF, anexo) deste documento, o MCP exigirá consentimento expresso "
    "em chamada separada — apresente os riscos ao usuário e aguarde "
    "autorização explícita; NÃO tente obter o conteúdo via troca de "
    "unidade, listagens, ou outras tools de leitura."
)


def _bloco_base(nivel: str, hipotese_legal: str | None, alvo: dict) -> dict:
    return {
        "nivel_acesso": nivel,
        "rotulo_nivel": ROTULOS.get(nivel, "Desconhecido"),
        "hipotese_legal": hipotese_legal or None,
        "alvo": alvo,
        "riscos": list(_RISCOS),
    }


def construir_aviso_bloqueio(
    nivel: str,
    hipotese_legal: str | None,
    alvo: dict,
) -> dict:
    """Aviso retornado quando o gate BLOQUEIA a entrega de conteúdo."""
    base = _bloco_base(nivel, hipotese_legal, alvo)
    # Ordem é deliberada: campos de framing (não-erro / instrução ao modelo)
    # vêm ANTES dos riscos para que modelos que param de ler cedo já
    # entendam o tipo de resposta antes de pensar em "consertar".
    aviso = {
        "tipo_resposta": "consentimento_pendente",
        "nao_e_erro_tecnico": _NAO_E_ERRO_TECNICO,
        "instrucao_para_modelo": _INSTRUCAO_MODELO,
        "mensagem_para_usuario_humano": (
            f"Documento/processo classificado como "
            f"{ROTULOS.get(nivel, 'restrito')} no SEI. Antes de o conteúdo "
            "bruto ser enviado ao LLM, o MCP precisa que você confirme "
            "ciência dos riscos abaixo e autorize o acesso expressamente."
        ),
        "consentimento_necessario": True,
        **base,
        "como_liberar": list(_COMO_LIBERAR),
    }
    return aviso


def construir_disclaimer_acompanhante(
    nivel: str,
    hipotese_legal: str | None,
    alvo: dict,
) -> dict:
    """Disclaimer informativo anexado ao conteúdo quando ele É liberado.

    Usado tanto após consentimento (acompanha conteúdo bruto) quanto em
    respostas de metadados (`_aviso_acesso` em sei_consultar_processo,
    sei_consultar_documento_externo, etc).
    """
    base = _bloco_base(nivel, hipotese_legal, alvo)
    aviso = {
        "tipo_resposta": "aviso_classificacao_informativo",
        "instrucao_para_modelo": _INSTRUCAO_MODELO_METADATA,
        "mensagem": (
            f"ATENÇÃO: o registro está classificado como "
            f"{ROTULOS.get(nivel, 'restrito')} no SEI. Trate-o com a cautela "
            "exigida pela LGPD/LAI e pela política do provedor LLM. Os "
            "demais campos desta resposta foram retornados normalmente."
        ),
        **base,
        "consentimento_necessario": False,
    }
    return aviso


def avaliar_acesso(
    nivel_acesso,
    hipotese_legal: str | None = None,
    *,
    confirmou: bool,
    alvo: dict,
) -> tuple[Decisao, dict | None]:
    """Decide se o conteúdo pode ser entregue.

    Retorna ("liberar", None) para conteúdo público.
    Retorna ("liberar", disclaimer) para conteúdo restrito quando há
    consentimento (parâmetro per-call ou env var).
    Retorna ("bloquear", aviso) quando há restrição sem consentimento.
    """
    nivel = normalizar_nivel(nivel_acesso)
    if not precisa_disclaimer(nivel):
        return "liberar", None

    if confirmou or env_permite_restritos():
        return "liberar", construir_disclaimer_acompanhante(nivel, hipotese_legal, alvo)

    return "bloquear", construir_aviso_bloqueio(nivel, hipotese_legal, alvo)


def prefixar_markdown(disclaimer: dict, conteudo: str) -> str:
    """Insere bloco de citação Markdown com o disclaimer antes do conteúdo."""
    linhas = [
        f"> **{disclaimer['mensagem']}**",
        ">",
        f"> Nível: {disclaimer['rotulo_nivel']} (nivelAcesso={disclaimer['nivel_acesso']})",
    ]
    if disclaimer.get("hipotese_legal"):
        linhas.append(f"> Hipótese legal: {disclaimer['hipotese_legal']}")
    linhas.append(">")
    linhas.append("> Riscos:")
    for r in disclaimer["riscos"]:
        linhas.append(f"> - {r}")
    return "\n".join(linhas) + "\n\n" + conteudo


def prefixar_texto(disclaimer: dict, conteudo: str) -> str:
    """Insere bloco em texto plano com o disclaimer antes do conteúdo."""
    linhas = [
        "=" * 70,
        f"AVISO: {disclaimer['mensagem']}",
        f"Nível: {disclaimer['rotulo_nivel']} (nivelAcesso={disclaimer['nivel_acesso']})",
    ]
    if disclaimer.get("hipotese_legal"):
        linhas.append(f"Hipótese legal: {disclaimer['hipotese_legal']}")
    linhas.append("Riscos:")
    for r in disclaimer["riscos"]:
        linhas.append(f"  - {r}")
    linhas.append("=" * 70)
    return "\n".join(linhas) + "\n\n" + conteudo


def envelopar_html(disclaimer: dict, conteudo: str) -> str:
    """Insere <aside> com o disclaimer antes do HTML."""
    riscos_html = "".join(f"<li>{r}</li>" for r in disclaimer["riscos"])
    hl = disclaimer.get("hipotese_legal")
    hl_html = f"<p><strong>Hipótese legal:</strong> {hl}</p>" if hl else ""
    aside = (
        '<aside style="border:2px solid #c00;padding:12px;margin-bottom:12px;'
        'background:#fff8f8;font-family:sans-serif">'
        f'<p><strong>{disclaimer["mensagem"]}</strong></p>'
        f'<p>Nível: {disclaimer["rotulo_nivel"]} '
        f'(nivelAcesso={disclaimer["nivel_acesso"]})</p>'
        f'{hl_html}'
        f'<p><strong>Riscos:</strong></p><ul>{riscos_html}</ul>'
        '</aside>'
    )
    return aside + conteudo


def riscos_padrao() -> list[str]:
    """Lista padrão de riscos exibida em disclaimers e elicit prompts."""
    return list(_RISCOS)


def extrair_nivel(metadata: dict) -> tuple[str | None, str | None]:
    """Extrai (nivel_acesso, hipotese_legal) de um dict de metadados do SEI.

    Aceita tanto chaves camelCase da REST (`nivelAcesso`, `hipoteseLegal`,
    `nivelAcessoGlobal`) quanto variantes encontradas em respostas web.
    """
    if not isinstance(metadata, dict):
        return None, None
    nivel = (
        metadata.get("nivelAcesso")
        or metadata.get("nivel_acesso")
        or metadata.get("nivelAcessoGlobal")
    )
    hl_raw = (
        metadata.get("hipoteseLegal")
        or metadata.get("hipotese_legal")
        or metadata.get("nomeHipoteseLegal")
        or metadata.get("idHipoteseLegal")
    )
    hipotese: str | None = None
    if isinstance(hl_raw, dict):
        hipotese = hl_raw.get("nome") or hl_raw.get("descricao") or str(hl_raw.get("id", "") or "")
        hipotese = hipotese or None
    elif hl_raw:
        hipotese = str(hl_raw)
    return normalizar_nivel(nivel), hipotese
