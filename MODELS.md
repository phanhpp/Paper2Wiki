# Choosing and configuring models

Everything about which LLM runs, where the request goes, and how to check before you spend
anything.

**The one command to know:**

```bash
paper2wiki config show
```

It prints every task's model, provider and endpoint — resolved exactly the way a real run
resolves them, with no API call. Use it after every change in this document.

---

## Quick start

Pick one model, set that provider's key, done. Everything else here is for when you want
one task to differ, or a non-default endpoint.

```yaml
# config.yaml
model:
  default: claude-sonnet-4-6
```

```bash
# .env
ANTHROPIC_API_KEY=sk-ant-...
```

```bash
paper2wiki config show      # confirm before running anything
```

---

## Provider recipes

Each block goes in `config.yaml`. After each one, run `paper2wiki config show` and check
the **Provider** and **Endpoint** columns say what you expect.

### Anthropic

```yaml
model:
  default: claude-sonnet-4-6
```
```bash
ANTHROPIC_API_KEY=sk-ant-...
```

### OpenAI

```yaml
model:
  default: openai:gpt-4o
```
```bash
OPENAI_API_KEY=sk-...
```

### Google Gemini

```yaml
model:
  default: google_genai:gemini-2.0-flash
```
```bash
GOOGLE_API_KEY=...
```

### OpenRouter — one key, most models

OpenRouter is OpenAI-compatible, so it is `provider: openai` plus a `base_url`. **No extra
package to install.**

```yaml
model:
  default: anthropic/claude-sonnet-4.5   # OpenRouter uses provider/model with a slash
  provider: openai
  base_url: https://openrouter.ai/api/v1
  api_key: sk-or-...
```

`config show` should read:

```
supervisor  anthropic/claude-sonnet-4.5  openai  https://openrouter.ai/api/v1
```

That row is the whole point of the endpoint column: the model is a Claude model, but the
request goes to OpenRouter, not to Anthropic.

Switch models within OpenRouter without editing the file:

```bash
paper2wiki chat "hi" -m openai/gpt-4o
paper2wiki chat "hi" -m google/gemini-2.5-flash
```

### Ollama — local

First pull a model that runs on your machine:

```bash
ollama pull llama3.2
ollama list          # confirm it is there and NOT a ":cloud" entry
```

```yaml
model:
  default: llama3.2
  provider: openai                       # Ollama serves an OpenAI-compatible API
  base_url: http://localhost:11434/v1
  api_key: ollama                        # required by the client, ignored by the server
```

No key, no cost, works offline. `ollama serve` must be running.

### Ollama Cloud

Models listed as `something:cloud` do **not** run locally — they run on ollama.com and need
a key.

```yaml
model:
  default: qwen3.5:cloud
  provider: openai
  base_url: https://ollama.com/v1
  api_key: <your ollama.com key>
```

> The `:cloud` suffix is a **model tag**, not a `provider:model` prefix. `qwen3.5` is not a
> provider name, so it is left alone — see *Troubleshooting* below for when a colon **is**
> treated as a provider.

### LiteLLM gateway

If you run the optional proxy in `gateway/`, route through it with an env var instead of
config:

```bash
PAPER2WIKI_LLM_GATEWAY=litellm
LITELLM_BASE_URL=http://localhost:4000     # optional, this is the default
LITELLM_API_KEY=sk-virtual-key-...
```

Every task then goes to the proxy, which handles the real provider keys, budgets and
fallbacks. See `gateway/README.md`.

---

## Which model a task ends up using

Five levels. **The first one that exists wins:**

```
Task-Specific Env Var → Task Config → Global Env Var → Base Config → Default Fallback
```

| Level | Where | Example | Applies to |
|---|---|---|---|
| **1 · Task-Specific Env Var** | `.env` | `PAPER2WIKI_MODEL_SUMMARIZE=openai:gpt-4o-mini` | one task |
| **2 · Task Config** | `config.yaml` | `auxiliary.summarize.model: openai:gpt-4o-mini` | one task |
| **3 · Global Env Var** | `.env`, or `-m` | `PAPER2WIKI_MODEL=openai:gpt-4o` | every task |
| **4 · Base Config** | `config.yaml` | `model.default: openai:gpt-4o` | every task |
| **5 · Default Fallback** | built in | `claude-sonnet-4-6` | every task |

The pattern: **task beats global, and env beats config.**

The six tasks: `supervisor`, `subagent`, `title`, `summarize`, `judge`, `web_summarize`.

### The `-m` / `--model` flag

`-m` writes `PAPER2WIKI_MODEL` for that one run, so it lands at **level 3**.

```bash
paper2wiki repl -m openai:gpt-4o
paper2wiki chat "summarise this" -m openai:gpt-4o
paper2wiki config show -m openai:gpt-4o        # preview only, no API call
paper2wiki serve -m openai:gpt-4o
```

It must come **after** a command — `paper2wiki -m openai:gpt-4o` alone is an error.

**It does not override a task pinned in `config.yaml`.** With `auxiliary.judge.model` set,
`-m` moves every other task and leaves `judge` alone:

```bash
paper2wiki config show -m openai:gpt-4o
#   supervisor  openai:gpt-4o              ← the flag
#   judge       claude-haiku-4-5-20251001  ← auxiliary.judge.model won
```

That is deliberate, and copied from hermes-agent: side tasks are pinned for a reason —
cheap summarisation, vision — and one flag silently retargeting them would break them.
To change a pinned task too, use its own env var:

```bash
PAPER2WIKI_MODEL_JUDGE=openai:gpt-4o paper2wiki config show -m openai:gpt-4o
```

**`-m` sets only the model, never the endpoint.** With OpenRouter configured, `-m` switches
models *within* OpenRouter. To change endpoint, edit `config.yaml`.

---

## Per-task models

Any task can have its own model, provider, endpoint and key:

```yaml
model:
  default: claude-sonnet-4-6            # the supervisor: the model that does the thinking

auxiliary:
  title:                                 # naming a session — trivial, use something cheap
    model: claude-haiku-4-5-20251001
  summarize:
    model: claude-haiku-4-5-20251001
  judge:                                 # eval judge — can be a different provider entirely
    model: openai:gpt-4o-mini
    api_key: sk-...                      # its own key, if you like
```

Which fields inherit from the `model:` block when a task omits them:

| field | inherits? |
|---|---|
| `model` | yes |
| `provider` | yes |
| `base_url` | yes |
| `api_key` | yes |
| `timeout` | **no** — task-only |
| `extra_body` | **no** — task-only |

Setting `model.timeout` does not give it to the tasks; only a task's own block does.

---

## Checking without spending anything

`config show` reads config the same way a run does, so it is a free dry run. Preview an
override before committing to it:

```bash
paper2wiki config show                                  # what runs today
paper2wiki config show -m openai:gpt-4o                 # what the flag would change
PAPER2WIKI_CONFIG=/tmp/try.yaml paper2wiki config show  # try a whole config, safely
```

`PAPER2WIKI_CONFIG` points at any file, so you can test a config without touching your real
one:

```bash
TMP=$(mktemp -d)
cat > "$TMP/c.yaml" <<'YAML'
model:
  default: anthropic/claude-sonnet-4.5
  provider: openai
  base_url: https://openrouter.ai/api/v1
YAML
PAPER2WIKI_CONFIG=$TMP/c.yaml paper2wiki config show
```

Read the **Provider** and **Endpoint** columns, not just the model. `provider default`
means the provider's own API; anything else is where your requests are really going.

---

## Troubleshooting

### `404 not_found_error — model: openai:gpt-4o`

The request went to the **wrong provider**. The error comes from Anthropic, complaining
about a model name that is obviously OpenAI's — meaning the provider was pinned to
`anthropic` while the model said `openai`.

Check the Provider column:

```bash
paper2wiki config show
```

If Model and Provider disagree, one of them is wrong. An explicit `provider:` prefix in the
model string now wins over `model.provider` in config, so this should not recur — but a
stale `provider:` line in `config.yaml` is still worth removing if you have switched
providers.

### `The api_key client option must be set`

The provider's key is missing from `.env`. Which key depends on the model, not on Anthropic:

| model looks like | needs |
|---|---|
| `claude-…`, `anthropic:…` | `ANTHROPIC_API_KEY` |
| `gpt-…`, `openai:…` | `OPENAI_API_KEY` |
| `gemini-…`, `google_genai:…` | `GOOGLE_API_KEY` |
| `groq:…` | `GROQ_API_KEY` |
| anything with a `base_url` | that endpoint's key, in `api_key:` |

### `insufficient_quota` / `credit_balance_exhausted`

Routing is correct — the account is out of credit. This error naming the right provider is
actually a good sign.

### `ModuleNotFoundError: No module named 'langchain_ollama'`

Native Ollama support is not installed. Use the OpenAI-compatible recipe above
(`provider: openai` + `base_url`) — no extra package needed. Or `uv add langchain-ollama`
if you would rather use `ollama:model` directly.

### `Unable to infer model provider`

The model name matches no known provider and none was configured. Either use the
`provider:model` form (`openai:gpt-4o`) or set `provider:` explicitly.

### A task ignores `-m`

It has its own `auxiliary.<task>.model`. Expected — see the flag section above. Override it
with `PAPER2WIKI_MODEL_<TASK>`, or remove the pin from `config.yaml`.

---

## Related

- `README.md` → **Using the CLI** — every command and flag
- `config.example.yaml` — every option, commented
- `gateway/README.md` — the optional LiteLLM proxy
- `src/llm_roles.py` — the resolver, if you want the exact rules in code
