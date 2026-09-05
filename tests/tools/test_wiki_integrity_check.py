"""Unit tests for ``wiki_integrity_check`` frontmatter and wikilink rules on a temp wiki."""
from __future__ import annotations

from pathlib import Path

import pytest

import src.tools.wiki_integrity_check as wiki_check


def _write_page(path: Path, body: str) -> None:
    """Create parent dirs and write a markdown page under the fake wiki root."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


@pytest.mark.unit
def test_run_wiki_integrity_ok_for_valid_page(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Valid frontmatter and resolving ``[[wikilink]]`` yield ``wiki-check: OK``."""
    monkeypatch.setattr(wiki_check, "WIKI_ROOT", tmp_path)

    _write_page(
        tmp_path / "concepts" / "attention.md",
        """---
title: Attention
created: 2026-01-01
updated: 2026-01-02
type: concept
tags: [nlp, transformer]
sources: [raw/papers/attention.pdf]
---

Reference to [[Self Attention]].
""",
    )
    _write_page(
        tmp_path / "concepts" / "self-attention.md",
        """---
title: Self Attention
created: 2026-01-01
updated: 2026-01-02
type: concept
tags: [nlp]
sources: [raw/papers/attention.pdf]
---
""",
    )

    summary = wiki_check.run_wiki_integrity_check()
    assert summary == "wiki-check: OK"


@pytest.mark.unit
def test_run_wiki_integrity_detects_broken_link(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Unknown ``[[slug]]`` target produces an error in the summary string."""
    monkeypatch.setattr(wiki_check, "WIKI_ROOT", tmp_path)

    _write_page(
        tmp_path / "concepts" / "broken.md",
        """---
title: Broken
created: 2026-01-01
updated: 2026-01-02
type: concept
tags: [test]
sources: [raw/papers/x.pdf]
---

This points to [[Missing Page]].
""",
    )

    summary = wiki_check.run_wiki_integrity_check()
    assert "error(s)" in summary
    assert "broken wikilink" in summary


@pytest.mark.unit
@pytest.mark.parametrize(
    ("frontmatter", "expected_error"),
    [
        (
            """---
title: Missing Sources
created: 2026-01-01
updated: 2026-01-02
type: concept
tags: [test]
---""",
            "missing frontmatter fields",
        ),
        (
            """---
title: Bad Type
created: 2026-01-01
updated: 2026-01-02
type: note
tags: [test]
sources: [raw/papers/x.pdf]
---""",
            "invalid type",
        ),
        (
            """---
title: Bad Date
created: 01-01-2026
updated: 2026-01-02
type: concept
tags: [test]
sources: [raw/papers/x.pdf]
---""",
            "invalid created date",
        ),
        (
            """---
title: Bad Tags
created: 2026-01-01
updated: 2026-01-02
type: concept
tags: test
sources: [raw/papers/x.pdf]
---""",
            "tags must be a list",
        ),
        (
            """---
title: Bad Sources
created: 2026-01-01
updated: 2026-01-02
type: concept
tags: [test]
sources: raw/papers/x.pdf
---""",
            "sources must be a list",
        ),
    ],
)
def test_run_wiki_integrity_detects_invalid_frontmatter(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    frontmatter: str,
    expected_error: str,
) -> None:
    """Verify malformed wiki metadata fails fast with specific frontmatter errors.

    These cases guard the schema contract the agent depends on when creating or
    updating pages: required fields, allowed page types, ISO dates, and list-like
    ``tags`` / ``sources``.
    """
    monkeypatch.setattr(wiki_check, "WIKI_ROOT", tmp_path)
    _write_page(tmp_path / "concepts" / "bad.md", frontmatter)

    summary = wiki_check.run_wiki_integrity_check()

    assert "error(s)" in summary
    assert expected_error in summary


@pytest.mark.unit
def test_run_wiki_integrity_ignores_links_inside_html_comments(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify disabled wikilinks inside HTML comments are not linted.

    This lets authors temporarily comment out draft links without causing a
    false broken-link failure in the wiki integrity check.
    """
    monkeypatch.setattr(wiki_check, "WIKI_ROOT", tmp_path)
    _write_page(
        tmp_path / "concepts" / "commented.md",
        """---
title: Commented
created: 2026-01-01
updated: 2026-01-02
type: concept
tags: [test]
sources: [raw/papers/x.pdf]
---

<!-- [[Missing Page]] -->
""",
    )

    assert wiki_check.run_wiki_integrity_check() == "wiki-check: OK"


@pytest.mark.unit
def test_quick_wiki_integrity_check_converts_virtual_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Tool paths like ``/wiki/concepts/x.md`` map to real paths under ``WIKI_ROOT``."""
    monkeypatch.setattr(wiki_check, "WIKI_ROOT", tmp_path)
    target = tmp_path / "concepts" / "x.md"
    _write_page(
        target,
        """---
title: X
created: 2026-01-01
updated: 2026-01-02
type: concept
tags: [x]
sources: [raw/papers/x.pdf]
---
""",
    )

    captured: dict[str, list[Path] | None] = {"files": None}

    def _fake_run(files: list[Path] | None = None) -> str:
        captured["files"] = files
        return "wiki-check: OK"

    monkeypatch.setattr(wiki_check, "run_wiki_integrity_check", _fake_run)
    wiki_check.quick_wiki_integrity_check.invoke({"files": ["/wiki/concepts/x.md"]})

    assert captured["files"] == [target]
