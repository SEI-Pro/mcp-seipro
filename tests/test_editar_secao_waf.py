"""Testes do contorno automático do bloqueio de WAF no sei_editar_secao.

Mocka o cliente para exercitar: detecção do WAF → reenvio do cabeçalho base64
vazio → verificação de regeneração (sucesso) ou abort por corrupção.
"""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import mcp_seipro.server as srv  # noqa: E402
from mcp_seipro.sei_client import SEICloudflareBlocked  # noqa: E402
from mcp_seipro.shaping import _sn  # noqa: F401,E402 (garante path do pacote)

HEADER_B64 = '&lt;img src=&quot;data:image/png;base64,AAAABBBB&quot;&gt;'


class _Ctx:
    def __init__(self, cli):
        self.request_context = type("R", (), {"lifespan_context": {"sei": cli}})()


class _FakeClient:
    """header 656 (somenteLeitura, base64) + editável 658."""
    def __init__(self, regenera=True):
        self.alterar_calls = []
        self._regenera = regenera

    async def listar_secao_documento(self, doc_id):
        # após a escrita benigna, o cabeçalho regenera (volta base64) ou não
        header = HEADER_B64
        if self.alterar_calls and not self._regenera:
            header = "&lt;p&gt;&amp;nbsp;&lt;/p&gt;"  # ficou o placeholder → corrompido
        return {"ultimaVersaoDocumento": "1", "secoes": [
            {"id": "1", "idSecaoModelo": "656", "somenteLeitura": "S", "conteudo": header},
            {"id": "2", "idSecaoModelo": "658", "somenteLeitura": "N", "conteudo": "&lt;p&gt;orig&lt;/p&gt;"},
        ]}

    async def alterar_secao_documento(self, id_documento, secoes, versao):
        self.alterar_calls.append(secoes)
        if len(self.alterar_calls) == 1:
            raise SEICloudflareBlocked("WAF managed rule block")
        return [{"sucesso": True}]


def _run(cli, secoes, **kwargs):
    # neutraliza resolução/identidade (dependeriam de REST) e roda a tool real
    orig_res, orig_ident = srv._resolver_documento, srv._identidade_documento

    async def fake_res(c, ref):
        return str(ref), "I"

    async def fake_ident(c, d):
        return {"id_documento": str(d)}

    srv._resolver_documento = fake_res
    srv._identidade_documento = fake_ident
    kwargs.setdefault("validar_referencias", False)
    try:
        return json.loads(asyncio.run(
            srv.sei_editar_secao(id_documento="3", secoes=secoes, versao="",
                                 ctx=_Ctx(cli), **kwargs)
        ))
    finally:
        srv._resolver_documento, srv._identidade_documento = orig_res, orig_ident


def test_helper_detecta_cabecalho_base64():
    assert srv._secao_cabecalho_base64({"somenteLeitura": "S", "conteudo": HEADER_B64}) is True
    assert srv._secao_cabecalho_base64({"somenteLeitura": "N", "conteudo": HEADER_B64}) is False
    assert srv._secao_cabecalho_base64({"somenteLeitura": "S", "conteudo": "<p>x</p>"}) is False


def test_fallback_contorna_e_preserva():
    cli = _FakeClient(regenera=True)
    res = _run(cli, [{"idSecaoModelo": "658", "conteudo": "<p>novo</p>"}])
    assert res.get("error") is None, res
    assert res.get("_waf_contornado"), "deveria ter contornado o WAF"
    # a 2ª chamada de alterar benignou SÓ o cabeçalho 656, manteve a 658
    seg = cli.alterar_calls[1]
    h656 = next(s for s in seg if s["idSecaoModelo"] == "656")
    h658 = next(s for s in seg if s["idSecaoModelo"] == "658")
    assert "base64" not in h656["conteudo"] and "nbsp" in h656["conteudo"]
    assert "novo" in h658["conteudo"]


def test_fallback_aborta_se_corromper():
    cli = _FakeClient(regenera=False)  # cabeçalho NÃO regenera → corrupção
    res = _run(cli, [{"idSecaoModelo": "658", "conteudo": "<p>novo</p>"}])
    assert res.get("error"), "deveria abortar com erro de corrupção"
    assert "não foram regeneradas" in res["error"].lower() or "regeneradas" in res["error"].lower()


def test_mensagem_de_falha_lista_tentativas_sem_prescrever_causa():
    """A mensagem antiga afirmava causa raiz não medida ('está em conteúdo
    não-regenerável', 'só a exceção de WAF resolve') e levava a desistir da API."""
    class SempreBloqueia(_FakeClient):
        async def alterar_secao_documento(self, id_documento, secoes, versao):
            self.alterar_calls.append(secoes)
            raise SEICloudflareBlocked("WAF managed rule block")

    res = _run(SempreBloqueia(), [{"idSecaoModelo": "658", "conteudo": "<p>x</p>"}])
    erro = res.get("error", "")
    assert erro, "deveria falhar após esgotar a escada"
    assert "Tentativas:" in erro, erro
    assert "envio normalizado" in erro and "cabeçalho base64" in erro, erro
    assert "NÃO está determinada" in erro or "não está determinada" in erro.lower(), erro
    assert "dry_run" in erro, "deve apontar o caminho de isolamento"
    # nada de conclusão categórica sobre onde está o gatilho
    assert "só a exceção de WAF" not in erro.lower()
    assert res.get("erro_origem") == "cloudflare_waf", res


def test_dry_run_nao_grava_e_devolve_payload():
    cli = _FakeClient()
    res = _run(cli, [{"idSecaoModelo": "658", "conteudo": "<p>novo</p>"}], dry_run=True)
    assert res["dry_run"] is True and res["nada_foi_gravado"] is True
    assert cli.alterar_calls == [], "dry_run não pode fazer POST"
    modelos = {s["idSecaoModelo"] for s in res["secoes"]}
    assert modelos == {"656", "658"}, "payload deve trazer TODAS as seções"
    assert res["bytes_total"] > 0
    resumo = {r["idSecaoModelo"]: r for r in res["resumo"]}
    assert resumo["658"]["alterada_por_voce"] is True
    assert resumo["656"]["cabecalho_base64_regeneravel"] is True


def test_entidades_normalizadas_no_payload_enviado():
    cli = _FakeClient()
    _run(cli, [{"idSecaoModelo": "658", "conteudo": "<p>Aten&ccedil;&atilde;o&nbsp;final</p>"}])
    enviado = {s["idSecaoModelo"]: s["conteudo"] for s in cli.alterar_calls[0]}
    assert "&ccedil;" not in enviado["658"] and "Atenção" in enviado["658"]
    assert "&nbsp;" not in enviado["658"]
    # a estrutura do HTML permanece intacta
    assert enviado["658"].startswith("<p>") and enviado["658"].endswith("</p>")


def test_secao_inexistente_nao_vira_falso_sucesso():
    cli = _FakeClient()
    res = _run(cli, [{"idSecaoModelo": "999", "conteudo": "<p>x</p>"}])
    assert res.get("error"), "gravar num idSecaoModelo inexistente não pode dar sucesso"
    assert "999" in res["error"] and "658" in res["error"], res["error"]
    assert cli.alterar_calls == [], "não deve chegar a fazer o POST"


def test_secao_inexistente_parcial_vira_aviso():
    cli = _FakeClient()
    res = _run(cli, [
        {"idSecaoModelo": "658", "conteudo": "<p>vale</p>"},
        {"idSecaoModelo": "999", "conteudo": "<p>não existe</p>"},
    ])
    assert not res.get("error"), res
    avisos = res.get("_avisos") or []
    assert avisos and avisos[0]["secoes_ignoradas"] == ["999"]
    enviado = {s["idSecaoModelo"]: s["conteudo"] for s in cli.alterar_calls[0]}
    assert "vale" in enviado["658"], "a seção válida deve ter sido gravada"


def test_ancora_com_numero_sei_gera_aviso():
    class ComProtocolo(_FakeClient):
        async def consultar_documento_interno_formatado(self, numero):
            if numero == "2949729":
                return {"idDocumento": "3151234", "nomeDocumento": "Despacho 2949729"}
            raise Exception("não encontrado")

    cli = ComProtocolo()
    res = _run(
        cli,
        [{"idSecaoModelo": "658",
          "conteudo": '<a class="ancoraSei" id="lnkSei2949729">SEI 2949729</a>'}],
        validar_referencias=True,
    )
    avisos = res.get("_avisos") or []
    assert avisos, "âncora com nº SEI deveria gerar aviso"
    assert avisos[0]["id_interno_correto"] == "3151234"
    assert "lnkSei3151234" in avisos[0]["correcao"]


def test_ancora_com_id_interno_nao_gera_aviso():
    class SemProtocolo(_FakeClient):
        async def consultar_documento_interno_formatado(self, numero):
            raise Exception("Documento não encontrado")  # não é protocoloFormatado

    res = _run(
        SemProtocolo(),
        [{"idSecaoModelo": "658", "conteudo": '<a id="lnkSei3151234">ref</a>'}],
        validar_referencias=True,
    )
    assert not res.get("_avisos"), res.get("_avisos")


def test_sem_cabecalho_base64_propaga_erro_waf():
    class SemHeader(_FakeClient):
        async def listar_secao_documento(self, doc_id):
            return {"ultimaVersaoDocumento": "1", "secoes": [
                {"id": "2", "idSecaoModelo": "658", "somenteLeitura": "N", "conteudo": "&lt;p&gt;orig&lt;/p&gt;"},
            ]}
    res = _run(SemHeader(), [{"idSecaoModelo": "658", "conteudo": "<p>x</p>"}])
    assert res.get("error"), "sem cabeçalho regenerável, o erro de WAF deve propagar"


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
