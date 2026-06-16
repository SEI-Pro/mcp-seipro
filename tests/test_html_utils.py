"""Tests for html_utils — the SEI HTML/PDF → text/markdown transforms.

These functions feed the LLM the actual content of every document the user
reads, so a regression here silently corrupts output for every tool. The HTML
and sanitize functions are pure; the PDF functions' extraction depends on heavy
optional deps (pdfplumber/pytesseract), so we monkeypatch `_extract_pdf_pages`
and exercise only the pure formatting logic on top of it.
"""

from __future__ import annotations

import pytest

from todos import html_utils as hu

# ---------------------------------------------------------------------------
# html_to_text
# ---------------------------------------------------------------------------


class TestHtmlToText:
    def test_unescapes_entities(self) -> None:
        # SEI returns HTML-escaped markup (&lt;p&gt; instead of <p>).
        raw = "&lt;p&gt;Olá &amp; tchau&lt;/p&gt;"
        assert hu.html_to_text(raw) == "Olá & tchau"

    def test_strips_style_blocks(self) -> None:
        raw = "<style>.x{color:red}</style><p>Conteúdo</p>"
        out = hu.html_to_text(raw)
        assert "color:red" not in out
        assert "Conteúdo" in out

    def test_strips_script_blocks(self) -> None:
        raw = "<script>alert('x')</script><p>Texto</p>"
        out = hu.html_to_text(raw)
        assert "alert" not in out
        assert "Texto" in out

    def test_removes_head(self) -> None:
        raw = "<html><head><meta charset='utf-8'><title>T</title></head><body>Corpo</body></html>"
        out = hu.html_to_text(raw)
        assert "Corpo" in out
        assert "utf-8" not in out
        assert "T" not in out.split()

    def test_collapses_blank_lines(self) -> None:
        raw = "<p>A</p><p></p><p>B</p>"
        out = hu.html_to_text(raw)
        assert out == "A\nB"

    def test_plain_text_passthrough(self) -> None:
        assert hu.html_to_text("sem tags aqui") == "sem tags aqui"

    def test_empty_input(self) -> None:
        assert hu.html_to_text("") == ""


# ---------------------------------------------------------------------------
# _clean_markdown_tables
# ---------------------------------------------------------------------------


class TestCleanMarkdownTables:
    def test_compacts_empty_layout_columns(self) -> None:
        md = "| conteúdo | | | | | | | | | | | | |"
        assert hu._clean_markdown_tables(md) == "| conteúdo |"

    def test_drops_fully_empty_table_row(self) -> None:
        md = "antes\n| | | | |\ndepois"
        assert hu._clean_markdown_tables(md) == "antes\ndepois"

    def test_separator_row_kept_with_its_own_dash_count(self) -> None:
        # A separator row keeps one cell per non-empty dash cell it carries.
        md = "| A | B |\n| --- | --- |\n| 1 | 2 |"
        out = hu._clean_markdown_tables(md)
        assert out == "| A | B |\n| --- | --- |\n| 1 | 2 |"

    def test_separator_padded_with_empty_cells_drops_them(self) -> None:
        # Layout padding (extra empty cells around the dashes) is stripped, so
        # the separator collapses to just the real dash columns.
        md = "| A | B |\n| --- | --- | | |\n| 1 | 2 |"
        out = hu._clean_markdown_tables(md)
        assert out == "| A | B |\n| --- | --- |\n| 1 | 2 |"

    def test_non_table_lines_untouched(self) -> None:
        md = "# Título\n\nParágrafo normal."
        assert hu._clean_markdown_tables(md) == md


# ---------------------------------------------------------------------------
# html_to_markdown
# ---------------------------------------------------------------------------


class TestHtmlToMarkdown:
    def test_preserves_bold(self) -> None:
        out = hu.html_to_markdown("<p>texto <strong>forte</strong></p>")
        assert "**forte**" in out

    def test_heading_becomes_atx(self) -> None:
        out = hu.html_to_markdown("<h1>Título</h1>")
        assert out.startswith("#")
        assert "Título" in out

    def test_strips_images(self) -> None:
        out = hu.html_to_markdown("<p>antes<img src='x.png' alt='logo'>depois</p>")
        assert "x.png" not in out
        assert "antes" in out
        assert "depois" in out

    def test_collapses_excessive_newlines(self) -> None:
        out = hu.html_to_markdown("<p>A</p><br><br><br><br><p>B</p>")
        assert "\n\n\n" not in out

    def test_unescapes_before_converting(self) -> None:
        out = hu.html_to_markdown("&lt;strong&gt;X&lt;/strong&gt;")
        assert "**X**" in out


# ---------------------------------------------------------------------------
# pdf_to_text / pdf_to_markdown (formatting logic via stubbed extraction)
# ---------------------------------------------------------------------------


class TestPdfToText:
    def test_formats_pages_with_numbering(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(hu, "_extract_pdf_pages", lambda _c: [(1, "Primeira"), (2, "Segunda")])
        out = hu.pdf_to_text(b"ignored")
        assert "--- Página 1/2 ---\nPrimeira" in out
        assert "--- Página 2/2 ---\nSegunda" in out

    def test_total_reflects_highest_page_number(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # OCR may skip blank pages, so total comes from the max page number.
        monkeypatch.setattr(hu, "_extract_pdf_pages", lambda _c: [(1, "A"), (5, "B")])
        out = hu.pdf_to_text(b"ignored")
        assert "Página 1/5" in out
        assert "Página 5/5" in out

    def test_empty_extraction_returns_fallback(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(hu, "_extract_pdf_pages", lambda _c: [])
        assert "sem texto extraível" in hu.pdf_to_text(b"ignored")


class TestPdfToMarkdown:
    def test_uppercase_lines_become_bold(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(hu, "_extract_pdf_pages", lambda _c: [(1, "TÍTULO\ncorpo normal")])
        out = hu.pdf_to_markdown(b"ignored")
        assert "**TÍTULO**" in out
        assert "corpo normal" in out

    def test_long_uppercase_line_not_bolded(self, monkeypatch: pytest.MonkeyPatch) -> None:
        long_upper = "X" * 100
        monkeypatch.setattr(hu, "_extract_pdf_pages", lambda _c: [(1, long_upper)])
        out = hu.pdf_to_markdown(b"ignored")
        assert f"**{long_upper}**" not in out

    def test_blank_lines_dropped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(hu, "_extract_pdf_pages", lambda _c: [(1, "linha a\n\n\nlinha b")])
        out = hu.pdf_to_markdown(b"ignored")
        assert "linha a\n\nlinha b" in out

    def test_empty_extraction_returns_fallback(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(hu, "_extract_pdf_pages", lambda _c: [])
        assert "sem texto extraível" in hu.pdf_to_markdown(b"ignored")


# ---------------------------------------------------------------------------
# pdf_to_text / pdf_to_markdown — real extraction (no monkeypatching)
# ---------------------------------------------------------------------------

pdfplumber = pytest.importorskip("pdfplumber")

# Minimal valid PDF that contains the text "Hello SEI" encoded as a Type1 font
# stream.  pdfplumber can extract this text natively without OCR.
_MINIMAL_PDF = b"""%PDF-1.4
1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj
2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj
3 0 obj<</Type/Page/MediaBox[0 0 612 792]/Parent 2 0 R/Contents 4 0 R/Resources<</Font<</F1 5 0 R>>>>>>endobj
4 0 obj<</Length 44>>stream
BT /F1 12 Tf 100 700 Td (Hello SEI) Tj ET
endstream
endobj
5 0 obj<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>endobj
xref
0 6
0000000000 65535 f
0000000009 00000 n
0000000058 00000 n
0000000115 00000 n
0000000266 00000 n
0000000360 00000 n
trailer<</Size 6/Root 1 0 R>>
startxref
441
%%EOF"""


class TestRealPdfExtraction:
    """Exercises the real pdfplumber extraction path without any monkeypatching."""

    def test_pdf_to_text_returns_nonempty(self) -> None:
        """pdf_to_text on a valid minimal PDF must return a non-empty string."""
        result = hu.pdf_to_text(_MINIMAL_PDF)
        assert result
        assert "sem texto extraível" not in result

    def test_pdf_to_text_contains_hello(self) -> None:
        """pdfplumber must extract the 'Hello' text embedded in the PDF stream."""
        result = hu.pdf_to_text(_MINIMAL_PDF)
        assert "Hello" in result

    def test_pdf_to_markdown_returns_nonempty(self) -> None:
        """pdf_to_markdown on a valid minimal PDF must return a non-empty string."""
        result = hu.pdf_to_markdown(_MINIMAL_PDF)
        assert result
        assert "sem texto extraível" not in result

    def test_pdf_to_markdown_contains_hello(self) -> None:
        """pdfplumber must also surface the text in the markdown path."""
        result = hu.pdf_to_markdown(_MINIMAL_PDF)
        assert "Hello" in result

    def test_pdf_to_text_empty_bytes_returns_fallback(self) -> None:
        """Empty bytes must return the fallback string, not raise."""
        result = hu.pdf_to_text(b"")
        assert "sem texto extraível" in result

    def test_pdf_to_text_invalid_bytes_returns_fallback(self) -> None:
        """Invalid PDF bytes must return the fallback string, not raise."""
        result = hu.pdf_to_text(b"not a pdf at all")
        assert "sem texto extraível" in result

    def test_pdf_to_markdown_empty_bytes_returns_fallback(self) -> None:
        """Empty bytes must return the fallback string, not raise."""
        result = hu.pdf_to_markdown(b"")
        assert "sem texto extraível" in result


# ---------------------------------------------------------------------------
# sanitize_iso8859
# ---------------------------------------------------------------------------


class TestSanitizeIso8859:
    def test_latin1_chars_pass_through(self) -> None:
        # Accented Portuguese is within ISO-8859-1 — must not be escaped.
        assert hu.sanitize_iso8859("ação coração") == "ação coração"

    def test_non_latin1_becomes_numeric_entity(self) -> None:
        # Em-dash (U+2014) is outside ISO-8859-1.
        assert hu.sanitize_iso8859("a—b") == "a&#8212;b"

    def test_emoji_escaped(self) -> None:
        result = hu.sanitize_iso8859("ok 😀")
        assert "😀" not in result
        assert f"&#{ord('😀')};" in result

    def test_empty_string(self) -> None:
        assert hu.sanitize_iso8859("") == ""

    def test_ascii_unchanged(self) -> None:
        assert hu.sanitize_iso8859("plain ascii 123") == "plain ascii 123"
