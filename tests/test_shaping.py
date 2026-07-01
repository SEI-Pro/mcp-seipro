"""Testes do módulo de shaping da listagem de processos (defeitos D-1..D-7).

Fixtures SINTÉTICAS (nomes fictícios) espelhando as 4 estruturas reais
observadas na caixa GPF. Roda com pytest OU direto: `python tests/test_shaping.py`.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from mcp_seipro.shaping import (  # noqa: E402
    _limpar_marcador_nome,
    _parse_data_iso,
    _sn,
    atribuido_unidade_atual,
    shape_processo_resumido,
)

GPF = "110000037"


def _att(**kw):
    base = {
        "idProcedimento": "1",
        "numero": "50300.000000/2026-00",
        "tipoProcesso": "Tipo X",
        "descricao": "desc",
        "usuarioAtribuido": None,
        "unidade": {},
        "ciencias": "",
        "marcador": [],
        "dadosAbertura": {"unidades": [], "lista": []},
        "anotacoes": [],
        "status": {},
    }
    base.update(kw)
    return base


# ---------------------------------------------------------------- D-1
def test_d1_caso_conteineres_regra1_dadosAbertura():
    """Atribuição da sessão vem do sufixo (atribuído a X) em dadosAbertura."""
    att = _att(
        unidade={"idUnidade": "110000061", "sigla": "SFC"},
        usuarioAtribuido={"idUsuario": "3", "nome": "Raquel Fictícia"},
        dadosAbertura={
            "unidades": [{"id": "110000343", "nome": "GEF"}, {"id": GPF, "nome": "GPF"}],
            "lista": [{"sigla": "GEF"}, {"sigla": "GPF (atribuído a Fulano de Tal)"}],
        },
    )
    r = atribuido_unidade_atual(att, GPF)
    assert r == {"id_usuario": None, "nome": "Fulano de Tal"}, r


def test_d1_caso_paint_regra3_somente_na_unidade():
    """Aberto só na unidade da sessão → usa usuarioAtribuido mesmo com unidade-topo diferente."""
    att = _att(
        unidade={"idUnidade": "110000008", "sigla": "AUD"},
        usuarioAtribuido={"idUsuario": "266", "nome": "Sicrano de Souza"},
        dadosAbertura={"unidades": [{"id": GPF, "nome": "GPF"}], "lista": [{"sigla": "GPF"}]},
    )
    r = atribuido_unidade_atual(att, GPF)
    assert r == {"id_usuario": "266", "nome": "Sicrano de Souza"}, r


def test_d1_caso_computadores_regra2_unidade_topo():
    """unidade-topo == sessão (sem sufixo em dadosAbertura) → usa usuarioAtribuido."""
    att = _att(
        unidade={"idUnidade": GPF, "sigla": "GPF"},
        usuarioAtribuido={"idUsuario": "2073", "nome": "Beltrano Heim"},
        dadosAbertura={
            "unidades": [{"id": GPF, "nome": "GPF"}, {"id": "110000201", "nome": "GREBL"}],
            "lista": [{"sigla": "GPF"}, {"sigla": "GREBL (atribuído a Outro)"}],
        },
    )
    r = atribuido_unidade_atual(att, GPF)
    assert r == {"id_usuario": "2073", "nome": "Beltrano Heim"}, r


def test_d1_caso_sem_atribuicao_null():
    """Aberto em várias unidades, sessão sem sufixo, topo é outra unidade → None."""
    att = _att(
        unidade={"idUnidade": "110000058", "sigla": "SAF"},
        usuarioAtribuido=None,
        dadosAbertura={
            "unidades": [{"id": "110000271", "nome": "ARINT"}, {"id": GPF, "nome": "GPF"}],
            "lista": [{"sigla": "ARINT (atribuído a Alguém)"}, {"sigla": "GPF"}],
        },
    )
    assert atribuido_unidade_atual(att, GPF) is None


def test_d1_sem_unidade_ativa_retorna_none():
    att = _att(unidade={"idUnidade": GPF}, usuarioAtribuido={"idUsuario": "1", "nome": "X"})
    assert atribuido_unidade_atual(att, None) is None


# ---------------------------------------------------------------- D-3 tipos
def test_d3_sn_normaliza():
    assert _sn("S") is True and _sn("N") is False
    assert _sn(True) is True and _sn(False) is False
    assert _sn("") is False


def test_d3_flags_sempre_boolean_e_marcador_null():
    att = _att(status={"processoEmTramitacao": "S", "processoSobrestado": "N",
                       "processoBloqueado": "N", "documentoNovo": "S",
                       "anotacao": "N", "ciencia": "S", "nivelAcessoGlobal": "0",
                       "processoGeradoRecebido": "G"})
    out = shape_processo_resumido(att_wrap(att), GPF)
    for k in ("em_tramitacao", "sobrestado", "bloqueado", "tem_documento_novo",
              "tem_anotacao", "tem_ciencia"):
        assert isinstance(out[k], bool), (k, out[k])
    assert out["em_tramitacao"] is True and out["sobrestado"] is False
    assert out["marcador"] is None
    assert out["acesso"] == "publico"
    assert out["gerado_ou_recebido"] == "gerado"


def test_d3_ciencias_fora_da_listview_por_padrao():
    att = _att(ciencias="")  # às vezes vem string vazia
    out = shape_processo_resumido(att_wrap(att), GPF)
    assert "ciencias" not in out
    out2 = shape_processo_resumido(att_wrap(att), GPF, incluir_detalhe=True)
    assert out2["ciencias"] == []  # normalizado para lista


# ---------------------------------------------------------------- D-4 texto
def test_d4_decodifica_entidades_html():
    att = _att(descricao="EPI&#039;s", tipoProcesso="PPF 2020 &amp; PAF 2024")
    out = shape_processo_resumido(att_wrap(att), GPF)
    assert out["descricao"] == "EPI's"
    assert out["tipo"] == "PPF 2020 & PAF 2024"


# ---------------------------------------------------------------- D-5 prazo
def test_d5_prazo_iso_de_marcador_texto():
    att = _att(marcador=[{"nome": "Com prazo", "texto": "Ate 03/03/2024 23:59", "descricaoCor": "Rosa"}])
    out = shape_processo_resumido(att_wrap(att), GPF)
    assert out["prazo"] == "2024-03-03", out["prazo"]


def test_d5_prazo_retornoData_tem_precedencia():
    att = _att(status={"retornoData": "10/01/2025"},
               marcador=[{"nome": "x", "texto": "Ate 03/03/2024", "descricaoCor": "Rosa"}])
    out = shape_processo_resumido(att_wrap(att), GPF)
    assert out["prazo"] == "2025-01-10", out["prazo"]


def test_d5_prazo_null_quando_ausente():
    out = shape_processo_resumido(att_wrap(_att()), GPF)
    assert out["prazo"] is None


# ---------------------------------------------------------------- D-7 marcador hex
def test_d7_remove_hex_do_nome_do_marcador():
    assert _limpar_marcador_nome("Administrativo (Recursos logísticos) #25796b") == \
        "Administrativo (Recursos logísticos)"
    att = _att(marcador=[{"nome": "Rótulo #abcdef", "texto": "", "descricaoCor": "Verde"}])
    out = shape_processo_resumido(att_wrap(att), GPF)
    assert out["marcador"] == {"nome": "Rótulo", "cor": "verde"}


# ---------------------------------------------------------------- schema
def test_schema_aberto_em_unidades_e_protocolo():
    att = _att(numero="50300.015295/2026-50",
               dadosAbertura={"unidades": [{"id": GPF, "nome": "GPF"}, {"id": "2", "nome": "SGE"}],
                              "lista": [{"sigla": "GPF"}, {"sigla": "SGE"}]})
    out = shape_processo_resumido(att_wrap(att), GPF)
    assert out["aberto_em_unidades"] == ["GPF", "SGE"]
    assert out["protocolo"] == "50300.015295/2026-50"


# ---------------------------------------------------------------- robustez
def test_entrada_malformada_nao_estoura():
    """Itens não-dict em unidades/lista/marcador não podem derrubar a listagem."""
    att = _att(
        usuarioAtribuido={"idUsuario": "1", "nome": "X"},
        marcador=[None, {"nome": "ok", "descricaoCor": "Azul"}],
        dadosAbertura={"unidades": [None, "lixo", {"id": GPF, "nome": "GPF"}],
                       "lista": [None, {"sigla": "GPF"}]},
    )
    out = shape_processo_resumido(att_wrap(att), GPF)  # não deve levantar
    assert out["marcador"] is None  # 1º marcador é None → ignora item ruim
    # unidade da sessão existe (3ª), sem sufixo; topo != sessão; >1 unidade → null
    assert out["atribuido_unidade_atual"] is None
    # marcador não-dict em 1ª posição → _marcador retorna None sem crashar
    assert atribuido_unidade_atual(_att(dadosAbertura={"unidades": [None], "lista": []}), GPF) is None


def att_wrap(att):
    """Envolve `atributos` num item de listagem (com id/status de topo)."""
    return {"id": att.get("idProcedimento", "1"), "status": "P",
            "seiNumMaxDocsPasta": "20", "atributos": att}


if __name__ == "__main__":
    import traceback
    mod = sys.modules[__name__]
    testes = [getattr(mod, n) for n in dir(mod) if n.startswith("test_")]
    ok = 0
    for t in testes:
        try:
            t()
            print(f"  ✅ {t.__name__}")
            ok += 1
        except Exception:
            print(f"  ❌ {t.__name__}")
            traceback.print_exc()
    print(f"\n{ok}/{len(testes)} passaram")
    sys.exit(0 if ok == len(testes) else 1)
