import asyncio
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from bs4 import BeautifulSoup

from todos.exceptions import SEIValidationError
from todos.sei_action_plans import (
    _apply_values,
    _collect_form_pairs,
    _follow_redirects_same_origin,
    _hidden_field_names,
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
    assert plans[callback["trigger_id"]]["mutations"] == [
        {"field": "hdnInfraItemId", "value": "76861634-123"}
    ]


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


def test_raw_redaction_matches_dynamic_hdntoken_field_name() -> None:
    """`hdnToken` carries a per-page dynamic hash suffix in real SEI markup
    (CLAUDE.md: "o token CSRF é dinâmico (hdnToken<hash>)") — an exact-match
    pattern would let the real field name through unredacted."""
    raw = (
        '<input type="hidden" name="hdnTokenAB12CD34" value="supersecretlivevalue123">'
        '<a href="controlador.php?acao=x&hdnTokenAB12CD34=urlvalue456">x</a>'
    )

    redacted = _redact(raw)

    assert "supersecretlivevalue123" not in redacted
    assert "urlvalue456" not in redacted
    assert "<redacted>" in redacted


class TestApplyValuesOverrideCannotRetargetMutation:
    """Regression test for the override-substitution finding (RFC 0025 review).

    `mutations` come from parsing the page's own onclick/callback JS — e.g.
    which record id a destructive action targets. A caller-supplied override
    must never be able to silently replace one of those, or a discovered
    "delete this record" trigger could be redirected to a different,
    never-inspected record.
    """

    def test_override_on_a_mutation_field_is_rejected(self) -> None:
        pairs = [("hdnInfraItemId", ""), ("hdnOutroCampo", "x")]
        mutations = [{"field": "hdnInfraItemId", "value": "76861634-123"}]
        overrides = [{"name": "hdnInfraItemId", "value": "9999999-attacker-controlled"}]

        with pytest.raises(SEIValidationError, match="hdnInfraItemId"):
            _apply_values(pairs, mutations, overrides)

    def test_override_on_a_non_mutation_field_is_applied(self) -> None:
        pairs = [("hdnInfraItemId", ""), ("txtDescricao", "old")]
        mutations = [{"field": "hdnInfraItemId", "value": "76861634-123"}]
        overrides = [{"name": "txtDescricao", "value": "new"}]

        result = _apply_values(pairs, mutations, overrides)

        assert ("hdnInfraItemId", "76861634-123") in result
        assert ("txtDescricao", "new") in result
        assert ("txtDescricao", "old") not in result

    def test_mutation_applies_when_no_override_given(self) -> None:
        pairs = [("hdnInfraItemId", "")]
        mutations = [{"field": "hdnInfraItemId", "value": "76861634-123"}]

        result = _apply_values(pairs, mutations, None)

        assert result == [("hdnInfraItemId", "76861634-123")]

    def test_override_on_a_protected_hidden_field_is_rejected(self) -> None:
        """The common case: a plain `<form>` (no onclick JS, so `mutations=[]`)
        bakes the target record id into a hidden field directly. Without
        `protected_fields`, `_apply_values` would have no reason to reject an
        override on it — the vulnerability this fix closes."""
        pairs = [("hdnInfraItemId", "76861634-123"), ("txtDescricao", "old")]
        overrides = [{"name": "hdnInfraItemId", "value": "9999999-attacker-controlled"}]

        with pytest.raises(SEIValidationError, match="hdnInfraItemId"):
            _apply_values(pairs, [], overrides, protected_fields=frozenset({"hdnInfraItemId"}))

    def test_override_on_a_visible_field_is_still_applied_with_protected_fields(self) -> None:
        pairs = [("hdnInfraItemId", "76861634-123"), ("txtDescricao", "old")]
        overrides = [{"name": "txtDescricao", "value": "new"}]

        result = _apply_values(pairs, [], overrides, protected_fields=frozenset({"hdnInfraItemId"}))

        assert ("hdnInfraItemId", "76861634-123") in result
        assert ("txtDescricao", "new") in result


class TestHiddenFieldNames:
    def test_finds_hidden_input_names_ignoring_disabled_and_visible(self) -> None:
        soup = BeautifulSoup(
            """
            <form>
              <input type="hidden" name="hdnInfraItemId" value="123">
              <input type="hidden" name="hdnDisabled" value="x" disabled>
              <input type="text" name="txtDescricao" value="visible">
            </form>
            """,
            "html.parser",
        )

        assert _hidden_field_names(soup.form) == frozenset({"hdnInfraItemId"})


class TestFollowRedirectsSameOrigin:
    """Regression test for the missing-redirect-following finding (RFC 0025
    review): SEI's write actions commonly answer with a redirect (PRG
    pattern) whose target page carries the real success/error indicator —
    the executor must follow it (validating origin per hop) instead of
    inspecting the near-empty redirect response itself."""

    def test_follows_one_redirect_and_validates_origin_per_hop(self) -> None:
        first_request = httpx.Request("POST", "https://sei.example/sei/controlador.php?acao=x")
        first_response = httpx.Response(302, request=first_request)
        next_request = httpx.Request("GET", "https://sei.example/sei/controlador.php?acao=y")
        first_response.next_request = next_request

        final_response = httpx.Response(200, request=next_request)
        final_response.next_request = None

        client = MagicMock()
        client.validar_mesma_origem = MagicMock(side_effect=lambda url, **_: url)
        client.http_send = AsyncMock(return_value=final_response)

        result = asyncio.run(_follow_redirects_same_origin(client, first_response))

        assert result is final_response
        client.validar_mesma_origem.assert_called_once_with(str(next_request.url))
        client.http_send.assert_awaited_once_with(next_request, follow_redirects=False)

    def test_no_redirect_returns_response_unchanged(self) -> None:
        request = httpx.Request("GET", "https://sei.example/sei/controlador.php?acao=x")
        response = httpx.Response(200, request=request)
        response.next_request = None

        client = MagicMock()
        client.http_send = AsyncMock()

        result = asyncio.run(_follow_redirects_same_origin(client, response))

        assert result is response
        client.http_send.assert_not_awaited()

    def test_redirect_to_foreign_origin_is_rejected(self) -> None:
        request = httpx.Request("POST", "https://sei.example/sei/controlador.php?acao=x")
        response = httpx.Response(302, request=request)
        response.next_request = httpx.Request("GET", "https://attacker.evil/roubar")

        client = MagicMock()
        client.validar_mesma_origem = MagicMock(side_effect=SEIValidationError("fora da origem"))
        client.http_send = AsyncMock()

        with pytest.raises(SEIValidationError):
            asyncio.run(_follow_redirects_same_origin(client, response))
        client.http_send.assert_not_awaited()
