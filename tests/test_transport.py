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

from mcp_seipro.sei_client import SEIClient, SEICloudflareBlocked  # noqa: E402

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
