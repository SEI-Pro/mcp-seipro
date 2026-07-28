"""Testes da seleção/auto-detecção de transporte (multi-órgão).

Verifica que o modo 'auto' escala para o browser SÓ quando detecta um desafio
do Cloudflare, e que órgãos sem WAF seguem em httpx. Sem lançar browser real
(o escalonamento é mockado).
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import httpx  # noqa: E402

from mcp_seipro.sei_client import (  # noqa: E402
    SEIAcessoNegado, SEIClient, SEICloudflareBlocked,
)

URL = "https://exemplo.gov.br/sei/modulos/wssei/controlador_ws.php/api/v2"


def _client(mode):
    return SEIClient(sei_url=URL, sei_usuario="u", sei_senha="p", sei_transport=mode)


def _resp_cf():
    return httpx.Response(
        403,
        headers={"cf-mitigated": "challenge", "cf-ray": "abc123", "server": "cloudflare"},
        request=httpx.Request("GET", URL + "/versao"),
    )


def _resp_ok():
    return httpx.Response(200, json={"sucesso": True},
                         request=httpx.Request("GET", URL + "/versao"))


# nome precisa bater com _is_browser_client (checa type(...).__name__ == "BrowserClient")
class BrowserClient:
    pass


def test_auto_escala_ao_detectar_cloudflare():
    c = _client("auto")
    chamou = {"v": False}

    def fake_escalate():
        chamou["v"] = True
        c._client = BrowserClient()
        c._token = None
        return True

    c._escalate_to_browser = fake_escalate
    assert asyncio.run(c._handle_cloudflare(_resp_cf())) is True
    assert chamou["v"] is True
    assert c._is_browser_client() is True


def test_httpx_forcado_nunca_escala_e_levanta():
    c = _client("httpx")
    escalou = {"v": False}
    c._escalate_to_browser = lambda: escalou.__setitem__("v", True) or True
    try:
        asyncio.run(c._handle_cloudflare(_resp_cf()))
        assert False, "deveria ter levantado SEICloudflareBlocked"
    except SEICloudflareBlocked:
        pass
    assert escalou["v"] is False, "modo httpx não pode escalar"


def test_auto_sem_playwright_levanta_erro_generico():
    c = _client("auto")
    c._escalate_to_browser = lambda: False  # simula Playwright ausente
    try:
        asyncio.run(c._handle_cloudflare(_resp_cf()))
        assert False, "deveria ter levantado"
    except SEICloudflareBlocked as e:
        msg = str(e)
        assert "playwright" in msg.lower()
        assert "ANTAQ" not in msg, "mensagem deve ser genérica (multi-órgão)"


def test_resposta_normal_nao_escala():
    c = _client("auto")
    c._escalate_to_browser = lambda: (_ for _ in ()).throw(AssertionError("não deveria escalar"))
    assert asyncio.run(c._handle_cloudflare(_resp_ok())) is False


def test_deteccao_por_corpo_just_a_moment():
    r = httpx.Response(
        403, headers={"server": "cloudflare"},
        content=b"<title>Just a moment...</title>",
        request=httpx.Request("GET", URL),
    )
    assert SEIClient._is_cloudflare_challenge(r) is True


def test_waf_block_e_detectado_e_distinto_do_challenge():
    # Bloqueio duro de WAF: server=cloudflare, 403, "Attention Required", SEM cf-mitigated
    waf = httpx.Response(
        403, headers={"server": "cloudflare", "cf-ray": "z9"},
        content=b"<title>Attention Required! | Cloudflare</title> ... /cdn-cgi/styles/cf.errors.css",
        request=httpx.Request("POST", URL + "/documento/secao/alterar"),
    )
    assert SEIClient._is_cloudflare_waf_block(waf) is True
    # não deve ser confundido com o desafio JS
    assert SEIClient._is_cloudflare_challenge(waf) is False
    c = _client("auto")
    try:
        c._raise_if_waf_block(waf)
        assert False, "deveria levantar SEICloudflareBlocked"
    except SEICloudflareBlocked as e:
        assert "waf" in str(e).lower() and "managed rule" in str(e).lower()


def test_challenge_nao_e_waf_block():
    ch = httpx.Response(403, headers={"server": "cloudflare", "cf-mitigated": "challenge"},
                        request=httpx.Request("GET", URL))
    assert SEIClient._is_cloudflare_waf_block(ch) is False


def test_raise_http_with_body_inclui_corpo():
    r = httpx.Response(500, json={"sucesso": False, "mensagem": "Conteúdo do documento incompleto."},
                       request=httpx.Request("POST", URL + "/documento/secao/alterar"))
    try:
        SEIClient._raise_http_with_body(r)
        assert False, "deveria levantar"
    except httpx.HTTPStatusError as e:
        assert "documento incompleto" in str(e), str(e)


def test_403_do_sei_nao_e_confundido_com_waf():
    """403 do wssei (permissão/unidade) e 403 da borda pedem ações opostas."""
    r = httpx.Response(
        403,
        json={"sucesso": False, "mensagem": "Acesso ao documento 123 não autorizado."},
        request=httpx.Request("GET", URL + "/documento/interno/consultar/123"),
    )
    assert SEIClient._is_cloudflare_waf_block(r) is False
    assert SEIClient._is_cloudflare_challenge(r) is False
    try:
        SEIClient._raise_acesso_negado(r)
        assert False, "deveria levantar SEIAcessoNegado"
    except SEIAcessoNegado as e:
        assert e.origem == "sei" and e.status == 403
        assert "não autorizado" in e.corpo
        assert "sei_trocar_unidade" in str(e)


def test_403_cru_da_borda_vira_erro_de_cloudflare():
    # 403 com server=cloudflare e corpo que não é JSON do wssei → veio da borda
    r = httpx.Response(
        403, headers={"server": "cloudflare", "cf-ray": "r1"},
        content=b"<html><body>error 1020</body></html>",
        request=httpx.Request("GET", URL + "/versao"),
    )
    try:
        SEIClient._raise_acesso_negado(r)
        assert False, "deveria levantar SEICloudflareBlocked"
    except SEICloudflareBlocked as e:
        assert "borda" in str(e).lower() and "credencial" in str(e).lower()


def test_classificacao_de_erro_no_server():
    import mcp_seipro.server as srv
    waf = srv._classificar_erro("O Cloudflare bloqueou por uma REGRA DE WAF (managed rule)")
    borda = srv._classificar_erro("Requisição barrada por um desafio do Cloudflare")
    sei = srv._classificar_erro("403 do SEI (autenticação/permissão), não do WAF")
    assert waf["erro_origem"] == "cloudflare_waf"
    assert borda["erro_origem"] == "cloudflare_borda"
    assert sei["erro_origem"] == "sei_acesso"
    assert "sei_trocar_unidade" in sei["erro_acao"]
    assert srv._classificar_erro("erro qualquer") == {}


def test_default_mode_e_auto():
    old = os.environ.pop("SEI_TRANSPORT", None)
    try:
        c = SEIClient(sei_url=URL, sei_usuario="u", sei_senha="p")
        assert c._transport_mode == "auto"
        assert isinstance(c._client, httpx.AsyncClient), "auto começa em httpx"
    finally:
        if old is not None:
            os.environ["SEI_TRANSPORT"] = old


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
