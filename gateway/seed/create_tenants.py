#!/usr/bin/env python3
"""Seed LiteLLM multi-tenant state: Companies → Teams, employees → Virtual Keys.

Models real B2B multi-tenancy:
  - a TIER template = the reusable rules (allowed models, team budget, per-user budget)
  - a Company       = one Team (gets the tier's allowlist + budget)
  - an employee      = one Virtual Key under that team (its own sub-budget, shared team cap)

Run AFTER the proxy is up (Step 1). `requests` is already a project dep, so:

    # from the gateway/ dir, with gateway/.env loaded:
    export $(grep -v '^#' .env | xargs)
    python seed/create_tenants.py

Reads LITELLM_MASTER_KEY (admin auth) and optional LITELLM_PROXY_URL (default
http://localhost:4000). Prints every employee's virtual key — drop one into LITELLM_API_KEY to act
as that user. All RBAC/billing lives at the proxy; no agent code is touched.
"""
from __future__ import annotations

import os
import sys

import requests

PROXY_URL = os.environ.get("LITELLM_PROXY_URL", "http://localhost:4000").rstrip("/")
MASTER_KEY = os.environ.get("LITELLM_MASTER_KEY")

# 1. Tier templates — the rules each subscription level enforces.
#    `models` must match the model_name aliases in gateway/config.yaml.
TIER_TEMPLATES = {
    "free_tier": {
        "models": ["claude-haiku-4-5-20251001"],   # Haiku only
        "max_budget": 50.0,                         # team cap (USD) — realistic
        "key_budget": 2.0,                          # per-user cap (USD)
        "budget_duration": "1mo",                   # resets monthly
    },
    "pro_tier": {
        "models": ["claude-sonnet-4-6", "claude-haiku-4-5-20251001"],
        "max_budget": 500.0,
        "key_budget": 50.0,
        "budget_duration": "1mo",
    },
}

# 2. The customers to provision (in reality: read from your signup DB).
COMPANIES_TO_SEED = [
    {
        "company_name": "AlphaCorp_Pro",
        "tier": "pro_tier",
        "employees": ["alice@alpha.com", "bob@alpha.com", "developer@alpha.com"],
    },
    {
        "company_name": "BetaLabs_Free",
        "tier": "free_tier",
        "employees": ["intern@beta.com"],
    },
]


def _post(path: str, payload: dict) -> dict:
    if not MASTER_KEY:
        sys.exit("LITELLM_MASTER_KEY not set — `export $(grep -v '^#' .env | xargs)` first.")
    resp = requests.post(
        f"{PROXY_URL}{path}",
        headers={"Authorization": f"Bearer {MASTER_KEY}", "Content-Type": "application/json"},
        json=payload,
        timeout=30,
    )
    if not resp.ok:
        sys.exit(f"POST {path} failed [{resp.status_code}]: {resp.text}")
    return resp.json()

def make_test_key() -> str:
    """Mint a THROWAWAY team+key with a micro budget, purely to exercise the budget guardrails.

    Kept entirely separate from the real tenants so seeding never caps a real user.
    soft_budget cross → budget alert (no block); max_budget cross → 402 hard block.
    """
    team = _post("/team/new", {
        "team_alias": "budget_test",
        "models": ["claude-haiku-4-5-20251001"],
        "max_budget": 0.0001,
    })
    key = _post("/key/generate", {
        "user_id": "budget-test@example.com",
        "team_id": team["team_id"],
        "soft_budget": 0.00005,   # cross → budget ALERT (no block)
        "max_budget": 0.0001,     # cross → 402 hard block
        "budget_duration": "1mo",
    })
    return key["key"]


def test_budget_block(key: str, n: int = 6) -> None:
    """Spam cheap calls until the proxy enforces the cap (4xx). Crossing also fires the budget
    alert — confirm that in Slack / webhook.site; this only asserts the block."""
    import time

    import openai
    # Will show in alert as openAI not the model name: third call expect 429 (Hard buget alert and llm alert for exceeding threshold)
    client = openai.OpenAI(api_key=key, base_url=PROXY_URL)
    for i in range(1, n + 1):
        try:
            client.chat.completions.create(
                model="claude-haiku-4-5-20251001",
                messages=[{"role": "user", "content": "hi"}],
            )
            print(f"   call {i}: ok (still under budget)")
        except openai.APIStatusError as e:
            print(f"   call {i}: BLOCKED [{e.status_code}] — budget enforced ✅")
            return
        time.sleep(1)  # let the spend update flush before the next check
    print("   never blocked — spend may not have flushed; check :4000/ui")


def main() -> None:
    print(f"Seeding multi-tenant architecture on {PROXY_URL}\n")

    for company in COMPANIES_TO_SEED:
        rules = TIER_TEMPLATES[company["tier"]]  # rules for this company's subscription tier

        # One Team per company — carries the tier's allowlist + budget.
        team = _post("/team/new", {
            "team_alias": company["company_name"],
            "models": rules["models"],
            "max_budget": rules["max_budget"],
        })
        team_id = team["team_id"]
        print(f"🏢 {company['company_name']}  [{company['tier']}]  team_id={team_id}")

        # One Virtual Key per employee — own sub-budget, shares the team cap.
        for email in company["employees"]:
            key = _post("/key/generate", {
                "user_id": email,
                "team_id": team_id,
                "max_budget": rules["key_budget"],
                "budget_duration": rules["budget_duration"],
            })
            print(f"   🔑 {email:24} {key['key']}")

        print("-" * 60)


    print("\nUse any KEY above as that user:")
    print("  PAPER2WIKI_LLM_GATEWAY=litellm LITELLM_API_KEY=<key> \\")
    print("    uv run python -m src.cli.app chat 'hi in 3 words'")
    print("\nNote: re-running creates NEW teams (team_alias is not deduped). Delete stale ones in :4000/ui.")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Seed LiteLLM tenants; optionally exercise budget enforcement."
    )
    parser.add_argument(
        "--test-budget",
        action="store_true",
        help="after seeding, mint a throwaway micro-budget key and prove the 402 block + alert",
    )
    args = parser.parse_args()

    main()
    if args.test_budget:
        print("\n=== Budget block + alert test (throwaway key) ===")
        test_budget_block(make_test_key())
