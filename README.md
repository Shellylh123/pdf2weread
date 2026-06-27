# pdf2weread

Agent Skill for converting authorized scanned book PDFs into WeRead-friendly EPUBs:

- OCR to Markdown and image assets
- paragraph and list cleanup
- figure/table preservation
- original book TOC extraction
- clickable EPUB side TOC via `nav.xhtml` and `toc.ncx`
- EPUB output suitable for highlighting in WeRead/微信读书

## Layout

```text
.agents/skills/pdf2weread/   # Codex/project Agent Skills layout
.claude/skills/pdf2weread/   # Claude Code project layout
```

Each directory contains the same skill body:

```text
SKILL.md
agents/openai.yaml
scripts/
references/
requirements.txt
```

For Codex personal install, copy the inner skill folder to `~/.codex/skills/pdf2weread`.

For Claude Code personal install, copy the inner skill folder to `~/.claude/skills/pdf2weread`. For project use, keep the `.claude/skills/...` directory in the project root.

The companion `pdf2weread.skill.zip` artifact has the skill directory at the zip root.

## Requirements

```bash
python -m pip install -r requirements.txt
```

Set your MinerU token:

```bash
export MINERU_API_TOKEN="..."
```

## Example Prompt

```text
Use $pdf2weread to convert /path/to/book.pdf into a WeRead-friendly EPUB.
Use MinerU for OCR, preserve the original book TOC, and output the EPUB plus QA report.
```

## Notes

Only process PDFs you are authorized to convert. The included scripts target MinerU for OCR, but the cleanup and EPUB packaging rules can also be used with any OCR service that outputs Markdown plus image assets.

Do not commit `.env` files or real API tokens. This repository only documents the `MINERU_API_TOKEN` environment variable; it does not contain a token.
