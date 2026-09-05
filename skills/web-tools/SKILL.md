---
name: web-tools
description: "Web search and extraction for Any2Wiki. Covers provider selection, call patterns, and response handling for Firecrawl, Tavily, and Exa."
version: 0
author: phanhpp
license: MIT
metadata:
  Any2Wiki:
    tags: [web, search, extract, firecrawl, tavily, exa, ingest]
    category: ingest
---

# Web Tools

Thin wrapper over three web providers — Firecrawl, Tavily, Exa — with security gating, config-driven routing, and optional LLM summarization. Public API lives in `src/tools/web_tools/__init__.py`.

## Public API

```python
from src.tools.web_tools import web_search, web_extract

# Search — sync, returns metadata only
results = web_search("attention is all you need", limit=5)
# → list[SearchResult(title, url, description, position)]

# Extract — async, returns content (summarized if large)
pages = await web_extract(["https://arxiv.org/abs/1706.03762"])
# → list[ExtractResult(url, title, content, error, raw_content)]
```

`web_extract` returns `raw_content` (the pre-summary text) alongside `content` (the summary) when the summarizer runs.

## Provider Matrix

| Provider   | Priority | Search | Extract | Env vars |
|------------|----------|--------|---------|----------|
| Firecrawl  | 1st      | ✓      | ✓       | `FIRECRAWL_API_KEY` or `FIRECRAWL_API_URL` |
| Tavily     | 2nd      | ✓      | ✓       | `TAVILY_API_KEY` |
| Exa        | 3rd      | ✓      | ✓       | `EXA_API_KEY` |

Priority is the default walk order when no config override is set. Override per capability via `~/.any2wiki/config.yaml`:

```yaml
web:
  search_backend: exa      # override for search only
  extract_backend: firecrawl  # override for extract only
  # backend: tavily        # shared default (lower precedence than above)
```

## How Each Provider Works

### Firecrawl (SDK: `firecrawl-py`, class `Firecrawl`)

**Search** — `client.search(query, limit=N)` → response object with `.web` list.  
Each item has `.url`, `.title`, `.description` as attributes (not dict keys).  
Supports `categories=["research"]` for academic-focused results and `includeDomains`/`excludeDomains`.

**Extract** — `client.scrape(url, formats=["markdown"])` per URL (parallelized with `asyncio.gather`).  
Response object: `.markdown` for content, `.metadata.title` for title.  
Auto-handles PDFs and JS-rendered pages server-side.

### Tavily (SDK: `tavily-python`, class `TavilyClient`)

**Search** — `client.search(query=query, max_results=N)`.  
Returns a dict: `results[].url`, `results[].title`, `results[].content` (NLP snippet).  
Supports `search_depth="advanced"` for higher-quality snippets (2× credits), `topic="news"` or `"finance"`, and `time_range`.

**Extract** — `client.extract(urls=urls)`.  
Returns a dict: `results[].url`, `results[].raw_content` (full page text, markdown format by default).  
Failed URLs appear in `failed_results[].url` + `failed_results[].error`.

### Exa (SDK: `exa-py`, class `Exa`)

**Search** — `client.search(query, num_results=N, contents={"highlights": True})`.  
`highlights` must be requested explicitly — basic search returns no description.  
Each result item: `.title`, `.url`, `.highlights` (list of excerpt strings).  
Use `category="research paper"` to target academic content (relevant for Any2Wiki).

**Extract** — `client.get_contents(urls, text=True)`.  
`text=True` is required — omitting it returns no content.  
Response: `.results[].url`, `.results[].title`, `.results[].text` (full markdown).  
Always check `.statuses` for per-URL errors (endpoint returns HTTP 200 even when individual URLs fail).

## Security

`check_urls(urls)` in `security.py` runs before any fetch. Blocks:
- URLs containing credential patterns (`sk-`, `api_key=`, etc.)
- SSRF targets (localhost, private IP ranges, cloud metadata endpoints)

Blocked URLs are returned as `ExtractResult(error=...)` rather than silently dropped.

## Summarizer

`summarize(content, url, title)` in `summarizer.py` uses Haiku with tiered logic:

| Content length | Behavior |
|---------------|----------|
| < 5k chars    | Skip (return `None`) |
| 5k – 500k     | Single LLM call |
| 500k – 2M     | Chunk into 100k pieces → parallel LLM → synthesis call |
| > 2M          | Refuse (return error string) |

Output capped at 5k chars. Falls back to truncated raw content on LLM failure.  
Disable with `use_summarizer=False` in `web_extract()`.

## Common Mistakes

- **Firecrawl**: don't use `FirecrawlApp` (old class) or `scrape_url()` (old method) — use `Firecrawl` and `scrape()`.
- **Exa search**: calling `client.search(query, num_results=N)` without `contents={"highlights": True}` returns results with empty descriptions.
- **Exa extract**: calling `client.get_contents(urls)` without `text=True` returns results with empty content.
- **Exa extract errors**: always check `result.statuses` — the endpoint returns HTTP 200 even on per-URL failures.
- **Config file**: the registry silently falls back to env-var priority walk if `~/.any2wiki/config.yaml` is missing. This is intentional.
