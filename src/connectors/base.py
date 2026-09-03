"""The connector contract: what a source must provide, and what it must not do.

Phase 1 of the two-phase design. A connector hits a source, hands back raw items,
and exits — **no LLM anywhere**. Phase 2 (the agent) reads the dumps and writes
wiki pages. If the model writes a bad page, re-synthesis is free because the raw
data is already on disk.

**Connectors never write files.** They yield ``Item``s; everything below owns the
writing, the hashing, and the ledger. That is deliberate: a connector *cannot*
forget to record a content hash, because it never writes one. OpenWiki wrote the
same rule into a skill file and their own connectors then ignored it — nothing
enforced it. Structure enforces it here.

The ledger (``manifest.json``) records **one entry per item**, not per run, which
is what buys us four things OpenWiki's connector side has none of:

    content_hash    unchanged item -> skip it            (dedupe)
    deleted_at      it stopped appearing at the source   (deletion detection)
    cursor          where the next run resumes           (incremental fetch)
    synthesised_at  has this become a wiki page yet?     (the phase-1/2 boundary)

Without the last one, "what's new" is a matter of prompt convention and synthesis
cost grows with the size of the archive.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Protocol, runtime_checkable

from src.tools.hash_tools import compute_sha256

logger = logging.getLogger(__name__)

CONNECTORS_DIR = Path(__file__).resolve().parents[2] / "connectors"
MANIFEST_VERSION = 1


@dataclass(frozen=True)
class Item:
    """One thing fetched from a source.

    Note what is *not* here: ``content_hash`` and ``fetched_at``. Those are
    derived by :func:`run_fetch`, so no connector can omit or fake them.
    """

    id: str                       # stable at the source; the ledger key
    source_url: str
    payload: dict[str, Any]       # the response, as returned
    cursor: str | None = None     # this item's position, for resuming


@runtime_checkable
class Connector(Protocol):
    """Structural, like ``Renderer`` — no base class to inherit.

    ``fetch`` talks to the source and yields items. That is the whole contract;
    everything else (disk, hashing, the ledger, error isolation) is handled for
    you, so adding a source means writing one function.
    """

    name: str

    def fetch(self, config: dict[str, Any], cursor: str | None) -> Iterator[Item]:
        ...


@dataclass
class FetchResult:
    """What one fetch did, for the CLI to report."""

    connector: str
    new: int = 0
    unchanged: int = 0
    deleted: int = 0
    warnings: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return self.new + self.unchanged


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connector_dir(name: str, root: Path | None = None) -> Path:
    return (root or CONNECTORS_DIR) / name


def read_manifest(name: str, root: Path | None = None) -> dict[str, Any]:
    """Load the ledger, or an empty one. Never raises on a missing/corrupt file."""
    path = connector_dir(name, root) / "manifest.json"
    if not path.is_file():
        return {"version": MANIFEST_VERSION, "connector": name, "cursor": None,
                "last_run": None, "items": {}}
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        logger.warning("manifest for %s unreadable; starting fresh", name)
        return {"version": MANIFEST_VERSION, "connector": name, "cursor": None,
                "last_run": None, "items": {}}


def write_manifest(name: str, manifest: dict[str, Any], root: Path | None = None) -> None:
    """Write the ledger atomically — temp file then rename.

    Called after *every* item, not once at the end, so a crash mid-fetch leaves
    the items that completed properly recorded rather than losing the run.
    """
    directory = connector_dir(name, root)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "manifest.json"
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(manifest, indent=2, sort_keys=True))
    tmp.replace(path)


def run_fetch(
    connector: Connector,
    config: dict[str, Any],
    root: Path | None = None,
) -> FetchResult:
    """Run one connector: fetch, dedupe, write raw, update the ledger.

    Failures are isolated **per item**: one bad item is recorded as a warning and
    the rest continue. OpenWiki's Gmail connector loops without a try/except, so
    a single 403 on message 6 of 20 loses messages 1-5 as well; their X connector
    gets it right. Doing it here means no connector can repeat that mistake.
    """
    name = connector.name
    manifest = read_manifest(name, root)
    items: dict[str, Any] = manifest.setdefault("items", {})
    result = FetchResult(connector=name)

    raw_dir = connector_dir(name, root) / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)

    seen_ids: set[str] = set()
    previous_cursor = manifest.get("cursor")
    latest_cursor = previous_cursor

    for item in _iter_safely(connector, config, manifest.get("cursor"), result):
        seen_ids.add(item.id)
        try:
            body = json.dumps(item.payload, indent=2, sort_keys=True, default=str)
            content_hash = f"sha256:{compute_sha256.func(body)}"

            existing = items.get(item.id)
            if existing and existing.get("content_hash") == content_hash:
                result.unchanged += 1
                if existing.get("deleted_at"):
                    existing["deleted_at"] = None   # it came back
                continue

            rel_path = f"raw/{_safe_filename(item.id)}.json"
            (connector_dir(name, root) / rel_path).write_text(body)

            items[item.id] = {
                "path": rel_path,
                "source_url": item.source_url,
                "content_hash": content_hash,
                "fetched_at": _now(),
                "deleted_at": None,
                # Preserved across re-fetches: re-fetching does not un-synthesise.
                "synthesised_at": (existing or {}).get("synthesised_at"),
            }
            result.new += 1

            if item.cursor:
                latest_cursor = item.cursor

            # Per item, so a crash keeps what finished.
            manifest["cursor"] = latest_cursor
            write_manifest(name, manifest, root)

        except Exception as exc:                      # noqa: BLE001 - isolation is the point
            result.warnings.append(f"item {item.id}: {exc}")
            logger.exception("connector %s failed on item %s", name, item.id)

    # Only a full sweep can prove absence. A cursor-based run sees a window,
    # so an item simply outside it is not deleted.
    if previous_cursor is None:
        _mark_deleted(items, seen_ids, result)

    manifest["cursor"] = latest_cursor
    manifest["last_run"] = _now()
    write_manifest(name, manifest, root)
    return result


def _iter_safely(
    connector: Connector,
    config: dict[str, Any],
    cursor: str | None,
    result: FetchResult,
) -> Iterator[Item]:
    """Yield from the connector, turning a mid-stream failure into a warning.

    A generator that raises half way through has still produced real items; we
    keep them rather than discarding the run.
    """
    iterator = connector.fetch(config, cursor)
    while True:
        try:
            yield next(iterator)
        except StopIteration:
            return
        except Exception as exc:                      # noqa: BLE001
            result.warnings.append(f"fetch stopped early: {exc}")
            logger.exception("connector %s stopped early", connector.name)
            return


def _mark_deleted(items: dict[str, Any], seen_ids: set[str], result: FetchResult) -> None:
    """Flag items the source no longer returns.

    Only called on a full sweep (no cursor). See the guard at the call site.
    """
    if not seen_ids:
        return
    for item_id, record in items.items():
        if item_id not in seen_ids and not record.get("deleted_at"):
            record["deleted_at"] = _now()
            result.deleted += 1


def _safe_filename(item_id: str) -> str:
    """Make an item id safe as a filename without losing uniqueness."""
    safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in item_id)
    return safe[:120] or "item"
