# Workflow

## Directory Layout

Use one run directory per book:

```text
runs/book-name/
  pdf_chunks/
  raw_zips/
  chunks/
  assets/
  manifests/
  clean_chunks/
  logs/
  book-api.md
  book-clean.md
  book-toc.md
  ocr_summary.json
  qa_report.json
```

`book-api.md` is an archive of raw OCR Markdown. `book-clean.md` is the only Markdown input for final EPUB packaging.

## Setup

Install dependencies:

```bash
python -m pip install requests PyMuPDF
```

Set the MinerU token:

```bash
export MINERU_API_TOKEN="..."
```

Or put it in an env file:

```text
MINERU_API_TOKEN=...
MINERU_UPLOAD_WORKERS=4
MINERU_DOWNLOAD_WORKERS=4
```

## Range Planning

1. Get the PDF page count with a PDF tool or Python.
2. Plan a discovery range, usually `1-80`.
3. Plan remaining ranges at 70-80 pages each.
4. Adjust for figure-heavy books or failed batches.

Example for a 400 page book:

```text
1-80,81-160,161-240,241-320,321-400
```

If the first 80 pages already contain front matter, TOC, and body start, reuse that OCR result in the final merge.

## OCR And Cache

Run OCR batches:

```bash
python "$SKILL_DIR/scripts/mineru_book_chunks.py" \
  --pdf "$PDF" \
  --ranges "$RANGES" \
  --run-dir "$RUN_DIR" \
  --env-file "$ENV_FILE" \
  --reuse
```

`--reuse` means existing ZIPs are parsed again and missing chunks are submitted. It does not reduce quality.

For agents that support subagents, use at most 3-4 parallel workers and assign non-overlapping ranges. If the API queues or rate-limits, reduce to 2 workers.

## Normalize

Run:

```bash
python "$SKILL_DIR/scripts/normalize_book_run.py" \
  --run-dir "$RUN_DIR"
```

If `qa_report.json` shows no TOC or the front matter/body split is wrong, inspect the first chunk Markdown and pass a better body-start regex:

```bash
python "$SKILL_DIR/scripts/normalize_book_run.py" \
  --run-dir "$RUN_DIR" \
  --body-start-regex "^第\\s*1\\s*章|^第一部分|^Chapter\\s+1"
```

For clearly non-book lines, repeat `--drop-line`:

```bash
python "$SKILL_DIR/scripts/normalize_book_run.py" \
  --run-dir "$RUN_DIR" \
  --drop-line "扫码加入书架领取阅读激励"
```

## Build EPUB

Run:

```bash
python "$SKILL_DIR/scripts/markdown_to_weread_epub.py" \
  --markdown "$RUN_DIR/book-clean.md" \
  --output "$OUTPUT_EPUB" \
  --title "$BOOK_TITLE" \
  --author "$BOOK_AUTHOR"
```

The packager writes an EPUB with a front matter XHTML, an in-book TOC XHTML, body XHTML, `nav.xhtml`, and `toc.ncx` when the normalized Markdown contains those sections.

## Iteration

- OCR incomplete: rerun only the missing or bad range.
- TOC link wrong: rerun normalization and EPUB packaging.
- paragraph/list/image style wrong: adjust cleanup or CSS and rerun EPUB packaging.
- WeRead import slow: check EPUB size and image count; do not rerun OCR first.

## Deliverables

Provide the EPUB plus, when useful, `book-clean.md`, `book-toc.md`, and `qa_report.json`. The Markdown files make future fixes faster because they avoid repeated OCR.
