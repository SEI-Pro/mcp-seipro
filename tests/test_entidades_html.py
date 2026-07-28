"""Testes da normalização de entidades HTML antes do POST de seções.

O SEI aceita UTF-8 literal; deixar entidades no corpo faz elas se acumularem a
cada ciclo ler→reenviar e engorda o payload à toa. As entidades ESTRUTURAIS
(&lt; &gt; &amp; &quot; &apos;) precisam sobreviver, senão texto vira marcação.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from mcp_seipro.html_utils import normalizar_entidades_html, sanitize_iso8859  # noqa: E402


def test_nbsp_vira_caractere_literal():
    # NBSP escrito como escape de propósito — literal no fonte é invisível
    assert normalizar_entidades_html("<p>a&nbsp;b</p>") == "<p>a\u00a0b</p>"
    assert normalizar_entidades_html("&#160;") == "\u00a0"
    assert normalizar_entidades_html("&nbsp;") != " ", "NBSP não é espaço comum"


def test_acentos_nomeados_e_numericos():
    assert normalizar_entidades_html("Advoca&ccedil;&atilde;o") == "Advocação"
    assert normalizar_entidades_html("resolu&#231;&#227;o") == "resolução"
    assert normalizar_entidades_html("&#xe9;") == "é"


def test_estruturais_sao_preservadas():
    # desescapar isto transformaria texto literal em tag
    original = "<p>use &lt;strong&gt; para negrito &amp; nada mais &quot;ok&quot;</p>"
    assert normalizar_entidades_html(original) == original
    assert normalizar_entidades_html("&#60;script&#62;") == "&#60;script&#62;"


def test_nao_quebra_conteudo_sem_entidade():
    html = '<p class="Texto_Justificado">Texto simples</p>'
    assert normalizar_entidades_html(html) == html
    assert normalizar_entidades_html("") == ""


def test_entidade_desconhecida_fica_intacta():
    assert normalizar_entidades_html("&naoexisteisso;") == "&naoexisteisso;"


def test_nbsp_sobrevive_ao_sanitize_iso8859():
    # NBSP está dentro do ISO-8859-1 → segue literal, não vira entidade numérica
    assert sanitize_iso8859(normalizar_entidades_html("a&nbsp;b")) == "a\u00a0b"
    # já um caractere fora do ISO-8859-1 continua sendo escapado (exigência do wssei)
    assert sanitize_iso8859(normalizar_entidades_html("&hellip;")) == "&#8230;"


def test_idempotente():
    uma = normalizar_entidades_html("Aten&ccedil;&atilde;o&nbsp;&lt;b&gt;")
    assert normalizar_entidades_html(uma) == uma


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
