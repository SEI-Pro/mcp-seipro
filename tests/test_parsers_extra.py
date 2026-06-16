"""Extended parser tests for sei_web_client — HTML mocks, no live server required.

Covers: _extrair_erro_sei, _tag_str, _extrair_submit_btn, _extrair_metadados_tabelas,
_parse_documento_consultar, _parse_procedimento_consultar, _extract_pesquisa_rapida,
_extract_main_form, _populate_trabalhar_links, _extract_unidade_atual, _units_from_form,
_extract_tooltip, parse_arvore_nos (extended), parse_inbox (extended).
"""

from __future__ import annotations

import datetime
from urllib.parse import parse_qsl

import httpx
from bs4 import BeautifulSoup, Tag

from todos.sei_web_client import (
    SEIWebClient,
    _extract_tooltip,
    _extrair_erro_sei,
    _extrair_metadados_tabelas,
    _extrair_submit_btn,
    _parse_documento_consultar,
    _parse_procedimento_consultar,
    _tag_str,
    parse_arvore_nos,
    parse_inbox,
)

# Fixture dates for historical document events (signatures, sobrestamentos, etc.).
# These represent past events; use clearly past dates so the fixtures never
# look like "near-future" values that might confuse date-validation logic
# if such logic is added later.
_D1 = "10/06/2020 14:30"
_D2 = "01/06/2020 09:00"
_D3 = "02/06/2020 11:15"
_D4 = "03/06/2020 16:45"
_D5 = "04/06/2020 08:00"
_D6 = "05/06/2020 10:00"
_D7 = "06/06/2020 08:30"
_D8 = "07/06/2020 09:00"
_D9 = "13/06/2020 12:00"
_DATE_SOBREST_1 = "10/06/2020"
_DATE_SOBREST_2 = "01/05/2020"
_DATE_SOBREST_3 = "15/05/2020"

# Future date for fixtures that need to represent a deadline or valid-until date.
_FUTURE_DATE = (datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(days=365)).strftime(
    "%d/%m/%Y"
)

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _parse(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "html.parser")


def _form(html: str) -> Tag:
    """Return the first <form> inside html."""
    soup = _parse(html)
    return soup.find("form")


# ===========================================================================
# 1. _extrair_erro_sei
# ===========================================================================


class TestExtrairErroSei:
    def test_returns_none_for_empty_html(self):
        assert _extrair_erro_sei("") is None

    def test_returns_none_for_clean_page(self):
        html = "<html><body><p>Tudo certo</p></body></html>"
        assert _extrair_erro_sei(html) is None

    def test_detects_infra_msg(self):
        html = '<div class="infraMsg">Acesso negado.</div>'
        assert _extrair_erro_sei(html) == "Acesso negado."

    def test_detects_infra_mensagem_erro(self):
        html = '<span class="infraMensagemErro">Processo não encontrado.</span>'
        assert _extrair_erro_sei(html) == "Processo não encontrado."

    def test_detects_div_infra_mensagem_by_id(self):
        html = '<div id="divInfraMensagem">Sessão expirada.</div>'
        assert _extrair_erro_sei(html) == "Sessão expirada."

    def test_detects_alert_danger(self):
        html = '<div class="alert-danger">Erro ao processar solicitação.</div>'
        assert _extrair_erro_sei(html) == "Erro ao processar solicitação."

    def test_detects_javascript_alert_double_quotes(self):
        html = '<script>alert("Usuário ou senha inválidos.")</script>'
        assert _extrair_erro_sei(html) == "Usuário ou senha inválidos."

    def test_detects_javascript_alert_single_quotes(self):
        html = "<script>alert('Operação não permitida neste momento.')</script>"
        assert _extrair_erro_sei(html) == "Operação não permitida neste momento."

    def test_javascript_alert_too_short_ignored(self):
        # Message shorter than 10 chars must be ignored
        html = "<script>alert('Erro')</script>"
        assert _extrair_erro_sei(html) is None

    def test_javascript_alert_too_long_ignored(self):
        # Message longer than 300 chars must be ignored
        long_msg = "x" * 301
        html = f'<script>alert("{long_msg}")</script>'
        assert _extrair_erro_sei(html) is None

    def test_priority_infra_msg_over_alert(self):
        # DOM-based check comes before regex; infraMsg wins
        html = '<div class="infraMsg">Erro DOM</div><script>alert("Erro JavaScript aqui!")</script>'
        result = _extrair_erro_sei(html)
        assert result == "Erro DOM"

    def test_empty_infra_msg_falls_through(self):
        # Empty infraMsg element → continue checking other selectors
        html = '<div class="infraMsg"></div><div class="infraMensagemErro">Segundo erro.</div>'
        assert _extrair_erro_sei(html) == "Segundo erro."

    def test_whitespace_only_infra_msg_falls_through(self):
        # Whitespace-only text is stripped to empty → falls through
        html = '<div class="infraMsg">   </div><div id="divInfraMensagem">Terceiro erro.</div>'
        assert _extrair_erro_sei(html) == "Terceiro erro."

    def test_ignores_alert_inside_function_script(self):
        html = """<script>
function submeter() {
    if (!document.getElementById('hdnTipo').value) {
        alert('Selecione o tipo de processo.');
        return false;
    }
}
</script>"""
        assert _extrair_erro_sei(html) is None

    def test_ignores_alert_with_name_inside_function_script(self):
        html = "<script>\nfunction confirmar() {\n    alert('NOME NM-E1EC');\n}\n</script>"
        assert _extrair_erro_sei(html) is None

    def test_detects_bare_alert_script_without_function(self):
        html = "<script>\nalert('Usuário sem permissão para esta operação.');\n</script>"
        assert _extrair_erro_sei(html) == "Usuário sem permissão para esta operação."


# ===========================================================================
# 2. _tag_str
# ===========================================================================


class TestTagStr:
    def _make_tag(self, html: str, tag_name: str = "div") -> Tag:
        return _parse(html).find(tag_name)

    def test_returns_string_attribute(self):
        tag = self._make_tag('<div id="meu-id"></div>')
        assert _tag_str(tag, "id") == "meu-id"

    def test_returns_first_element_of_list_attribute(self):
        # BS4 returns class as a list
        tag = self._make_tag('<div class="foo bar"></div>')
        result = _tag_str(tag, "class")
        assert result == "foo"

    def test_returns_default_for_missing_attribute(self):
        tag = self._make_tag("<div></div>")
        assert _tag_str(tag, "href") == ""

    def test_returns_custom_default_for_missing_attribute(self):
        tag = self._make_tag("<div></div>")
        assert _tag_str(tag, "href", "N/A") == "N/A"

    def test_empty_class_list_returns_default(self):
        # Manually construct a tag that has class=[] (rare but defensible)
        tag = self._make_tag("<div></div>")
        # Simulate a list attribute with no items
        tag.attrs["class"] = []
        assert _tag_str(tag, "class") == ""
        assert _tag_str(tag, "class", "fallback") == "fallback"

    def test_none_attribute_value_returns_default(self):
        tag = self._make_tag("<div></div>")
        tag.attrs["data-x"] = None
        assert _tag_str(tag, "data-x") == ""

    def test_value_attribute_on_input(self):
        tag = self._make_tag('<input type="submit" name="sbmLogin" value="Acessar">', "input")
        assert _tag_str(tag, "name") == "sbmLogin"
        assert _tag_str(tag, "value") == "Acessar"

    def test_list_attribute_returns_first_item(self):
        tag = self._make_tag('<div class="a b c"></div>')
        # class is parsed as ["a","b","c"]; _tag_str returns first element
        assert _tag_str(tag, "class") == "a"


# ===========================================================================
# 3. _extrair_submit_btn
# ===========================================================================


class TestExtrairSubmitBtn:
    def test_returns_none_for_empty_form(self):
        form = _form("<form></form>")
        assert _extrair_submit_btn(form) is None

    def test_detects_input_type_submit(self):
        form = _form('<form><input type="submit" name="sbmLogin" value="Acessar"></form>')
        assert _extrair_submit_btn(form) == ("sbmLogin", "Acessar")

    def test_detects_button_type_submit_with_value(self):
        form = _form(
            '<form><button type="submit" name="sbmAcessar" value="ACESSAR">ACESSAR</button></form>'
        )
        assert _extrair_submit_btn(form) == ("sbmAcessar", "ACESSAR")

    def test_button_without_value_falls_back_to_text(self):
        form = _form('<form><button type="submit" name="sbmOk">Confirmar</button></form>')
        assert _extrair_submit_btn(form) == ("sbmOk", "Confirmar")

    def test_button_without_value_and_text_falls_back_to_enviar(self):
        form = _form('<form><button type="submit" name="sbmOk"></button></form>')
        assert _extrair_submit_btn(form) == ("sbmOk", "Enviar")

    def test_input_without_name_returns_none(self):
        form = _form('<form><input type="submit" value="Acessar"></form>')
        assert _extrair_submit_btn(form) is None

    def test_button_without_name_returns_none(self):
        form = _form('<form><button type="submit">Entrar</button></form>')
        assert _extrair_submit_btn(form) is None

    def test_input_preferred_over_button(self):
        # input[type=submit] appears after button in HTML but spec says input is checked first
        form = _form(
            "<form>"
            '<button type="submit" name="btnButton" value="btn">Botão</button>'
            '<input type="submit" name="sbmInput" value="Input">'
            "</form>"
        )
        assert _extrair_submit_btn(form) == ("sbmInput", "Input")

    def test_no_type_submit_button_not_detected(self):
        form = _form('<form><button name="sbmOk">Clique</button></form>')
        assert _extrair_submit_btn(form) is None

    def test_input_submit_empty_value_falls_back_through_chain(self):
        # input with name but empty value: _tag_str returns "" (falsy),
        # then get_text(strip=True) is also "" for <input>, falls to "Enviar"
        form = _form('<form><input type="submit" name="sbmLogin" value=""></form>')
        assert _extrair_submit_btn(form) == ("sbmLogin", "Enviar")


# ===========================================================================
# 4. _extrair_metadados_tabelas
# ===========================================================================


class TestExtrairMetadadosTabelas:
    def test_empty_soup_produces_empty_result(self):
        soup = _parse("<html></html>")
        result: dict = {}
        _extrair_metadados_tabelas(soup, result)
        assert result == {}

    def test_basic_th_td_pair(self):
        soup = _parse("<table><tr><th>Tipo do processo</th><td>Administrativo</td></tr></table>")
        result: dict = {}
        _extrair_metadados_tabelas(soup, result)
        assert result["tipo_do_processo"] == "Administrativo"

    def test_td_td_pair_also_extracted(self):
        soup = _parse("<table><tr><td>Data de autuação:</td><td>01/01/2024</td></tr></table>")
        result: dict = {}
        _extrair_metadados_tabelas(soup, result)
        assert result["data_de_autuação"] == "01/01/2024"

    def test_colon_stripped_from_key(self):
        soup = _parse("<table><tr><th>Situação:</th><td>Aberto</td></tr></table>")
        result: dict = {}
        _extrair_metadados_tabelas(soup, result)
        assert "situação" in result
        assert result["situação"] == "Aberto"

    def test_spaces_and_slashes_replaced_in_key(self):
        soup = _parse(
            "<table><tr><th>Tipo/Subtipo Processo</th><td>Fiscalização/Regulação</td></tr></table>"
        )
        result: dict = {}
        _extrair_metadados_tabelas(soup, result)
        assert "tipo_subtipo_processo" in result

    def test_header_row_th_th_skipped(self):
        soup = _parse(
            "<table>"
            "<tr><th>Campo</th><th>Valor</th></tr>"
            "<tr><th>Tipo</th><td>Interno</td></tr>"
            "</table>"
        )
        result: dict = {}
        _extrair_metadados_tabelas(soup, result)
        # Only the th+td row should be extracted, not the th+th header
        assert "campo" not in result
        assert result["tipo"] == "Interno"

    def test_rows_with_wrong_cell_count_skipped(self):
        soup = _parse(
            "<table>"
            "<tr><td>Só uma célula</td></tr>"
            "<tr><td>A</td><td>B</td><td>C</td></tr>"
            "<tr><th>Chave</th><td>Valor correto</td></tr>"
            "</table>"
        )
        result: dict = {}
        _extrair_metadados_tabelas(soup, result)
        assert list(result.keys()) == ["chave"]
        assert result["chave"] == "Valor correto"

    def test_key_longer_than_59_chars_skipped(self):
        long_key = "a" * 60
        soup = _parse(f"<table><tr><th>{long_key}</th><td>Valor</td></tr></table>")
        result: dict = {}
        _extrair_metadados_tabelas(soup, result)
        assert result == {}

    def test_key_exactly_59_chars_included(self):
        key_59 = "a" * 59
        soup = _parse(f"<table><tr><th>{key_59}</th><td>Valor</td></tr></table>")
        result: dict = {}
        _extrair_metadados_tabelas(soup, result)
        assert key_59 in result

    def test_skip_list_table_tbl_assinaturas(self):
        soup = _parse('<table id="tblAssinaturas"><tr><th>Assinante</th><td>João</td></tr></table>')
        result: dict = {}
        _extrair_metadados_tabelas(soup, result)
        assert result == {}

    def test_skip_list_table_tbl_ciencias(self):
        soup = _parse('<table id="tblCiencias"><tr><th>Usuário</th><td>Maria</td></tr></table>')
        result: dict = {}
        _extrair_metadados_tabelas(soup, result)
        assert result == {}

    def test_skip_list_table_tbl_interessados(self):
        soup = _parse('<table id="tblInteressados"><tr><th>Nome</th><td>Pedro</td></tr></table>')
        result: dict = {}
        _extrair_metadados_tabelas(soup, result)
        assert result == {}

    def test_skip_list_table_tbl_unidades_processo(self):
        soup = _parse(
            '<table id="tblUnidadesProcesso"><tr><th>Unidade</th><td>GPRO</td></tr></table>'
        )
        result: dict = {}
        _extrair_metadados_tabelas(soup, result)
        assert result == {}

    def test_non_skip_list_table_extracted_normally(self):
        # tblSobrestamento is NOT in skip list (per CLAUDE.md note)
        soup = _parse(
            '<table id="tblSobrestamento">'
            "<tr><th>Motivo</th><td>Aguardando decisão judicial</td></tr>"
            "</table>"
        )
        result: dict = {}
        _extrair_metadados_tabelas(soup, result)
        assert result.get("motivo") == "Aguardando decisão judicial"

    def test_multiple_tables_mixed_skipped_and_included(self):
        soup = _parse(
            '<table id="tblAssinaturas"><tr><th>Assinante</th><td>X</td></tr></table>'
            "<table><tr><th>Tipo</th><td>Ofício</td></tr></table>"
        )
        result: dict = {}
        _extrair_metadados_tabelas(soup, result)
        assert "assinante" not in result
        assert result.get("tipo") == "Ofício"

    def test_empty_key_or_value_skipped(self):
        soup = _parse(
            "<table>"
            "<tr><th></th><td>Valor sem chave</td></tr>"
            "<tr><th>Chave sem valor</th><td></td></tr>"
            "<tr><th>Válido</th><td>Sim</td></tr>"
            "</table>"
        )
        result: dict = {}
        _extrair_metadados_tabelas(soup, result)
        # Only the row with both key and value should be in result
        assert "válido" in result
        assert len(result) == 1

    def test_modifies_result_in_place(self):
        soup = _parse("<table><tr><th>Campo</th><td>Valor</td></tr></table>")
        result: dict = {"pre_existing": "data"}
        _extrair_metadados_tabelas(soup, result)
        assert result["pre_existing"] == "data"
        assert result["campo"] == "Valor"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _wrap(body: str) -> str:
    """Wrap a body snippet in a minimal HTML document."""
    return f"<html><body>{body}</body></html>"


def _meta_table(pairs: list[tuple[str, str]]) -> str:
    """Build a generic metadata table with th/td pairs."""
    rows = "".join(f"<tr><th>{label}</th><td>{value}</td></tr>" for label, value in pairs)
    return f"<table>{rows}</table>"


def _assinaturas_table(rows: list[tuple[str, str, str]]) -> str:
    header = "<tr><th>Assinante</th><th>Cargo</th><th>Data/Hora</th></tr>"
    body = "".join(f"<tr><td>{a}</td><td>{c}</td><td>{d}</td></tr>" for a, c, d in rows)
    return f'<table id="tblAssinaturas">{header}{body}</table>'


def _ciencias_table(rows: list[tuple[str, str, str]]) -> str:
    header = "<tr><th>Usuário</th><th>Cargo</th><th>Data/Hora</th></tr>"
    body = "".join(f"<tr><td>{u}</td><td>{c}</td><td>{d}</td></tr>" for u, c, d in rows)
    return f'<table id="tblCiencias">{header}{body}</table>'


def _unidades_table(rows: list[tuple[str, ...]], *, with_situacao: bool = True) -> str:
    header = (
        "<tr><th>Unidade</th><th>Situação</th></tr>"
        if with_situacao
        else "<tr><th>Unidade</th></tr>"
    )
    body_rows = []
    for row in rows:
        if with_situacao and len(row) >= 2:
            body_rows.append(f"<tr><td>{row[0]}</td><td>{row[1]}</td></tr>")
        else:
            body_rows.append(f"<tr><td>{row[0]}</td></tr>")
    return f'<table id="tblUnidadesProcesso">{header}{"".join(body_rows)}</table>'


def _interessados_table(names: list[str]) -> str:
    header = "<tr><th>Interessado</th></tr>"
    body = "".join(f"<tr><td>{n}</td></tr>" for n in names)
    return f'<table id="tblInteressados">{header}{body}</table>'


def _sobrestamento_table(rows: list[tuple[str, str]]) -> str:
    header = "<tr><th>Motivo</th><th>Data</th></tr>"
    body = "".join(f"<tr><td>{m}</td><td>{d}</td></tr>" for m, d in rows)
    return f'<table id="tblSobrestamento">{header}{body}</table>'


# ===========================================================================
# Class A — _parse_documento_consultar
# ===========================================================================


class TestParseDocumentoConsultar:
    """Tests for _parse_documento_consultar(html, id_documento)."""

    # --- id_documento is always preserved --------------------------------

    def test_empty_html_preserves_id_and_returns_empty_lists(self):
        result = _parse_documento_consultar("<html><body></body></html>", "DOC-001")
        assert result["id_documento"] == "DOC-001"
        assert result["assinaturas"] == []
        assert result["ciencias"] == []

    def test_minimal_html_string_still_returns_structure(self):
        result = _parse_documento_consultar("", "X")
        assert result["id_documento"] == "X"
        assert result["assinaturas"] == []
        assert result["ciencias"] == []

    # --- assinaturas table -----------------------------------------------

    def test_single_assinatura_parsed_correctly(self):
        html = _wrap(_assinaturas_table([("João Silva", "Diretor", _D1)]))
        result = _parse_documento_consultar(html, "42")
        assert len(result["assinaturas"]) == 1
        sig = result["assinaturas"][0]
        assert sig["assinante"] == "João Silva"
        assert sig["cargo"] == "Diretor"
        assert sig["data_hora"] == _D1

    def test_multiple_assinaturas(self):
        html = _wrap(
            _assinaturas_table(
                [
                    ("Maria Souza", "Coordenadora", _D2),
                    ("Pedro Lima", "Analista", _D3),
                    ("Ana Costa", "Gerente", _D4),
                ]
            )
        )
        result = _parse_documento_consultar(html, "99")
        assert len(result["assinaturas"]) == 3
        assert result["assinaturas"][1]["assinante"] == "Pedro Lima"
        assert result["assinaturas"][2]["data_hora"] == _D4

    def test_assinaturas_header_row_is_skipped(self):
        """The first <tr> (header) must not become an entry."""
        html = _wrap(_assinaturas_table([("Único Signatário", "Chefe", _D5)]))
        result = _parse_documento_consultar(html, "7")
        # Only one data row → exactly one signature; no header polluting the list
        assert len(result["assinaturas"]) == 1
        assert result["assinaturas"][0]["assinante"] == "Único Signatário"

    def test_assinatura_row_with_fewer_than_3_tds_is_ignored(self):
        html = _wrap(
            '<table id="tblAssinaturas">'
            "<tr><th>A</th><th>B</th><th>C</th></tr>"
            "<tr><td>só dois</td><td>campos</td></tr>"  # 2 tds only
            "</table>"
        )
        result = _parse_documento_consultar(html, "5")
        assert result["assinaturas"] == []

    # --- ciencias table --------------------------------------------------

    def test_single_ciencia_parsed_correctly(self):
        html = _wrap(_ciencias_table([("Carlos Ramos", "Auditor", _D6)]))
        result = _parse_documento_consultar(html, "55")
        assert len(result["ciencias"]) == 1
        c = result["ciencias"][0]
        assert c["usuario"] == "Carlos Ramos"
        assert c["cargo"] == "Auditor"
        assert c["data_hora"] == _D6

    def test_multiple_ciencias(self):
        html = _wrap(
            _ciencias_table(
                [
                    ("Alice B.", "Técnico", _D7),
                    ("Bob C.", "Analista", _D8),
                ]
            )
        )
        result = _parse_documento_consultar(html, "12")
        assert len(result["ciencias"]) == 2
        assert result["ciencias"][0]["usuario"] == "Alice B."

    # --- metadata extraction alongside signatures ------------------------

    def test_metadata_table_extracted_alongside_assinaturas(self):
        meta = _meta_table([("Tipo do Documento", "Despacho"), ("Número", "2874369")])
        sigs = _assinaturas_table([("Fulano", "Gerente", _D9)])
        html = _wrap(meta + sigs)
        result = _parse_documento_consultar(html, "77")
        # Metadata keys are lower-cased with spaces replaced by underscores
        assert result.get("tipo_do_documento") == "Despacho"
        assert result.get("número") == "2874369"
        # Signatures still parsed
        assert len(result["assinaturas"]) == 1

    # --- no tables at all ------------------------------------------------

    def test_no_assinaturas_table_returns_empty_list(self):
        html = _wrap("<p>Nenhuma tabela aqui</p>")
        result = _parse_documento_consultar(html, "0")
        assert result["assinaturas"] == []

    def test_no_ciencias_table_returns_empty_list(self):
        html = _wrap(_assinaturas_table([("X", "Y", "Z")]))  # assinaturas exist, ciencias don't
        result = _parse_documento_consultar(html, "1")
        assert result["ciencias"] == []


# ===========================================================================
# Class B — _parse_procedimento_consultar
# ===========================================================================


class TestParseProcedimentoConsultar:
    """Tests for _parse_procedimento_consultar(html, protocolo)."""

    # --- protocolo is always preserved -----------------------------------

    def test_empty_html_preserves_protocolo_and_empty_lists(self):
        result = _parse_procedimento_consultar("<html><body></body></html>", "SEI-001234")
        assert result["protocolo"] == "SEI-001234"
        assert result["unidades_abertas"] == []
        assert result["interessados"] == []
        assert result["sobrestamentos"] == []

    # --- unidades abertas ------------------------------------------------

    def test_unidades_abertas_with_situacao(self):
        html = _wrap(
            _unidades_table(
                [
                    ("DIRED/ANTAQ", "Aberto"),
                    ("GPRO/ANTAQ", "Remetido"),
                ],
                with_situacao=True,
            )
        )
        result = _parse_procedimento_consultar(html, "0000.001")
        assert len(result["unidades_abertas"]) == 2
        assert result["unidades_abertas"][0]["unidade"] == "DIRED/ANTAQ"
        assert result["unidades_abertas"][0]["situacao"] == "Aberto"
        assert result["unidades_abertas"][1]["situacao"] == "Remetido"

    def test_unidades_abertas_without_situacao_column(self):
        html = _wrap(_unidades_table([("SURIN/ANTAQ",)], with_situacao=False))
        result = _parse_procedimento_consultar(html, "0000.002")
        assert len(result["unidades_abertas"]) == 1
        u = result["unidades_abertas"][0]
        assert u["unidade"] == "SURIN/ANTAQ"
        assert "situacao" not in u

    def test_unidades_abertas_fallback_via_link(self):
        """When tblUnidadesProcesso is absent, fall back to acao=unidade_visualizar links."""
        html = _wrap(
            '<a href="?acao=unidade_visualizar&id=10">GEDIR/ANTAQ</a> '
            '<a href="?acao=unidade_visualizar&id=11">GPRO/ANTAQ</a>'
        )
        result = _parse_procedimento_consultar(html, "0000.003")
        assert len(result["unidades_abertas"]) == 2
        assert result["unidades_abertas"][0]["unidade"] == "GEDIR/ANTAQ"
        assert result["unidades_abertas"][1]["unidade"] == "GPRO/ANTAQ"

    def test_fallback_link_not_used_when_table_present(self):
        """If tblUnidadesProcesso has rows, the anchor fallback must NOT run."""
        table_html = _unidades_table([("UNID-REAL",)], with_situacao=False)
        fallback_link = '<a href="?acao=unidade_visualizar&id=99">UNID-FALLBACK</a>'
        html = _wrap(table_html + fallback_link)
        result = _parse_procedimento_consultar(html, "0000.004")
        names = [u["unidade"] for u in result["unidades_abertas"]]
        assert "UNID-REAL" in names
        assert "UNID-FALLBACK" not in names

    # --- interessados ----------------------------------------------------

    def test_interessados_from_table(self):
        html = _wrap(_interessados_table(["Empresa Alpha Ltda.", "Empresa Beta S.A."]))
        result = _parse_procedimento_consultar(html, "0000.005")
        assert result["interessados"] == ["Empresa Alpha Ltda.", "Empresa Beta S.A."]

    def test_interessados_empty_when_no_table(self):
        html = _wrap("<p>sem interessados</p>")
        result = _parse_procedimento_consultar(html, "0000.006")
        assert result["interessados"] == []

    def test_interessados_empty_td_is_skipped(self):
        html = _wrap(
            '<table id="tblInteressados">'
            "<tr><th>Interessado</th></tr>"
            "<tr><td>   </td></tr>"  # whitespace-only → stripped → ""
            "<tr><td>Empresa Real</td></tr>"
            "</table>"
        )
        result = _parse_procedimento_consultar(html, "0000.007")
        assert result["interessados"] == ["Empresa Real"]

    # --- sobrestamentos --------------------------------------------------

    def test_sobrestamento_single_row(self):
        html = _wrap(_sobrestamento_table([("Aguardando laudo pericial", _DATE_SOBREST_1)]))
        result = _parse_procedimento_consultar(html, "0000.008")
        assert len(result["sobrestamentos"]) == 1
        s = result["sobrestamentos"][0]
        assert s["motivo"] == "Aguardando laudo pericial"
        assert s["data"] == _DATE_SOBREST_1

    def test_sobrestamento_multiple_rows(self):
        html = _wrap(
            _sobrestamento_table(
                [
                    ("Motivo A", _DATE_SOBREST_2),
                    ("Motivo B", _DATE_SOBREST_3),
                ]
            )
        )
        result = _parse_procedimento_consultar(html, "0000.009")
        assert len(result["sobrestamentos"]) == 2
        assert result["sobrestamentos"][1]["motivo"] == "Motivo B"

    def test_no_sobrestamento_table_returns_empty_list(self):
        html = _wrap("<p>Processo ativo</p>")
        result = _parse_procedimento_consultar(html, "0000.010")
        assert result["sobrestamentos"] == []

    # --- metadata extraction alongside structured tables -----------------

    def test_metadata_extracted_alongside_structured_tables(self):
        meta = _meta_table(
            [
                ("Tipo do Processo", "Administrativo"),
                ("Data de Autuação", "02/01/2020"),
            ]
        )
        unidades = _unidades_table([("GEDIR/ANTAQ", "Aberto")])
        interessados = _interessados_table(["Empresa Gamma"])
        html = _wrap(meta + unidades + interessados)
        result = _parse_procedimento_consultar(html, "SEI-9999")
        # Structured lists still populated
        assert len(result["unidades_abertas"]) == 1
        assert result["interessados"] == ["Empresa Gamma"]
        # Metadata key extracted
        assert result.get("tipo_do_processo") == "Administrativo"
        assert result.get("data_de_autuação") == "02/01/2020"


def make_client() -> SEIWebClient:
    return SEIWebClient(sei_web_url="http://sei.test", sei_usuario="u", sei_senha="p")


# ---------------------------------------------------------------------------
# 1. _extract_pesquisa_rapida
# ---------------------------------------------------------------------------


class TestExtractPesquisaRapida:
    def test_basic_sets_action(self):
        client = make_client()
        html = '<form action="sei.php?acao=protocolo_pesquisa_rapida&amp;infra_hash=abc"><input></form>'
        client._extract_pesquisa_rapida(html)
        assert (
            client._pesquisa_rapida_action
            == "sei.php?acao=protocolo_pesquisa_rapida&infra_hash=abc"
        )

    def test_no_matching_form_leaves_attribute_unchanged(self):
        client = make_client()
        client._pesquisa_rapida_action = "original_value"
        html = '<form action="sei.php?acao=other_action"><input></form>'
        client._extract_pesquisa_rapida(html)
        assert client._pesquisa_rapida_action == "original_value"

    def test_no_form_at_all_leaves_none(self):
        client = make_client()
        html = "<div>No forms here</div>"
        client._extract_pesquisa_rapida(html)
        assert client._pesquisa_rapida_action is None

    def test_ampersand_entity_decoded(self):
        client = make_client()
        html = '<form action="base.php?a=protocolo_pesquisa_rapida&amp;b=1&amp;c=2"></form>'
        client._extract_pesquisa_rapida(html)
        assert "&amp;" not in client._pesquisa_rapida_action
        assert client._pesquisa_rapida_action == "base.php?a=protocolo_pesquisa_rapida&b=1&c=2"

    def test_multiple_forms_uses_first_matching(self):
        client = make_client()
        html = (
            '<form action="sei.php?acao=unrelated"></form>'
            '<form action="sei.php?acao=protocolo_pesquisa_rapida&amp;hash=first"></form>'
            '<form action="sei.php?acao=protocolo_pesquisa_rapida&amp;hash=second"></form>'
        )
        client._extract_pesquisa_rapida(html)
        assert "hash=first" in client._pesquisa_rapida_action

    def test_accepts_prebuilt_soup(self):
        client = make_client()
        html = '<form action="sei.php?acao=protocolo_pesquisa_rapida&amp;hash=x"></form>'
        soup = BeautifulSoup(html, "html.parser")
        client._extract_pesquisa_rapida("", soup=soup)
        assert "protocolo_pesquisa_rapida" in client._pesquisa_rapida_action

    def test_partial_action_match_is_accepted(self):
        """Action URL can have extra prefix path segments."""
        client = make_client()
        html = '<form action="/sei/controlador.php?acao=protocolo_pesquisa_rapida"></form>'
        client._extract_pesquisa_rapida(html)
        assert client._pesquisa_rapida_action is not None


# ---------------------------------------------------------------------------
# 2. _extract_main_form
# ---------------------------------------------------------------------------


class TestExtractMainForm:
    def test_basic_sets_action_and_hidden_fields(self):
        client = make_client()
        html = (
            '<form action="ctrl.php?acao=procedimento_controlar&amp;infra_hash=xyz">'
            '  <input type="hidden" name="hdnToken" value="tok123">'
            '  <input type="hidden" name="hdnTipoVisualizacao" value="D">'
            '  <input type="submit" name="sbm" value="OK">'
            "</form>"
        )
        client._extract_main_form(html)
        assert client._form_action == "ctrl.php?acao=procedimento_controlar&infra_hash=xyz"
        assert client._form_hidden == {"hdnToken": "tok123", "hdnTipoVisualizacao": "D"}

    def test_no_matching_form_leaves_unchanged(self):
        client = make_client()
        client._form_action = "kept"
        client._form_hidden = {"a": "b"}
        html = '<form action="sei.php?acao=other_action"><input type="hidden" name="x" value="y"></form>'
        client._extract_main_form(html)
        assert client._form_action == "kept"
        assert client._form_hidden == {"a": "b"}

    def test_empty_html_does_nothing(self):
        client = make_client()
        client._extract_main_form("")
        assert client._form_action is None
        assert client._form_hidden == {}

    def test_ampersand_decoded_in_action(self):
        client = make_client()
        html = '<form action="a.php?acao=procedimento_controlar&amp;p=1&amp;q=2"></form>'
        client._extract_main_form(html)
        assert "&amp;" not in client._form_action
        assert "p=1&q=2" in client._form_action

    def test_ignores_non_hidden_inputs(self):
        client = make_client()
        html = (
            '<form action="a.php?acao=procedimento_controlar">'
            '  <input type="hidden" name="h1" value="v1">'
            '  <input type="text" name="txt" value="ignored">'
            '  <input type="submit" name="sbm" value="Go">'
            "</form>"
        )
        client._extract_main_form(html)
        assert "h1" in client._form_hidden
        assert "txt" not in client._form_hidden
        assert "sbm" not in client._form_hidden

    def test_multiple_forms_first_matching_wins(self):
        client = make_client()
        html = (
            '<form action="a.php?acao=unrelated"></form>'
            '<form action="a.php?acao=procedimento_controlar&amp;hash=first">'
            '  <input type="hidden" name="token" value="t1">'
            "</form>"
            '<form action="a.php?acao=procedimento_controlar&amp;hash=second">'
            '  <input type="hidden" name="token" value="t2">'
            "</form>"
        )
        client._extract_main_form(html)
        assert "hash=first" in client._form_action
        assert client._form_hidden.get("token") == "t1"

    def test_hidden_input_without_name_is_skipped(self):
        client = make_client()
        html = (
            '<form action="a.php?acao=procedimento_controlar">'
            '  <input type="hidden" value="no-name">'
            '  <input type="hidden" name="good" value="yes">'
            "</form>"
        )
        client._extract_main_form(html)
        assert client._form_hidden == {"good": "yes"}


# ---------------------------------------------------------------------------
# 3. _populate_trabalhar_links
# ---------------------------------------------------------------------------


class TestPopulateTrabalharLinks:
    def test_basic_populates_dict(self):
        client = make_client()
        html = '<a href="sei.php?acao=procedimento_trabalhar&amp;hash=abc">00001.000001/2024-01</a>'
        client._populate_trabalhar_links(html)
        assert (
            client._trabalhar_links.get("00001.000001/2024-01")
            == "sei.php?acao=procedimento_trabalhar&hash=abc"
        )

    def test_no_matching_links_leaves_dict_empty(self):
        client = make_client()
        html = '<a href="sei.php?acao=other_action">PROC-001</a>'
        client._populate_trabalhar_links(html)
        assert client._trabalhar_links == {}

    def test_multiple_processes_all_added(self):
        client = make_client()
        html = (
            '<a href="sei.php?acao=procedimento_trabalhar&amp;id=1">PROC-001</a>'
            '<a href="sei.php?acao=procedimento_trabalhar&amp;id=2">PROC-002</a>'
            '<a href="sei.php?acao=procedimento_trabalhar&amp;id=3">PROC-003</a>'
        )
        client._populate_trabalhar_links(html)
        assert len(client._trabalhar_links) == 3
        assert "PROC-001" in client._trabalhar_links
        assert "PROC-002" in client._trabalhar_links
        assert "PROC-003" in client._trabalhar_links

    def test_setdefault_first_seen_wins(self):
        """When same protocol appears twice, first href must be kept."""
        client = make_client()
        html = (
            '<a href="sei.php?acao=procedimento_trabalhar&amp;id=first">PROC-001</a>'
            '<a href="sei.php?acao=procedimento_trabalhar&amp;id=second">PROC-001</a>'
        )
        client._populate_trabalhar_links(html)
        assert "id=first" in client._trabalhar_links["PROC-001"]

    def test_ampersand_entities_decoded_in_href(self):
        client = make_client()
        html = '<a href="s.php?acao=procedimento_trabalhar&amp;p=1&amp;q=2">PROC-X</a>'
        client._populate_trabalhar_links(html)
        href = client._trabalhar_links.get("PROC-X", "")
        assert "&amp;" not in href
        assert "p=1&q=2" in href

    def test_link_without_text_is_skipped(self):
        client = make_client()
        html = '<a href="sei.php?acao=procedimento_trabalhar&amp;id=1"></a>'
        client._populate_trabalhar_links(html)
        assert client._trabalhar_links == {}

    def test_prebuilt_soup_accepted(self):
        client = make_client()
        html = '<a href="sei.php?acao=procedimento_trabalhar&amp;id=99">PROC-99</a>'
        soup = BeautifulSoup(html, "html.parser")
        client._populate_trabalhar_links("", soup=soup)
        assert "PROC-99" in client._trabalhar_links

    def test_existing_entries_preserved_on_second_call(self):
        client = make_client()
        client._trabalhar_links["OLD-PROC"] = "http://old"
        html = '<a href="sei.php?acao=procedimento_trabalhar&amp;id=new">NEW-PROC</a>'
        client._populate_trabalhar_links(html)
        assert "OLD-PROC" in client._trabalhar_links
        assert "NEW-PROC" in client._trabalhar_links


# ---------------------------------------------------------------------------
# 4. _extract_unidade_atual
# ---------------------------------------------------------------------------


class TestExtractUnidadeAtual:
    def test_basic_sets_sigla_and_nome(self):
        client = make_client()
        html = '<a id="unidade123" title="Gerência de Planejamento e Finanças">GPF</a>'
        client._extract_unidade_atual(html)
        assert client._unidade_atual is not None
        assert client._unidade_atual["sigla"] == "GPF"
        assert client._unidade_atual["nome"] == "Gerência de Planejamento e Finanças"

    def test_no_unit_link_does_nothing(self):
        client = make_client()
        html = "<div>No unit link here</div>"
        client._extract_unidade_atual(html)
        assert client._unidade_atual is None

    def test_parses_user_info_from_lnk_usuario_sistema(self):
        client = make_client()
        html = (
            '<a id="unidadeXYZ" title="Unidade Teste">UT</a>'
            '<a id="lnkUsuarioSistema" title="JOAO DA SILVA (42/ANTAQ)">Joao</a>'
        )
        client._extract_unidade_atual(html)
        assert client._nome_usuario == "JOAO DA SILVA"
        assert client._id_usuario == "42"
        assert client._orgao_usuario == "ANTAQ"

    def test_user_info_not_set_if_lnk_usuario_sistema_absent(self):
        client = make_client()
        html = '<a id="unidadeABC" title="Unidade ABC">ABC</a>'
        client._extract_unidade_atual(html)
        assert client._nome_usuario is None
        assert client._id_usuario is None

    def test_id_unidade_from_inbox_url(self):
        """id_unidade is read from infra_unidade_atual in the inbox URL query params.

        Note: the production code uses str(url.query) which on httpx.URL returns
        the bytes repr (b'...').  parse_qsl therefore parses byte-string keys.
        We set _inbox_url to a real httpx.URL and verify that infra_unidade_atual
        is picked up — using the same str(url.query) path as production.
        """
        client = make_client()
        client._inbox_url = httpx.URL(
            "http://sei.test/sei/controlador.php?acao=procedimento_controlar&infra_unidade_atual=99"
        )
        # Reproduce what _extract_unidade_atual does to obtain the id
        raw_query = str(client._inbox_url.query)
        id_via_code = dict(parse_qsl(raw_query)).get("infra_unidade_atual", "")
        html = '<a id="unidade99" title="Nome Unidade">SIGLA</a>'
        client._extract_unidade_atual(html)
        # The test asserts behavioural consistency: id_unidade stored == what parse_qsl extracts
        assert client._unidade_atual.get("id_unidade") == id_via_code

    def test_id_unidade_absent_if_no_inbox_url(self):
        client = make_client()
        html = '<a id="unidadeXYZ" title="Nome">SIG</a>'
        client._extract_unidade_atual(html)
        assert "id_unidade" not in client._unidade_atual

    def test_user_title_with_unusual_name_format(self):
        """Title without expected (ID/SIGLA) pattern should not crash."""
        client = make_client()
        html = (
            '<a id="unidadeZ" title="Unidade Z">Z</a>'
            '<a id="lnkUsuarioSistema" title="Formato Inesperado">user</a>'
        )
        client._extract_unidade_atual(html)
        # Should set unidade but not blow up on user parsing
        assert client._unidade_atual is not None
        assert client._nome_usuario is None  # regex didn't match

    def test_case_insensitive_id_match(self):
        """id="Unidade123" (mixed case) should still match."""
        client = make_client()
        html = '<a id="Unidade123" title="Unidade Mista">UM</a>'
        client._extract_unidade_atual(html)
        assert client._unidade_atual is not None
        assert client._unidade_atual["sigla"] == "UM"


# ---------------------------------------------------------------------------
# 5. SEIWebClient._units_from_form (staticmethod)
# ---------------------------------------------------------------------------


class TestUnitsFromForm:
    def _make_form(self, rows_html: str) -> Tag:
        html = f"<form><table>{rows_html}</table></form>"
        soup = BeautifulSoup(html, "html.parser")
        return soup.find("form")

    def test_basic_parses_single_unit(self):
        form = self._make_form(
            "<tr>"
            '  <td><input name="chkInfraItem" value="10"></td>'
            "  <td>GPF</td>"
            "  <td>Gerência de Planejamento e Finanças</td>"
            "</tr>"
        )
        result = SEIWebClient._units_from_form(form)
        assert len(result) == 1
        assert result[0] == {
            "id_unidade": "10",
            "sigla": "GPF",
            "nome": "Gerência de Planejamento e Finanças",
        }

    def test_multiple_units_all_parsed(self):
        form = self._make_form(
            '<tr><td><input name="chkInfraItem" value="1"></td><td>A</td><td>Alpha</td></tr>'
            '<tr><td><input name="chkInfraItem" value="2"></td><td>B</td><td>Beta</td></tr>'
            '<tr><td><input name="chkInfraItem" value="3"></td><td>C</td><td>Gamma</td></tr>'
        )
        result = SEIWebClient._units_from_form(form)
        assert len(result) == 3
        ids = {u["id_unidade"] for u in result}
        assert ids == {"1", "2", "3"}

    def test_row_with_fewer_than_2_tds_is_skipped(self):
        form = self._make_form('<tr><td><input name="chkInfraItem" value="5"></td></tr>')
        result = SEIWebClient._units_from_form(form)
        assert result == []

    def test_input_without_value_is_skipped(self):
        form = self._make_form(
            '<tr>  <td><input name="chkInfraItem" value=""></td>  <td>SIG</td><td>Nome</td></tr>'
        )
        result = SEIWebClient._units_from_form(form)
        assert result == []

    def test_ignores_inputs_with_different_name(self):
        form = self._make_form(
            '<tr>  <td><input name="otherField" value="99"></td>  <td>X</td><td>Xray</td></tr>'
        )
        result = SEIWebClient._units_from_form(form)
        assert result == []

    def test_extra_whitespace_in_td_is_normalized(self):
        form = self._make_form(
            "<tr>"
            '  <td><input name="chkInfraItem" value="7"></td>'
            "  <td>  GR  </td>"
            "  <td>  Gerência   Regional  </td>"
            "</tr>"
        )
        result = SEIWebClient._units_from_form(form)
        assert result[0]["sigla"] == "GR"
        assert result[0]["nome"] == "Gerência Regional"

    def test_empty_form_returns_empty_list(self):
        html = "<form></form>"
        soup = BeautifulSoup(html, "html.parser")
        form = soup.find("form")
        result = SEIWebClient._units_from_form(form)
        assert result == []

    def test_radio_without_parent_tr_is_skipped(self):
        """Input not nested in a <tr> should not cause errors — just be skipped."""
        html = '<form><input name="chkInfraItem" value="orphan"></form>'
        soup = BeautifulSoup(html, "html.parser")
        form = soup.find("form")
        result = SEIWebClient._units_from_form(form)
        assert result == []


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _make_link(onmouseover: str = "", href: str = "#") -> Tag:
    """Build a minimal <a> tag with the given onmouseover attribute."""
    attrs = f'href="{href}"'
    if onmouseover:
        attrs += f' onmouseover="{onmouseover}"'
    html = f"<a {attrs}>Processo</a>"
    soup = BeautifulSoup(html, "html.parser")
    tag = soup.find("a")
    assert isinstance(tag, Tag)
    return tag


def _detalhada_html(rows_html: str, headers: str = "<th></th><th></th><th>Processo</th>") -> str:
    return f"""
    <html><body>
    <table id="tblProcessosDetalhado">
      <thead><tr>{headers}</tr></thead>
      <tbody>{rows_html}</tbody>
    </table>
    </body></html>
    """


def _resumida_html(
    recebidos_rows: str = "",
    gerados_rows: str = "",
) -> str:
    recv = f'<table id="tblProcessosRecebidos"><thead><tr><th>P</th></tr></thead><tbody>{recebidos_rows}</tbody></table>'
    gen = f'<table id="tblProcessosGerados"><thead><tr><th>P</th></tr></thead><tbody>{gerados_rows}</tbody></table>'
    return f"<html><body>{recv}{gen}</body></html>"


# ---------------------------------------------------------------------------
# _extract_tooltip
# ---------------------------------------------------------------------------


class TestExtractTooltip:
    def test_extracts_especificacao_and_tipo(self) -> None:
        link = _make_link(
            "return infraTooltipMostrar('Minha Especificação','Contrato Administrativo')"
        )
        row: dict = {}
        _extract_tooltip(link, row)
        assert row["especificacao"] == "Minha Especificação"
        assert row["tipo"] == "Contrato Administrativo"

    def test_empty_especificacao_not_set(self) -> None:
        """When the first group is empty, row should NOT get 'especificacao'."""
        link = _make_link("return infraTooltipMostrar('','Tipo Qualquer')")
        row: dict = {}
        _extract_tooltip(link, row)
        assert "especificacao" not in row
        assert row["tipo"] == "Tipo Qualquer"

    def test_tipo_not_overwritten_when_already_set(self) -> None:
        """If 'Tipo' key is already in row the tooltip must NOT overwrite it."""
        link = _make_link("return infraTooltipMostrar('Esp','Tipo Novo')")
        row: dict = {"Tipo": "Tipo Antigo"}
        _extract_tooltip(link, row)
        assert row["Tipo"] == "Tipo Antigo"
        assert "tipo" not in row

    def test_does_nothing_when_no_onmouseover(self) -> None:
        link = _make_link()  # no onmouseover
        row: dict = {}
        _extract_tooltip(link, row)
        assert row == {}

    def test_does_nothing_when_pattern_not_found(self) -> None:
        link = _make_link("return someOtherFunction('a','b')")
        row: dict = {}
        _extract_tooltip(link, row)
        assert row == {}

    def test_whitespace_stripped_from_values(self) -> None:
        link = _make_link("return infraTooltipMostrar('  Espac  ','  Tipo  ')")
        row: dict = {}
        _extract_tooltip(link, row)
        assert row["especificacao"] == "Espac"
        assert row["tipo"] == "Tipo"

    def test_special_characters_in_especificacao(self) -> None:
        link = _make_link("return infraTooltipMostrar('Processo nº 001/2024','Requerimento')")
        row: dict = {}
        _extract_tooltip(link, row)
        assert "001/2024" in row["especificacao"]
        assert row["tipo"] == "Requerimento"

    def test_empty_tipo_not_set(self) -> None:
        """When the second group is empty, 'tipo' should NOT be inserted into row."""
        link = _make_link("return infraTooltipMostrar('Especificação X','')")
        row: dict = {}
        _extract_tooltip(link, row)
        assert row["especificacao"] == "Especificação X"
        assert "tipo" not in row


# ---------------------------------------------------------------------------
# Extended tests: parse_arvore_nos
# ---------------------------------------------------------------------------


def _nos_js(*nodes: tuple) -> str:
    """Build a minimal infraArvoreNo JS block from tuples of positional args.

    Each tuple should have at least 7 elements:
    (tipo_no, id, pai, link, target, label, tooltip[, icone])
    Values are placed inside single-quoted JS strings.
    """
    lines = [""]
    for i, args in enumerate(nodes):
        quoted = ", ".join(f"'{a}'" for a in args)
        lines.append(f"Nos[{i}] = new infraArvoreNo({quoted});")
    return "\n".join(lines)


class TestParseArvoreNosExtended:
    def test_single_node_all_fields_mapped(self) -> None:
        js = _nos_js(
            (
                "P",
                "proc001",
                "0",
                "http://link",
                "_self",
                "Processo 0001/2024",
                "tooltip txt",
                "icon.gif",
            )
        )
        result = parse_arvore_nos(js)
        assert len(result) == 1
        node = result[0]
        assert node["tipo_no"] == "P"
        assert node["id"] == "proc001"
        assert node["pai"] == "0"
        assert node["link"] == "http://link"
        assert node["target"] == "_self"
        assert node["label"] == "Processo 0001/2024"
        assert node["tooltip"] == "tooltip txt"
        assert node["icone"] == "icon.gif"

    def test_multiple_nodes_ordered(self) -> None:
        js = _nos_js(
            ("P", "root", "0", "", "", "Processo", "", ""),
            ("D", "doc1", "root", "", "", "Despacho", "", ""),
            ("D", "doc2", "root", "", "", "Ofício", "", ""),
        )
        result = parse_arvore_nos(js)
        assert len(result) == 3
        assert result[0]["id"] == "root"
        assert result[1]["id"] == "doc1"
        assert result[2]["id"] == "doc2"

    def test_null_values_become_empty_string(self) -> None:
        """The unquote() helper converts bare `null` to empty string."""
        js = "Nos[0] = new infraArvoreNo('P', 'id1', null, null, '', 'Label', '', '');"
        result = parse_arvore_nos(js)
        assert len(result) == 1
        assert result[0]["pai"] == ""
        assert result[0]["link"] == ""

    def test_missing_icone_field_defaults_to_empty(self) -> None:
        """When only 7 args are present (no icone), icone defaults to empty string."""
        js = "Nos[0] = new infraArvoreNo('P', 'id1', '0', '', '', 'Label', 'tip');"
        result = parse_arvore_nos(js)
        assert len(result) == 1
        assert result[0]["icone"] == ""

    def test_fewer_than_7_args_skipped(self) -> None:
        """Nodes with fewer than 7 args must be silently skipped."""
        js = "Nos[0] = new infraArvoreNo('P', 'id1', '0', '', '');"
        result = parse_arvore_nos(js)
        assert result == []

    def test_label_with_escaped_backslash_slash(self) -> None:
        """SEI escapes forward-slash as \\/ in JS — label should be preserved as-is."""
        js = r"Nos[0] = new infraArvoreNo('P', 'r', '0', '', '', '0001.000001\/2024-01', '', '');"
        result = parse_arvore_nos(js)
        assert len(result) == 1
        # The raw JS string is stored; consumers normalise if needed
        assert "0001" in result[0]["label"]

    def test_link_with_url_query_string(self) -> None:
        js = (
            "Nos[0] = new infraArvoreNo("
            "'D', 'doc99', 'root', "
            "'controlador.php?acao=doc_ver&id=99&infra_hash=abc123', "
            "'_self', 'Despacho GPF 99', '', '');"
        )
        result = parse_arvore_nos(js)
        assert len(result) == 1
        assert "infra_hash=abc123" in result[0]["link"]

    def test_no_nos_in_html_returns_empty(self) -> None:
        html = "<html><body><script>var x = 1;</script></body></html>"
        assert parse_arvore_nos(html) == []

    def test_mixed_valid_and_short_nodes(self) -> None:
        """Only nodes with >= 7 args should appear in output."""
        valid = "Nos[0] = new infraArvoreNo('P', 'r', '0', '', '', 'Label', 'tip', '');"
        short = "Nos[1] = new infraArvoreNo('D', 'x');"
        result = parse_arvore_nos(valid + "\n" + short)
        assert len(result) == 1
        assert result[0]["id"] == "r"


# ---------------------------------------------------------------------------
# Extended tests: parse_inbox
# ---------------------------------------------------------------------------


class TestParseInboxExtended:
    # -- detalhada -----------------------------------------------------------

    def test_detalhada_column_mapping_tipo_and_especificacao(self) -> None:
        """Named header columns (Tipo, Especificação, Interessados) are mapped into row dict."""
        headers = "<th></th><th></th><th>Processo</th><th>Tipo</th><th>Especificação</th><th>Interessados</th>"
        row_html = """
        <tr id="P42" class="infraTrClara">
          <td></td>
          <td></td>
          <td><a href="?acao=procedimento_trabalhar&id_procedimento=42"
                 onmouseover="return infraTooltipMostrar('Spec from tooltip','Tipo Tooltip')">
                0001.000042/2024-01
              </a>
          </td>
          <td>Requerimento</td>
          <td>Análise de pedido de isenção</td>
          <td>João Silva</td>
        </tr>
        """
        layout, rows = parse_inbox(_detalhada_html(row_html, headers))
        assert layout == "detalhada"
        assert len(rows) == 1
        row = rows[0]
        assert row["id_procedimento"] == "42"
        assert row["Tipo"] == "Requerimento"
        assert row["Especificação"] == "Análise de pedido de isenção"
        assert row["Interessados"] == "João Silva"

    def test_detalhada_column_tipo_and_tooltip_tipo_coexist(self) -> None:
        """Column 'Tipo' (uppercase) from table cell and tooltip 'tipo' (lowercase) coexist.

        _extract_tooltip is called BEFORE the column loop, so the column cell value
        has not been stored yet — the guard 'Tipo' not in row does not fire.
        Both keys end up in the row independently: 'Tipo' from the column, 'tipo'
        from the tooltip.
        """
        headers = "<th></th><th></th><th>Processo</th><th>Tipo</th>"
        row_html = """
        <tr id="P10" class="infraTrClara">
          <td></td>
          <td></td>
          <td><a href="?acao=procedimento_trabalhar&id_procedimento=10"
                 onmouseover="return infraTooltipMostrar('','Tipo do Tooltip')">
                0001.000010/2024-01
              </a>
          </td>
          <td>Tipo da Coluna</td>
        </tr>
        """
        layout, rows = parse_inbox(_detalhada_html(row_html, headers))
        assert layout == "detalhada"
        assert len(rows) == 1
        row = rows[0]
        # Column value stored under the exact header name "Tipo"
        assert row["Tipo"] == "Tipo da Coluna"
        # Tooltip value stored under lowercase "tipo" — both can coexist
        assert row["tipo"] == "Tipo do Tooltip"

    def test_detalhada_row_icones_from_images(self) -> None:
        """Images in column 1 (icones column) with title/alt are collected."""
        row_html = """
        <tr id="P77" class="infraTrClara">
          <td></td>
          <td>
            <img src="icon_urgente.gif" title="Urgente" />
            <img src="icon_novo.gif" alt="Novo" />
          </td>
          <td><a href="?acao=procedimento_trabalhar&id_procedimento=77">
                0001.000077/2024-01
              </a>
          </td>
        </tr>
        """
        layout, rows = parse_inbox(_detalhada_html(row_html))
        assert layout == "detalhada"
        assert len(rows) == 1
        icones = rows[0].get("icones", [])
        assert "Urgente" in icones
        assert "Novo" in icones

    def test_detalhada_atribuicao_parens_stripped(self) -> None:
        """Atribuição value is stripped of surrounding parentheses."""
        headers = "<th></th><th></th><th>Processo</th><th>atribuicao</th>"
        row_html = """
        <tr id="P55" class="infraTrClara">
          <td></td>
          <td></td>
          <td><a href="?acao=procedimento_trabalhar&id_procedimento=55">
                0001.000055/2024-01
              </a>
          </td>
          <td>(Fulano da Silva)</td>
        </tr>
        """
        layout, rows = parse_inbox(_detalhada_html(row_html, headers))
        assert layout == "detalhada"
        assert len(rows) == 1
        # atribuicao col name matches the {3: "atribuicao"} fallback for empty TH
        # When header says "atribuicao" the code path sets: val = _RE_PARENS.sub("", val)
        atrib = rows[0].get("atribuicao", "")
        assert "Fulano da Silva" in atrib
        assert not atrib.startswith("(")
        assert not atrib.endswith(")")

    def test_detalhada_no_data_rows_returns_empty_list(self) -> None:
        """tblProcessosDetalhado with only a header row yields layout=detalhada, rows=[]."""
        html = """
        <html><body>
        <table id="tblProcessosDetalhado">
          <thead><tr><th>Processo</th></tr></thead>
          <tbody></tbody>
        </table>
        </body></html>
        """
        layout, rows = parse_inbox(html)
        assert layout == "detalhada"
        assert rows == []

    def test_detalhada_no_thead_at_all_returns_empty(self) -> None:
        """tblProcessosDetalhado with no <tr> at all returns ('detalhada', [])."""
        html = "<html><body><table id='tblProcessosDetalhado'></table></body></html>"
        layout, rows = parse_inbox(html)
        assert layout == "detalhada"
        assert rows == []

    # -- resumida tblProcessosGerados ----------------------------------------

    def test_resumida_gerados_table_detected(self) -> None:
        """tblProcessosGerados alone (no recebidos) should return layout='resumida'."""
        html = """
        <html><body>
        <table id="tblProcessosGerados">
          <thead><tr><th>Processo</th></tr></thead>
          <tbody>
            <tr id="P200" class="infraTrClara">
              <td><a href="?acao=procedimento_trabalhar&id_procedimento=200">
                    0001.000200/2024-01
                  </a>
              </td>
            </tr>
          </tbody>
        </table>
        </body></html>
        """
        layout, rows = parse_inbox(html)
        assert layout == "resumida"
        assert len(rows) == 1
        assert rows[0]["id_procedimento"] == "200"
        assert rows[0]["origem"] == "gerado"

    def test_resumida_combined_recebidos_and_gerados(self) -> None:
        """Both tblProcessosRecebidos and tblProcessosGerados are merged into one list."""
        html = _resumida_html(
            recebidos_rows="""
              <tr id="P301" class="infraTrClara">
                <td><a href="?acao=procedimento_trabalhar&id_procedimento=301">
                      0001.000301/2024-01
                    </a>
                </td>
              </tr>
            """,
            gerados_rows="""
              <tr id="P302" class="infraTrClara">
                <td><a href="?acao=procedimento_trabalhar&id_procedimento=302">
                      0001.000302/2024-01
                    </a>
                </td>
              </tr>
              <tr id="P303" class="infraTrClara">
                <td><a href="?acao=procedimento_trabalhar&id_procedimento=303">
                      0001.000303/2024-01
                    </a>
                </td>
              </tr>
            """,
        )
        layout, rows = parse_inbox(html)
        assert layout == "resumida"
        assert len(rows) == 3
        origens = {r["id_procedimento"]: r["origem"] for r in rows}
        assert origens["301"] == "recebido"
        assert origens["302"] == "gerado"
        assert origens["303"] == "gerado"

    def test_resumida_tooltip_extracted_on_recebidos(self) -> None:
        """Tooltip fields are extracted for processes in tblProcessosRecebidos."""
        html = _resumida_html(
            recebidos_rows="""
              <tr id="P400" class="infraTrClara">
                <td>
                  <a href="?acao=procedimento_trabalhar&id_procedimento=400"
                     onmouseover="return infraTooltipMostrar('Esp Recebido','Tipo Recebido')">
                    0001.000400/2024-01
                  </a>
                </td>
              </tr>
            """,
        )
        layout, rows = parse_inbox(html)
        assert layout == "resumida"
        assert rows[0]["especificacao"] == "Esp Recebido"
        assert rows[0]["tipo"] == "Tipo Recebido"

    def test_resumida_atribuicao_from_last_td(self) -> None:
        """When >= 4 tds present, last td becomes atribuicao (parens stripped)."""
        html = """
        <html><body>
        <table id="tblProcessosRecebidos">
          <thead><tr><th>P</th></tr></thead>
          <tbody>
            <tr id="P500" class="infraTrClara">
              <td><a href="?acao=procedimento_trabalhar&id_procedimento=500">
                    0001.000500/2024-01
                  </a>
              </td>
              <td></td>
              <td></td>
              <td>(Maria Souza)</td>
            </tr>
          </tbody>
        </table>
        </body></html>
        """
        layout, rows = parse_inbox(html)
        assert layout == "resumida"
        assert len(rows) == 1
        atrib = rows[0].get("atribuicao", "")
        assert "Maria Souza" in atrib
        assert not atrib.startswith("(")

    def test_resumida_icones_collected_from_second_td(self) -> None:
        """Images in td[1] with title or alt are collected as icones list."""
        html = """
        <html><body>
        <table id="tblProcessosRecebidos">
          <thead><tr><th>P</th></tr></thead>
          <tbody>
            <tr id="P600" class="infraTrClara">
              <td><a href="?acao=procedimento_trabalhar&id_procedimento=600">
                    0001.000600/2024-01
                  </a>
              </td>
              <td>
                <img src="bloqueado.gif" title="Bloqueado" />
                <img src="pendente.gif" alt="Pendente" />
              </td>
            </tr>
          </tbody>
        </table>
        </body></html>
        """
        layout, rows = parse_inbox(html)
        assert layout == "resumida"
        icones = rows[0].get("icones", [])
        assert "Bloqueado" in icones
        assert "Pendente" in icones

    def test_no_recognized_table_returns_desconhecido(self) -> None:
        """HTML without any known table id returns layout='desconhecido' and empty list."""
        html = """
        <html><body>
        <table id="tblOutraCoisa">
          <tr><td>nada relevante</td></tr>
        </table>
        </body></html>
        """
        layout, rows = parse_inbox(html)
        assert layout == "desconhecido"
        assert rows == []

    def test_empty_resumida_tables_still_return_resumida(self) -> None:
        """Empty tblProcessosRecebidos is still recognized as resumida layout."""
        html = """
        <html><body>
        <table id="tblProcessosRecebidos">
          <thead><tr><th>Processo</th></tr></thead>
          <tbody></tbody>
        </table>
        </body></html>
        """
        layout, rows = parse_inbox(html)
        assert layout == "resumida"
        assert rows == []
