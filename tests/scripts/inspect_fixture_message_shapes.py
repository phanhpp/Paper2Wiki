"""Print how ``inputs["messages"]`` is shaped in ``tests/fixtures/runs.json``.

Does not print message bodies — only counts and a few IDs — so safe for large fixtures.

    uv run python tests/scripts/inspect_fixture_message_shapes.py
"""
from __future__ import annotations

import json
from pathlib import Path


def main() -> None:
    path = Path(__file__).parent / "fixtures" / "runs.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    flat = nested = missing = other = 0
    samples: list[tuple[str, str, str]] = []

    for r in data:
        rid = str(r.get("id", ""))
        inp = r.get("inputs") or {}
        msgs = inp.get("messages")
        if msgs is None:
            missing += 1
            continue
        if not isinstance(msgs, list) or not msgs:
            other += 1
            continue
        first = msgs[0]
        if isinstance(first, dict):
            flat += 1
            if len(samples) < 5:
                samples.append(("flat", rid, f"len={len(msgs)}"))
        elif isinstance(first, list):
            nested += 1
            if len(samples) < 10:
                samples.append(("nested", rid, f"outer={len(msgs)} inner0={len(first)}"))
        else:
            other += 1

    print(f"runs: {len(data)}")
    print(f"inputs.messages present, first elem dict (flat): {flat}")
    print(f"inputs.messages present, first elem list (nested batch): {nested}")
    print(f"inputs.messages missing/empty: {missing}")
    print(f"other (non-list or empty list): {other}")
    print("samples:")
    for row in samples:
        print(" ", row)


if __name__ == "__main__":
    main()
