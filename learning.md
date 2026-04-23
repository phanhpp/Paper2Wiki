# Learning notes — wiki lint and paths

## How `lint.py` resolves wikilinks

1. **`all_slugs` is built from the entire wiki tree**  
   The linter collects every Markdown file under the wiki directory (recursively) and turns each path into a **slug** using `pathlib.Path.stem` (see below). That set is built **once** per `run_lint()` call, before any per-file checks.

2. **Per-file loop only finds links in that file**  
   For each page, `re.findall` collects `[[...]]` wikilinks in that file’s text. Each target is normalized (trim, lowercase, spaces to hyphens) and checked with `slug not in all_slugs`. So the **target list** is global (whole wiki); the **source** of links is whichever files you pass in (full run vs scoped).

3. **Globally unique slugs by filename**  
   Slugs are **only** the filename stem, not the folder. Two files `wiki/concepts/foo.md` and `wiki/papers/foo.md` would both contribute `foo` once to the set. The wiki convention is to avoid stem collisions across directories.

## What `p.stem` means (`pathlib`)

For a path `p` pointing at `wiki/papers/attention_is_all_you_need.md`:

| Attribute   | Value |
|------------|--------|
| `p.name`   | `attention_is_all_you_need.md` |
| `p.suffix` | `.md` |
| `p.stem`   | `attention_is_all_you_need` |

`p.stem` is the filename **without** the last extension. The linter treats that as the canonical slug for “does `[[attention_is_all_you_need]]` resolve?” (after the same normalization applied to the link text).

## Wiki directory and Jupyter / `notebooks/`

**Problem:** If `WIKI_DIR` were `Path("wiki")`, it would be relative to the **process current working directory**. With Jupyter often started with cwd = `notebooks/`, Python would look for `notebooks/wiki/`, which usually does not exist, so `all_slugs` would be empty and every wikilink would look broken.

**Fix (in code):** `lint.py` sets:

- `REPO_ROOT` from `Path(__file__).resolve().parents[2]` (the repo root that contains `src/`),
- `WIKI_DIR = REPO_ROOT / "wiki"`.

So **slug collection always uses `<repo>/wiki/`**, regardless of whether you run the CLI from the repo root, from `notebooks/`, or call `run_lint()` from a notebook.

**Notebook usage:**

- Add the repo to `sys.path` so `from src.tools.lint import run_lint` works (or install the project in editable mode).
- You do **not** need `os.chdir(repo)` for wikilink resolution to find the right wiki.
- For **scoped** lint, paths in `files=[...]` are still plain `Path(...)` strings: relative paths resolve against **cwd**, not `REPO_ROOT`. Prefer absolute paths, e.g. `repo / "wiki/papers/attention_is_all_you_need.md"`, when cwd is `notebooks/`.

## Related checks in `lint.py`

- **`index.md`:** Same wikilink pattern; each `[[slug]]` must exist in `all_slugs`.
- **Images:** `![](relative/path)` is resolved relative to **the markdown file’s directory**, then checked with `.exists()`; `http` URLs are skipped.

## CLI examples

From repo root:

```bash
python src/tools/lint.py
python src/tools/lint.py wiki/papers/attention_is_all_you_need.md
```

For the second form, if your shell cwd is not the repo root, use an absolute path to the `.md` file so the file is found.
