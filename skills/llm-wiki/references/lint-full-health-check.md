# Lint - Full Health Check

Full 12-step procedure. Run only when the user explicitly asks to lint / health-check / audit the wiki.

① Run `quick_wiki_integrity_check` tool which only checks:

- **Broken wikilinks:** use `files=None` (or no args) to scan wikilinks in all files
- **Frontmatter validation:** Every wiki page must have all required fields
   (title, created, updated, type, tags, sources). Tags must be in the taxonomy.

② **Orphan pages:** Find pages with no inbound `[[wikilinks]]` from other pages.

```python
# Use execute_code for this — programmatic scan across all wiki pages
import os, re
from collections import defaultdict
wiki = "<WIKI_PATH>"
# Scan all .md files in entities/, concepts/, comparisons/, queries/
# Extract all [[wikilinks]] — build inbound link map
# Pages with zero inbound links are orphans
```

③ **Index completeness:** Every wiki page should appear in `index.md`. Compare the filesystem against index entries.

④ **Stale content:** Pages whose `updated` date is >90 days older than the most
   recent source that mentions the same entities.

⑤ **Contradictions:** Pages on the same topic with conflicting claims. Look for
   pages that share tags/entities but state different facts. Surface all pages
   with `contested: true` or `contradictions:` frontmatter for user review.

⑥ **Quality signals:** List pages with `confidence: low` and any page that cites
   only a single source but has no confidence field set — these are candidates
   for either finding corroboration or demoting to `confidence: medium`.

⑦ **Source drift:** For each file in `raw/` with a `sha256:` frontmatter, recompute
   the hash and flag mismatches. Mismatches indicate the raw file was edited
   (shouldn't happen — raw/ is immutable) or ingested from a URL that has since
   changed. Not a hard error, but worth reporting.

⑧ **Page size:** Flag pages over 200 lines — candidates for splitting.

⑨ **Tag audit:** List all tags in use, flag any not in the SCHEMA.md taxonomy.

⑩ **Log rotation:** If log.md exceeds 500 entries, rotate it.

⑪ **Report findings** with specific file paths and suggested actions, grouped by
   severity (broken links > orphans > source drift > contested pages > stale content > style issues).

⑫ **Append to log.md:** `## [YYYY-MM-DD] lint | N issues found`
