"""Testes de ordenação, paginação e list view de sei_listar_documentos.

Em processo antigo e volumoso a listagem do wssei (ASC, sem parâmetro de ordem)
esconde justamente os documentos recém-criados, e o payload bruto é grande
demais para a janela de contexto.
"""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import mcp_seipro.server as srv  # noqa: E402
from mcp_seipro.sei_client import SEIClient  # noqa: E402


def _doc(n: int) -> dict:
    return {
        "id": str(3000000 + n),
        "atributos": {
            "protocoloFormatado": str(2900000 + n),
            "tipo": "Despacho",
            "tipoDocumento": "I",
            "siglaUnidade": "GPF",
            "nomeComposto": f"Despacho GPF {2900000 + n}",
            "idProcedimento": "999",
            "mimeType": "html",
            "status": {
                "documentoAssinado": "S" if n % 2 else "N",
                "documentoCancelado": "N",
                "documentoRestrito": "N",
                "documentoSigiloso": "N",
                "podeVisualizarDocumento": "S",
            },
        },
    }


class _Ctx:
    def __init__(self, cli):
        self.request_context = type("R", (), {"lifespan_context": {"sei": cli}})()


class _FakeClient:
    """Processo com 488 documentos, servidos em páginas de 200."""

    def __init__(self, total=488):
        self.todos = [_doc(i) for i in range(total)]
        self.paginas_pedidas = []

    async def listar_documentos_pagina(self, id_proc, limit=200, start=0):
        self.paginas_pedidas.append(start)
        ini = start * limit
        return {"documentos": self.todos[ini:ini + limit], "total": len(self.todos)}

    async def listar_documentos_todos(self, id_proc, max_paginas=25, tam_pagina=200):
        return await SEIClient.listar_documentos_todos(self, id_proc, max_paginas, tam_pagina)


def _run(cli, **kwargs):
    orig = srv._resolver_processo

    async def fake_resolver(c, ref):
        return "999"

    srv._resolver_processo = fake_resolver
    try:
        return json.loads(asyncio.run(
            srv.sei_listar_documentos(protocolo_formatado="50300.1/2018-67",
                                      ctx=_Ctx(cli), **kwargs)
        ))
    finally:
        srv._resolver_processo = orig


def test_desc_traz_os_mais_recentes_primeiro():
    cli = _FakeClient()
    res = _run(cli, ordem="desc", limite=3)
    protos = [d["protocoloFormatado"] for d in res["documentos"]]
    assert protos == ["2900487", "2900486", "2900485"], protos
    assert res["total"] == 488
    assert res["retornados"] == 3
    assert res["truncado"] is False


def test_desc_pagina_o_processo_inteiro():
    cli = _FakeClient()
    _run(cli, ordem="desc", limite=5)
    # 488 itens não cabem numa página de 200 → precisa varrer tudo para ordenar
    assert cli.paginas_pedidas == [0, 1, 2], cli.paginas_pedidas


def test_asc_no_inicio_usa_uma_pagina_so():
    cli = _FakeClient()
    res = _run(cli, ordem="asc", limite=10)
    assert cli.paginas_pedidas == [0], "recorte inicial não deve varrer o processo"
    assert res["documentos"][0]["protocoloFormatado"] == "2900000"


def test_offset_em_itens():
    cli = _FakeClient()
    res = _run(cli, ordem="asc", limite=2, offset=3)
    assert [d["protocoloFormatado"] for d in res["documentos"]] == ["2900003", "2900004"]
    assert res["offset"] == 3


def test_resumido_enxuga_o_payload():
    cli = _FakeClient()
    resumido = _run(cli, limite=1)
    completo = _run(cli, limite=1, resumido=False)
    assert set(resumido["documentos"][0]) == {
        "id", "protocoloFormatado", "tipo", "tipo_documento", "unidade", "nome",
        "assinado", "cancelado", "acesso",
    }
    assert len(json.dumps(resumido)) < len(json.dumps(completo))
    assert "status" in completo["documentos"][0]["atributos"], "bruto preservado"


def test_shape_deriva_flags_e_acesso():
    d = _doc(1)
    d["atributos"]["status"]["documentoRestrito"] = "S"
    out = srv._shape_documento_resumido(d)
    assert out["assinado"] is True and out["cancelado"] is False
    assert out["acesso"] == "restrito"
    assert out["tipo_documento"] == "I" and out["unidade"] == "GPF"


def test_shape_nao_estoura_com_item_malformado():
    assert srv._shape_documento_resumido({})["id"] == ""
    assert srv._shape_documento_resumido({"id": 7, "atributos": {}})["id"] == "7"


def test_listar_todos_marca_truncado_no_teto():
    cli = _FakeClient(total=488)
    res = asyncio.run(SEIClient.listar_documentos_todos(cli, "999", max_paginas=1))
    assert res["truncado"] is True and len(res["documentos"]) == 200
    assert res["total"] == 488


if __name__ == "__main__":
    import traceback
    mod = sys.modules[__name__]
    testes = [getattr(mod, n) for n in dir(mod) if n.startswith("test_")]
    ok = 0
    for t in testes:
        try:
            t(); print(f"  ✅ {t.__name__}"); ok += 1
        except Exception:
            print(f"  ❌ {t.__name__}"); traceback.print_exc()
    print(f"\n{ok}/{len(testes)} passaram")
    sys.exit(0 if ok == len(testes) else 1)
