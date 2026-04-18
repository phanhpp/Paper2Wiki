import re
import fitz  # pymupdf
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]


def _cleanup_pdf_text(text: str) -> str:
    """
    Normalize common PDF text-extraction artifacts while staying conservative.

    Goals:
    - de-hyphenate linebreaks (e.g. "multi-\\nplicative" -> "multiplicative")
    - remove stray page-number lines
    - join section numbers with following headings (e.g. "3.2\\nAttention" -> "3.2 Attention")
    - trim excessive whitespace while preserving paragraph breaks
    """
    if not text:
        return ""

    # Normalize line endings early.
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    # De-hyphenate words broken across lines.
    text = re.sub(r"(?<=\w)-\n(?=\w)", "", text)

    # Join section numbers with the next heading line when obviously split.
    text = re.sub(r"(?m)^(\d+(?:\.\d+)*)\s*\n\s*([A-Z][^\n]*)$", r"\1 \2", text)

    # Drop lines that are just a page number.
    text = re.sub(r"(?m)^\s*\d+\s*$\n?", "", text)

    # Collapse trailing spaces per line.
    text = re.sub(r"[ \t]+\n", "\n", text)

    # Collapse huge vertical whitespace, but keep paragraph breaks.
    text = re.sub(r"\n{4,}", "\n\n\n", text)

    return text.strip()


def _extract_table_blocks_from_text(text: str) -> list[str]:
    """
    Extract table-like blocks as plain text.

    This does *not* attempt to parse rows/cells (PDFs don't store that reliably).
    Instead, it returns contiguous line blocks that look tabular: lots of spacing
    and/or many numbers across multiple lines.
    """
    if not text:
        return []

    lines = text.splitlines()

    def is_tableish(line: str) -> bool:
        if not line.strip():
            return False
        # Heuristics: multiple "columns" separated by 2+ spaces OR many numeric tokens.
        has_columns = bool(re.search(r"\S\s{2,}\S", line))
        numeric_tokens = len(re.findall(r"(?<![A-Za-z])[0-9]+(?:\.[0-9]+)?", line))
        return has_columns or numeric_tokens >= 4

    blocks: list[str] = []
    buf: list[str] = []
    for line in lines:
        if is_tableish(line):
            buf.append(line.rstrip())
            continue
        if buf:
            if len(buf) >= 3:
                blocks.append("\n".join(buf).strip())
            buf = []
    if buf and len(buf) >= 3:
        blocks.append("\n".join(buf).strip())

    # De-dupe while keeping order (tables can repeat across page headers).
    seen: set[str] = set()
    out: list[str] = []
    for b in blocks:
        key = re.sub(r"\s+", " ", b).strip()
        if key in seen:
            continue
        seen.add(key)
        out.append(b)
    return out


def extract_pdf_images(pdf_path: str, output_dir: str | None = None) -> list[dict]:
    """
    Extract embedded raster images from a PDF and write them to disk.

    Output directory:
    - If `output_dir` is provided, images are saved under that directory.
    - If `output_dir` is None, images are saved under:
      `<repo_root>/raw/assets/<pdf_stem>_images/`

    Returns a list of dicts: {page, xref, path, width, height, ext}.
    Notes:
    - Many paper figures are vector drawings; those won't appear as embedded images.
    - For vector figures, prefer rendering the page (not implemented here).
    """
    pdf_stem = Path(pdf_path).stem
    out_dir = Path(output_dir) if output_dir else (REPO_ROOT / "raw" / "assets" / f"{pdf_stem}_images")
    out_dir.mkdir(parents=True, exist_ok=True)

    doc = fitz.open(pdf_path)
    try:
        extracted: list[dict] = []
        for page_index in range(doc.page_count):
            page = doc.load_page(page_index)
            for img in page.get_images(full=True):
                xref = img[0]
                info = doc.extract_image(xref)
                ext = info.get("ext", "bin")
                data = info.get("image", b"")

                filename = f"page_{page_index + 1:03d}_xref_{xref}.{ext}"
                dest = out_dir / filename
                dest.write_bytes(data)

                extracted.append(
                    {
                        "page": page_index + 1,
                        "xref": xref,
                        "path": str(dest),
                        "width": info.get("width"),
                        "height": info.get("height"),
                        "ext": ext,
                    }
                )
        return extracted
    finally:
        doc.close()


def parse_pdf(path: str, parse_images: bool = False) -> dict:
    """
    Extract plain text from a PDF in a dependable way.

    PDF "structure" (sections/tables) is not reliably recoverable from arbitrary
    PDFs without a layout-aware pipeline, so this function focuses on what it
    can guarantee: per-page text with page boundaries preserved.

    Returns a dict with:
    - title: best-effort (first non-empty line of page 1)
    - abstract: best-effort (regex over extracted text; may be empty)
    - page_count: int
    - body: str (main content, with title/abstract/references removed where detectable)
    - table_blocks: list[str] (table-like blocks as text, best-effort)
    - images: list[dict] (only when parse_images=True). Paths are where the files were saved
      by `extract_pdf_images()` (default: `<repo_root>/raw/assets/<pdf_stem>_images/`).
    """
    doc = fitz.open(path)
    try:
        page_count = doc.page_count
        pages: list[str] = []
        raw_pages: list[str] = []
        for i in range(page_count):
            page = doc.load_page(i)
            # `sort=True` tends to improve reading order for multi-column layouts and
            # positioned glyphs (common in academic PDFs).
            raw = page.get_text("text", sort=True).strip()
            raw_pages.append(raw)
            pages.append(_cleanup_pdf_text(raw))

        # Title: best-effort = first non-empty line on page 0.
        title = ""
        if pages:
            for line in pages[0].splitlines():
                line = line.strip()
                if line:
                    title = line
                    break

        # Full text: keep page boundaries explicit for downstream chunking/citation.
        full_text = "\n\n".join(
            f"=== Page {i + 1} / {page_count} ===\n{txt}" for i, txt in enumerate(pages) if txt
        ).strip()

        # Table-like blocks are extracted from the *raw* page text so spacing is preserved.
        table_blocks: list[str] = []
        for i, raw in enumerate(raw_pages):
            for block in _extract_table_blocks_from_text(raw):
                table_blocks.append(f"=== Page {i + 1} / {page_count} ===\n{block}".strip())

        # Abstract: best-effort. Stop at "Introduction" or a numbered section heading.
        abstract = ""
        m = re.search(
            r"(?is)\babstract\b\s*[:—-]?\s*(.*?)(?=\n\s*(?:\d+\s+)?introduction\b|\n\s*\d+\s|$)",
            full_text,
        )
        if m:
            abstract = re.sub(r"\s+", " ", m.group(1)).strip()

        body = full_text

        # Remove obvious title/header at the very start if we have one.
        if title:
            body = re.sub(rf"(?m)^\s*{re.escape(title)}\s*$\n?", "", body, count=1)

        # Remove Abstract block (best-effort).
        body = re.sub(
            r"(?is)\babstract\b\s*[:—-]?\s*.*?(?=\n\s*(?:\d+\s+)?introduction\b|\n\s*\d+\s|\n\s*=== Page|\Z)",
            "",
            body,
        ).strip()

        # Remove trailing references/bibliography (best-effort).
        body = re.sub(
            r"(?is)\n\s*(references|bibliography|works cited)\s*\n.*\Z",
            "\n",
            body,
        ).strip()

        result: dict = {
            "title": title,
            "abstract": abstract,
            "page_count": page_count,
            "body": body,
            "table_blocks": table_blocks,
        }
        if parse_images:
            result["images"] = extract_pdf_images(path)
        return result
    finally:
        doc.close()
