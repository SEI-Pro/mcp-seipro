"""Testes do upload de documento externo por conteúdo em memória (base64).

O caminho só-por-arquivo_path presume que o arquivo está no disco do SERVIDOR
onde o MCP roda — o que quebra qualquer fluxo em que o PDF nasce do outro lado
da rede (gerado na conversa, vindo do Drive, baixado de outra tool).
"""
import asyncio
import base64
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import mcp_seipro.server as srv  # noqa: E402
from mcp_seipro.sei_client import SEIClient  # noqa: E402

PDF = b"%PDF-1.4\nconteudo de teste\n%%EOF"
PDF_B64 = base64.b64encode(PDF).decode()


class _Ctx:
    def __init__(self, cli):
        self.request_context = type("R", (), {"lifespan_context": {"sei": cli}})()


class _FakeClient:
    def __init__(self, limite_mb=25):
        self.chamadas = []
        self._limite_mb = limite_mb

    async def parametros_upload(self):
        return {"tamanhoDocDefault": self._limite_mb,
                "extensoes": [{"extensao": "pdf", "tamanho": self._limite_mb}]}

    async def criar_documento_externo(self, **kwargs):
        self.chamadas.append(kwargs)
        return {"idDocumento": "3151234", "protocoloDocumentoFormatado": "2949730"}


def _run(cli, **kwargs):
    orig = srv._resolver_processo

    async def fake_resolver(c, ref):
        return "999"

    srv._resolver_processo = fake_resolver
    try:
        return json.loads(asyncio.run(
            srv.sei_criar_documento_externo(processo="50300.1/2018-67", id_serie="99",
                                            ctx=_Ctx(cli), **kwargs)
        ))
    finally:
        srv._resolver_processo = orig


def test_base64_chega_como_bytes_ao_cliente():
    cli = _FakeClient()
    res = _run(cli, arquivo_base64=PDF_B64, nome_arquivo="parecer.pdf",
               descricao="Parecer")
    assert res["idDocumento"] == "3151234"
    chamada = cli.chamadas[0]
    assert chamada["arquivo_bytes"] == PDF
    assert chamada["nome_arquivo"] == "parecer.pdf"
    assert chamada["arquivo_path"] == ""
    assert res["bytes_enviados"] == len(PDF)


def test_aceita_data_uri():
    cli = _FakeClient()
    _run(cli, arquivo_base64=f"data:application/pdf;base64,{PDF_B64}",
         nome_arquivo="x.pdf")
    assert cli.chamadas[0]["arquivo_bytes"] == PDF


def test_nome_arquivo_obrigatorio_com_base64():
    res = _run(_FakeClient(), arquivo_base64=PDF_B64)
    assert "nome_arquivo" in res["error"]


def test_base64_invalido_e_recusado_antes_do_post():
    cli = _FakeClient()
    res = _run(cli, arquivo_base64="isto não é base64!!", nome_arquivo="x.pdf")
    assert "base64" in res["error"].lower()
    assert cli.chamadas == [], "não pode chamar o SEI com lixo"


def test_excede_limite_do_sei():
    cli = _FakeClient(limite_mb=0.00001)  # ~10 bytes
    res = _run(cli, arquivo_base64=PDF_B64, nome_arquivo="grande.pdf")
    assert "excede o limite" in res["error"]
    assert cli.chamadas == []


def test_teto_local_do_base64_independe_do_limite_do_sei():
    """O limite do SEI pode ser enorme (5124 MB na ANTAQ); o gargalo real é
    trafegar o arquivo dentro de uma mensagem MCP."""
    cli = _FakeClient(limite_mb=5124)
    original = srv._MAX_UPLOAD_BASE64_MB
    srv._MAX_UPLOAD_BASE64_MB = 0.00001  # ~10 bytes
    try:
        res = _run(cli, arquivo_base64=PDF_B64, nome_arquivo="grande.pdf")
    finally:
        srv._MAX_UPLOAD_BASE64_MB = original
    assert "teto desta tool" in res["error"] and "arquivo_path" in res["error"]
    assert cli.chamadas == []


def test_exige_um_dos_dois_caminhos():
    assert "arquivo_base64" in _run(_FakeClient())["error"]
    res = _run(_FakeClient(), arquivo_base64=PDF_B64, nome_arquivo="a.pdf",
               arquivo_path="/tmp/a.pdf")
    assert "não os dois" in res["error"]


def test_ler_arquivo_resolve_bytes_e_path(tmp_path=None):
    nome, conteudo = SEIClient._ler_arquivo(arquivo_bytes=PDF, nome_arquivo="dir/x.pdf")
    assert (nome, conteudo) == ("x.pdf", PDF), "deve usar só o basename"

    caminho = os.path.join(os.path.dirname(__file__), "_tmp_upload.pdf")
    with open(caminho, "wb") as f:
        f.write(PDF)
    try:
        assert SEIClient._ler_arquivo(arquivo_path=caminho) == ("_tmp_upload.pdf", PDF)
    finally:
        os.remove(caminho)

    for kwargs in ({}, {"arquivo_bytes": PDF}, {"arquivo_path": "/nao/existe.pdf"}):
        try:
            SEIClient._ler_arquivo(**kwargs)
            assert False, f"deveria falhar: {kwargs}"
        except Exception:
            pass


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
