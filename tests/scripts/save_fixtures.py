"""One-shot script to fetch raw LangSmith runs and save them as a JSON fixture.

Run manually when you need to refresh the fixture:
    uv run --env-file .env python -m tests.scripts.save_fixtures

Requires LANGSMITH_API_KEY to be set. The fixture is intentionally committed
so tests can run offline without hitting the API.

Serialization: Run objects are Pydantic models — saved via model_dump(mode="json")
which converts datetimes and UUIDs to strings. Loaded back as SimpleNamespace
objects (see conftest.py) since the processing code only uses dot-attribute access.
"""
import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from langsmith import AsyncClient

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "runs.json"


async def main() -> None:
    client = AsyncClient()
    runs = [
        run async for run in client.list_runs(
            project_name="any2wiki",
            start_time=datetime.now(timezone.utc) - timedelta(days=7),
            limit=100,
        )
    ]
    FIXTURE_PATH.parent.mkdir(parents=True, exist_ok=True)
    FIXTURE_PATH.write_text(
        json.dumps([r.model_dump(mode="json") for r in runs], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"Saved {len(runs)} runs to {FIXTURE_PATH}")


if __name__ == "__main__":
    asyncio.run(main())
