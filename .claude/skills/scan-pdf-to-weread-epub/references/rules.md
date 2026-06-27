# Rules

## Goal

Produce a reflowable EPUB that behaves like a normal WeRead book:

- text can be highlighted
- paragraphs are readable and separated
- original book TOC is visible in the book and clickable in the WeRead side TOC
- figures, tables, and captions stay near their original positions
- PDF page boundaries do not become EPUB chapters or visible page headings

## First Principles

1. OCR extracts text and images; it does not define final book structure.
2. Markdown is the canonical intermediate layer.
3. Raw OCR output is immutable evidence; cleaned Markdown is the editing layer.
4. Cache OCR outputs and rerun local cleanup/EPUB builds freely.
5. Preserve original reading order: front matter, book TOC, body, appendices, notes.
6. Use automatic rules first; human spot checks should improve rules, not become manual page editing.

## Discovery

For a new book, start with the first 80 PDF pages unless the user gives a better clue. Identify:

- cover and copyright pages
- recommendations, translator notes, prefaces, introductions
- original book TOC
- real body start
- figure/table density

If the TOC or body start is not found, extend discovery to pages 81-120, then continue as needed. Do not turn this discovery range into a hard-coded rule for future books.

## Batch Size

For 300-500 page scanned books:

- default: 70-80 pages per batch
- stable pure text: 80-100 pages per batch
- figure/table-heavy books: 40-60 pages per batch
- failed or timed-out batch: split only that failed batch

Avoid one-page OCR except for diagnosis. It is slow and creates more merge problems.

## Markdown Cleanup

- Preserve blank lines as paragraph boundaries.
- Merge visual line wraps inside the same paragraph.
- Merge across page or batch boundaries only when the previous block clearly lacks terminal punctuation and the next block clearly continues it.
- Do not merge headings, images, captions, lists, or TOC lines into ordinary paragraphs.
- Remove OCR service notes, empty image placeholders, and non-book promotional garbage.
- Convert obvious LaTeX OCR fragments to plain text when safe, such as `$^{3}$` to `3`, `$233\\%$` to `233%`, and `$20 / 20^{①}$` to `20/20①`.

## Figures And Tables

- Preserve figures and tables as images.
- Keep each image at its original Markdown position.
- Preserve the following caption when it matches `图...` or `表...`.
- Remove OCR text that came from inside the figure, such as axis labels, formula explanations, and chart labels duplicated below the image.
- Keep normal body explanation after the caption.
- Drop blank or meaningless screenshots.

## Lists

- Render bullets as real unordered lists in EPUB.
- Normalize original dots, black bullets, and OCR dash variants into list items.
- Repair list items split across pages or batches.
- Do not convert ordinary colon sentences into list items just because they contain a colon.

## TOC

- The book TOC must come from the original TOC pages, not from body subheadings guessed after the fact.
- Keep front matter before the book TOC in reading order.
- Put the in-book TOC into its own XHTML reading unit when building EPUB.
- Build a clickable side TOC from EPUB metadata, not only from a visible TOC page.
- Match TOC entries to body anchors while ignoring page numbers, whitespace, punctuation, chapter prefixes, and harmless prefix differences such as `附录A`.
- Never link TOC entries to author names, recommender names, or incidental repeated phrases.

## EPUB Structure

Use this reading order when the source contains these sections:

1. `text/frontmatter.xhtml`
2. `text/book_toc.xhtml`
3. `text/body.xhtml`

The package must include:

- `EPUB/content.opf`
- `EPUB/nav.xhtml`
- `EPUB/toc.ncx`
- `EPUB/style/nav.css`
- `EPUB/images/...`

The side TOC should contain front matter entries, parts, chapters, important section anchors, appendices, acknowledgements, and notes when they exist in the original book.

## Style

- Leave font choice mostly to WeRead.
- Use comfortable body line height around 1.7.
- Indent normal paragraphs.
- Leave visible paragraph spacing.
- Use restrained headings.
- Use real `ul/li` lists.
- Set images to max-width 100 percent and keep captions close to images.

## Do Not Do

- Do not make each PDF page an EPUB chapter.
- Do not show `PDF 第 N 页` in the final book.
- Do not place the book TOC before front matter if the original book does not.
- Do not rerun OCR for CSS, paragraph, TOC-link, or EPUB metadata changes.
- Do not flatten all paragraphs into one block.
- Do not rely on fixed page numbers as reusable rules.
