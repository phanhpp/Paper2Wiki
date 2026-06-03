# web_tools

Two LangChain tools — `web_search` and `web_extract` — backed by a provider registry that
routes to Firecrawl, Tavily, or Exa depending on which API key is set.

## How it fits together

```
Agent calls web_search(query, limit, category) or web_extract(urls)
          │
          ▼
     tools.py
       web_search()          web_extract()
         │                      │
         │                      ├─ security.check_urls()   ← SSRF + secret scan
         │                      │
         ▼                      ▼
     registry.py — ProviderRegistry
       1. config override  (~/.paper2wiki/config.yaml  web.search_backend / web.extract_backend)
       2. shared backend   (config  web.backend)
       3. priority walk    firecrawl → tavily → exa  (first with valid API key wins)
          │
          ▼
     providers/
       firecrawl.py   FirecrawlProvider.search() / .extract()
       tavily.py      TavilyProvider.search()    / .extract()
       exa.py         ExaProvider.search()       / .extract()
          │
          ▼
     types.py — SearchResult / ExtractResult   (shared data shapes)
          │
          ▼  (extract only)
     summarizer.py — LLM summarization for large pages
```

## Files

| File | Purpose |
|---|---|
| `tools.py` | `web_search` + `web_extract` LangChain tools. Entry point. |
| `registry.py` | Picks the active provider via config → priority walk. Module-level singleton `registry`. |
| `security.py` | `check_urls()` — blocks SSRF targets (localhost, RFC-1918, metadata endpoints) and URLs with embedded secrets. |
| `summarizer.py` | LLM-summarizes pages above `min_length`. Stores original in `raw_content`, summary in `content`. |
| `types.py` | `SearchResult`, `ExtractResult` dataclasses + `SearchProvider` Protocol. |
| `providers/firecrawl.py` | Firecrawl SDK — search + scrape. Highest priority. |
| `providers/tavily.py` | Tavily SDK — search + extract. Second priority. |
| `providers/exa.py` | Exa SDK — search + get_contents. Third priority. |

## Category filtering

`web_search` accepts an optional `category` param. Each provider maps it automatically:

| category value | firecrawl | exa | tavily |
|---|---|---|---|
| `"research"` | `categories=["research"]` | `category="research paper"` | ignored |
| `"news"` | `sources=["news"]` | `category="news"` | `topic="news"` |
| `"github"` | `categories=["github"]` | ignored | ignored |
| `"pdf"` | `categories=["pdf"]` | ignored | ignored |
| `"company"` | ignored | `category="company"` | ignored |
| `"financial report"` | ignored | `category="financial report"` | ignored |

The tool description is generated dynamically at import time and shows only the values
valid for the active provider.

## Provider selection

```yaml
# ~/.paper2wiki/config.yaml
web:
  backend: firecrawl          # default for both
  search_backend: exa         # override search only
  extract_backend: firecrawl  # override extract only
```

If no config, priority walk picks the first provider with a valid API key:
`FIRECRAWL_API_KEY` → `TAVILY_API_KEY` → `EXA_API_KEY`.

## Strategy pattern

Yes — this is a textbook Strategy pattern:

- **`SearchProvider` Protocol** (`types.py`) is the strategy interface. It declares
  `search()` and `extract()` without caring which vendor implements them.
- **`FirecrawlProvider`, `TavilyProvider`, `ExaProvider`** are concrete strategies —
  each encapsulates a different SDK and maps the normalised params to its own API format.
- **`ProviderRegistry`** is the context — it selects the right strategy at runtime
  (config override → priority walk) and hands it to the tools. The tools never import
  a provider directly; they only talk to the interface.

This means adding a new provider (e.g. Brave Search) requires zero changes to `tools.py`,
`security.py`, or `summarizer.py` — just a new class + registry entry.

## Summarizer thresholds

`summarize()` in `summarizer.py` applies tiered processing based on content length:

```
< 5,000 chars   → skip  — short enough to pass raw to the agent, no LLM cost
5k – 500k       → single LLM call (Haiku) → structured markdown summary
500k – 2M       → chunked: split into 100k-char chunks → parallel Haiku per chunk
                  → synthesis call to merge into one summary
> 2M            → refused — too large to process, returns error string
```

**Why 5,000 chars as the floor?**
A typical wiki ingest prompt already has context from prior pages. Feeding another 5k chars of
raw extracted web content doesn't materially hurt the context window. The LLM call costs more
than the context savings for small pages. 5k is ~1,000 tokens — the crossover point where
summarization overhead starts to pay off. Configurable via `min_length_for_summary` in
`~/.paper2wiki/config.yaml`.

**Why 500k as the chunk threshold?**
Claude Haiku's context window is 200k tokens (~800k chars). Staying at 500k chars keeps a
comfortable buffer for the system prompt and output. Above that, a single call would likely
fail or produce degraded output — chunking is safer.

**Output is always capped at 5,000 chars** (`MAX_OUTPUT`) regardless of path, so the agent
receives a predictable-sized result.

## Adding a new provider

1. Create `providers/myprovider.py` implementing `search()` and/or `extract()`
2. Add it to `providers/__init__.py`
3. Instantiate in `registry.py` `ProviderRegistry.__init__` and add to `DEFAULT_PRIORITY`
4. Add category mapping in `_CATEGORY_MAP` if the provider has a category filter
