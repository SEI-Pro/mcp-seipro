"""Unit tests for HTML parser functions in sei_web_client.

These tests exercise the pure parser functions with synthetic HTML snippets —
no live SEI server required.
"""

from __future__ import annotations

import pytest
from bs4 import BeautifulSoup, Tag

from todos.exceptions import SEIValidationError
from todos.sei_web_client import (
    SEIWebClient,
    _max_no_index,
    _parse_doc_label,
    _renumerar_nos_chunk,
    parse_arvore_nos,
    parse_inbox,
)
from todos.tools.configuracao import (
    _CNJ_PROTOCOLO_RE,
    _SEI_PROTOCOLO_RE,
    _formato_protocolo,
    _validar_protocolo,
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
# _formato_protocolo / _validar_protocolo — regex fixo, sem descoberta nem
# keyring (RFC 0010/0017 revogadas em favor de dois formatos fixos + fallback
# permissivo).
# ---------------------------------------------------------------------------


class TestFormatoProtocolo:
    def test_formato_sei_prefixo_5_digitos(self) -> None:
        assert _formato_protocolo("50300.000123/2025-00") == "sei"

    def test_formato_sei_prefixo_4_digitos(self) -> None:
        assert _formato_protocolo("0016.441115/2021-14") == "sei"

    def test_formato_sei_prefixo_6_digitos(self) -> None:
        assert _formato_protocolo("500300.007186/2026-69") == "sei"

    def test_formato_cnj(self) -> None:
        assert _formato_protocolo("7002098-94.2026.8.22.0014") == "cnj"

    def test_formato_desconhecido_retorna_none(self) -> None:
        assert _formato_protocolo("qualquer-coisa") is None
        assert _formato_protocolo("123") is None

    def test_sei_prefixo_fora_do_intervalo_nao_bate(self) -> None:
        # 3 dígitos de prefixo: fora do intervalo 4-6 documentado na RFC 0010.
        assert _formato_protocolo("007.000123/2025-00") is None

    def test_regexes_sao_mutuamente_exclusivos_nas_amostras(self) -> None:
        assert _SEI_PROTOCOLO_RE.fullmatch("7002098-94.2026.8.22.0014") is None
        assert _CNJ_PROTOCOLO_RE.fullmatch("50300.000123/2025-00") is None


class TestValidarProtocolo:
    """Regex fixo (SEI ou CNJ) com fallback permissivo obrigatório."""

    def test_formato_sei_passa(self) -> None:
        _validar_protocolo("50300.000123/2025-00")

    def test_formato_cnj_passa(self) -> None:
        _validar_protocolo("7002098-94.2026.8.22.0014")

    def test_formato_desconhecido_passa_pelo_fallback_permissivo(self) -> None:
        """Requisito central: formato não mapeado nunca é bloqueado."""
        _validar_protocolo("formato-totalmente-desconhecido-123")

    def test_protocolo_vazio_levanta(self) -> None:
        with pytest.raises(SEIValidationError, match="não pode ser vazio"):
            _validar_protocolo("")

    def test_protocolo_so_espacos_levanta(self) -> None:
        with pytest.raises(SEIValidationError, match="não pode ser vazio"):
            _validar_protocolo("   ")


# ---------------------------------------------------------------------------
# _max_no_index  (PR #91 — colisão de índices de pasta/paginação)
# ---------------------------------------------------------------------------


class TestMaxNoIndex:
    def test_no_nos_returns_minus_one(self) -> None:
        assert _max_no_index("var x = 1;") == -1

    def test_single_index(self) -> None:
        assert _max_no_index("Nos[3] = new infraArvoreNo();") == 3

    def test_multiple_indices_returns_max(self) -> None:
        js = "Nos[0] = x; Nos[5] = y; Nos[2] = z;"
        assert _max_no_index(js) == 5

    def test_empty_string(self) -> None:
        assert _max_no_index("") == -1


# ---------------------------------------------------------------------------
# _renumerar_nos_chunk  (PR #91 — reescrita de índices para evitar colisão)
# ---------------------------------------------------------------------------


class TestRenumerarNosChunk:
    def test_no_nos_unchanged(self) -> None:
        chunk = "var x = 1;"
        result, next_off = _renumerar_nos_chunk(chunk, 10)
        assert result == chunk
        assert next_off == 10

    def test_offset_applied(self) -> None:
        chunk = "Nos[0] = a; Nos[1] = b;"
        result, next_off = _renumerar_nos_chunk(chunk, 5)
        assert "Nos[5]" in result
        assert "Nos[6]" in result
        assert "Nos[0]" not in result
        assert next_off == 7  # 5 + max_idx(1) + 1

    def test_next_offset_correct(self) -> None:
        chunk = "Nos[0] = a; Nos[2] = b;"
        _, next_off = _renumerar_nos_chunk(chunk, 0)
        assert next_off == 3  # 0 + max_idx(2) + 1

    def test_chaining_two_chunks_no_overlap(self) -> None:
        chunk_a = "Nos[0] = a; Nos[1] = b;"
        chunk_b = "Nos[0] = c; Nos[1] = d;"
        a_renum, off = _renumerar_nos_chunk(chunk_a, 0)
        b_renum, _ = _renumerar_nos_chunk(chunk_b, off)
        combined = a_renum + b_renum
        indices = [int(m.group(1)) for m in __import__("re").finditer(r"Nos\[(\d+)\]", combined)]
        assert len(indices) == len(set(indices)), "Índices duplicados após renumeração encadeada"


# ---------------------------------------------------------------------------
# parse_arvore_nos — regressão colisão de acoes_map/src_map (PR #91)
# ---------------------------------------------------------------------------


class TestParseArvoreNosCollisionRegression:
    """Garante que acoes e src do Nos[0] de um chunk expandido não
    sobrescrevam os do Nos[0] do HTML original após renumeração."""

    @staticmethod
    def _make_js(idx: int, node_id: str, acoes_val: str, src_val: str) -> str:
        """Chunk de JS com Nos[idx] tendo id=node_id, acoes e src específicos."""
        return (
            f"Nos[{idx}] = new infraArvoreNo('doc', '{node_id}', 'proc', "
            f"'link{idx}', '_self', 'Label{idx}', '', '');\n"
            f"Nos[{idx}].acoes = '{acoes_val}';\n"
            f"Nos[{idx}].src = '{src_val}';\n"
        )

    def test_acoes_not_overwritten_after_renumber(self) -> None:
        # Chunk original: Nos[0] com id='docA'
        chunk_orig = self._make_js(0, "docA", "ACOES_ORIGINAL", "SRC_ORIGINAL")
        # Chunk expandido: também começa em Nos[0] com id='docB'
        chunk_expanded = self._make_js(0, "docB", "ACOES_EXPANDED", "SRC_EXPANDED")
        # Renumera o chunk expandido para Nos[1]
        chunk_renum, _ = _renumerar_nos_chunk(chunk_expanded, 1)
        combined = chunk_orig + chunk_renum
        nos = parse_arvore_nos(combined)
        # Deve haver 2 nós distintos
        assert len(nos) == 2
        # nó original (índice 0) mantém acoes_html/src originais
        no_orig = next(n for n in nos if n["id"] == "docA")
        assert no_orig.get("acoes_html") == "ACOES_ORIGINAL"
        assert no_orig.get("src") == "SRC_ORIGINAL"
        # nó expandido (renumerado para índice 1) mantém suas próprias acoes_html/src
        no_exp = next(n for n in nos if n["id"] == "docB")
        assert no_exp.get("acoes_html") == "ACOES_EXPANDED"
        assert no_exp.get("src") == "SRC_EXPANDED"

    def test_no_collision_without_renumber_would_fail(self) -> None:
        """Demonstra que sem renumeração os acoes_map se sobrescrevem.

        Dois chunks com Nos[0]: acoes_map["0"] recebe o último valor encontrado
        (EXPANDED), portanto docA perde sua acoes_html — essa é a colisão que
        PR #91 corrige com _renumerar_nos_chunk.
        """
        chunk_orig = self._make_js(0, "docA", "ACOES_ORIGINAL", "SRC_ORIGINAL")
        chunk_expanded = self._make_js(0, "docB", "ACOES_EXPANDED", "SRC_EXPANDED")
        # Concatena SEM renumerar — acoes_map["0"] é sobrescrito por EXPANDED
        combined = chunk_orig + chunk_expanded
        nos = parse_arvore_nos(combined)
        assert len(nos) == 2
        # Ambos os nós recebem acoes_html do acoes_map["0"] (sobrescrito)
        no_orig = next(n for n in nos if n["id"] == "docA")
        assert no_orig.get("acoes_html") == "ACOES_EXPANDED"  # ORIGINAL foi perdido


# ---------------------------------------------------------------------------
# SEIWebClient._parse_tabela_historico  (PR #91 — paginação do histórico)
# ---------------------------------------------------------------------------


def _make_historico_html(rows: list[tuple[str, str, str, str]]) -> str:
    """Gera HTML mínimo de tblHistorico com as linhas fornecidas."""
    trs = "".join(
        f"<tr><td>{dh}</td><td>{un}</td><td>{us}</td><td>{desc}</td></tr>"
        for dh, un, us, desc in rows
    )
    return f"""
    <html><body>
    <table id="tblHistorico">
      <thead><tr><th>Data/Hora</th><th>Unidade</th><th>Usuário</th><th>Descrição</th></tr></thead>
      <tbody>{trs}</tbody>
    </table>
    </body></html>
    """


class TestParseTabeHistorico:
    def test_empty_table(self) -> None:
        html = _make_historico_html([])
        assert SEIWebClient._parse_tabela_historico(html) == []

    def test_no_table_returns_empty(self) -> None:
        assert SEIWebClient._parse_tabela_historico("<html><body></body></html>") == []

    def test_single_row(self) -> None:
        html = _make_historico_html([("01/01/2024 10:00", "GPF", "joao.silva", "Recebido")])
        rows = SEIWebClient._parse_tabela_historico(html)
        assert len(rows) == 1
        assert rows[0]["data_hora"] == "01/01/2024 10:00"
        assert rows[0]["unidade"] == "GPF"
        assert rows[0]["usuario"] == "joao.silva"
        assert rows[0]["descricao"] == "Recebido"

    def test_multiple_rows(self) -> None:
        data = [
            ("01/01/2024 08:00", "A", "u1", "Ação 1"),
            ("01/01/2024 09:00", "B", "u2", "Ação 2"),
            ("01/01/2024 10:00", "C", "u3", "Ação 3"),
        ]
        html = _make_historico_html(data)
        rows = SEIWebClient._parse_tabela_historico(html)
        assert len(rows) == 3
        assert rows[2]["descricao"] == "Ação 3"

    def test_row_with_fewer_than_4_cols_skipped(self) -> None:
        html = """
        <html><body>
        <table id="tblHistorico">
          <thead><tr><th>H</th></tr></thead>
          <tbody>
            <tr><td>só uma coluna</td></tr>
            <tr><td>01/01/2024</td><td>U</td><td>u</td><td>Válida</td></tr>
          </tbody>
        </table>
        </body></html>
        """
        rows = SEIWebClient._parse_tabela_historico(html)
        assert len(rows) == 1
        assert rows[0]["descricao"] == "Válida"
