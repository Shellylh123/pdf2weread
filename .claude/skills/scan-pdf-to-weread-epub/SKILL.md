---
name: scan-pdf-to-weread-epub
description: Convert scanned book PDFs into WeRead-friendly reflowable EPUBs using OCR Markdown, cleanup rules, clickable book table of contents, image/table preservation, and EPUB navigation metadata. Use when asked to process scanned PDFs, OCR books, MinerU API or Markdown output, or Markdown into EPUB for 微信读书/WeRead with highlighting, paragraph cleanup, figure captions, and navigable table of contents.
---

# Scanned PDF to WeRead EPUB

## Core Contract

Convert authorized scanned book PDFs into readable, reflowable EPUBs for WeRead/微信读书. Preserve the original reading order, front matter, book table of contents, body text, figures, tables, captions, lists, appendices, acknowledgements, and notes unless the user explicitly asks to omit them.

Treat Markdown as the central intermediate format. OCR should produce raw Markdown and image assets; all typography, paragraph repair, TOC linking, figure cleanup, and EPUB packaging happen after OCR. Never rerun OCR just to adjust style, paragraph rules, or EPUB metadata.

## Read References

Read these files when the task needs the corresponding detail:

- `references/rules.md`: use before converting a full scanned book or when deciding cleanup, TOC, image, list, and EPUB rules.
- `references/workflow.md`: use before running OCR/API batches or building an end-to-end run directory.
- `references/qa.md`: use before delivering an EPUB or after changing cleanup/packaging scripts.

## Standard Workflow

1. Confirm the user has the right to convert the PDF. If the user asks to bypass DRM, access controls, or copyright restrictions, decline that part and offer to process authorized files.
2. Run a discovery pass on the beginning of the PDF, usually the first 80 pages, to identify front matter, the original book TOC, the real body start, and image/table density. Do not hard-code page numbers as rules.
3. OCR the book in large continuous batches. Prefer 70-80 pages per batch for a 300-500 page book, reduce to 40-60 for figure-heavy books, and split only failed batches.
4. Cache raw OCR outputs: original ZIPs, `chunk-api.md`, assets, and manifests. Reuse cached OCR whenever possible.
5. Normalize each chunk, then merge into `book-clean.md` and `book-toc.md`. Preserve paragraph boundaries; only merge lines that are clearly the same paragraph.
6. Build the EPUB from `book-clean.md`. The EPUB must include both a readable in-book TOC page and EPUB navigation metadata (`nav.xhtml` and `toc.ncx`) so WeRead can show a clickable side TOC.
7. Run QA before delivery. Verify TOC links, spine order, missing anchors, image packaging, paragraph spacing, list rendering, and absence of OCR/service garbage.

## Script Map

- `scripts/mineru_book_chunks.py`: split PDF page ranges, call MinerU API, cache chunk Markdown, ZIPs, assets, manifests, and rebuild `book-api.md`.
- `scripts/normalize_book_run.py`: convert cached chunk Markdown into `clean_chunks/`, `book-clean.md`, `book-toc.md`, and `qa_report.json`.
- `scripts/markdown_to_weread_epub.py`: package normalized Markdown and assets into a WeRead-friendly EPUB with clickable book TOC metadata.
- `scripts/mineru_range_markdown.py`: lower-level helper for one PDF range.
- `scripts/mineru_to_weread_epub.py`: legacy one-range helper and shared MinerU API functions.
- `scripts/normalize_mineru_markdown.py`: lower-level one-file Markdown cleanup helper.

## Environment

Use Python 3.9+ with `requests` and `PyMuPDF` installed. Install bundled dependencies with `python -m pip install -r "$SKILL_DIR/requirements.txt"` when the runtime does not already have them. MinerU calls need `MINERU_API_TOKEN` in the environment or in an env file loaded with `--env-file`.

Useful optional variables:

```bash
export MINERU_UPLOAD_WORKERS=4
export MINERU_DOWNLOAD_WORKERS=4
```

Lower these to `2` if the API rate-limits, queues heavily, or fails intermittently.

## Typical Commands

Run from any working directory. In Claude Code, use `${CLAUDE_SKILL_DIR}` as the skill folder. In Codex or another client, set `SKILL_DIR` to this skill folder.

```bash
SKILL_DIR="${CLAUDE_SKILL_DIR:-$SKILL_DIR}"

python "$SKILL_DIR/scripts/mineru_book_chunks.py" \
  --pdf "$PDF" \
  --ranges "1-80,81-160,161-240,241-320,321-400" \
  --run-dir "$RUN_DIR" \
  --env-file "$ENV_FILE" \
  --reuse

python "$SKILL_DIR/scripts/normalize_book_run.py" \
  --run-dir "$RUN_DIR"

python "$SKILL_DIR/scripts/markdown_to_weread_epub.py" \
  --markdown "$RUN_DIR/book-clean.md" \
  --output "$OUTPUT_EPUB" \
  --title "$BOOK_TITLE" \
  --author "$BOOK_AUTHOR"
```

If automatic body-start detection fails, rerun normalization with a book-specific regex:

```bash
SKILL_DIR="${CLAUDE_SKILL_DIR:-$SKILL_DIR}"

python "$SKILL_DIR/scripts/normalize_book_run.py" \
  --run-dir "$RUN_DIR" \
  --body-start-regex "^第\\s*1\\s*章|^第一部分|^Chapter\\s+1"
```

Use repeated `--drop-line "exact noise line"` only for lines that are clearly not part of the book.

## Recovery Rules

If a batch is incomplete, rerun only that page range. If cleanup or EPUB style is wrong, do not rerun OCR; rerun `normalize_book_run.py` and `markdown_to_weread_epub.py`. If WeRead does not show a clickable TOC, inspect `nav.xhtml`, `toc.ncx`, and missing anchor targets before changing OCR settings.
