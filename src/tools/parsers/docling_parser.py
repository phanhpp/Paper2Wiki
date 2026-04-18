import re
from pathlib import Path
from langchain_core.tools import tool

REPO_ROOT = Path(__file__).resolve().parents[3]

# Some arXiv/Google PDFs lead with this paragraph before the paper title.
_BOILERPLATE_LEAD_RE = re.compile(
    r"(?is)\A\s*[^\n]*provided proper attribution[^\n]*\n+",
)


def _strip_leading_pdf_boilerplate(md: str) -> str:
    """Remove leading permission lines so title/body do not start with copyright text."""
    if not md:
        return md
    return _BOILERPLATE_LEAD_RE.sub("", md, count=1)


def _line_is_title_boilerplate(line: str) -> bool:
    s = line.strip()
    if not s:
        return False
    return bool(
        re.search(r"(?is)provided proper attribution|google hereby grants permission", s)
    )


def _strip_references(md: str) -> str:
    """Remove trailing references/bibliography section from markdown."""
    if not md:
        return ""
    return re.sub(
        r"(?is)\n{2,}#{1,6}\s*(references|bibliography|works cited)\s*\n.*\Z",
        "\n",
        md,
    ).strip()


def _extract_md_table_blocks(md: str) -> list[str]:
    """Extract markdown table blocks (Docling outputs GFM-style tables)."""
    if not md:
        return []

    lines = md.splitlines()
    blocks: list[str] = []
    buf: list[str] = []

    for line in lines:
        s = line.strip()
        is_table_line = (
            (s.startswith("|") and s.endswith("|"))
            or bool(re.fullmatch(r"\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?", s))
        )
        if is_table_line:
            buf.append(line.rstrip())
        else:
            if len(buf) >= 2:
                blocks.append("\n".join(buf).strip())
            buf = []
    if len(buf) >= 2:
        blocks.append("\n".join(buf).strip())

    # de-dupe preserving order
    seen, out = set(), []
    for b in blocks:
        key = re.sub(r"\s+", " ", b)
        if key not in seen:
            seen.add(key)
            out.append(b)
    return out


def _fix_display_math_inner_for_katex(inner: str) -> str:
    """
    Docling formula enrichment often emits `&` and `\\\\` as if inside `aligned`, but
    wraps only in `$$...$$`. KaTeX then errors with "Expected 'EOF', got '&'".
    """
    stripped = inner.strip()
    if not stripped or r"\begin{" in stripped:
        return inner
    if "&" not in stripped:
        return inner

    latex_row_break = r"\\"  # two backslashes = LaTeX line break
    if latex_row_break in stripped:
        wrapped = f"\\begin{{aligned}}\n{stripped}\n\\end{{aligned}}"
        lead, trail = inner[: len(inner) - len(inner.lstrip())], inner[len(inner.rstrip()) :]
        return f"{lead}{wrapped}{trail}"

    # Single-line garbage: e.g. "... ) V & & ( 1 )" — equation number pasted with align tabs.
    cleaned = re.sub(r"\s*(?:&\s*)+\(\s*\d+\s*\)\s*$", "", stripped)
    cleaned = re.sub(r"\s*&\s*(?==)", " ", cleaned)
    if "&" in cleaned:
        cleaned = re.sub(r"\s*&\s*", " ", cleaned)

    if cleaned == stripped:
        return inner
    lead, trail = inner[: len(inner) - len(inner.lstrip())], inner[len(inner.rstrip()) :]
    return f"{lead}{cleaned}{trail}"


def _sanitize_display_math_for_katex(md: str) -> str:
    """Rewrite `$$...$$` blocks so standalone alignment tabs are KaTeX-safe."""
    if not md or "$$" not in md:
        return md
    parts: list[str] = []
    pos = 0
    while True:
        start = md.find("$$", pos)
        if start == -1:
            parts.append(md[pos:])
            break
        parts.append(md[pos:start])
        end = md.find("$$", start + 2)
        if end == -1:
            parts.append(md[start:])
            break
        inner = md[start + 2 : end]
        parts.append("$$")
        parts.append(_fix_display_math_inner_for_katex(inner))
        parts.append("$$")
        pos = end + 2
    return "".join(parts)


def _rewrite_markdown_local_images_to_relative(md: str, md_file: Path) -> str:
    """
    Docling emits absolute ``![](/abs/path.png)`` when ``save_as_markdown`` is given an
    absolute path; many Markdown previews show broken images. Rewrite to paths relative
    to the directory containing ``md_file``.
    """
    base = md_file.parent.resolve()

    def repl(m: re.Match[str]) -> str:
        alt, url = m.group(1), m.group(2).strip()
        if url.startswith(("http://", "https://", "data:")):
            return m.group(0)
        p = Path(url)
        if not p.is_absolute():
            return m.group(0)
        try:
            rel = p.resolve().relative_to(base)
        except ValueError:
            return m.group(0)
        return f"![{alt}]({rel.as_posix()})"

    return re.sub(r"!\[([^\]]*)\]\(([^)]+)\)", repl, md)


@tool
def parse_pdf_docling(
    path: str,
    parse_images: bool = True,
    *,
    save_table_images: bool = True,
    images_scale: float = 3.0,
) -> dict:
    """
    Convert a PDF to Markdown using Docling. All outputs go under raw/assets/<slug>/.

    Args:
        path: absolute path to the PDF file (e.g. from fetch_arxiv's pdf_path).
        parse_images: extract figure PNGs alongside the markdown (default True).
        save_table_images: rasterize each detected table as a PNG (default True).
                           Embed only the tables you need — you don't have to use all of them.
        images_scale: upscale factor for rasterized figures/tables (default 3.0).
                      Higher = larger files and slower, but sharper images.

    Returns:
        dict with keys:
          - slug (str): PDF filename stem, used as the directory name under raw/assets/
          - title (str): best-effort title extracted from the first heading
          - page_count (int): total pages in the PDF
          - body_chars (int): character count of main text (abstract + references stripped)
          - table_blocks (int): number of GFM table blocks in the markdown export
          - markdown_path (str): absolute path to raw/assets/<slug>/<slug>.md
          - images_dir (str): absolute path to raw/assets/<slug>/<slug>_artifacts/
          - images (int): number of figure PNGs saved in images_dir
          - tables_dir (str): absolute path to raw/assets/<slug>/tables/
          - table_images (int): number of table PNGs saved in tables_dir
    """
    try:
        from docling.document_converter import DocumentConverter, PdfFormatOption
        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.pipeline_options import PdfPipelineOptions
        from docling_core.types.doc import ImageRefMode, TableItem

        pipeline_options = PdfPipelineOptions()
        pipeline_options.do_formula_enrichment = True
        pipeline_options.do_table_structure = True  # default is True
        if parse_images:
            # Needed so `PictureItem.get_image(doc)` returns a usable PIL image.
            pipeline_options.generate_picture_images = True
        if save_table_images:
            # Raster per detected table so TableItem.get_image(doc) returns a crop.
            pipeline_options.generate_table_images = True
        if (parse_images or save_table_images) and float(images_scale) != 1.0:
            pipeline_options.images_scale = float(images_scale)

        converter = DocumentConverter(
            format_options={
                InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options),
            }
        )
    except ImportError as e:
        raise ImportError(
            "docling is not installed. Install with `uv add docling`."
        ) from e

    pdf_path = Path(path)
    slug = pdf_path.stem
    slug_dir = REPO_ROOT / "raw" / "assets" / slug
    tables_dir = slug_dir / "tables"
    if parse_images or save_table_images:
        slug_dir.mkdir(parents=True, exist_ok=True)
    if save_table_images:
        tables_dir.mkdir(parents=True, exist_ok=True)

    # ``save_as_markdown(REFERENCED)`` writes ``<slug>_artifacts/*.png`` next to
    # ``<slug>.md`` under ``raw/assets/<slug>/``. Rewrite absolute ``![](...)`` URLs
    # to paths relative to that directory for Markdown previews.

    result = converter.convert(str(pdf_path))
    doc = result.document

    markdown_path: Path | None = None
    if parse_images:
        markdown_path = slug_dir / f"{slug}.md"
        doc.save_as_markdown(
            str(markdown_path.resolve()),
            image_mode=ImageRefMode.REFERENCED,
        )
        md = markdown_path.read_text(encoding="utf-8")
        md = _rewrite_markdown_local_images_to_relative(md, markdown_path)
    else:
        md = doc.export_to_markdown()

    md = _sanitize_display_math_for_katex(md)
    md = _strip_leading_pdf_boilerplate(md)
    if parse_images and markdown_path is not None:
        # Keep on-disk export aligned with post-processed math / boilerplate stripping.
        markdown_path.write_text(md, encoding="utf-8")

    # Page count
    page_count = len(doc.pages) if hasattr(doc, "pages") else None

    # Title: first markdown heading (Docling often uses ## for the paper name), else
    # first non-boilerplate non-empty line.
    title = ""
    m_title = re.search(r"(?m)^#{1,6}\s+(.+?)\s*$", md)
    if m_title:
        title = m_title.group(1).strip()
    else:
        for line in md.splitlines():
            if not line.strip() or _line_is_title_boilerplate(line):
                continue
            title = line.strip().lstrip("#").strip()
            break

    # Strip abstract + references only to compute body_chars (not returned as text).
    body = re.sub(
        r"(?is)\babstract\b\s*\n+.*?(?=\n{2,}#{1,6}\s+introduction\b|\n{2,}#{1,6}\s+\d+\b|\Z)",
        "",
        md,
    ).strip()
    body = _strip_references(body)
    body_chars = len(body)

    table_block_list = _extract_md_table_blocks(md)
    table_block_count = len(table_block_list)

    result_dict: dict = {
        "slug": slug,
        "title": title,
        "page_count": page_count,
        "body_chars": body_chars,
        "table_blocks": table_block_count,
    }

    if parse_images and markdown_path is not None:
        artifacts_dir = markdown_path.parent / f"{markdown_path.stem}_artifacts"
        image_count = len(list(artifacts_dir.glob("*.png")))
        result_dict["markdown_path"] = str(markdown_path.resolve())
        result_dict["images_dir"] = str(artifacts_dir.resolve())
        result_dict["images"] = image_count

    if save_table_images:
        table_image_count = 0
        for item in doc.iterate_items():
            element, _ = item if isinstance(item, tuple) else (item, None)
            if not isinstance(element, TableItem):
                continue
            img_path = tables_dir / f"table_{table_image_count:03d}.png"
            try:
                pil_img = element.get_image(doc)
                if pil_img:
                    pil_img.save(img_path)
                    table_image_count += 1
            except Exception:
                continue
        result_dict["tables_dir"] = str(tables_dir.resolve())
        result_dict["table_images"] = table_image_count

    return result_dict


def _cli_main(argv: list[str] | None = None) -> None:
    """CLI: ``python src/tools/parsers/docling_parser.py <pdf>`` from repo root."""
    import argparse
    import json

    p = argparse.ArgumentParser(
        description="Run Paper2Wiki Docling wrapper (parse_pdf_docling): "
        "referenced images + KaTeX-safe math + optional table PNGs.",
    )
    p.add_argument("pdf", help="Path to a PDF (cwd-relative or absolute)")
    p.add_argument(
        "--no-images",
        action="store_true",
        help="Do not run save_as_markdown / figure export",
    )
    p.add_argument(
        "--no-table-images",
        action="store_true",
        help="Do not save rasterized table PNGs",
    )
    p.add_argument(
        "--images-scale",
        type=float,
        default=None,
        metavar="N",
        help="Docling images_scale (default: parser default, often 3.0)",
    )
    p.add_argument(
        "--json",
        action="store_true",
        help="Print full result dict as JSON (large)",
    )
    ns = p.parse_args(argv)

    kw: dict = {
        "path": ns.pdf,
        "parse_images": not ns.no_images,
        "save_table_images": not ns.no_table_images,
    }
    if ns.images_scale is not None:
        kw["images_scale"] = ns.images_scale

    out = parse_pdf_docling(**kw)

    if ns.json:
        print(json.dumps(out, indent=2, ensure_ascii=False))
        return

    print("title:", out.get("title", ""))
    print("page_count:", out.get("page_count"))
    print("markdown_path:", out.get("markdown_path"))
    print("images_dir:", out.get("images_dir"))
    print("images:", out.get("images"))
    print("table_images:", out.get("table_images"))
    print("tables_dir:", out.get("tables_dir"))
    print("table_blocks:", out.get("table_blocks"))
    print("body_chars:", out.get("body_chars"))


if __name__ == "__main__":
    import sys

    _cli_main(sys.argv[1:])