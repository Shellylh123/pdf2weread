#!/usr/bin/env python3
"""Normalize MinerU Markdown before EPUB generation."""

from __future__ import annotations

import argparse
import re
from pathlib import Path

from mineru_to_weread_epub import (
    BULLET_RE,
    CAPTION_RE,
    clean_line,
    image_markdown_path,
    is_image_ocr_note,
    is_noise,
    is_term_list_item,
    is_terminal_text,
    markdown_blocks,
)


def cjk_join(left: str, right: str) -> str:
    if not left:
        return right
    if not right:
        return left
    if re.search(r"[\u4e00-\u9fff]$", left) and re.search(r"^[\u4e00-\u9fff]", right):
        return left + right
    if right[0] in "，。！？；：、）】》”’」』" or left[-1] in "，。！？；：、（【《“‘「『":
        return left + right
    return left + " " + right


def should_merge_paragraph(left: str, right: str) -> bool:
    if not left or not right or is_terminal_text(left):
        return False
    if left.endswith(("：", ":")) and right.startswith(("能", "能够", "可以", "可", "无须", "通过", "如")):
        return True
    if len(left) < 30:
        return False
    if re.match(r"^#{1,6}\s+", right) or image_markdown_path(right) or BULLET_RE.match(right):
        return False
    return bool(re.search(r"[\u4e00-\u9fff]$", left) and re.search(r"^[\u4e00-\u9fff]", right))


def rewrite_image(block: str, asset_prefix: str) -> str:
    image_ref = image_markdown_path(block)
    if not image_ref:
        return block
    name = Path(image_ref).name
    return f"![]({asset_prefix.rstrip('/')}/{name})"


def normalize_blocks(raw_markdown: str, asset_prefix: str) -> list[str]:
    raw_blocks = [block for block in markdown_blocks(raw_markdown, strip_headings=False) if not block.startswith("<!--")]
    merged: list[str] = []
    for block in raw_blocks:
        if is_image_ocr_note(block):
            continue
        if merged and should_merge_paragraph(merged[-1], block):
            merged[-1] = cjk_join(merged[-1], block)
        else:
            merged.append(block)

    output: list[str] = []
    i = 0
    while i < len(merged):
        block = merged[i]
        if is_image_ocr_note(block):
            i += 1
            continue

        image_ref = image_markdown_path(block)
        if image_ref:
            output.append(rewrite_image(block, asset_prefix))
            j = i + 1
            while j < len(merged) and is_image_ocr_note(merged[j]):
                j += 1
            if j < len(merged) and CAPTION_RE.match(merged[j]):
                output.append(merged[j])
                i = j + 1
            else:
                i += 1
            continue

        bullet = BULLET_RE.match(block)
        if bullet:
            output.append(f"- {bullet.group(1).strip()}")
            i += 1
            continue

        if is_term_list_item(block):
            output.append(f"- {block}")
            i += 1
            continue

        if re.match(r"^#{1,6}\s+", block):
            output.append(block)
            i += 1
            continue

        output.append(block)
        i += 1

    return output


def normalize_toc_blocks(raw_markdown: str) -> list[str]:
    output: list[str] = []
    for raw in raw_markdown.splitlines():
        line = clean_line(raw)
        if not line or line.startswith("<!--") or is_noise(line):
            continue
        if line in {"natural_image", "text_image"} or is_image_ocr_note(line):
            continue
        output.append(line)
    return output


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--asset-prefix", required=True)
    args = parser.parse_args()

    raw = args.input.read_text(encoding="utf-8")
    blocks = normalize_blocks(raw, args.asset_prefix)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("\n\n".join(blocks).strip() + "\n", encoding="utf-8")
    print(f"blocks={len(blocks)} output={args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
