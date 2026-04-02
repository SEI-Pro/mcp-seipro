"""Utilitários para manipulação de HTML do SEI."""

import html as html_module
import os
import re

from bs4 import BeautifulSoup
from markdownify import markdownify


def html_to_text(raw: str) -> str:
    """Extrai texto limpo de conteúdo HTML do SEI.

    A API do SEI (mod-wssei) retorna documentos internos como HTML
    HTML-escaped (&lt; ao invés de <). Esta função faz unescape,
    remove CSS/scripts, e retorna texto legível.
    """
    try:
        # A API retorna HTML-escaped — decodificar primeiro
        html = html_module.unescape(raw)

        # Remover blocos <style> e <script> antes do parse
        cleaned = re.sub(r"<style[^>]*>.*?</style>", "", html, flags=re.DOTALL | re.IGNORECASE)
        cleaned = re.sub(r"<script[^>]*>.*?</script>", "", cleaned, flags=re.DOTALL | re.IGNORECASE)

        soup = BeautifulSoup(cleaned, "html.parser")

        # Remover head inteiro (meta tags, etc.)
        if soup.head:
            soup.head.decompose()

        # Extrair texto do body (ou do documento inteiro se não houver body)
        target = soup.body if soup.body else soup
        text = target.get_text(separator="\n", strip=True)

        # Limpar linhas vazias e espaços excessivos
        lines = [line.strip() for line in text.split("\n") if line.strip()]
        return "\n".join(lines)
    except Exception:
        # Fallback: regex brutal
        text = html_module.unescape(raw)
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        return text[:10000]


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
            inner = cells[1:-1] if len(cells) > 2 else cells
            filled = [c for c in inner if c.strip()]

            if not filled:
                # Linha inteira vazia — pular
                i += 1
                continue

            # Verificar se é linha de separador (| --- | --- |)
            if all(re.match(r"\s*-+\s*$", c) for c in inner if c.strip()):
                # Ajustar separador para o número de colunas preenchidas da próxima/anterior tabela
                # Buscar a linha de dados mais próxima para contar colunas
                ncols = len(filled)
                if ncols == 0:
                    # Contar da linha anterior
                    for prev in reversed(result):
                        if "|" in prev and prev.strip().startswith("|"):
                            prev_cells = [c for c in prev.split("|")[1:-1] if c.strip()]
                            ncols = max(len(prev_cells), 1)
                            break
                    else:
                        ncols = 1
                result.append("| " + " | ".join(["---"] * ncols) + " |")
            else:
                result.append("| " + " | ".join(c.strip() for c in filled) + " |")
        else:
            result.append(line)
        i += 1
    return "\n".join(result)


def html_to_markdown(raw: str) -> str:
    """Converte HTML do SEI para Markdown formatado.

    Preserva negrito, tabelas, links e linhas horizontais.
    Remove colunas vazias de tabelas de layout do SEI.
    Ideal para exibição em chat/terminal com suporte a Markdown.
    """
    try:
        html = html_module.unescape(raw)
        cleaned = re.sub(r"<style[^>]*>.*?</style>", "", html, flags=re.DOTALL | re.IGNORECASE)
        cleaned = re.sub(r"<script[^>]*>.*?</script>", "", cleaned, flags=re.DOTALL | re.IGNORECASE)
        resultado = markdownify(
            cleaned,
            heading_style="ATX",
            strip=["img", "link", "meta", "head", "title"],
        )
        resultado = _clean_markdown_tables(resultado)
        resultado = re.sub(r"\n{3,}", "\n\n", resultado).strip()
        return resultado
    except Exception:
        return html_to_text(raw)


MAX_OCR_PAGES = 20  # Limite de páginas para OCR (evitar timeout)
OCR_LANG = os.environ.get("SEI_OCR_LANG", "por")


def _ocr_pdf(content: bytes, lang: str = "") -> list[tuple[int, str]]:
    """Extrai texto de PDF via OCR (pdf2image + tesseract).

    Retorna lista de (num_pagina, texto).
    Limita a MAX_OCR_PAGES páginas para evitar timeout.
    """
    import io
    from pdf2image import convert_from_bytes
    import pytesseract

    lang = lang or OCR_LANG
    images = convert_from_bytes(content, dpi=200)
    pages = []
    limit = min(len(images), MAX_OCR_PAGES)
    for i, img in enumerate(images[:limit], 1):
        text = pytesseract.image_to_string(img, lang=lang)
        if text and text.strip():
            pages.append((i, text.strip()))
    if len(images) > MAX_OCR_PAGES:
        pages.append((
            MAX_OCR_PAGES + 1,
            f"[OCR limitado a {MAX_OCR_PAGES} páginas. "
            f"O documento tem {len(images)} páginas no total. "
            f"Use sei_baixar_anexo para obter o PDF completo.]",
        ))
    return pages


def _extract_pdf_pages(content: bytes) -> list[tuple[int, str]]:
    """Extrai texto de PDF, usando pdfplumber primeiro e OCR como fallback.

    Retorna lista de (num_pagina, texto).
    """
    import io
    import pdfplumber

    pages = []
    total = 0
    with pdfplumber.open(io.BytesIO(content)) as pdf:
        total = len(pdf.pages)
        for i, page in enumerate(pdf.pages, 1):
            text = page.extract_text()
            if text and text.strip():
                pages.append((i, text.strip()))

    if pages:
        return pages

    # Fallback: OCR para PDFs de imagem
    try:
        return _ocr_pdf(content)
    except Exception:
        return []


def pdf_to_text(content: bytes) -> str:
    """Extrai texto de um PDF binário.

    Usa pdfplumber para PDFs com texto nativo, e OCR (tesseract)
    como fallback para PDFs escaneados.
    """
    pages = _extract_pdf_pages(content)
    if not pages:
        return "[PDF sem texto extraível — nem texto nativo nem OCR disponível]"
    total = max(p[0] for p in pages)
    return "\n\n".join(
        f"--- Página {num}/{total} ---\n{text}" for num, text in pages
    )


def pdf_to_markdown(content: bytes) -> str:
    """Extrai texto de um PDF e formata como Markdown.

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
            if stripped.isupper() and len(stripped) < 100:
                lines.append(f"**{stripped}**")
            else:
                lines.append(stripped)
        if lines:
            result.append(f"---\n**Página {num}/{total}**\n\n" + "\n\n".join(lines))

    return "\n\n".join(result)


def sanitize_iso8859(text: str) -> str:
    """Converte caracteres fora do ISO-8859-1 para entidades HTML numéricas.

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
