from bs4 import BeautifulSoup

from todos.sei_action_plans import (
    _collect_form_pairs,
    _inspect_html,
    _parse_js_call,
    _redact,
    _risk,
)


def test_collect_form_pairs_preserves_repeated_and_multiple_controls() -> None:
    soup = BeautifulSoup(
        """
        <form>
          <input type="hidden" name="hdnInfra" value="one-time">
          <input type="checkbox" name="item" value="a" checked>
          <input type="checkbox" name="item" value="b" checked>
          <input type="checkbox" name="item" value="c">
          <select name="unit" multiple>
            <option value="10" selected>U1</option>
            <option value="11" selected>U2</option>
          </select>
          <select name="fallback"><option value="first">First</option></select>
        </form>
        """,
        "html.parser",
    )

    assert _collect_form_pairs(soup.form) == [
        ("hdnInfra", "one-time"),
        ("item", "a"),
        ("item", "b"),
        ("unit", "10"),
        ("unit", "11"),
        ("fallback", "first"),
    ]


def test_inspection_turns_static_callback_into_a_plan_without_leaking_hash() -> None:
    html = """
    <form id="frmLista" method="post" action="controlador.php?acao=rel_listar&infra_hash=pagehash">
      <input type="hidden" name="hdnInfraItemId" value="">
    </form>
    <a onclick="acaoExcluir('76861634-123', 'Anexo')">Excluir</a>
    <script>
      function acaoExcluir(id, descricao) {
        var frm = document.getElementById('frmLista');
        frm.action = 'controlador.php?acao=rel_excluir&infra_hash=deletehash';
        document.getElementById('hdnInfraItemId').value = id;
        frm.submit();
      }
    </script>
    """

    public, plans = _inspect_html(html, "https://sei.example/sei/controlador.php?acao=rel_listar")
    callback = next(action for action in public["actions"] if action["kind"] == "form_submit")

    assert callback["risk"] == "destructive"
    assert callback["action_name"] == "rel_excluir"
    assert "infra_hash" not in str(public)
    assert plans[callback["trigger_id"]]["mutations"] == [{"field": "hdnInfraItemId", "value": "76861634-123"}]


def test_unknown_callback_is_diagnostic_only() -> None:
    html = """
    <button onclick="acaoComplexa(window.location.href)">Executar</button>
    <script>function acaoComplexa(value) { eval(value); }</script>
    """

    public, _ = _inspect_html(html, "https://sei.example/sei/controlador.php?acao=foo_listar")

    assert public["actions"][0]["kind"] == "unsupported_callback"
    assert public["actions"][0]["supported"] is False


def test_literal_callback_arguments_and_risk_classification() -> None:
    assert _parse_js_call("acaoExcluir('1', 2, true)") == ("acaoExcluir", ["1", "2", "true"])
    assert _parse_js_call("acaoExcluir(window.location.href)") is None
    assert _risk("procedimento_reabrir") == "write"
    assert _risk("documento_excluir") == "destructive"
    assert _risk("documento_consultar") == "read"


def test_raw_redaction_removes_common_signed_capabilities() -> None:
    raw = '<a href="controlador.php?acao=x&infra_hash=abcdef&token=secret">x</a>'

    redacted = _redact(raw)

    assert "abcdef" not in redacted
    assert "secret" not in redacted
    assert "<redacted>" in redacted
