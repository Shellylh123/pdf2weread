# QA

Run QA after every full EPUB build and after any change to cleanup or packaging rules.

## Markdown Checks

- `book-clean.md` exists and has substantial text.
- `book-toc.md` exists when the original book has a TOC.
- Front matter appears before the in-book TOC.
- The in-book TOC appears before body content.
- Paragraphs are separated by blank lines.
- No visible `PDF 第 N 页` headings remain.
- No OCR placeholders such as `[无法识别]` remain.
- No obvious service, app, QR, or unrelated promotional lines remain.
- Figure captions are present after figure images.
- Figure-internal OCR garbage does not appear below images.
- Bulleted material is normalized as list items.

## EPUB Package Checks

Open the EPUB as a zip and verify:

- `mimetype` is first and stored.
- `META-INF/container.xml` points to `EPUB/content.opf`.
- `EPUB/content.opf` has a spine in reading order: front matter, book TOC, body when all exist.
- `EPUB/nav.xhtml` exists and has TOC entries.
- `EPUB/toc.ncx` exists and has matching TOC entries for WeRead compatibility.
- image files referenced by XHTML exist under `EPUB/images/`.
- CSS exists under `EPUB/style/`.

## Link Checks

For every href in `nav.xhtml` and `toc.ncx`:

- the target XHTML file exists
- the anchor exists when an anchor fragment is present
- key entries jump to the intended sections, not to repeated incidental text

Check at least:

- recommendations or prefaces
- first part if present
- chapter 1
- one mid-book chapter
- one figure-heavy section
- appendices
- acknowledgements
- notes

## Visual Checks In WeRead

Import the EPUB into WeRead and inspect several pages:

- side TOC is populated and clickable
- in-book TOC is readable and not squeezed into one paragraph
- text can be highlighted
- paragraphs have readable spacing
- page boundaries from PDF are not visible as headings
- figures fit screen width
- captions are close to figures
- lists use bullets consistently
- no large blank image blocks appear

## Failure Interpretation

- Few pages or missing chapters usually means OCR range planning or cache reuse is incomplete.
- Long empty gaps usually mean page-based splitting or broken paragraph/image handling.
- A messy TOC usually means original TOC lines were merged as body paragraphs.
- Bad image captions usually mean figure-internal OCR text was not filtered.
- Missing WeRead side TOC usually means `nav.xhtml`, `toc.ncx`, or anchor targets are wrong.
