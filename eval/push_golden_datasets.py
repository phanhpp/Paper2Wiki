"""Push golden dataset JSON files to LangSmith.

Upsert strategy — preserves experiment history:
  - Dataset itself is never deleted (experiment results stay intact).
  - Each case has a stable ``metadata.id`` slug and a ``content_hash``.
  - On push: create new cases, update changed cases (hash mismatch),
    delete removed cases, skip unchanged cases.

Datasets pushed:
    paper2wiki-golden-ingest   ← eval/golden_datasets/ingest.json
    paper2wiki-golden-query    ← eval/golden_datasets/query.json
    paper2wiki-golden-marp     ← eval/golden_datasets/marp.json

Usage:
    uv run --env-file .env python eval/push_golden_datasets.py
    uv run --env-file .env python eval/push_golden_datasets.py --dataset marp
    uv run --env-file .env python eval/push_golden_datasets.py --dry-run

Exit codes:
    0 — success (or dry-run)
    1 — one or more datasets failed
"""

from __future__ import annotations

import argparse
import hashlib
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


def _content_hash(case: dict) -> str:
    """SHA-256 of inputs + outputs + metadata (excluding content_hash itself)."""
    payload = {
        "inputs":   case.get("inputs", {}),
        "outputs":  case.get("outputs", {}),
        "metadata": {k: v for k, v in case.get("metadata", {}).items() if k != "content_hash"},
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:16]


def push_dataset(client: Client, name: str, cases_path: Path, dry_run: bool) -> dict:
    """Upsert one LangSmith dataset from a golden JSON file.

    Keyed by metadata.id — creates new, updates changed (hash mismatch),
    deletes removed. Never deletes the dataset itself.
    """
    cases = json.loads(cases_path.read_text())

    # Inject content_hash into each case's metadata
    for c in cases:
        c["metadata"]["content_hash"] = _content_hash(c)

    # Ensure dataset exists
    if client.has_dataset(dataset_name=name):
        dataset = client.read_dataset(dataset_name=name)
    else:
        if not dry_run:
            dataset = client.create_dataset(
                dataset_name=name,
                description=f"Golden eval cases — {cases_path.name}",
                metadata={"source": str(cases_path.relative_to(Path(__file__).parents[1]))},
            )
            print(f"  created dataset: {name}")
        else:
            print(f"  [dry-run] would create dataset: {name}")
            return {"dataset": name, "total": len(cases), "new": len(cases), "updated": 0, "deleted": 0, "skipped": 0}

    # Build map of existing examples by metadata.id
    existing: dict[str, object] = {}
    for ex in client.list_examples(dataset_id=dataset.id):
        slug = (ex.metadata or {}).get("id")
        if slug:
            existing[slug] = ex

    json_by_id = {c["metadata"]["id"]: c for c in cases}

    created = updated = deleted = skipped = 0

    # Create or update
    for slug, case in json_by_id.items():
        if slug not in existing:
            if not dry_run:
                client.create_examples(
                    dataset_id=dataset.id,
                    examples=[{"inputs": case["inputs"], "outputs": case.get("outputs", {}),
                                "metadata": case["metadata"]}],
                )
            print(f"  {'[dry-run] would create' if dry_run else 'created'}: {slug}")
            created += 1
        else:
            ex = existing[slug]
            old_hash = (ex.metadata or {}).get("content_hash", "")
            new_hash = case["metadata"]["content_hash"]
            if old_hash != new_hash:
                if not dry_run:
                    client.delete_examples([ex.id])
                    client.create_examples(
                        dataset_id=dataset.id,
                        examples=[{"inputs": case["inputs"], "outputs": case.get("outputs", {}),
                                    "metadata": case["metadata"]}],
                    )
                print(f"  {'[dry-run] would update' if dry_run else 'updated'}: {slug}")
                updated += 1
            else:
                skipped += 1

    # Delete examples removed from JSON
    for slug, ex in existing.items():
        if slug not in json_by_id:
            if not dry_run:
                client.delete_examples([ex.id])
            print(f"  {'[dry-run] would delete' if dry_run else 'deleted'}: {slug}")
            deleted += 1

    print(f"  {name}: created={created} updated={updated} deleted={deleted} skipped={skipped}")
    return {"dataset": name, "total": len(cases), "new": created, "updated": updated,
            "deleted": deleted, "skipped": skipped}


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

    print(f"\nDone: {sum(r['new'] for r in results)} created, "
          f"{sum(r['updated'] for r in results)} updated, "
          f"{sum(r['deleted'] for r in results)} deleted "
          f"across {len(results)} dataset(s)")
    return 1 if any_error else 0


if __name__ == "__main__":
    from src.env import load_env

    load_env()
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dry-run", action="store_true", help="Preview without writing to LangSmith")
    p.add_argument("--dataset", choices=["ingest", "query", "marp"], help="Push only this dataset")
    args = p.parse_args()
    sys.exit(main(dry_run=args.dry_run, dataset=args.dataset))
