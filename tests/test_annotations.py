"""Testa as tool annotations (hints MCP) aplicadas por convenção de nome.

O gate "No approval received" é do cliente (Claude.ai). O servidor só sinaliza
via annotations: readOnlyHint em leituras (auto-aprovadas), e hints coerentes
nas escritas. Ver a nota técnica e _aplicar_tool_annotations em server.py.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
os.environ.setdefault("SEI_URL", "https://exemplo.gov.br/api/v2")

import mcp_seipro.server as srv  # noqa: E402

_TOOLS = srv.mcp._tool_manager._tools


def _ann(nome):
    return _TOOLS[nome].annotations


def test_todas_as_tools_tem_annotations():
    faltando = [n for n, t in _TOOLS.items() if t.annotations is None]
    assert not faltando, f"tools sem annotation: {faltando}"


def test_leitura_pura_read_only():
    for n in ("sei_listar_secoes", "sei_listar_processos", "sei_consultar_processo",
              "sei_ler_documento", "sei_buscar_documento", "sei_arvore_processo",
              "sei_pesquisar_processos", "sei_gerar_referencia", "sei_versao"):
        assert _ann(n).readOnlyHint is True, n


def test_editar_secao_hints():
    a = _ann("sei_editar_secao")
    assert a.readOnlyHint is False
    assert a.destructiveHint is False
    assert a.idempotentHint is True


def test_escritas_nao_sao_read_only():
    for n in ("sei_criar_documento", "sei_alterar_processo", "sei_assinar_documento",
              "sei_enviar_processo", "sei_editar_secao"):
        assert _ann(n).readOnlyHint is False, n


def test_exclusao_e_destrutiva():
    for n in ("sei_excluir_bloco_interno", "sei_excluir_marcador"):
        a = _ann(n)
        assert a.readOnlyHint is False and a.destructiveHint is True, n


def test_alterar_e_idempotente_mas_nao_destrutivo():
    a = _ann("sei_alterar_processo")
    assert a.idempotentHint is True and a.destructiveHint is False


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
