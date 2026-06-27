# Scanned PDF to WeRead EPUB Skill

Agent Skill for converting authorized scanned book PDFs into WeRead-friendly EPUBs:

- OCR to Markdown and image assets
- paragraph and list cleanup
- figure/table preservation
- original book TOC extraction
- clickable EPUB side TOC via `nav.xhtml` and `toc.ncx`
- EPUB output suitable for highlighting in WeRead/微信读书

## Layout

```text
.agents/skills/scan-pdf-to-weread-epub/   # Codex/project Agent Skills layout
.claude/skills/scan-pdf-to-weread-epub/   # Claude Code project layout
```

Each directory contains the same skill body:

```text
SKILL.md
agents/openai.yaml
scripts/
references/
requirements.txt
```

For Codex personal install, copy the inner skill folder to `~/.codex/skills/scan-pdf-to-weread-epub`.

For Claude Code personal install, copy the inner skill folder to `~/.claude/skills/scan-pdf-to-weread-epub`. For project use, keep the `.claude/skills/...` directory in the project root.

The companion `scan-pdf-to-weread-epub.skill.zip` artifact has the skill directory at the zip root.

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
Use $scan-pdf-to-weread-epub to convert /path/to/book.pdf into a WeRead-friendly EPUB.
Use MinerU for OCR, preserve the original book TOC, and output the EPUB plus QA report.
```

## Notes

Only process PDFs you are authorized to convert. The included scripts target MinerU for OCR, but the cleanup and EPUB packaging rules can also be used with any OCR service that outputs Markdown plus image assets.

Do not commit `.env` files or real API tokens. This repository only documents the `MINERU_API_TOKEN` environment variable; it does not contain a token.
