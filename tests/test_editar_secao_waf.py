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
    """cabeçalho 656 (dinâmica, base64) + editável 658 + 660 (somenteLeitura NÃO dinâmica)."""
    def __init__(self, regenera=True, bloqueia_primeira=False):
        self.alterar_calls = []
        self._regenera = regenera
        self._bloqueia_primeira = bloqueia_primeira

    async def listar_secao_documento(self, doc_id):
        # o SEI regenera a seção dinâmica ao salvar (volta o base64) ou não
        header = HEADER_B64
        if self.alterar_calls and not self._regenera:
            header = ""  # ficou vazia → o SEI NÃO regenerou
        return {"ultimaVersaoDocumento": "1", "secoes": [
            {"id": "1", "idSecaoModelo": "656", "somenteLeitura": "S",
             "DinamicaSecaoDocumento": "S", "conteudo": header},
            {"id": "2", "idSecaoModelo": "658", "somenteLeitura": "N",
             "DinamicaSecaoDocumento": "N", "conteudo": "&lt;p&gt;orig&lt;/p&gt;"},
            {"id": "3", "idSecaoModelo": "660", "somenteLeitura": "S",
             "DinamicaSecaoDocumento": "N", "conteudo": "&lt;p&gt;fixa&lt;/p&gt;"},
        ]}

    async def alterar_secao_documento(self, id_documento, secoes, versao):
        self.alterar_calls.append(secoes)
        if self._bloqueia_primeira and len(self.alterar_calls) == 1:
            raise SEICloudflareBlocked("WAF managed rule block")
        return [{"sucesso": True}]


def test_secao_dinamica_vai_vazia_na_primeira_tentativa():
    """O SEI descarta o conteúdo de seção dinâmica (EditorRN::montarConteudoSecao
    usa ConteudoOriginal do banco). Reenviá-la só engorda o corpo do POST — que é
    exatamente o que o WAF inspeciona. Deve sair vazia já na 1ª tentativa."""
    cli = _FakeClient()
    res = _run(cli, [{"idSecaoModelo": "658", "conteudo": "<p>novo</p>"}])
    assert res.get("error") is None, res
    assert len(cli.alterar_calls) == 1, "não deve precisar de fallback"
    enviado = {s["idSecaoModelo"]: s["conteudo"] for s in cli.alterar_calls[0]}
    assert enviado["656"] == "", "seção dinâmica deveria ir vazia"
    assert "novo" in enviado["658"]


def test_somenteLeitura_nao_dinamica_nunca_e_esvaziada():
    """Caso vizinho perigoso: somenteLeitura='S' com SinDinamica='N' cai no ramo
    else do montarConteudoSecao e GRAVA o que enviamos — esvaziar apagaria a seção."""
    cli = _FakeClient()
    _run(cli, [{"idSecaoModelo": "658", "conteudo": "<p>novo</p>"}])
    enviado = {s["idSecaoModelo"]: s["conteudo"] for s in cli.alterar_calls[0]}
    assert "fixa" in enviado["660"], "seção somenteLeitura NÃO dinâmica não pode ir vazia"


def test_aborta_se_secao_dinamica_nao_regenerar():
    cli = _FakeClient(regenera=False)
    res = _run(cli, [{"idSecaoModelo": "658", "conteudo": "<p>novo</p>"}])
    assert res.get("error"), "deveria abortar ao detectar que o SEI não regenerou"
    assert "regener" in res["error"].lower(), res["error"]


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


def test_converte_cor_hex_so_dentro_de_style():
    """O '#' de cor hex em atributo é o que o managed ruleset do Cloudflare pontua
    como injeção. rgb() renderiza idêntico. Mas a troca TEM que ser restrita ao
    style: um regex solto casaria entidades numéricas (&#233;) e corromperia texto."""
    f = srv._converter_cores_hex
    out, n = f('<p style="color:#000000;font-size:16px;">x</p>')
    assert "rgb(0,0,0)" in out and "#000000" not in out and n == 1
    out, n = f('<hr style="border-top:medium double #333" />')
    assert "rgb(51,51,51)" in out and n == 1
    # entidade numérica fora de style: intocada (seria corrupção)
    out, n = f("<p>Aten&#231;&#227;o &#233; teste</p>")
    assert out == "<p>Aten&#231;&#227;o &#233; teste</p>" and n == 0
    # '#' em texto comum: intocado
    out, n = f("<p>item #333 do contrato</p>")
    assert out == "<p>item #333 do contrato</p>" and n == 0
    # aspas simples também valem
    out, n = f("<p style='color:#ABC'>x</p>")
    assert "rgb(170,187,204)" in out and n == 1


def test_fallback_troca_hex_por_rgb_quando_o_waf_bloqueia():
    class BloqueiaComHex(_FakeClient):
        """Só bloqueia enquanto houver '#' de cor no corpo."""
        async def alterar_secao_documento(self, id_documento, secoes, versao):
            self.alterar_calls.append(secoes)
            if any("#000000" in s["conteudo"] for s in secoes):
                raise SEICloudflareBlocked("WAF managed rule block")
            return [{"sucesso": True}]

    cli = BloqueiaComHex()
    res = _run(cli, [{"idSecaoModelo": "658",
                      "conteudo": '<p style="color:#000000">novo</p>'}])
    assert res.get("error") is None, res
    assert len(cli.alterar_calls) == 2, "deveria ter tentado de novo sem o hex"
    enviado = {s["idSecaoModelo"]: s["conteudo"] for s in cli.alterar_calls[1]}
    assert "rgb(0,0,0)" in enviado["658"] and "novo" in enviado["658"]
    assert res.get("_waf_contornado"), res


def test_helper_detecta_secao_regenerada():
    """O critério é SinDinamica, não somenteLeitura — ver montarConteudoSecao."""
    assert srv._secao_regenerada_pelo_sei({"DinamicaSecaoDocumento": "S"}) is True
    assert srv._secao_regenerada_pelo_sei({"DinamicaSecaoDocumento": "N"}) is False
    # somenteLeitura sozinho NÃO autoriza esvaziar
    assert srv._secao_regenerada_pelo_sei(
        {"somenteLeitura": "S", "DinamicaSecaoDocumento": "N", "conteudo": HEADER_B64}
    ) is False


class _SempreBloqueia(_FakeClient):
    async def alterar_secao_documento(self, id_documento, secoes, versao):
        self.alterar_calls.append(secoes)
        raise SEICloudflareBlocked("WAF managed rule block")


def test_mensagem_de_falha_lista_tentativas_sem_prescrever_causa():
    """A mensagem antiga afirmava causa raiz não medida ('está em conteúdo
    não-regenerável', 'só a exceção de WAF resolve') e levava a desistir da API."""
    res = _run(_SempreBloqueia(), [{"idSecaoModelo": "658", "conteudo": "<p>x</p>"}])
    erro = res.get("error", "")
    assert erro, "deveria falhar após esgotar a escada"
    assert "Tentativas:" in erro, erro
    assert "dry_run" in erro, "deve apontar o caminho de isolamento"
    assert "só a exceção de WAF" not in erro.lower()
    assert res.get("erro_origem") == "cloudflare_waf", res


def test_localizador_de_gatilho_nao_grava_e_aponta_a_secao():
    """Quando o WAF barra, a tool localiza o trecho culpado com sondas de versão
    inválida — que o SEI recusa ANTES de escrever (EditorRN valida versao primeiro)."""
    class BloqueiaSoCom660(_FakeClient):
        """Só bloqueia enquanto a seção 660 for enviada com conteúdo."""
        def __init__(self):
            super().__init__()
            self.versoes_sondadas = []

        async def alterar_secao_documento(self, id_documento, secoes, versao):
            self.alterar_calls.append(secoes)
            self.versoes_sondadas.append(versao)
            c660 = next((s["conteudo"] for s in secoes if s["idSecaoModelo"] == "660"), "")
            if "fixa" in c660:
                raise SEICloudflareBlocked("WAF managed rule block")
            raise Exception("Existe uma nova versão (nº 1) para este documento")

    cli = BloqueiaSoCom660()
    res = _run(cli, [{"idSecaoModelo": "658", "conteudo": "<p>x</p>"}])
    erro = res.get("error", "")
    assert "660" in erro, f"deveria apontar a seção culpada: {erro}"
    # toda sonda usou versão inválida — nenhuma poderia ter gravado
    sondas = cli.versoes_sondadas[1:]
    assert sondas and all(v == srv._VERSAO_SONDA_WAF for v in sondas), cli.versoes_sondadas


def test_dry_run_nao_grava_e_devolve_payload():
    cli = _FakeClient()
    res = _run(cli, [{"idSecaoModelo": "658", "conteudo": "<p>novo</p>"}], dry_run=True)
    assert res["dry_run"] is True and res["nada_foi_gravado"] is True
    assert cli.alterar_calls == [], "dry_run não pode fazer POST"
    modelos = {s["idSecaoModelo"] for s in res["secoes"]}
    assert modelos == {"656", "658", "660"}, "payload deve trazer TODAS as seções"
    assert res["bytes_total"] > 0
    resumo = {r["idSecaoModelo"]: r for r in res["resumo"]}
    assert resumo["658"]["alterada_por_voce"] is True
    assert resumo["656"]["regenerada_pelo_sei"] is True
    assert resumo["660"]["regenerada_pelo_sei"] is False


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


def test_alterar_secao_dinamica_avisa_que_o_sei_ignora():
    """Pedir alteração numa seção dinâmica é no-op silencioso no SEI — tem que avisar."""
    cli = _FakeClient()
    res = _run(cli, [
        {"idSecaoModelo": "658", "conteudo": "<p>vale</p>"},
        {"idSecaoModelo": "656", "conteudo": "<p>não vai pegar</p>"},
    ])
    avisos = json.dumps(res.get("_avisos") or [], ensure_ascii=False)
    assert "656" in avisos and "dinâmic" in avisos.lower(), res.get("_avisos")


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


class _ComProtocolo(_FakeClient):
    """nº SEI 2949729 corresponde ao id interno 3151234."""
    async def consultar_documento_interno_formatado(self, numero):
        if numero == "2949729":
            return {"idDocumento": "3151234", "nomeDocumento": "Despacho 2949729"}
        raise Exception("não encontrado")


def test_ancora_com_numero_sei_gera_aviso():
    res = _run(
        _ComProtocolo(),
        [{"idSecaoModelo": "658",
          "conteudo": '<a class="ancoraSei" id="lnkSei2949729">2949729</a>'}],
        validar_referencias=True,
    )
    avisos = res.get("_avisos") or []
    assert avisos, "âncora cujo id não corresponde ao texto deveria avisar"
    assert avisos[0]["id_interno_correto"] == "3151234"
    assert "lnkSei3151234" in avisos[0]["correcao"]


def test_ancora_com_id_interno_nao_gera_aviso():
    """id bate com o texto do link → correta, nada a avisar."""
    res = _run(
        _ComProtocolo(),
        [{"idSecaoModelo": "658", "conteudo": '<a id="lnkSei3151234">2949729</a>'}],
        validar_referencias=True,
    )
    assert not res.get("_avisos"), res.get("_avisos")


def test_ancora_de_processo_nunca_gera_aviso():
    """Regressão real: o texto é nº de PROCESSO, e idProtocolo abrange processos.
    A versão anterior avisava aqui e induziu a 'correção' de uma âncora correta,
    que o SEI então descartou — o link sumiu do documento."""
    class ColideComDocumento(_FakeClient):
        async def consultar_documento_interno_formatado(self, numero):
            # 2378553 TAMBÉM existe como nº SEI de um documento — a colisão que
            # produzia o falso positivo.
            return {"idDocumento": "2600907", "nomeDocumento": "Anexo (2378553)"}

    res = _run(
        ColideComDocumento(),
        [{"idSecaoModelo": "658",
          "conteudo": '<a class="ancora_sei" id="lnkSei2378553">'
                      '50300.004460/2024-86</a>'}],
        validar_referencias=True,
    )
    assert not res.get("_avisos"), res.get("_avisos")


def test_sem_secao_dinamica_propaga_erro_waf():
    class SemDinamica(_SempreBloqueia):
        async def listar_secao_documento(self, doc_id):
            return {"ultimaVersaoDocumento": "1", "secoes": [
                {"id": "2", "idSecaoModelo": "658", "somenteLeitura": "N",
                 "DinamicaSecaoDocumento": "N", "conteudo": "&lt;p&gt;orig&lt;/p&gt;"},
            ]}
    res = _run(SemDinamica(), [{"idSecaoModelo": "658", "conteudo": "<p>x</p>"}])
    assert res.get("error"), "sem seção regenerável, o erro de WAF deve propagar"


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
