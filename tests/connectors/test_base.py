"""The connector contract: ledger, dedupe, deletion, error isolation.

No network anywhere. A fake connector yields whatever a test needs, so these
exercise `base.py`'s guarantees rather than any particular source.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.connectors.base import Connector, Item, read_manifest, run_fetch


class FakeConnector:
    """Yields a scripted list of items; optionally raises part way through."""

    name = "fake"

    def __init__(self, items, raise_after=None):
        self._items = items
        self._raise_after = raise_after

    def fetch(self, config, cursor):
        for index, item in enumerate(self._items):
            if self._raise_after is not None and index == self._raise_after:
                raise RuntimeError("source blew up")
            yield item


def _item(item_id: str, body: str = "v1", cursor: str | None = None) -> Item:
    return Item(id=item_id, source_url=f"https://x/{item_id}",
                payload={"body": body}, cursor=cursor)


@pytest.mark.unit
def test_first_run_writes_raw_and_ledger(tmp_path: Path):
    result = run_fetch(FakeConnector([_item("a"), _item("b")]), {}, tmp_path)

    assert (result.new, result.unchanged) == (2, 0)
    manifest = read_manifest("fake", tmp_path)
    assert set(manifest["items"]) == {"a", "b"}
    assert (tmp_path / "fake" / "raw" / "a.json").is_file()

    record = manifest["items"]["a"]
    assert record["content_hash"].startswith("sha256:")
    assert record["fetched_at"] and record["deleted_at"] is None
    assert record["synthesised_at"] is None      # phase 2 hasn't run


@pytest.mark.unit
def test_unchanged_item_is_skipped(tmp_path: Path):
    """Dedupe by content hash — OpenWiki's Gmail re-dumps 24h of mail every run."""
    run_fetch(FakeConnector([_item("a", "v1")]), {}, tmp_path)
    written_at = (tmp_path / "fake" / "raw" / "a.json").stat().st_mtime_ns

    result = run_fetch(FakeConnector([_item("a", "v1")]), {}, tmp_path)

    assert (result.new, result.unchanged) == (0, 1)
    assert (tmp_path / "fake" / "raw" / "a.json").stat().st_mtime_ns == written_at


@pytest.mark.unit
def test_changed_item_is_rewritten(tmp_path: Path):
    run_fetch(FakeConnector([_item("a", "v1")]), {}, tmp_path)
    result = run_fetch(FakeConnector([_item("a", "v2")]), {}, tmp_path)

    assert (result.new, result.unchanged) == (1, 0)
    body = json.loads((tmp_path / "fake" / "raw" / "a.json").read_text())
    assert body["body"] == "v2"


@pytest.mark.unit
def test_synthesised_at_survives_a_refetch(tmp_path: Path):
    """Re-fetching must not un-synthesise: it would re-do phase-2 work forever."""
    run_fetch(FakeConnector([_item("a", "v1")]), {}, tmp_path)

    manifest = read_manifest("fake", tmp_path)
    manifest["items"]["a"]["synthesised_at"] = "2026-01-01T00:00:00+00:00"
    (tmp_path / "fake" / "manifest.json").write_text(json.dumps(manifest))

    run_fetch(FakeConnector([_item("a", "v2")]), {}, tmp_path)     # content changed
    assert read_manifest("fake", tmp_path)["items"]["a"]["synthesised_at"] is not None


@pytest.mark.unit
def test_vanished_item_is_marked_deleted(tmp_path: Path):
    """OpenWiki tracks this nowhere — an item just stops appearing."""
    run_fetch(FakeConnector([_item("a"), _item("b")]), {}, tmp_path)
    result = run_fetch(FakeConnector([_item("a")]), {}, tmp_path)

    assert result.deleted == 1
    items = read_manifest("fake", tmp_path)["items"]
    assert items["b"]["deleted_at"] is not None
    assert items["a"]["deleted_at"] is None


@pytest.mark.unit
def test_a_returning_item_is_undeleted(tmp_path: Path):
    run_fetch(FakeConnector([_item("a"), _item("b")]), {}, tmp_path)
    run_fetch(FakeConnector([_item("a")]), {}, tmp_path)              # b vanishes
    run_fetch(FakeConnector([_item("a"), _item("b")]), {}, tmp_path)  # b returns

    assert read_manifest("fake", tmp_path)["items"]["b"]["deleted_at"] is None


@pytest.mark.unit
def test_deletion_is_not_inferred_from_a_windowed_fetch(tmp_path: Path):
    """A cursor-based run sees a window, so absence proves nothing."""
    run_fetch(FakeConnector([_item("a", cursor="c1"), _item("b", cursor="c2")]), {}, tmp_path)
    assert read_manifest("fake", tmp_path)["cursor"] == "c2"

    result = run_fetch(FakeConnector([_item("c", cursor="c3")]), {}, tmp_path)

    assert result.deleted == 0                                   # a and b are not gone
    assert read_manifest("fake", tmp_path)["items"]["a"]["deleted_at"] is None


@pytest.mark.unit
def test_cursor_round_trips(tmp_path: Path):
    assert read_manifest("fake", tmp_path)["cursor"] is None
    run_fetch(FakeConnector([_item("a", cursor="c1")]), {}, tmp_path)
    assert read_manifest("fake", tmp_path)["cursor"] == "c1"


@pytest.mark.unit
def test_a_failure_part_way_keeps_what_completed(tmp_path: Path):
    """OpenWiki's Gmail loses items 1-5 when item 6 throws. This must not."""
    connector = FakeConnector([_item("a"), _item("b"), _item("c")], raise_after=2)

    result = run_fetch(connector, {}, tmp_path)

    assert result.new == 2
    assert set(read_manifest("fake", tmp_path)["items"]) == {"a", "b"}
    assert any("blew up" in w for w in result.warnings)


@pytest.mark.unit
def test_manifest_is_written_per_item_not_at_the_end(tmp_path: Path):
    """So a crash mid-fetch leaves the completed items recorded.

    The connector reads the manifest from disk mid-stream; it can only see
    item "a" there if the write already happened.
    """
    seen = {}

    class Watching(FakeConnector):
        def fetch(self, config, cursor):
            yield _item("a")
            seen["mid_fetch"] = set(read_manifest("fake", tmp_path)["items"])
            yield _item("b")

    run_fetch(Watching([]), {}, tmp_path)
    assert seen["mid_fetch"] == {"a"}


@pytest.mark.unit
def test_a_corrupt_manifest_does_not_crash_the_run(tmp_path: Path):
    (tmp_path / "fake").mkdir()
    (tmp_path / "fake" / "manifest.json").write_text("{ not json")

    result = run_fetch(FakeConnector([_item("a")]), {}, tmp_path)
    assert result.new == 1


@pytest.mark.unit
def test_every_registered_connector_satisfies_the_protocol():
    from src.connectors import REGISTRY

    assert REGISTRY
    assert all(isinstance(c, Connector) for c in REGISTRY.values())
