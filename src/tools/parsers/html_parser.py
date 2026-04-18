import re
import requests
from html.parser import HTMLParser
from pathlib import Path

from src.tools.parsers.pymu_parser import parse_pdf


class _HTMLToMarkdown(HTMLParser):
    """Minimal HTML → Markdown, no extra deps."""

    _SKIP = {"script", "style", "nav", "footer", "header", "aside", "form", "button", "iframe"}
    _BLOCK = {"p", "div", "article", "section", "main"}

    def __init__(self):
        super().__init__()
        self.parts: list[str] = []
        self._skip_depth = 0
        self.images: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in self._SKIP:
            self._skip_depth += 1
            return
        attrs_dict = dict(attrs)
        if tag == "img" and "src" in attrs_dict:
            self.images.append(attrs_dict["src"])
        if tag in ("h1", "h2", "h3", "h4"):
            self.parts.append("\n" + "#" * int(tag[1]) + " ")
        elif tag == "li":
            self.parts.append("\n- ")
        elif tag == "br":
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self._SKIP:
            self._skip_depth = max(0, self._skip_depth - 1)
        if tag in self._BLOCK | {"h1", "h2", "h3", "h4", "li", "ul", "ol"}:
            self.parts.append("\n")

    def handle_data(self, data):
        if self._skip_depth:
            return
        text = data.strip()
        if text:
            self.parts.append(text + " ")

    def markdown(self) -> str:
        return re.sub(r'\n{3,}', '\n\n', "".join(self.parts)).strip()


REPO_ROOT = Path(__file__).resolve().parents[3]


def _extract_mainish_html(html: str) -> str:
    """
    Best-effort "main content" extraction with no extra deps.

    Prefer <main> or <article> when present; otherwise fall back to <body>.
    This won't be perfect, but avoids converting entire nav-heavy pages.
    """
    if not html:
        return ""

    # Prefer explicit main/article blocks.
    for tag in ("main", "article"):
        m = re.search(rf"(?is)<{tag}\b[^>]*>(.*?)</{tag}>", html)
        if m and m.group(1).strip():
            return m.group(0)

    # Fall back to body.
    m = re.search(r"(?is)<body\b[^>]*>(.*?)</body>", html)
    if m and m.group(1).strip():
        return m.group(0)

    return html


def _is_arxiv_abs_url(url: str) -> re.Match | None:
    # Matches:
    # - https://arxiv.org/abs/1706.03762
    # - https://arxiv.org/abs/1706.03762v7
    return re.search(r"arxiv\.org/abs/([0-9]+\.[0-9]+(?:v[0-9]+)?)", url)


def _download_to(path: Path, url: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    resp = requests.get(url, timeout=60, stream=True, headers={"User-Agent": "Mozilla/5.0 (Paper2Wiki)"})
    resp.raise_for_status()
    with path.open("wb") as f:
        for chunk in resp.iter_content(chunk_size=1024 * 128):
            if chunk:
                f.write(chunk)


def fetch_web_article(url: str) -> dict:
    """Fetch web article and convert to markdown (Obsidian Web Clipper equivalent).
    Returns: {title, content_md, images: [urls]}"""
    # arXiv "abs" pages are landing pages (metadata), not the paper body.
    # Special-case: fetch the PDF and parse it instead.
    m_arxiv = _is_arxiv_abs_url(url)
    if m_arxiv:
        arxiv_id = m_arxiv.group(1)
        pdf_url = f"https://arxiv.org/pdf/{arxiv_id}.pdf"
        pdf_path = REPO_ROOT / "raw" / "papers" / f"arxiv_{arxiv_id}.pdf"
        if not pdf_path.exists():
            _download_to(pdf_path, pdf_url)
        parsed = parse_pdf(str(pdf_path), parse_images=False)
        title = parsed.get("title") or f"arXiv:{arxiv_id}"
        # Keep key name `content_md` for downstream compatibility, even though this is plain text.
        return {"title": title, "content_md": parsed.get("body", ""), "images": []}

    resp = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0 (Paper2Wiki)"})
    resp.raise_for_status()

    title_match = re.search(r'<title[^>]*>(.*?)</title>', resp.text, re.IGNORECASE | re.DOTALL)
    title = re.sub(r'\s+', ' ', title_match.group(1)).strip() if title_match else url

    parser = _HTMLToMarkdown()
    parser.feed(_extract_mainish_html(resp.text))

    return {
        "title": title,
        "content_md": parser.markdown(),
        "images": parser.images,
    }
