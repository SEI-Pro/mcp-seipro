"""Unit tests for HTML parser functions in sei_web_client.

These tests exercise the pure parser functions with synthetic HTML snippets —
no live SEI server required.
"""

from __future__ import annotations

from bs4 import BeautifulSoup, Tag

from todos.sei_web_client import (
    SEIWebClient,
    _parse_doc_label,
    parse_arvore_nos,
    parse_inbox,
)

# ---------------------------------------------------------------------------
# _parse_doc_label
# ---------------------------------------------------------------------------


class TestParseDocLabel:
    def test_tipo_sigla_numero(self) -> None:
        result = _parse_doc_label("Despacho GPF 2874369")
        assert result["tipo_documento"] == "Despacho"
        assert result["sigla_unidade"] == "GPF"
        assert result["numero_sei"] == "2874369"

    def test_parentheses_format(self) -> None:
        result = _parse_doc_label("Ofício (0012345)")
        assert result["tipo_documento"] == "Ofício"
        assert result["numero_sei"] == "0012345"

    def test_sigla_with_slash(self) -> None:
        result = _parse_doc_label("Nota Técnica SA/NT 9876543")
        assert result["numero_sei"] == "9876543"
        assert "SA/NT" in result.get("sigla_unidade", result.get("tipo_documento", ""))

    def test_empty_string(self) -> None:
        result = _parse_doc_label("")
        assert result == {}, f"Label vazio deve retornar dict vazio, retornou: {result!r}"

    def test_no_number(self) -> None:
        result = _parse_doc_label("Memorando")
        assert result["tipo_documento"] == "Memorando"
        assert result.get("numero_sei", "") == ""

    def test_type_with_long_number_suffix(self) -> None:
        result = _parse_doc_label("Relatório 1234567")
        assert result["numero_sei"] == "1234567"

    def test_parentheses_with_sigla(self) -> None:
        result = _parse_doc_label("Comprovante e-CGU SA (4567890)")
        assert result["numero_sei"] == "4567890"
        assert "Comprovante" in result["tipo_documento"]


# ---------------------------------------------------------------------------
# SEIWebClient._parse_acompanhamento_tabela  (static method)
# ---------------------------------------------------------------------------


def _make_infra_table(rows_html: str) -> Tag:
    """Helper: wraps row HTML in a minimal infraTable."""
    html = f"""
    <table class="infraTable">
      <thead><tr><th>Processo</th><th>Tipo</th><th>Obs</th></tr></thead>
      <tbody>{rows_html}</tbody>
    </table>
    """
    soup = BeautifulSoup(html, "html.parser")
    result = soup.find("table")
    assert isinstance(result, Tag)
    return result


class TestParseAcompanhamentoTabela:
    def test_none_returns_empty(self) -> None:
        assert SEIWebClient._parse_acompanhamento_tabela(None, 50) == []

    def test_empty_table_body(self) -> None:
        tbl = _make_infra_table("")
        assert SEIWebClient._parse_acompanhamento_tabela(tbl, 50) == []

    def test_single_row_with_link(self) -> None:
        tbl = _make_infra_table("""
          <tr>
            <td><a href="?id_procedimento=456&acao=procedimento_trabalhar">
              0001.000002/2024-01
            </a></td>
            <td>Requerimento</td>
            <td>Em análise</td>
          </tr>
        """)
        rows = SEIWebClient._parse_acompanhamento_tabela(tbl, 50)
        assert len(rows) == 1
        row = rows[0]
        assert row["idProcedimento"] == "456"
        assert "0001.000002/2024-01" in row["protocoloFormatado"]
        assert row["tipo"] == "Requerimento"
        assert row["observacao"] == "Em análise"

    def test_row_without_id_procedimento(self) -> None:
        tbl = _make_infra_table("""
          <tr>
            <td>0001.000003/2024-01</td>
            <td>Ofício</td>
            <td></td>
          </tr>
        """)
        rows = SEIWebClient._parse_acompanhamento_tabela(tbl, 50)
        assert len(rows) == 1
        assert rows[0]["protocoloFormatado"] == "0001.000003/2024-01"
        assert rows[0]["tipo"] == "Ofício"
        assert "idProcedimento" not in rows[0]

    def test_limit_is_respected(self) -> None:
        row_html = "".join(
            f'<tr><td><a href="?id_procedimento={i}">000{i}/2024</a></td>'
            f"<td>Tipo</td><td></td></tr>"
            for i in range(10)
        )
        tbl = _make_infra_table(row_html)
        rows = SEIWebClient._parse_acompanhamento_tabela(tbl, 3)
        assert len(rows) == 3

    def test_skips_header_row(self) -> None:
        tbl = _make_infra_table("""
          <tr>
            <td><a href="?id_procedimento=1">A/2024</a></td>
            <td>T</td><td></td>
          </tr>
        """)
        # The first <tr> in thead is skipped by [1:] slice; tbody rows are parsed
        rows = SEIWebClient._parse_acompanhamento_tabela(tbl, 50)
        assert len(rows) == 1

    def test_empty_row_skipped(self) -> None:
        tbl = _make_infra_table("<tr></tr>")
        rows = SEIWebClient._parse_acompanhamento_tabela(tbl, 50)
        assert rows == []


# ---------------------------------------------------------------------------
# parse_inbox
# ---------------------------------------------------------------------------


class TestParseInbox:
    def test_empty_html(self) -> None:
        layout, rows = parse_inbox("<html><body></body></html>")
        assert layout == "desconhecido"
        assert rows == []

    def test_detalhada_layout_detected(self) -> None:
        # SEI uses id="P{id_procedimento}" on data rows in tblProcessosDetalhado
        html = """
        <html><body>
        <table id="tblProcessosDetalhado">
          <thead><tr><th>Processo</th></tr></thead>
          <tbody>
            <tr id="P789" class="infraTrClara">
              <td>
                <a href="?acao=procedimento_trabalhar&id_procedimento=789"
                   onmouseover="return infraTooltipMostrar('Especificação X','Contrato')">
                  0001.000001/2024-01
                </a>
              </td>
            </tr>
          </tbody>
        </table>
        </body></html>
        """
        layout, rows = parse_inbox(html)
        assert layout == "detalhada"
        assert len(rows) >= 1
        assert any("0001.000001/2024-01" in r.get("protocolo", "") for r in rows)

    def test_detalhada_extracts_tooltip(self) -> None:
        html = """
        <html><body>
        <table id="tblProcessosDetalhado">
          <thead><tr><th>Processo</th></tr></thead>
          <tbody>
            <tr id="P999" class="infraTrClara">
              <td>
                <a href="?acao=procedimento_trabalhar&id_procedimento=999"
                   onmouseover="return infraTooltipMostrar('Minha Especificacao','Tipo X')">
                  9999.000001/2024-01
                </a>
              </td>
            </tr>
          </tbody>
        </table>
        </body></html>
        """
        _, rows = parse_inbox(html)
        assert len(rows) >= 1
        row = rows[0]
        assert row.get("especificacao") == "Minha Especificacao"
        assert row.get("tipo") == "Tipo X"

    def test_resumida_table_detected(self) -> None:
        # Resumida also uses id="P{id}" on data rows
        html = """
        <html><body>
        <table id="tblProcessosRecebidos">
          <thead><tr><th>Processo</th></tr></thead>
          <tbody>
            <tr id="P111" class="infraTrClara">
              <td>
                <a href="?acao=procedimento_trabalhar&id_procedimento=111">
                  0001.000010/2024-01
                </a>
              </td>
            </tr>
          </tbody>
        </table>
        </body></html>
        """
        layout, rows = parse_inbox(html)
        assert layout == "resumida"
        assert len(rows) >= 1

    def test_multiple_processos_returned(self) -> None:
        rows_html = "".join(
            f"""<tr id="P{i}" class="infraTrClara">
              <td>
                <a href="?acao=procedimento_trabalhar&id_procedimento={i}"
                   onmouseover="return infraTooltipMostrar('Esp {i}','Tipo')">
                  0001.{i:06d}/2024-01
                </a>
              </td>
            </tr>"""
            for i in range(1, 4)
        )
        html = f"""
        <html><body>
        <table id="tblProcessosDetalhado">
          <thead><tr><th>Processo</th></tr></thead>
          <tbody>{rows_html}</tbody>
        </table>
        </body></html>
        """
        _, rows = parse_inbox(html)
        assert len(rows) == 3


# ---------------------------------------------------------------------------
# parse_arvore_nos
# ---------------------------------------------------------------------------


class TestParseArvoreNos:
    def test_empty_string_returns_empty_list(self) -> None:
        result = parse_arvore_nos("")
        assert result == []

    def test_garbage_input_returns_empty_list(self) -> None:
        result = parse_arvore_nos("this is not JS")
        assert result == []

    def test_single_no_has_all_expected_fields(self) -> None:
        # Strengthen: check field values, not just the type of the return.
        js = r"""
        Nos = [];
        Nos[0] = new infraArvoreNo('proc', 'p777', null, '', '', 'SEI-001/2024', '', '');
        """
        result = parse_arvore_nos(js)
        assert len(result) == 1
        assert result[0]["tipo_no"] == "proc"
        assert result[0]["id"] == "p777"
        assert result[0]["label"] == "SEI-001/2024"

    def test_minimal_nos_structure(self) -> None:
        js = r"""
        Nos = [];
        Nos[0] = new infraArvoreNo('proctipo', 'id123', null, '', '', 'Processo', '', '');
        """
        result = parse_arvore_nos(js)
        assert len(result) == 1, f"Esperado 1 nó, obtido {len(result)}: {result!r}"
        assert result[0]["tipo_no"] == "proctipo", f"tipo_no inesperado: {result[0]!r}"
        assert result[0]["id"] == "id123", f"id inesperado: {result[0]!r}"

    def test_full_nos_with_link(self) -> None:
        js = r"""
        Nos = [];
        Nos[0] = new infraArvoreNo('proctipo', 'proc0001', null, 'controlador.php?acao=procedimento_concluir&id_procedimento=123&infra_hash=abc', '_self', 'Processo 0001.000001\/2024-01', 'tooltip', 'icone.gif');
        Nos[1] = new infraArvoreNo('doc', 'doc456', 'proc0001', '', '_self', 'Despacho', '', '');
        """
        result = parse_arvore_nos(js)
        assert len(result) == 2, f"Esperados 2 nós, obtidos {len(result)}: {result!r}"
        assert result[0]["tipo_no"] == "proctipo", f"tipo_no do nó raiz inesperado: {result[0]!r}"
        assert result[0]["id"] == "proc0001", f"id do nó raiz inesperado: {result[0]!r}"
        assert "Processo" in result[0]["label"], f"label do nó raiz inesperado: {result[0]!r}"
        assert result[1]["tipo_no"] == "doc", f"tipo_no do nó documento inesperado: {result[1]!r}"
        assert result[1]["id"] == "doc456", f"id do nó documento inesperado: {result[1]!r}"


# ---------------------------------------------------------------------------
# _inferir_padrao_protocolo (RFC 0010)
# ---------------------------------------------------------------------------


class TestInferirPadraoProtocolo:
    def test_uniform_prefix_5_digits(self) -> None:
        from todos.tools.processos import _inferir_padrao_protocolo

        amostras = [f"50300.{i:06d}/2024-01" for i in range(10)]
        padrao, min_len, max_len = _inferir_padrao_protocolo(amostras)
        assert min_len == max_len == 5
        assert r"\d{5}" in padrao
        assert "/\\d{4}-\\d{2}$" in padrao

    def test_uniform_prefix_4_digits(self) -> None:
        from todos.tools.processos import _inferir_padrao_protocolo

        amostras = [f"0007.{i:06d}/2025-00" for i in range(10)]
        padrao, min_len, max_len = _inferir_padrao_protocolo(amostras)
        assert min_len == max_len == 4
        assert r"\d{4}" in padrao

    def test_variable_prefix_range(self) -> None:
        from todos.tools.processos import _inferir_padrao_protocolo

        amostras = [f"50300.{i:06d}/2024-01" for i in range(8)] + [
            f"0007.{i:06d}/2024-01" for i in range(2)
        ]
        padrao, min_len, max_len = _inferir_padrao_protocolo(amostras)
        assert min_len == 4
        assert max_len == 5
        assert r"\d{4,5}" in padrao

    def test_no_valid_samples_raises(self) -> None:
        import pytest

        from todos.exceptions import SEIValidationError
        from todos.tools.processos import _inferir_padrao_protocolo

        with pytest.raises(SEIValidationError, match="Nenhum protocolo"):
            _inferir_padrao_protocolo(["not-a-protocol", "also-bad", "1234"])

    def test_mixed_valid_invalid_uses_valid_only(self) -> None:
        from todos.tools.processos import _inferir_padrao_protocolo

        amostras = [f"50300.{i:06d}/2024-01" for i in range(10)] + [
            "garbage",
            "also-not-valid",
        ]
        padrao, min_len, max_len = _inferir_padrao_protocolo(amostras)
        assert min_len == max_len == 5

    def test_keyring_pattern_key_format(self) -> None:
        from todos.tools.processos import _keyring_pattern_key

        key = _keyring_pattern_key("sei.antaq.gov.br")
        assert key == "SEI_PROTOCOLO_PATTERN@sei.antaq.gov.br"

    def test_canonical_re_filters_non_protocol_strings(self) -> None:
        from todos.tools.processos import _CANONICAL_PROTOCOLO_RE, _inferir_padrao_protocolo

        # Simulate what sei_detectar_formato_protocolo does: filter before counting
        todas = [f"50300.{i:06d}/2024-01" for i in range(10)] + [
            "garbage",
            "legacy-row",
            "123",
        ]
        amostras = [s for s in todas if _CANONICAL_PROTOCOLO_RE.match(s)]
        assert len(amostras) == 10
        padrao, min_len, max_len = _inferir_padrao_protocolo(amostras)
        assert min_len == max_len == 5
