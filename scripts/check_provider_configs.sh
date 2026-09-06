#!/usr/bin/env bash
# Check every provider recipe in MODELS.md resolves correctly — no API calls, no keys.
#
# `config show` resolves models exactly as a real run does but never contacts a provider,
# so this exercises the whole resolution path for free: prefix parsing, provider
# inference, endpoint routing, and which precedence level won.
#
#   ./scripts/check_provider_configs.sh
#
# Each case asserts the Model, Provider and Endpoint a run would actually use. A wrong
# Provider is what produced `404 not_found_error: model: openai:gpt-4o` — the model name
# alone never reveals it.

set -uo pipefail
cd "$(dirname "$0")/.."

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
PASS=0; FAIL=0

# run <name> <yaml> <expected-substrings...>
run() {
  local name=$1 yaml=$2; shift 2
  printf '%s\n' "$yaml" > "$TMP/c.yaml"
  local out
  out=$(ANY2WIKI_CONFIG="$TMP/c.yaml" COLUMNS=200 \
        env -u ANY2WIKI_MODEL -u ANY2WIKI_MODEL_SUPERVISOR \
        uv run python -m src.cli.app config show 2>/dev/null \
        | grep supervisor | tr -s ' ')

  local missing=()
  for want in "$@"; do
    [[ "$out" == *"$want"* ]] || missing+=("$want")
  done

  if [ ${#missing[@]} -eq 0 ]; then
    printf '  \033[32mok\033[0m   %-22s %s\n' "$name" "$(echo "$out" | cut -c1-78)"
    PASS=$((PASS+1))
  else
    printf '  \033[31mFAIL\033[0m %-22s missing: %s\n' "$name" "${missing[*]}"
    printf '       got: %s\n' "$out"
    FAIL=$((FAIL+1))
  fi
}

echo
echo "Provider recipes from MODELS.md — resolution only, no API calls"
echo

run "anthropic" \
  'model:
  default: claude-sonnet-4-6' \
  "claude-sonnet-4-6" "anthropic"

run "openai" \
  'model:
  default: openai:gpt-4o' \
  "openai:gpt-4o" "openai"

run "gemini" \
  'model:
  default: google_genai:gemini-2.0-flash' \
  "gemini-2.0-flash" "google_genai"

run "groq" \
  'model:
  default: groq:llama-3.3-70b-versatile' \
  "groq"

run "openrouter" \
  'model:
  default: anthropic/claude-sonnet-4.5
  provider: openai
  base_url: https://openrouter.ai/api/v1' \
  "claude-sonnet-4.5" "openai" "openrouter.ai"

run "ollama local" \
  'model:
  default: llama3.2
  provider: openai
  base_url: http://localhost:11434/v1' \
  "llama3.2" "openai" "localhost:11434"

run "ollama cloud" \
  'model:
  default: qwen3.5:cloud
  provider: openai
  base_url: https://ollama.com/v1' \
  "qwen3.5:cloud" "openai" "ollama.com"

echo
echo "Edge cases the resolver has to get right"
echo

# The bug that produced a 404 from Anthropic for an OpenAI model name.
run "prefix beats provider" \
  'model:
  default: openai:gpt-4o
  provider: anthropic' \
  "openai:gpt-4o" "openai"

# ":cloud" is a model tag, not a provider prefix — the config provider must survive.
run "colon is not a prefix" \
  'model:
  default: qwen3.5:cloud
  provider: openai
  base_url: https://ollama.com/v1' \
  "qwen3.5:cloud" "openai"

# A task pinned in config must beat the global default.
printf 'model:\n  default: claude-sonnet-4-6\nauxiliary:\n  judge:\n    model: openai:gpt-4o-mini\n' > "$TMP/c.yaml"
judge=$(ANY2WIKI_CONFIG="$TMP/c.yaml" COLUMNS=200 uv run python -m src.cli.app config show 2>/dev/null | grep judge | tr -s ' ')
if [[ "$judge" == *"gpt-4o-mini"* && "$judge" == *"auxiliary.judge.model"* ]]; then
  printf '  \033[32mok\033[0m   %-22s %s\n' "task pin wins" "$(echo "$judge" | cut -c1-78)"; PASS=$((PASS+1))
else
  printf '  \033[31mFAIL\033[0m %-22s %s\n' "task pin wins" "$judge"; FAIL=$((FAIL+1))
fi

# ...but a task env var beats even that, and the table must say so.
judge=$(ANY2WIKI_CONFIG="$TMP/c.yaml" ANY2WIKI_MODEL_JUDGE=env-wins COLUMNS=200 \
        uv run python -m src.cli.app config show 2>/dev/null | grep judge | tr -s ' ')
if [[ "$judge" == *"env-wins"* && "$judge" == *"env"* ]]; then
  printf '  \033[32mok\033[0m   %-22s %s\n' "env beats task pin" "$(echo "$judge" | cut -c1-78)"; PASS=$((PASS+1))
else
  printf '  \033[31mFAIL\033[0m %-22s %s\n' "env beats task pin" "$judge"; FAIL=$((FAIL+1))
fi

echo
echo "  $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
