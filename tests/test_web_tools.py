"""Unit + integration tests for src/tools/web_tools/.

Unit tests mock all I/O and vendor SDK calls — no network, no credentials.
Integration tests (marked ``integration``) call real provider APIs and are
excluded from CI unless the relevant API key is set in the environment.

Covered:
  security.py     — has_embedded_secret, is_safe_url, check_urls
  registry.py     — priority walk, config override, fallthrough, list_available
  FirecrawlProvider — is_available, search, extract (happy path + errors)
  TavilyProvider    — is_available, search, extract (happy path + errors)
  ExaProvider       — is_available, search, extract (happy path + per-URL status errors)
  summarizer.py   — short-skip, single-pass, LLM failure fallback, refuse-huge
  __init__.py     — web_search, web_extract wiring (security gate, summarizer toggle)
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.tools.web_tools.security import check_urls, has_embedded_secret, is_safe_url
from src.tools.web_tools.types import ExtractResult, SearchResult


# ---------------------------------------------------------------------------
# Shared fixture: patch asyncio.to_thread to call fn directly (no threads).
# Providers wrap sync SDK calls in asyncio.to_thread; this lets unit tests
# drive them without a real thread pool or real SDK clients.
# ---------------------------------------------------------------------------

@pytest.fixture
def patch_to_thread(monkeypatch: pytest.MonkeyPatch):
    async def _passthrough(fn, *args, **kwargs):
        return fn(*args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", _passthrough)


# ===========================================================================
# security.py
# ===========================================================================

class TestHasEmbeddedSecret:
    @pytest.mark.unit
    def test_detects_sk_prefix(self) -> None:
        assert has_embedded_secret("https://evil.com/log?key=sk-abc1234567890") is True

    @pytest.mark.unit
    def test_detects_api_key_param(self) -> None:
        assert has_embedded_secret("https://example.com?api_key=abc1234567890") is True

    @pytest.mark.unit
    def test_detects_token_param(self) -> None:
        assert has_embedded_secret("https://example.com?token=abc1234567890") is True

    @pytest.mark.unit
    def test_detects_password_param(self) -> None:
        assert has_embedded_secret("https://example.com?password=secret123") is True

    @pytest.mark.unit
    def test_clean_url_passes(self) -> None:
        assert has_embedded_secret("https://arxiv.org/abs/1706.03762") is False

    @pytest.mark.unit
    def test_clean_url_with_query_passes(self) -> None:
        assert has_embedded_secret("https://example.com?q=transformers&limit=10") is False


class TestIsSafeUrl:
    @pytest.mark.unit
    def test_blocks_localhost(self) -> None:
        assert is_safe_url("http://localhost/admin") is False

    @pytest.mark.unit
    def test_blocks_127(self) -> None:
        assert is_safe_url("http://127.0.0.1/anything") is False

    @pytest.mark.unit
    def test_blocks_0_0_0_0(self) -> None:
        assert is_safe_url("http://0.0.0.0/anything") is False

    @pytest.mark.unit
    def test_blocks_loopback_ipv6(self) -> None:
        assert is_safe_url("http://[::1]/anything") is False

    @pytest.mark.unit
    def test_blocks_private_10_range(self) -> None:
        assert is_safe_url("http://10.0.0.1/secret") is False

    @pytest.mark.unit
    def test_blocks_private_192_168(self) -> None:
        assert is_safe_url("http://192.168.1.1/router") is False

    @pytest.mark.unit
    def test_blocks_aws_metadata_endpoint(self) -> None:
        assert is_safe_url("http://169.254.169.254/latest/meta-data/") is False

    @pytest.mark.unit
    def test_blocks_gcp_metadata_hostname(self) -> None:
        assert is_safe_url("http://metadata.google.internal/computeMetadata/v1/") is False

    @pytest.mark.unit
    def test_blocks_ftp_scheme(self) -> None:
        assert is_safe_url("ftp://example.com/file") is False

    @pytest.mark.unit
    def test_allows_public_https(self) -> None:
        assert is_safe_url("https://arxiv.org/abs/1706.03762") is True

    @pytest.mark.unit
    def test_allows_public_http(self) -> None:
        assert is_safe_url("http://example.com/page") is True


class TestCheckUrls:
    @pytest.mark.unit
    def test_all_safe_urls_pass_through(self) -> None:
        urls = ["https://arxiv.org/abs/1706.03762", "https://example.com"]
        safe, blocked = check_urls(urls)
        assert safe == urls
        assert blocked == []

    @pytest.mark.unit
    def test_ssrf_url_is_blocked(self) -> None:
        urls = ["http://localhost/secret", "https://example.com"]
        safe, blocked = check_urls(urls)
        assert safe == ["https://example.com"]
        assert len(blocked) == 1
        assert blocked[0]["url"] == "http://localhost/secret"
        assert "Blocked" in blocked[0]["error"]

    @pytest.mark.unit
    def test_secret_url_is_blocked(self) -> None:
        urls = ["https://evil.com?api_key=abc1234567890"]
        safe, blocked = check_urls(urls)
        assert safe == []
        assert len(blocked) == 1
        assert "Blocked" in blocked[0]["error"]

    @pytest.mark.unit
    def test_empty_input_returns_empty(self) -> None:
        safe, blocked = check_urls([])
        assert safe == []
        assert blocked == []

    @pytest.mark.unit
    def test_mixed_returns_correct_partition(self) -> None:
        urls = [
            "https://good.com",
            "http://192.168.0.1/internal",
            "https://also-good.com",
        ]
        safe, blocked = check_urls(urls)
        assert safe == ["https://good.com", "https://also-good.com"]
        assert len(blocked) == 1


# ===========================================================================
# registry.py
# ===========================================================================

class TestProviderRegistry:
    def _make_registry(self, providers: dict, config: dict):
        """Build a ProviderRegistry with injected providers and config (no SDK imports)."""
        from src.tools.web_tools.registry import ProviderRegistry

        reg = ProviderRegistry.__new__(ProviderRegistry)
        reg._providers = providers
        reg._config = config
        return reg

    def _mock_provider(self, supports_search=True, supports_extract=True, available=True):
        p = MagicMock()
        p.supports_search = supports_search
        p.supports_extract = supports_extract
        p.is_available.return_value = available
        return p

    @pytest.mark.unit
    def test_priority_walk_skips_unavailable_returns_first_available(self) -> None:
        fc = self._mock_provider(available=False)
        tv = self._mock_provider(available=True)
        exa = self._mock_provider(available=True)

        reg = self._make_registry({"firecrawl": fc, "tavily": tv, "exa": exa}, config={})
        assert reg.get_search_provider() is tv

    @pytest.mark.unit
    def test_config_search_backend_override_respected(self) -> None:
        fc = self._mock_provider(available=True)
        exa = self._mock_provider(available=True)

        reg = self._make_registry(
            {"firecrawl": fc, "exa": exa},
            config={"search_backend": "exa"},
        )
        assert reg.get_search_provider() is exa

    @pytest.mark.unit
    def test_config_extract_backend_override_respected(self) -> None:
        tv = self._mock_provider(available=True)
        exa = self._mock_provider(available=True)

        reg = self._make_registry(
            {"tavily": tv, "exa": exa},
            config={"extract_backend": "exa"},
        )
        assert reg.get_extract_provider() is exa

    @pytest.mark.unit
    def test_config_override_falls_through_when_provider_unavailable(self) -> None:
        fc = self._mock_provider(available=True)
        exa = self._mock_provider(available=False)  # config says exa but it's down

        reg = self._make_registry(
            {"firecrawl": fc, "exa": exa},
            config={"search_backend": "exa"},
        )
        assert reg.get_search_provider() is fc  # falls back to priority walk

    @pytest.mark.unit
    def test_shared_backend_config_used_when_no_capability_override(self) -> None:
        tv = self._mock_provider(available=True)
        exa = self._mock_provider(available=True)

        reg = self._make_registry(
            {"tavily": tv, "exa": exa},
            config={"backend": "exa"},
        )
        # no search_backend override → shared backend → exa
        assert reg.get_search_provider() is exa

    @pytest.mark.unit
    def test_returns_none_when_no_provider_available(self) -> None:
        p = self._mock_provider(available=False)
        reg = self._make_registry(
            {"firecrawl": p, "tavily": p, "exa": p},
            config={},
        )
        assert reg.get_search_provider() is None

    @pytest.mark.unit
    def test_list_available_returns_only_credentialed_providers(self) -> None:
        on = self._mock_provider(available=True)
        off = self._mock_provider(available=False)

        reg = self._make_registry(
            {"firecrawl": on, "tavily": off, "exa": on},
            config={},
        )
        assert sorted(reg.list_available()) == ["exa", "firecrawl"]

    @pytest.mark.unit
    def test_capability_flag_respected_in_walk(self) -> None:
        # firecrawl doesn't support extract; tavily does
        fc = self._mock_provider(supports_extract=False, available=True)
        tv = self._mock_provider(supports_extract=True, available=True)

        reg = self._make_registry({"firecrawl": fc, "tavily": tv}, config={})
        assert reg.get_extract_provider() is tv


# ===========================================================================
# FirecrawlProvider
# ===========================================================================

class TestFirecrawlProvider:
    @pytest.mark.unit
    def test_is_available_with_api_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from src.tools.web_tools.providers.firecrawl import FirecrawlProvider

        monkeypatch.setenv("FIRECRAWL_API_KEY", "fc-test")
        assert FirecrawlProvider().is_available() is True

    @pytest.mark.unit
    def test_is_not_available_without_credentials(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from src.tools.web_tools.providers.firecrawl import FirecrawlProvider

        monkeypatch.delenv("FIRECRAWL_API_KEY", raising=False)
        assert FirecrawlProvider().is_available() is False

    @pytest.mark.unit
    def test_search_maps_web_items_to_search_results(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from src.tools.web_tools.providers.firecrawl import FirecrawlProvider

        fake_item = SimpleNamespace(url="https://example.com", title="Example", description="A page")
        mock_client = MagicMock()
        mock_client.search.return_value = SimpleNamespace(web=[fake_item])

        provider = FirecrawlProvider()
        monkeypatch.setattr(provider, "_get_client", lambda: mock_client)

        results = provider.search("transformers", limit=3)

        mock_client.search.assert_called_once_with("transformers", limit=3)
        assert len(results) == 1
        result = results[0]
        assert isinstance(result, SearchResult)
        assert result.url == "https://example.com"
        assert result.title == "Example"
        assert result.description == "A page"
        assert result.position == 1

    @pytest.mark.unit
    def test_search_handles_none_web_attribute(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from src.tools.web_tools.providers.firecrawl import FirecrawlProvider

        mock_client = MagicMock()
        mock_client.search.return_value = SimpleNamespace(web=None)

        provider = FirecrawlProvider()
        monkeypatch.setattr(provider, "_get_client", lambda: mock_client)

        assert provider.search("nothing") == []

    @pytest.mark.unit
    def test_search_assigns_positions_sequentially(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from src.tools.web_tools.providers.firecrawl import FirecrawlProvider

        items = [
            SimpleNamespace(url=f"https://example.com/{i}", title=f"T{i}", description="")
            for i in range(3)
        ]
        mock_client = MagicMock()
        mock_client.search.return_value = SimpleNamespace(web=items)

        provider = FirecrawlProvider()
        monkeypatch.setattr(provider, "_get_client", lambda: mock_client)

        results = provider.search("q")
        assert [r.position for r in results] == [1, 2, 3]

    @pytest.mark.unit
    async def test_extract_returns_markdown_and_title(
        self, monkeypatch: pytest.MonkeyPatch, patch_to_thread
    ) -> None:
        from src.tools.web_tools.providers.firecrawl import FirecrawlProvider

        fake_metadata = SimpleNamespace(title="Firecrawl Home")
        fake_scrape = SimpleNamespace(markdown="# Hello\nWorld", metadata=fake_metadata)
        mock_client = MagicMock()
        mock_client.scrape.return_value = fake_scrape

        provider = FirecrawlProvider()
        monkeypatch.setattr(provider, "_get_client", lambda: mock_client)

        results = await provider.extract(["https://firecrawl.dev"])

        mock_client.scrape.assert_called_once_with("https://firecrawl.dev", formats=["markdown"])
        assert len(results) == 1
        r = results[0]
        assert isinstance(r, ExtractResult)
        assert r.url == "https://firecrawl.dev"
        assert r.title == "Firecrawl Home"
        assert r.content == "# Hello\nWorld"
        assert r.error is None

    @pytest.mark.unit
    async def test_extract_parallelizes_multiple_urls(
        self, monkeypatch: pytest.MonkeyPatch, patch_to_thread
    ) -> None:
        from src.tools.web_tools.providers.firecrawl import FirecrawlProvider

        def fake_scrape(url, formats=None):
            return SimpleNamespace(
                markdown=f"content of {url}",
                metadata=SimpleNamespace(title=url),
            )

        mock_client = MagicMock()
        mock_client.scrape.side_effect = fake_scrape

        provider = FirecrawlProvider()
        monkeypatch.setattr(provider, "_get_client", lambda: mock_client)

        urls = ["https://a.com", "https://b.com", "https://c.com"]
        results = await provider.extract(urls)

        assert len(results) == 3
        assert {r.url for r in results} == set(urls)

    @pytest.mark.unit
    async def test_extract_returns_error_on_exception(
        self, monkeypatch: pytest.MonkeyPatch, patch_to_thread
    ) -> None:
        from src.tools.web_tools.providers.firecrawl import FirecrawlProvider

        mock_client = MagicMock()
        mock_client.scrape.side_effect = RuntimeError("API rate limit")

        provider = FirecrawlProvider()
        monkeypatch.setattr(provider, "_get_client", lambda: mock_client)

        results = await provider.extract(["https://firecrawl.dev"])

        assert len(results) == 1
        assert results[0].error is not None
        assert "Firecrawl extract failed" in results[0].error
        assert results[0].content == ""


# ===========================================================================
# TavilyProvider
# ===========================================================================

class TestTavilyProvider:
    @pytest.mark.unit
    def test_is_available_with_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from src.tools.web_tools.providers.tavily import TavilyProvider

        monkeypatch.setenv("TAVILY_API_KEY", "tvly-test")
        assert TavilyProvider().is_available() is True

    @pytest.mark.unit
    def test_is_not_available_without_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from src.tools.web_tools.providers.tavily import TavilyProvider

        monkeypatch.delenv("TAVILY_API_KEY", raising=False)
        assert TavilyProvider().is_available() is False

    @pytest.mark.unit
    def test_search_maps_results_to_search_results(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from src.tools.web_tools.providers.tavily import TavilyProvider

        mock_client = MagicMock()
        mock_client.search.return_value = {
            "results": [
                {"title": "Page A", "url": "https://a.com", "content": "snippet A"},
                {"title": "Page B", "url": "https://b.com", "content": "snippet B"},
            ]
        }

        provider = TavilyProvider()
        monkeypatch.setattr(provider, "_get_client", lambda: mock_client)

        results = provider.search("test query", limit=5)

        mock_client.search.assert_called_once_with(query="test query", max_results=5)
        assert len(results) == 2
        assert results[0].url == "https://a.com"
        assert results[0].description == "snippet A"
        assert results[1].position == 2

    @pytest.mark.unit
    def test_search_handles_empty_results(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from src.tools.web_tools.providers.tavily import TavilyProvider

        mock_client = MagicMock()
        mock_client.search.return_value = {"results": []}

        provider = TavilyProvider()
        monkeypatch.setattr(provider, "_get_client", lambda: mock_client)

        assert provider.search("nothing") == []

    @pytest.mark.unit
    async def test_extract_returns_raw_content(
        self, monkeypatch: pytest.MonkeyPatch, patch_to_thread
    ) -> None:
        from src.tools.web_tools.providers.tavily import TavilyProvider

        mock_client = MagicMock()
        mock_client.extract.return_value = {
            "results": [{"url": "https://a.com", "raw_content": "# Article\nContent here."}],
            "failed_results": [],
        }

        provider = TavilyProvider()
        monkeypatch.setattr(provider, "_get_client", lambda: mock_client)

        results = await provider.extract(["https://a.com"])

        mock_client.extract.assert_called_once_with(urls=["https://a.com"])
        assert len(results) == 1
        assert results[0].url == "https://a.com"
        assert results[0].content == "# Article\nContent here."
        assert results[0].error is None

    @pytest.mark.unit
    async def test_extract_converts_failed_results_to_errors(
        self, monkeypatch: pytest.MonkeyPatch, patch_to_thread
    ) -> None:
        from src.tools.web_tools.providers.tavily import TavilyProvider

        mock_client = MagicMock()
        mock_client.extract.return_value = {
            "results": [],
            "failed_results": [{"url": "https://blocked.com", "error": "403 Forbidden"}],
        }

        provider = TavilyProvider()
        monkeypatch.setattr(provider, "_get_client", lambda: mock_client)

        results = await provider.extract(["https://blocked.com"])

        assert len(results) == 1
        assert results[0].url == "https://blocked.com"
        assert results[0].error is not None
        assert "Tavily extract failed" in results[0].error
        assert results[0].content == ""

    @pytest.mark.unit
    async def test_extract_returns_all_errors_on_exception(
        self, monkeypatch: pytest.MonkeyPatch, patch_to_thread
    ) -> None:
        from src.tools.web_tools.providers.tavily import TavilyProvider

        mock_client = MagicMock()
        mock_client.extract.side_effect = ConnectionError("network error")

        provider = TavilyProvider()
        monkeypatch.setattr(provider, "_get_client", lambda: mock_client)

        urls = ["https://a.com", "https://b.com"]
        results = await provider.extract(urls)

        assert len(results) == 2
        assert all(r.error is not None for r in results)
        assert all("Tavily extract failed" in r.error for r in results)


# ===========================================================================
# ExaProvider
# ===========================================================================

class TestExaProvider:
    @pytest.mark.unit
    def test_is_available_with_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from src.tools.web_tools.providers.exa import ExaProvider

        monkeypatch.setenv("EXA_API_KEY", "exa-test")
        assert ExaProvider().is_available() is True

    @pytest.mark.unit
    def test_is_not_available_without_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from src.tools.web_tools.providers.exa import ExaProvider

        monkeypatch.delenv("EXA_API_KEY", raising=False)
        assert ExaProvider().is_available() is False

    @pytest.mark.unit
    def test_search_requests_highlights_and_joins_them(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from src.tools.web_tools.providers.exa import ExaProvider

        fake_item = SimpleNamespace(
            title="Attention Is All You Need",
            url="https://arxiv.org/abs/1706.03762",
            highlights=["Transformer model.", "Self-attention mechanism."],
        )
        mock_client = MagicMock()
        mock_client.search.return_value = SimpleNamespace(results=[fake_item])

        provider = ExaProvider()
        monkeypatch.setattr(provider, "_get_client", lambda: mock_client)

        results = provider.search("attention transformers", limit=5)

        mock_client.search.assert_called_once_with(
            "attention transformers", num_results=5, contents={"highlights": True}
        )
        assert len(results) == 1
        r = results[0]
        assert r.title == "Attention Is All You Need"
        assert r.url == "https://arxiv.org/abs/1706.03762"
        assert "Transformer model." in r.description
        assert "Self-attention mechanism." in r.description

    @pytest.mark.unit
    def test_search_description_empty_when_no_highlights(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from src.tools.web_tools.providers.exa import ExaProvider

        fake_item = SimpleNamespace(
            title="Some Page",
            url="https://example.com",
            highlights=None,
        )
        mock_client = MagicMock()
        mock_client.search.return_value = SimpleNamespace(results=[fake_item])

        provider = ExaProvider()
        monkeypatch.setattr(provider, "_get_client", lambda: mock_client)

        results = provider.search("something")
        assert results[0].description == ""

    @pytest.mark.unit
    async def test_extract_passes_text_true_and_returns_content(
        self, monkeypatch: pytest.MonkeyPatch, patch_to_thread
    ) -> None:
        from src.tools.web_tools.providers.exa import ExaProvider

        fake_item = SimpleNamespace(
            url="https://arxiv.org/abs/1706.03762",
            title="Attention Is All You Need",
            text="# Abstract\nTransformer architecture...",
        )
        fake_status = SimpleNamespace(
            id="https://arxiv.org/abs/1706.03762", status="success", error=None
        )
        mock_client = MagicMock()
        mock_client.get_contents.return_value = SimpleNamespace(
            results=[fake_item], statuses=[fake_status]
        )

        provider = ExaProvider()
        monkeypatch.setattr(provider, "_get_client", lambda: mock_client)

        results = await provider.extract(["https://arxiv.org/abs/1706.03762"])

        # text=True is required — verify it was passed
        mock_client.get_contents.assert_called_once_with(
            ["https://arxiv.org/abs/1706.03762"], text=True
        )
        assert len(results) == 1
        r = results[0]
        assert r.url == "https://arxiv.org/abs/1706.03762"
        assert r.title == "Attention Is All You Need"
        assert r.content == "# Abstract\nTransformer architecture..."
        assert r.error is None

    @pytest.mark.unit
    async def test_extract_reports_per_url_status_error(
        self, monkeypatch: pytest.MonkeyPatch, patch_to_thread
    ) -> None:
        from src.tools.web_tools.providers.exa import ExaProvider

        fake_item = SimpleNamespace(url="https://blocked.com", title="", text="")
        fake_error_obj = SimpleNamespace(tag="SOURCE_NOT_AVAILABLE")
        fake_status = SimpleNamespace(
            id="https://blocked.com", status="error", error=fake_error_obj
        )
        mock_client = MagicMock()
        mock_client.get_contents.return_value = SimpleNamespace(
            results=[fake_item], statuses=[fake_status]
        )

        provider = ExaProvider()
        monkeypatch.setattr(provider, "_get_client", lambda: mock_client)

        results = await provider.extract(["https://blocked.com"])

        assert len(results) == 1
        assert results[0].error is not None
        assert "SOURCE_NOT_AVAILABLE" in results[0].error

    @pytest.mark.unit
    async def test_extract_reports_error_for_url_missing_from_results(
        self, monkeypatch: pytest.MonkeyPatch, patch_to_thread
    ) -> None:
        # Exa returns HTTP 200 but the URL silently disappears from results
        mock_client = MagicMock()
        mock_client.get_contents.return_value = SimpleNamespace(results=[], statuses=[])

        from src.tools.web_tools.providers.exa import ExaProvider

        provider = ExaProvider()
        monkeypatch.setattr(provider, "_get_client", lambda: mock_client)

        results = await provider.extract(["https://missing.com"])

        assert len(results) == 1
        assert results[0].url == "https://missing.com"
        assert results[0].error is not None

    @pytest.mark.unit
    async def test_extract_returns_all_errors_on_exception(
        self, monkeypatch: pytest.MonkeyPatch, patch_to_thread
    ) -> None:
        from src.tools.web_tools.providers.exa import ExaProvider

        mock_client = MagicMock()
        mock_client.get_contents.side_effect = RuntimeError("quota exceeded")

        provider = ExaProvider()
        monkeypatch.setattr(provider, "_get_client", lambda: mock_client)

        results = await provider.extract(["https://a.com", "https://b.com"])

        assert len(results) == 2
        assert all(r.error is not None for r in results)
        assert all("Exa extract failed" in r.error for r in results)


# ===========================================================================
# summarizer.py
# ===========================================================================

class TestSummarizer:
    @pytest.mark.unit
    async def test_short_content_returns_none(self) -> None:
        from src.tools.web_tools.summarizer import summarize

        result = await summarize("short content", min_length=5000)
        assert result is None

    @pytest.mark.unit
    async def test_content_exceeding_max_returns_error_string(self) -> None:
        from src.tools.web_tools.summarizer import MAX_CONTENT, summarize

        huge = "x" * (MAX_CONTENT + 1)
        result = await summarize(huge)
        assert result is not None
        assert "too large" in result.lower()

    @pytest.mark.unit
    async def test_medium_content_calls_llm_and_returns_summary(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from src.tools.web_tools import summarizer

        async def fake_call_llm(content, system, model=None, max_tokens=4096):
            return "Summarized content."

        monkeypatch.setattr(summarizer, "_call_llm", fake_call_llm)

        result = await summarizer.summarize("x" * 10_000, min_length=5000)
        assert result == "Summarized content."

    @pytest.mark.unit
    async def test_llm_exception_falls_back_to_truncated_raw(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from src.tools.web_tools import summarizer

        async def fail_call_llm(content, system, model=None, max_tokens=4096):
            raise RuntimeError("LLM unavailable")

        monkeypatch.setattr(summarizer, "_call_llm", fail_call_llm)

        content = "Fallback word " * 500  # ~7k chars, above default 5k min
        result = await summarizer.summarize(content, min_length=5000)

        assert result is not None
        assert result.startswith("Fallback word")

    @pytest.mark.unit
    async def test_empty_llm_response_falls_back_to_truncated_raw(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from src.tools.web_tools import summarizer

        async def empty_call_llm(content, system, model=None, max_tokens=4096):
            return None

        monkeypatch.setattr(summarizer, "_call_llm", empty_call_llm)

        content = "Content word " * 500
        result = await summarizer.summarize(content, min_length=5000)

        assert result is not None
        assert "Content word" in result

    @pytest.mark.unit
    async def test_summary_capped_at_max_output(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from src.tools.web_tools import summarizer

        async def huge_llm_response(content, system, model=None, max_tokens=4096):
            return "word " * 10_000  # far beyond MAX_OUTPUT

        monkeypatch.setattr(summarizer, "_call_llm", huge_llm_response)

        result = await summarizer.summarize("x" * 10_000, min_length=5000)

        assert result is not None
        assert len(result) <= summarizer.MAX_OUTPUT + len("\n\n[...truncated...]")


# ===========================================================================
# Public API (__init__.py)
# ===========================================================================

class TestWebSearch:
    @pytest.mark.unit
    def test_returns_search_results_from_provider(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import src.tools.web_tools as wt

        mock_provider = MagicMock()
        mock_provider.search.return_value = [
            SearchResult(title="T", url="https://example.com", description="D", position=1)
        ]
        monkeypatch.setattr(wt.registry, "get_search_provider", lambda: mock_provider)

        results = wt.web_search.invoke({"query": "test", "limit": 5})

        mock_provider.search.assert_called_once_with("test", 5)
        assert len(results) == 1
        assert results[0].url == "https://example.com"

    @pytest.mark.unit
    def test_clamps_limit_to_valid_range(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import src.tools.web_tools as wt

        mock_provider = MagicMock()
        mock_provider.search.return_value = []
        monkeypatch.setattr(wt.registry, "get_search_provider", lambda: mock_provider)

        wt.web_search.invoke({"query": "q", "limit": 0})
        wt.web_search.invoke({"query": "q", "limit": 999})
        calls = mock_provider.search.call_args_list
        assert calls[0].args[1] == 1    # clamped up from 0
        assert calls[1].args[1] == 100  # clamped down from 999

    @pytest.mark.unit
    def test_raises_runtime_error_when_no_provider(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import src.tools.web_tools as wt

        monkeypatch.setattr(wt.registry, "get_search_provider", lambda: None)
        monkeypatch.setattr(wt.registry, "list_available", lambda: [])

        with pytest.raises(RuntimeError, match="No search provider"):
            wt.web_search.invoke({"query": "test"})


class TestWebExtract:
    @pytest.mark.unit
    async def test_empty_url_list_returns_empty(self) -> None:
        from src.tools.web_tools import web_extract

        assert await web_extract.ainvoke({"urls": []}) == []

    @pytest.mark.unit
    async def test_returns_extract_results_from_provider(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import src.tools.web_tools as wt

        mock_provider = MagicMock()
        mock_provider.extract = AsyncMock(return_value=[
            ExtractResult(url="https://example.com", title="T", content="content here")
        ])
        monkeypatch.setattr(wt.registry, "get_extract_provider", lambda: mock_provider)

        results = await wt.web_extract.ainvoke({"urls": ["https://example.com"], "use_summarizer": False})

        assert len(results) == 1
        assert results[0].content == "content here"

    @pytest.mark.unit
    async def test_ssrf_urls_blocked_before_provider_called(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import src.tools.web_tools as wt

        mock_provider = MagicMock()
        mock_provider.extract = AsyncMock(return_value=[])
        monkeypatch.setattr(wt.registry, "get_extract_provider", lambda: mock_provider)

        results = await wt.web_extract.ainvoke(
            {"urls": ["http://localhost/secret"], "use_summarizer": False}
        )

        assert len(results) == 1
        assert "Blocked" in results[0].error
        mock_provider.extract.assert_not_called()

    @pytest.mark.unit
    async def test_mixed_urls_only_safe_reach_provider(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import src.tools.web_tools as wt

        mock_provider = MagicMock()
        mock_provider.extract = AsyncMock(return_value=[
            ExtractResult(url="https://good.com", title="", content="ok")
        ])
        monkeypatch.setattr(wt.registry, "get_extract_provider", lambda: mock_provider)

        results = await wt.web_extract.ainvoke(
            {"urls": ["https://good.com", "http://192.168.0.1/evil"], "use_summarizer": False}
        )

        assert len(results) == 2
        good = next(r for r in results if r.url == "https://good.com")
        blocked = next(r for r in results if r.url == "http://192.168.0.1/evil")
        assert good.error is None
        assert blocked.error is not None
        # Only the safe URL was sent to the provider
        mock_provider.extract.assert_called_once_with(["https://good.com"])

    @pytest.mark.unit
    async def test_raises_runtime_error_when_no_provider(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import src.tools.web_tools as wt

        monkeypatch.setattr(wt.registry, "get_extract_provider", lambda: None)
        monkeypatch.setattr(wt.registry, "list_available", lambda: [])

        with pytest.raises(RuntimeError, match="No extract provider"):
            await wt.web_extract.ainvoke({"urls": ["https://example.com"]})

    @pytest.mark.unit
    async def test_summarizer_runs_and_stores_raw_content(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import src.tools.web_tools as wt

        long_content = "word " * 2000
        mock_provider = MagicMock()
        mock_provider.extract = AsyncMock(return_value=[
            ExtractResult(url="https://example.com", title="T", content=long_content)
        ])
        monkeypatch.setattr(wt.registry, "get_extract_provider", lambda: mock_provider)
        monkeypatch.setattr("src.tools.web_tools.tools.summarize", AsyncMock(return_value="summarized"))

        results = await wt.web_extract.ainvoke(
            {"urls": ["https://example.com"], "use_summarizer": True, "min_length": 100}
        )

        assert results[0].content == "summarized"
        assert results[0].raw_content == long_content

    @pytest.mark.unit
    async def test_summarizer_skipped_when_disabled(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import src.tools.web_tools as wt

        mock_provider = MagicMock()
        mock_provider.extract = AsyncMock(return_value=[
            ExtractResult(url="https://example.com", title="T", content="raw content")
        ])
        monkeypatch.setattr(wt.registry, "get_extract_provider", lambda: mock_provider)

        mock_summarize = AsyncMock(return_value="should not be called")
        monkeypatch.setattr("src.tools.web_tools.tools.summarize", mock_summarize)

        results = await wt.web_extract.ainvoke({"urls": ["https://example.com"], "use_summarizer": False})

        mock_summarize.assert_not_called()
        assert results[0].content == "raw content"

    @pytest.mark.unit
    async def test_summarizer_skipped_for_error_results(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import src.tools.web_tools as wt

        mock_provider = MagicMock()
        mock_provider.extract = AsyncMock(return_value=[
            ExtractResult(
                url="https://example.com", title="", content="",
                error="provider failed"
            )
        ])
        monkeypatch.setattr(wt.registry, "get_extract_provider", lambda: mock_provider)

        mock_summarize = AsyncMock(return_value="should not be called")
        monkeypatch.setattr("src.tools.web_tools.tools.summarize", mock_summarize)

        results = await wt.web_extract.ainvoke({"urls": ["https://example.com"], "use_summarizer": True})

        mock_summarize.assert_not_called()
        assert results[0].error == "provider failed"


# ===========================================================================
# Integration tests — real provider API calls (excluded from CI)
# ===========================================================================

@pytest.mark.integration
def test_firecrawl_search_live() -> None:
    """Real Firecrawl search. Requires FIRECRAWL_API_KEY."""
    from src.tools.web_tools.providers.firecrawl import FirecrawlProvider

    provider = FirecrawlProvider()
    if not provider.is_available():
        pytest.skip("FIRECRAWL_API_KEY not set")

    results = provider.search("transformer architecture", limit=3)
    assert len(results) >= 1
    assert all(isinstance(r, SearchResult) for r in results)
    assert all(r.url.startswith("http") for r in results)


@pytest.mark.integration
async def test_firecrawl_extract_live() -> None:
    """Real Firecrawl scrape. Requires FIRECRAWL_API_KEY."""
    from src.tools.web_tools.providers.firecrawl import FirecrawlProvider

    provider = FirecrawlProvider()
    if not provider.is_available():
        pytest.skip("FIRECRAWL_API_KEY not set")

    results = await provider.extract(["https://example.com"])
    assert len(results) == 1
    r = results[0]
    assert isinstance(r, ExtractResult)
    assert r.error is None
    assert len(r.content) > 0


@pytest.mark.integration
def test_tavily_search_live() -> None:
    """Real Tavily search. Requires TAVILY_API_KEY."""
    from src.tools.web_tools.providers.tavily import TavilyProvider

    provider = TavilyProvider()
    if not provider.is_available():
        pytest.skip("TAVILY_API_KEY not set")

    results = provider.search("transformer architecture", limit=3)
    assert len(results) >= 1
    assert all(isinstance(r, SearchResult) for r in results)


@pytest.mark.integration
async def test_tavily_extract_live() -> None:
    """Real Tavily extract. Requires TAVILY_API_KEY."""
    from src.tools.web_tools.providers.tavily import TavilyProvider

    provider = TavilyProvider()
    if not provider.is_available():
        pytest.skip("TAVILY_API_KEY not set")

    results = await provider.extract(["https://example.com"])
    assert len(results) >= 1
    assert results[0].content != "" or results[0].error is not None


@pytest.mark.integration
def test_exa_search_live() -> None:
    """Real Exa search. Requires EXA_API_KEY."""
    from src.tools.web_tools.providers.exa import ExaProvider

    provider = ExaProvider()
    if not provider.is_available():
        pytest.skip("EXA_API_KEY not set")

    results = provider.search("transformer architecture", limit=3)
    assert len(results) >= 1
    assert all(isinstance(r, SearchResult) for r in results)


@pytest.mark.integration
async def test_exa_extract_live() -> None:
    """Real Exa extract. Requires EXA_API_KEY."""
    from src.tools.web_tools.providers.exa import ExaProvider

    provider = ExaProvider()
    if not provider.is_available():
        pytest.skip("EXA_API_KEY not set")

    results = await provider.extract(["https://example.com"])
    assert len(results) >= 1
    assert results[0].error is None
    assert len(results[0].content) > 0
