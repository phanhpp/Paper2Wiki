"""LiteLLM proxy custom callback — one concise log line per request: cache HIT vs MISS.

The proxy doesn't print a clear cache-hit line by default (you'd infer it from response
headers / verbose logs). This callback reads ``kwargs["cache_hit"]`` (the same field the SDK
exposes) and prints e.g.:

    [cache] HIT  model=claude-haiku-4-5-20251001  0.42s
    [cache] miss model=claude-sonnet-4-6          2.91s

Wire it up in config.yaml (alongside any existing callbacks):

    litellm_settings:
      callbacks: ["prometheus", "cache_logger.cache_logger_instance"]

The file must be importable by the proxy, so mount it next to config.yaml in the container
(docker-compose: ``- ./cache_logger.py:/app/cache_logger.py``). Not part of the published package.
"""
from __future__ import annotations

from litellm.integrations.custom_logger import CustomLogger


class CacheHitLogger(CustomLogger):
    """Prints HIT/miss + model + latency (+ best-effort semantic similarity) per completion."""

    @staticmethod
    def _similarity(response_obj) -> float | None:
        """Best-effort: pull x-litellm-semantic-similarity if litellm exposed it on the response.

        It's surfaced in the response *header* (always) and sometimes in _hidden_params; the
        success-callback payload doesn't reliably carry it, so this may return None.
        """
        try:
            hp = getattr(response_obj, "_hidden_params", {}) or {}
            for blob in (hp, hp.get("additional_headers", {}) or {}):
                for k, v in blob.items():
                    if "semantic-similarity" in str(k):
                        return float(v)
        except Exception:
            pass
        return None

    def _line(self, kwargs, response_obj, start_time, end_time) -> str:
        hit = bool(kwargs.get("cache_hit"))
        model = kwargs.get("model", "?")
        secs = (end_time - start_time).total_seconds() if start_time and end_time else 0.0
        sim = self._similarity(response_obj)
        sim_str = f"  sim={sim:.3f}" if sim is not None else ""
        tag = "HIT " if hit else "miss"
        return f"[cache] {tag} model={model}  {secs:.2f}s{sim_str}"

    async def async_log_success_event(self, kwargs, response_obj, start_time, end_time):
        print(self._line(kwargs, response_obj, start_time, end_time), flush=True)

    def log_success_event(self, kwargs, response_obj, start_time, end_time):
        print(self._line(kwargs, response_obj, start_time, end_time), flush=True)


# Instance referenced from config.yaml: "cache_logger.cache_logger_instance"
cache_logger_instance = CacheHitLogger()
