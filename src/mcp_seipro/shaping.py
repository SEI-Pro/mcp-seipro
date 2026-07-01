"""Transformação do payload de listagem de processos para uma *list view*
enxuta, tipada e inequívoca — voltada ao consumo por agentes LLM.

A wssei devolve, na listagem, o mesmo payload da tela de detalhe do SEI:
~28 flags de status como "S"/"N", ciências completas, cores hex embutidas no
nome do marcador, entidades HTML nos textos e uma atribuição de topo que NÃO
corresponde à unidade consultada. Este módulo normaliza tudo isso.

Funções puras (sem I/O) para facilitar teste. Ver spec em
docs/spec_listar_processos.md e os defeitos D-1..D-7.
"""

from __future__ import annotations

import html
import re
from typing import Any, Optional

_ACESSO = {"0": "publico", "1": "restrito", "2": "sigiloso"}
_RE_ATRIBUIDO = re.compile(r"\(atribuíd[oa] a (.+?)\)\s*$")
_RE_DATA = re.compile(r"(\d{2})/(\d{2})/(\d{4})")
_RE_COR_HEX = re.compile(r"\s*#[0-9A-Fa-f]{3,8}\s*$")


def _sn(v: Any) -> bool:
    """Normaliza "S"/"N" (e booleanos já prontos) para bool."""
    if isinstance(v, bool):
        return v
    return str(v).strip().upper() == "S"


def _decode(s: Any) -> str:
    """Decodifica entidades HTML e devolve texto limpo (D-4)."""
    if s is None:
        return ""
    return html.unescape(str(s)).strip()


def _parse_data_iso(*fontes: Any) -> Optional[str]:
    """Extrai a 1ª data dd/mm/aaaa das fontes e devolve ISO (aaaa-mm-dd)."""
    for fonte in fontes:
        if not fonte:
            continue
        m = _RE_DATA.search(str(fonte))
        if m:
            dia, mes, ano = m.group(1), m.group(2), m.group(3)
            return f"{ano}-{mes}-{dia}"
    return None


def _limpar_marcador_nome(nome: Any) -> str:
    """Remove código de cor hex embutido no nome do marcador (D-7)."""
    return _RE_COR_HEX.sub("", _decode(nome)).strip()


def atribuido_unidade_atual(att: dict, unidade_ativa_id: Optional[str]) -> Optional[dict]:
    """Resolve a atribuição do processo NA unidade da sessão (D-1).

    Precedência (usa ids; `dadosAbertura.unidades[i]` ↔ `lista[i]` são paralelos):
      1. entrada de `dadosAbertura` da unidade da sessão com "(atribuído a X)" → X
      2. `atributos.unidade` (unidade do usuarioAtribuido) == sessão → usuarioAtribuido
      3. processo aberto SOMENTE na unidade da sessão → usuarioAtribuido
      4. senão → None (sem atribuição resolvível na unidade)

    Retorna {"id_usuario": str|None, "nome": str} ou None. Sem `unidade_ativa_id`
    (ex.: sem sei_trocar_unidade antes) não há como resolver com segurança → None.
    """
    if not unidade_ativa_id:
        return None
    uid = str(unidade_ativa_id)
    da = att.get("dadosAbertura") or {}
    unidades = da.get("unidades") or []
    lista = da.get("lista") or []
    ua = att.get("usuarioAtribuido") or {}
    unidade_topo = att.get("unidade") or {}

    # Regra 1: unidade da sessão presente em dadosAbertura com sufixo (atribuído a X)
    sessao_em_abertura = False
    for i, u in enumerate(unidades):
        if isinstance(u, dict) and str(u.get("id")) == uid:
            sessao_em_abertura = True
            sigla = ""
            if i < len(lista) and isinstance(lista[i], dict):
                sigla = lista[i].get("sigla") or ""
            m = _RE_ATRIBUIDO.search(sigla)
            if m:
                return {"id_usuario": None, "nome": _decode(m.group(1))}
            break

    # Regra 2: a atribuição de topo é da própria unidade da sessão
    if ua and str(unidade_topo.get("idUnidade")) == uid:
        return {"id_usuario": ua.get("idUsuario"), "nome": _decode(ua.get("nome"))}

    # Regra 3: processo aberto somente na unidade da sessão
    if (
        ua and len(unidades) == 1 and isinstance(unidades[0], dict)
        and str(unidades[0].get("id")) == uid
    ):
        return {"id_usuario": ua.get("idUsuario"), "nome": _decode(ua.get("nome"))}

    # (sessao_em_abertura sem sufixo e sem casar regra 2/3 → sem atribuição)
    return None


def _marcador(att: dict) -> Optional[dict]:
    marc = att.get("marcador") or []
    if not isinstance(marc, list) or not marc:
        return None
    m0 = marc[0]
    if not isinstance(m0, dict):
        return None
    nome = _limpar_marcador_nome(m0.get("nome"))
    if not nome:
        return None
    cor = (m0.get("descricaoCor") or "").strip().lower() or None
    return {"nome": nome, "cor": cor}


def shape_processo_resumido(
    raw: dict,
    unidade_ativa_id: Optional[str] = None,
    incluir_detalhe: bool = False,
) -> dict:
    """Converte um item bruto de `/processo/listar` na list view enxuta.

    Ver schema-alvo na spec. `incluir_detalhe=True` reanexa ciências e anotações
    completas (fora da list view por padrão, D-2).
    """
    att = raw.get("atributos") or {}
    status = att.get("status") or {}

    marc = att.get("marcador") or []
    prazo = _parse_data_iso(
        status.get("retornoData"),
        *[m.get("texto") for m in marc if isinstance(m, dict)],
    )

    unidades = ((att.get("dadosAbertura") or {}).get("unidades")) or []
    aberto_em = [u.get("nome") for u in unidades if isinstance(u, dict) and u.get("nome")]

    out = {
        "id_procedimento": str(att.get("idProcedimento") or raw.get("id") or ""),
        "protocolo": att.get("numero") or "",
        "tipo": _decode(att.get("tipoProcesso")),
        "descricao": _decode(att.get("descricao")),
        "acesso": _ACESSO.get(str(status.get("nivelAcessoGlobal", "0")), "desconhecido"),
        "atribuido_unidade_atual": atribuido_unidade_atual(att, unidade_ativa_id),
        "gerado_ou_recebido": "gerado" if str(status.get("processoGeradoRecebido")) == "G" else "recebido",
        "em_tramitacao": _sn(status.get("processoEmTramitacao")),
        "sobrestado": _sn(status.get("processoSobrestado")),
        "bloqueado": _sn(status.get("processoBloqueado")),
        "tem_documento_novo": _sn(status.get("documentoNovo")),
        "tem_anotacao": _sn(status.get("anotacao")),
        "tem_ciencia": _sn(status.get("ciencia")),
        "marcador": _marcador(att),
        "prazo": prazo,
        "aberto_em_unidades": aberto_em,
    }

    if incluir_detalhe:
        ci = att.get("ciencias")
        out["ciencias"] = ci if isinstance(ci, list) else []
        an = att.get("anotacoes")
        out["anotacoes"] = an if isinstance(an, list) else []

    return out
