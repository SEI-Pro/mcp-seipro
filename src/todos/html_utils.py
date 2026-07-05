"""Utilitários para manipulação de HTML do SEI."""

import html as html_module
import io
import logging
import re
from types import ModuleType
from typing import Any
from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup, Tag
from markdownify import MarkdownConverter

from todos.settings import get_settings

# Optional OCR deps — pre-declared so that except-block None/fallback assignments
# are always valid (ty checks that assigned types match the declared annotation).
_pytesseract: ModuleType | None = None
_convert_from_bytes: Any = None  # pdf2image.convert_from_bytes callable
PDFInfoNotInstalledError: type[BaseException] = OSError
PDFPageCountError: type[BaseException] = OSError
_HAS_OCR = False

try:
    import pytesseract as _pytesseract
    from pdf2image import convert_from_bytes as _convert_from_bytes
    from pdf2image.exceptions import PDFInfoNotInstalledError, PDFPageCountError

    _HAS_OCR = True
except ImportError:
    logging.getLogger(__name__).debug("pytesseract/pdf2image não disponíveis — OCR desabilitado")

# Optional PDF text-extraction dep
_pdfplumber: ModuleType | None = None
PdfminerException: type[BaseException] = ValueError
_HAS_PDFPLUMBER = False

try:
    import pdfplumber as _pdfplumber
    from pdfplumber.utils.exceptions import PdfminerException

    _HAS_PDFPLUMBER = True
except ImportError:
    logging.getLogger(__name__).debug(
        "pdfplumber não disponível — extração de texto PDF desabilitada"
    )

logger = logging.getLogger(__name__)

# Número máximo de caracteres retornados pelo fallback regex de html_to_text.
_FALLBACK_TEXT_LIMIT: int = 10_000

# Tamanho máximo de HTML aceito para parse completo (50 MB).
# Respostas maiores são truncadas antes de entrar no BeautifulSoup para evitar DoS.
_MAX_HTML_PARSE_BYTES: int = 50 * 1024 * 1024

# Comprimento máximo de uma linha para ser formatada como título em negrito no PDF.
_HEADER_MAX_LEN: int = 100

# DPI usado pelo pdf2image ao rasterizar páginas para OCR.
_OCR_DPI: int = 200

# Valor máximo aceito para o atributo HTML colspan (evita alocações excessivas).
_COLSPAN_MAX: int = 1_000

# Número mínimo de partes após split("|") para que a linha de tabela tenha delimitadores externos.
# Uma linha "|a|b|" gera ["", "a", "b", ""] — 4 partes (> 2 indica delimitadores presentes).
_TABLE_OUTER_DELIM_MIN: int = 2


def html_to_text(raw: str) -> str:
    """Extrai texto limpo de conteúdo HTML do SEI.

    A API do SEI (mod-wssei) retorna documentos internos como HTML
    HTML-escaped (&lt; ao invés de <). Esta função faz unescape,
    remove CSS/scripts, e retorna texto legível.
    """
    try:
        # A API retorna HTML-escaped — decodificar primeiro
        html = html_module.unescape(raw)

        if len(html) > _MAX_HTML_PARSE_BYTES:
            logger.warning(
                "HTML muito grande (%d bytes); truncando para %d antes de parsear",
                len(html),
                _MAX_HTML_PARSE_BYTES,
            )
            html = html[:_MAX_HTML_PARSE_BYTES]

        # Remover blocos <style> e <script> antes do parse
        cleaned = re.sub(r"<style[^>]*>.*?</style>", "", html, flags=re.DOTALL | re.IGNORECASE)
        cleaned = re.sub(r"<script[^>]*>.*?</script>", "", cleaned, flags=re.DOTALL | re.IGNORECASE)

        soup = BeautifulSoup(cleaned, "html.parser")

        # Remover head inteiro (meta tags, etc.)
        if soup.head:
            soup.head.decompose()

        # Extrair texto do body (ou do documento inteiro se não houver body)
        target = soup.body or soup
        text = target.get_text(separator="\n", strip=True)

        # Limpar linhas vazias e espaços excessivos
        lines = [line.strip() for line in text.split("\n") if line.strip()]
        return "\n".join(lines)
    except (TypeError, ValueError, UnicodeDecodeError) as _html_err:
        logger.warning("Falha ao converter HTML (fallback regex): %s", _html_err)
        # Fallback: regex brutal
        text = html_module.unescape(raw)
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        if len(text) > _FALLBACK_TEXT_LIMIT:
            logger.warning(
                "Texto truncado de %d para %d caracteres após fallback",
                len(text),
                _FALLBACK_TEXT_LIMIT,
            )
        return text[:_FALLBACK_TEXT_LIMIT]


def _clean_markdown_tables(md: str) -> str:
    """Remove colunas vazias de tabelas markdown.

    O SEI usa tabelas de 13 colunas para layout, gerando:
    | conteúdo | | | | | | | | | | | | |
    Esta função compacta para:
    | conteúdo |
    """
    lines = md.split("\n")
    result = []
    i = 0
    while i < len(lines):
        line = lines[i]
        # Detecta linha de tabela markdown
        if "|" in line and line.strip().startswith("|"):
            # Extrair células e filtrar vazias
            cells = line.split("|")
            # Manter delimitadores externos (primeiro e último são vazios por causa do split)
            inner = cells[1:-1] if len(cells) > _TABLE_OUTER_DELIM_MIN else cells
            filled = [c for c in inner if c.strip()]

            if not filled:
                # Linha inteira vazia — pular
                i += 1
                continue

            # Verificar se é linha de separador (| --- | --- | ou | ---: | :---: |)
            if all(re.match(r"\s*:?-+:?\s*$", c) for c in inner if c.strip()):
                # Preservar separadores de alinhamento (com ':') das colunas preenchidas
                filled_seps = [c.strip() for c in inner if c.strip()]
                result.append("| " + " | ".join(filled_seps) + " |")
            else:
                result.append("| " + " | ".join(c.strip() for c in filled) + " |")
        else:
            result.append(line)
        i += 1
    return "\n".join(result)


def _align_to_separator(align: str | None) -> str:
    """Return the Markdown column separator for an HTML align attribute value."""
    if align == "right":
        return "---:"
    if align == "center":
        return ":---:"
    # left or unspecified → left-align (default)
    return "---"


class _SEIMarkdownConverter(MarkdownConverter):
    """MarkdownConverter subclass that respects HTML 'align' attributes on table cells.

    When generating the header separator row, reads the ``align`` attribute from
    each ``<th>`` (or ``<td>`` when no ``<th>`` is present) and emits ``---``,
    ``---:`` or ``:---:`` accordingly. Falls back to left-align when the
    attribute is absent, instead of varying by content heuristics.
    """

    def convert_tr(self, el: object, text: str, parent_tags: object) -> str:
        """Convert a table row, honouring per-cell HTML align attributes."""
        del parent_tags  # unused; name is dictated by markdownify's calling convention
        cells = el.find_all(["td", "th"]) if isinstance(el, Tag) else []
        is_first_row = isinstance(el, Tag) and el.find_previous_sibling() is None
        parent = el.parent if isinstance(el, Tag) else None
        is_tag_parent = isinstance(parent, Tag)
        is_headrow = isinstance(el, Tag) and (
            (cells and all(cell.name == "th" for cell in cells))
            or (is_tag_parent and parent.name == "thead" and len(parent.find_all("tr")) == 1)
        )
        grandparent = parent.parent if is_tag_parent else None
        is_tag_grandparent = isinstance(grandparent, Tag)
        is_head_row_missing = (
            isinstance(el, Tag)
            and is_tag_parent
            and (
                (is_first_row and parent.name != "tbody")
                or (
                    is_first_row
                    and parent.name == "tbody"
                    and is_tag_grandparent
                    and len(grandparent.find_all(["thead"])) < 1
                )
            )
        )

        overline = ""
        underline = ""
        full_colspan = 0
        col_alignments: list[str] = []

        for cell in cells:
            colspan = 1
            if "colspan" in cell.attrs and str(cell["colspan"]).isdigit():
                colspan = max(1, min(_COLSPAN_MAX, int(str(cell["colspan"]))))
            full_colspan += colspan
            # Read align from HTML attribute first; fall back to left-align when absent.
            raw_align = cell.get("align")
            align_val = str(raw_align).lower().strip() if raw_align else None
            col_alignments.extend([_align_to_separator(align_val)] * colspan)

        separator_row = "| " + " | ".join(col_alignments) + " |"
        opts: dict[str, object] = getattr(self, "options", {})

        if (
            is_headrow or (is_head_row_missing and opts.get("table_infer_header", True))
        ) and is_first_row:
            underline += separator_row + "\n"
        elif (is_head_row_missing and not opts.get("table_infer_header", True)) or (
            is_first_row
            and isinstance(el, Tag)
            and is_tag_parent
            and (
                parent.name == "table"
                or (parent.name == "tbody" and not parent.find_previous_sibling())
            )
        ):
            overline += "| " + " | ".join([""] * full_colspan) + " |\n"
            overline += separator_row + "\n"

        return overline + "|" + text + "\n" + underline


def html_to_markdown(raw: str) -> str:
    """Convert SEI HTML to formatted Markdown.

    Preserva negrito, tabelas, links e linhas horizontais.
    Remove colunas vazias de tabelas de layout do SEI.
    Ideal para exibição em chat/terminal com suporte a Markdown.
    """
    try:
        html = html_module.unescape(raw)
        if len(html) > _MAX_HTML_PARSE_BYTES:
            logger.warning(
                "HTML muito grande (%d bytes); truncando para %d antes de converter para Markdown",
                len(html),
                _MAX_HTML_PARSE_BYTES,
            )
            html = html[:_MAX_HTML_PARSE_BYTES]
        cleaned = re.sub(r"<style[^>]*>.*?</style>", "", html, flags=re.DOTALL | re.IGNORECASE)
        cleaned = re.sub(r"<script[^>]*>.*?</script>", "", cleaned, flags=re.DOTALL | re.IGNORECASE)
        resultado = _SEIMarkdownConverter(
            heading_style="ATX",
            strip=["img", "link", "meta", "head", "title"],
        ).convert(cleaned)
        resultado = _clean_markdown_tables(resultado)
        return re.sub(r"\n{3,}", "\n\n", resultado).strip()
    except (AttributeError, TypeError, ValueError, UnicodeDecodeError) as _md_err:
        logger.warning("Falha ao converter HTML para Markdown (fallback texto): %s", _md_err)
        return html_to_text(raw)


def _ocr_pdf(content: bytes, lang: str = "") -> list[tuple[int, str]]:
    """Extract text from a PDF via OCR (pdf2image + tesseract).

    Returns list of (page_number, text) tuples.
    Processing is limited to SEI_MAX_OCR_PAGES pages to avoid timeouts.
    When OCR fails on an individual page, logs a WARNING and skips the page.
    """
    if not _HAS_OCR or _convert_from_bytes is None or _pytesseract is None:
        _msg = "OCR requer pytesseract e pdf2image instalados"
        raise ImportError(_msg)
    settings = get_settings()
    max_ocr_pages = settings.sei_max_ocr_pages
    lang = lang or settings.sei_ocr_lang
    try:
        images = _convert_from_bytes(content, dpi=_OCR_DPI)
    except (PDFInfoNotInstalledError, PDFPageCountError) as e:
        msg = f"poppler não instalado ou PDF inválido: {e}"
        raise OSError(msg) from e
    pages = []
    limit = min(len(images), max_ocr_pages)
    _tesseract_errors = (
        _pytesseract.TesseractError,
        _pytesseract.TesseractNotFoundError,
        OSError,
    )
    for i, img in enumerate(images[:limit], 1):
        try:
            text = _pytesseract.image_to_string(img, lang=lang)
        except _tesseract_errors as _ocr_page_err:
            logger.warning(
                "OCR falhou na página %d: %s",
                i,
                _ocr_page_err,
            )
            continue
        if text and text.strip():
            pages.append((i, text.strip()))
    if len(images) > max_ocr_pages:
        pages.append(
            (
                max_ocr_pages + 1,
                f"[OCR limitado a {max_ocr_pages} páginas. "
                f"O documento tem {len(images)} páginas no total. "
                f"Use sei_baixar_anexo para obter o PDF completo.]",
            )
        )
    return pages


def _extract_pdf_pages(content: bytes) -> list[tuple[int, str]]:
    """Extract text from a PDF, using pdfplumber first and OCR as fallback.

    Returns list of (page_number, text) tuples.
    Returns an empty list if the content is not a valid PDF or is empty.
    """
    pages = []
    if _HAS_PDFPLUMBER and _pdfplumber is not None:
        try:
            with _pdfplumber.open(io.BytesIO(content)) as pdf:
                for i, page in enumerate(pdf.pages, 1):
                    text = page.extract_text()
                    if text and text.strip():
                        pages.append((i, text.strip()))
        except (PdfminerException, ValueError, OSError) as e:
            logger.warning(
                "pdfplumber falhou (%s: %s) — tentando OCR",
                type(e).__name__,
                e,
            )

    if pages:
        return pages

    # Fallback: OCR para PDFs de imagem
    try:
        return _ocr_pdf(content)
    except (ImportError, OSError, RuntimeError, ValueError) as _ocr_err:
        logger.warning(
            "OCR falhou (%s: %s) — PDF retornado como sem texto",
            type(_ocr_err).__name__,
            _ocr_err,
            exc_info=True,
        )
        return []


def pdf_to_text(content: bytes) -> str:
    """Extract text from a binary PDF.

    Usa pdfplumber para PDFs com texto nativo, e OCR (tesseract)
    como fallback para PDFs escaneados.
    """
    pages = _extract_pdf_pages(content)
    if not pages:
        return "[PDF sem texto extraível — nem texto nativo nem OCR disponível]"
    total = max(p[0] for p in pages)
    return "\n\n".join(f"--- Página {num}/{total} ---\n{text}" for num, text in pages)


def pdf_to_markdown(content: bytes) -> str:
    """Extract text from a binary PDF and format as Markdown.

    Usa pdfplumber para PDFs com texto nativo, e OCR (tesseract)
    como fallback para PDFs escaneados. Formata títulos em negrito.
    """
    pages = _extract_pdf_pages(content)
    if not pages:
        return "[PDF sem texto extraível — nem texto nativo nem OCR disponível]"

    total = max(p[0] for p in pages)
    result = []
    for num, text in pages:
        lines = []
        for line in text.split("\n"):
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.isupper() and len(stripped) < _HEADER_MAX_LEN:
                lines.append(f"**{stripped}**")
            else:
                lines.append(stripped)
        if lines:
            result.append(f"---\n**Página {num}/{total}**\n\n" + "\n\n".join(lines))

    return "\n\n".join(result)


def sanitize_iso8859(text: str) -> str:
    """Convert characters outside ISO-8859-1 to numeric HTML entities.

    Necessário porque o WSSEI faz iconv UTF-8 -> ISO-8859-1 e retorna vazio
    se encontrar caracteres incompatíveis.
    """
    result = []
    for char in text:
        try:
            char.encode("iso-8859-1")
            result.append(char)
        except UnicodeEncodeError:
            result.append(f"&#{ord(char)};")
    return "".join(result)


_LOGIN_PAGE_PEEK_BYTES = 8192  # login forms sit near the top; no need to decode a whole PDF/ZIP


def is_login_page(body: str) -> bool:
    """Detect whether *body* is the SEI login page instead of the page requested.

    A dead SIP session doesn't fail with 401/403 — the server answers HTTP
    200 with the login form's HTML. Status-code checks can't catch this, so
    every multi-step scrape must inspect the body for this marker to tell
    "session expired mid-flow" apart from "a normal parse failure". Lives
    here (not in `sei_web_client.py`) so that `browser_capture.py` — which
    needs the same marker for a Playwright-rendered page — can import it
    without creating a circular import between the two modules.
    """
    return 'name="txtUsuario"' in body or 'id="txtUsuario"' in body


def peek_is_login_page(content: bytes) -> bool:
    """Like ``is_login_page``, but only decodes a bounded byte prefix.

    Used where decoding the full body would be wasteful (e.g. a large PDF/ZIP
    download) — the login form's marker always sits near the top when present.
    """
    peek = content[:_LOGIN_PAGE_PEEK_BYTES].decode("utf-8", errors="replace")
    return is_login_page(peek)


# A request method alone is not a safety classification: SEI uses GET for
# several state-changing actions (e.g. `linkReabrirProcesso`). Any code path
# that navigates to a caller-supplied SEI URL — the declarative action-plan
# executor (`sei_action_plans.py`) and the Playwright screenshot tool
# (`browser_capture.py`) alike — must restrict itself to known read routes
# before navigating. Lives here (not in either of those modules) so both can
# import it without a circular import between them.
READ_ACTIONS = frozenset(
    {
        "arvore_montar",
        "arvore_visualizar",
        "documento_consultar",
        "documento_visualizar",
        "editor_montar",
        "documento_escolher_tipo",
        "procedimento_consultar",
        "procedimento_consultar_historico",
        "procedimento_sobrestado_listar",
        "andamento_marcador_gerenciar",
        "acompanhamento_gerenciar",
        "rel_bloco_protocolo_listar",
        "bloco_assinatura_listar",
        "bloco_interno_listar",
    }
)
READ_SUFFIXES = ("_listar", "_consultar", "_visualizar", "_montar", "_gerenciar")
DESTRUCTIVE_MARKERS = (
    "excluir",
    "remover",
    "retirar",
    "cancelar",
    "concluir",
    "sobrestar",
)
WRITE_MARKERS = (
    "salvar",
    "cadastrar",
    "alterar",
    "assinar",
    "enviar",
    "tramitar",
    "reabrir",
    "registrar",
    "disponibilizar",
    "atribuir",
    "marcar",
)


def action_name(url: str) -> str:
    """Extract the SEI controller's ``acao`` query parameter from *url*."""
    return parse_qs(urlparse(url).query).get("acao", [""])[0]


def risk_of_action(action: str) -> str:
    """Classify a controller action conservatively: read / write / destructive."""
    folded = action.casefold()
    if not folded:
        return "write"
    if any(marker in folded for marker in DESTRUCTIVE_MARKERS):
        return "destructive"
    if any(marker in folded for marker in WRITE_MARKERS):
        return "write"
    if folded in READ_ACTIONS or folded.endswith(READ_SUFFIXES):
        return "read"
    return "write"


def is_read_action(action: str) -> bool:
    """Return True if *action* is conservatively classified as read-only."""
    return risk_of_action(action) == "read"


def redact_signed_capabilities(text: str) -> str:
    r"""Redact common SEI capabilities (infra_hash/tokens) from *text*.

    ``hdnToken`` is matched with a trailing ``\w*`` because the real field
    name carries a dynamic per-page hash suffix (``hdnToken<hash>`` — see
    CLAUDE.md "O token CSRF é dinâmico"), not the literal string ``hdnToken``.
    An exact-match pattern would silently let every real occurrence through.

    Lives here (not in `sei_action_plans.py`, where this redaction was first
    introduced) so `sei_web_client._check` can apply the same redaction to
    every error message it raises — not just the two call sites in
    `sei_action_plans.py` — since `httpx.HTTPStatusError`'s default message
    embeds the full signed request URL and `_check` would otherwise leak it
    on any transient 4xx/5xx across the entire scraper.
    """
    redacted = re.sub(
        r"(?i)([?&](?:infra_hash|hdnToken\w*|token|csrf(?:_token)?)=)[^&#'\"\s]+",
        r"\1<redacted>",
        text,
    )
    return re.sub(
        r'(?i)(name=["\'](?:hdnToken\w*|token|csrf(?:_token)?)["\'][^>]*value=["\'])[^"\']*',
        r"\1<redacted>",
        redacted,
    )
