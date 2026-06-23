# `gateway/` — LiteLLM proxy (learning build, NOT shipped)

A LiteLLM proxy (single container) fronting **managed** Postgres (Neon) + Redis Stack (Redis Cloud),
for learning multi-tenant RBAC, budgets, alerts, metrics, and semantic caching. **Not part of the
published package** — it lives outside `src/` (excluded by the wheel's `include = ["src*"]`) and runs
as a separate process. The app only *talks to it* over HTTP when `PAPER2WIKI_LLM_GATEWAY=litellm` is
set; it never imports `litellm`. Full design + rationale: `[docs/litellm/plan.md](../docs/litellm/plan.md)`.

## Progress


| Step | What                                                                            | Status                                                                                   |
| ---- | ------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------- |
| 1    | Boot the proxy — model routing + Postgres                                       | ✅ done                                                                                   |
| 2    | Seed tenants (Team → User → Key) + budget test                                  | ✅ done                                                                                   |
| 3    | App seam `_apply_gateway` in `src/llm_roles.py` (+ unit tests)                  | ✅ done                                                                                   |
| 4    | `config.yaml`: semantic cache, alerting, Prometheus metrics, LangSmith callback | ✅ done · fallbacks / rate limits / prompt-injection guardrail / app-side cache opt-in 🚧 |
| 5    | End-to-end through the agent with a virtual key                                 | ⬜ todo                                                                                   |


## Topology — single container + managed data stores (production-shaped)

The proxy runs as **one container**; the database and cache are **external managed services** (as
in production — you don't self-host Postgres/Redis):

- **Postgres** → Neon (or RDS). Persists virtual keys / teams / budgets / spend.
- **Redis Stack** → Redis Cloud. Must have the **Search/vector** module — the semantic cache
(`redis-semantic`) needs it; plain Redis won't work.

This is the `docker run litellm-database` (single container + external DB) method; the compose file
is just a one-service wrapper for env/volume ergonomics.

## What's configured (`config.yaml`)

Beyond model routing + DB, the proxy currently has:

- **Semantic cache** — `redis-semantic` on Redis Cloud (Search module), `mode: default_off` so
only requests that opt in are cached (the idempotent app roles — never the supervisor's
tool-calling turns), `similarity_threshold: 0.8`, embeddings via `text-embedding-3-small`.
- **Alerting** — `alerting: ["slack"]` with `alert_types` incl. `budget_alerts`, `llm_too_slow`,
`spend_reports`, outage/cooldown; anti-spam TTLs lowered for testing (`budget_alert_ttl: 1`).
Note `alerting_threshold: 0.05` (50 ms) fires an `llm_too_slow` alert on essentially every call.
- **Metrics** — Prometheus (`callbacks: ["prometheus"]`); `/metrics` requires the master key
(`require_auth_for_metrics_endpoint: true`).
- **Tracing** — `success_callback: ["langsmith"]`. ⚠️ Double-logs: LangChain already traces each
routed call app-side, so this logs it a second time — keep only if you want a separate
network-layer view in LangSmith.

## Step 1 — boot + verify

Prereqs: create a **Neon** Postgres DB and a **Redis Cloud** (Stack) instance; copy their
connection details into `.env`.

```bash
cd gateway
cp .env.example .env          # set LITELLM_MASTER_KEY, DATABASE_URL (Neon), REDIS_* (Redis Cloud),
                              # and real ANTHROPIC/OPENAI keys
docker compose up -d
docker compose logs -f litellm   # wait for "Application startup complete"
```

Equivalent without compose (the raw single-container command):

```bash
docker run -d --name litellm -p 4000:4000 --env-file .env \
  -v "$(pwd)/config.yaml:/app/config.yaml" \
  ghcr.io/berriai/litellm-database:main-latest \
  --config /app/config.yaml --port 4000
```

Verify, in order — **do not move to Step 2 until a raw curl returns a completion:**

```bash
# 1. proxy is up
curl -s http://localhost:4000/health/liveliness        # → "I'm alive!"

# 2. routing works end-to-end (master key acts as admin)
export $(cat .env | xargs) # load env to terminal first

curl -s http://localhost:4000/v1/chat/completions \
  -H "Authorization: Bearer $LITELLM_MASTER_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"claude-sonnet-4-6","messages":[{"role":"user","content":"say hi in 3 words"}]}'

# 3. embedding model works (needs OPENAI_API_KEY) — required for the semantic cache.
#    Catches a missing/empty OPENAI_API_KEY now, instead of as a recurring
#    "Malformed API Key ... Ensure Key has Bearer prefix" error from health checks later.
curl -s http://localhost:4000/v1/embeddings \
  -H "Authorization: Bearer $LITELLM_MASTER_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"text-embedding-3-small","input":"hello"}'   
# → JSON with a "data":[{"embedding":[...]}]

# 4. admin UI (login with the master key)
open http://localhost:4000/ui
```

If the chat call returns a completion **and** the embeddings call returns a vector, the model_list
aliases + provider keys (Anthropic *and* OpenAI) + Postgres wiring are all correct. If embeddings
returns `Malformed API Key ... Ensure Key has Bearer prefix`, your `OPENAI_API_KEY` is missing/empty
— fix it in `.env` and `docker compose up -d` before relying on the semantic cache. Tear down with
`docker compose down` (add `-v` to also drop the Postgres volume).

## Step 2 — seed tenants (Team → User → Key)

With the proxy up, create the tiers and per-user virtual keys:

```bash
# from gateway/, with .env loaded so LITELLM_MASTER_KEY is set
export $(grep -v '^#' .env | xargs)
python seed/create_tenants.py
```

Models real B2B multi-tenancy: a **tier template** = the rules, a **company** = a Team, an
**employee** = a Key under that team. The script seeds `AlphaCorp_Pro` (pro tier, 3 employees) and
`BetaLabs_Free` (free tier, 2 employees), printing a **virtual key per employee**. Edit
`TIER_TEMPLATES` / `COMPANIES_TO_SEED` at the top to change tenants. Verify: teams + keys show under
`:4000/ui`, and a chat call with a *virtual* key (not the master key) succeeds and logs spend.

> **Why a script, not curl?** It calls LiteLLM's management REST API (`POST /team/new` then
> `POST /key/generate`) with the master key — the same endpoints you'd curl. A script is used
> because the calls **chain** (the team's returned `team_id` feeds the key creation), the two tiers
> are declared as editable data, and it fails loudly with the proxy's error body. curl stays for
> one-off checks (health, a single completion, the RBAC-rejection test). The script only talks HTTP
> — no `litellm` import (`requests` is already a dep).

**Test budget enforcement (separate, opt-in):** `python seed/create_tenants.py --test-budget`
seeds as normal, then mints a **throwaway** `budget_test` key with a micro `soft_budget`/`max_budget`
and spams cheap calls until the proxy blocks it (4xx) — proving the cap and firing the budget alert
(watch Slack / webhook.site). Real tenants keep realistic budgets and are never touched.

### Testing alerts

Prereq: `alerting: ["slack"]` is set in `config.yaml`, so `SLACK_WEBHOOK_URL` must be in `.env`.
(No Slack? Use a throwaway [https://webhook.site](https://webhook.site) URL with `alerting: ["webhook"]` + `WEBHOOK_URL` to
just *see* the payloads.) `log_to_console: true` also prints every alert to the proxy logs, so you
can verify without leaving the terminal: `docker compose logs -f litellm`.

1. **Test the webhook plumbing first (no spend needed)** — LiteLLM's built-in alert tester
  ```bash
   curl -s "http://localhost:4000/health/services?service=slack" \
     -H "Authorization: Bearer $LITELLM_MASTER_KEY"
  ```
   If a message lands in Slack / webhook.site (or the logs), alerting is wired correctly.
2. **Budget alert** — `python seed/create_tenants.py --test-budget`. The throwaway key crosses
  `soft_budget` → **budget alert** (no block), then `max_budget` → **402**. Watch for the
   `budget_alerts` payload.
3. **Other alert types fire on their own:**
  - `llm_too_slow` — on essentially every call (`alerting_threshold: 0.05` = 50 ms).
  - `llm_exceptions` — a call with a bad/revoked key (401) or an off-plan model
  (a `free_tier` key requesting `claude-sonnet-4-6`).

Anti-spam TTLs are lowered (`budget_alert_ttl: 1`, etc.) so repeated tests aren't suppressed.

Then act as a tenant from the app:

```bash
curl -X POST 'http://0.0.0.0:4000/chat/completions' \
     -H 'Authorization: Bearer $USER_KEY' \
     -H 'Content-Type: application/json' \
     -d ' {
           "model": "claude-sonnet-4-6",
           "messages": [
             {
               "role": "user",
               "content": "hi"
             }
           ]
         }'
```

Or

```bash
PAPER2WIKI_LLM_GATEWAY=litellm LITELLM_API_KEY=<virtual-key> \
  uv run python -m src.cli.app chat 'hi in 3 words'
```

## Testing the semantic cache

`mode: default_off` means nothing caches unless the request opts in — so a plain repeated call
will **not** hit the cache. Pass the cache directive explicitly to test it:

```bash
# Two semantically-similar (not identical) prompts, both opting into the cache.
# The 2nd should return from cache → near-instant + an x-litellm-semantic-similarity header.
curl -i http://localhost:4000/v1/chat/completions \
  -H "Authorization: Bearer $LITELLM_MASTER_KEY" -H "Content-Type: application/json" \
  -d '{"model":"claude-haiku-4-5-20251001","cache":{"use-cache":true},
       "messages":[{"role":"user","content":"What is the capital of France?"}]}'

curl -i http://localhost:4000/v1/chat/completions \
  -H "Authorization: Bearer $LITELLM_MASTER_KEY" -H "Content-Type: application/json" \
  -d '{"model":"claude-haiku-4-5-20251001","cache":{"use-cache":true},
       "messages":[{"role":"user","content":"Tell me the capital city of France."}]}'
```

Confirm a hit by any of: the `**x-litellm-semantic-similarity**` response header (≥ `0.8`); the
2nd call returning much faster; `curl http://localhost:4000/cache/ping` healthy; and **no new
spend** for the 2nd call in `:4000/ui`. Lower `similarity_threshold` if near-matches miss.

## Remaining (see the plan)

- **Step 4 (partial)** — cache + alerting + metrics are wired (above). Still to add:
`router_settings` fallbacks (sonnet→haiku), per-tier rate limits (tpm/rpm), the
`detect_prompt_injection` guardrail, and the app-side cache opt-in
(`extra_body: {cache: {use-cache: true}}` on the idempotent roles in the app's `config.yaml`).
- **Step 5** — end-to-end through the agent with a virtual key.


## Operating the proxy — restart vs recreate

Each container reads **its own** file at startup, so restart the one whose file you changed:

| Changed | What to run | Why |
| --- | --- | --- |
| `config.yaml` (LiteLLM) | `docker compose restart litellm` | Volume-mounted, but LiteLLM parses it **at startup** — no hot reload; `restart` re-reads it (fast, no recreate). |
| `prometheus.yml` (scrape config) | `docker compose restart prometheus` | Prometheus reads its config at startup too (or `curl -X POST http://localhost:9090/-/reload` if started with `--web.enable-lifecycle`). |
| `.env` | `docker compose up -d` | env is injected at container **creation**, not live-mounted — `restart` won't pick up changes; you must recreate. |

So: **`config.yaml` → restart litellm, `prometheus.yml` → restart prometheus, `.env` → up -d.**
(If unsure, `up -d` always works — it just recreates the container.)

## Troubleshooting

**Flood of `llm_exceptions: Malformed API Key … Ensure Key has 'Bearer' prefix` every ~15 s.**
Not your LLM keys — it's the **Prometheus scraper hitting `/metrics` without valid auth**. With
`require_auth_for_metrics_endpoint: true`, every unauthenticated scrape → `401` → LiteLLM raises an
auth exception → and since `llm_exceptions` is in `alert_types`, it alerts on each one. The ~15 s
cadence is Prometheus's default `scrape_interval`. Confirm by pairing the alert timestamps with
`GET /metrics … 401` lines in `docker compose logs litellm`. Fix either way:
- **Auth the scraper** (keep `/metrics` private): in `prometheus.yml` set
  `bearer_token: <literal master key>` (or `bearer_token_file:`). **Not** `os.environ/…` — Prometheus
  has no env-var expansion, so it sends the literal string and still 401s. Then
  `docker compose restart prometheus`.
- **Or make `/metrics` public** (fine on localhost): `require_auth_for_metrics_endpoint: false` in
  `config.yaml`, then `docker compose restart litellm`.

Verify: `docker compose logs --since 1m litellm | grep /metrics` shows `200`, and
`http://localhost:9090/targets` lists the `litellm-proxy` job as **UP**.

**Recurring `Malformed API Key` but NO `/metrics` line?** Then it *is* a real provider call with a bad
key — check the embedding model's `OPENAI_API_KEY` with Step 1's embeddings curl.

**Every call fires `llm_too_slow` / `llm_requests_hanging`.** `alerting_threshold: 0.05` (50 ms) is a
test value; raise it (e.g. `30`, or `300` in prod) once you're done watching alerts fire.

**Config edit "didn't take."** You must restart the right container (table above), and you must edit
the file that's actually mounted — confirm with `docker compose exec litellm grep <key> /app/config.yaml`.
