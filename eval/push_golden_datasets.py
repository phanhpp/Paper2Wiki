"""Push golden dataset JSON files to LangSmith.

Idempotent — skips examples whose input message already exists in the dataset.
Run this whenever eval/golden_datasets/*.json changes (CI does this automatically
via path filter). Safe to re-run manually at any time.

Datasets pushed:
    paper2wiki-golden-ingest   ← eval/golden_datasets/ingest.json
    paper2wiki-golden-query    ← eval/golden_datasets/query.json
    paper2wiki-golden-marp     ← eval/golden_datasets/marp.json

Usage:
    uv run --env-file .env python eval/push_golden_datasets.py
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from langsmith import Client

GOLDEN_DIR = Path(__file__).parent / "golden_datasets"

DATASETS: list[tuple[str, Path]] = [
    ("paper2wiki-golden-ingest", GOLDEN_DIR / "ingest.json"),
    ("paper2wiki-golden-query",  GOLDEN_DIR / "query.json"),
    ("paper2wiki-golden-marp",   GOLDEN_DIR / "marp.json"),
]


def push_dataset(client: Client, name: str, cases_path: Path, dry_run: bool) -> dict:
    cases = json.loads(cases_path.read_text())

    if client.has_dataset(dataset_name=name):
        dataset = client.read_dataset(dataset_name=name)
        existing_messages = {
            ex.inputs.get("message", "")
            for ex in client.list_examples(dataset_id=dataset.id)
        }
    else:
        if not dry_run:
            dataset = client.create_dataset(
                dataset_name=name,
                description=f"Golden eval cases — {cases_path.name}",
                metadata={"source": str(cases_path.relative_to(Path(__file__).parents[1]))},
            )
        existing_messages = set()
        print(f"  {'[dry-run] would create' if dry_run else 'created'} dataset: {name}")

    new_examples = [
        c for c in cases
        if c["inputs"].get("message", "") not in existing_messages
    ]

    if not new_examples:
        print(f"  {name}: all {len(cases)} cases already present — skipped")
        return {"dataset": name, "total": len(cases), "new": 0, "skipped": len(cases)}

    if not dry_run:
        client.create_examples(
            dataset_id=dataset.id,
            examples=[
                {
                    "inputs":   c["inputs"],
                    "outputs":  c.get("outputs", {}),
                    "metadata": c.get("metadata", {}),
                }
                for c in new_examples
            ],
        )

    action = "[dry-run] would add" if dry_run else "added"
    print(f"  {name}: {action} {len(new_examples)} new / {len(cases)} total")
    return {"dataset": name, "total": len(cases), "new": len(new_examples), "skipped": len(cases) - len(new_examples)}


def main(dry_run: bool, dataset: str | None) -> int:
    client = Client()
    results = []
    any_error = False

    datasets = [(n, p) for n, p in DATASETS if dataset is None or n.endswith(dataset)]
    for name, path in datasets:
        if not path.exists():
            print(f"  WARNING: {path} not found — skipping {name}")
            continue
        try:
            result = push_dataset(client, name, path, dry_run)
            results.append(result)
        except Exception as exc:
            print(f"  ERROR pushing {name}: {exc}")
            any_error = True

    total_new = sum(r["new"] for r in results)
    print(f"\nDone: {total_new} new examples pushed across {len(results)} dataset(s)")
    return 1 if any_error else 0


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dry-run", action="store_true", help="Print what would be pushed without writing to LangSmith")
    p.add_argument("--dataset", choices=["ingest", "query", "marp"], help="Push only this dataset. Omit to push all.")
    args = p.parse_args()
    sys.exit(main(dry_run=args.dry_run, dataset=args.dataset))
