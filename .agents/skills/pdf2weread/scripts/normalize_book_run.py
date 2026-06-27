#!/usr/bin/env python3
"""Normalize a full OCR run into book-clean Markdown and QA metadata."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from normalize_mineru_markdown import (
    CAPTION_RE,
    BULLET_RE,
    clean_line,
    cjk_join,
    image_markdown_path,
    is_image_ocr_note,
    is_noise,
    normalize_blocks,
    normalize_toc_blocks,
    should_merge_paragraph,
)


CHUNK_RE = re.compile(r"chunk_p(\d+)_p(\d+)")
DEFAULT_BODY_START_RE = re.compile(
    r"^(?:"
    r"第\s*[0-9一二三四五六七八九十]+\s*章\b|"
    r"第[一二三四五六七八九十0-9]+部分\b|"
    r"chapter\s+\d+\b"
    r")",
    re.IGNORECASE,
)
TOC_START_RE = re.compile(
    r"^(?:"
    r"目录|"
    r"推荐序[一二三四五六七八九十0-9]*|"
    r"译者序|中文版序|前言|导言|"
    r"重磅赞誉|"
    r"第\s*[0-9一二三四五六七八九十]+\s*章|"
    r"第[一二三四五六七八九十0-9]+部分|"
    r"[一二三四五六七八九十0-9]+[、.]\s*|"
    r"附录|致谢|注释"
    r")"
)
PAGE_NO_RE = re.compile(r"(?:^|[\s　])\d{3}\s*$")
DROP_EXACT_LINES = {
    "[无法识别]",
    "扫码加入书架领取阅读激励",
    "扫描左侧二维码查看本书更多测试题",
}


def chunk_sort_key(path: Path) -> tuple[int, int]:
    match = CHUNK_RE.search(path.stem)
    if not match:
        return (10**9, 10**9)
    return (int(match.group(1)), int(match.group(2)))


def strip_md_heading(line: str) -> str:
    return re.sub(r"^#{1,6}\s*", "", line).strip()


def line_for_detection(raw: str) -> str:
    return strip_md_heading(clean_line(raw))


def find_body_start(lines: list[str], body_start_re: re.Pattern[str]) -> int | None:
    matches = []
    for idx, raw in enumerate(lines):
        line = line_for_detection(raw)
        if not line or PAGE_NO_RE.search(line):
            continue
        if body_start_re.search(line):
            matches.append(idx)
    if not matches:
        return None
    return matches[0]


def toc_density(lines: list[str], start: int, end: int) -> int:
    score = 0
    for raw in lines[start:end]:
        line = line_for_detection(raw)
        if not line or image_markdown_path(line) or is_noise(line):
            continue
        if PAGE_NO_RE.search(line):
            score += 2
        if re.search(r"第\s*[0-9一二三四五六七八九十]+\s*章", line):
            score += 3
        if TOC_START_RE.match(line):
            score += 1
    return score


def find_toc_start(lines: list[str], body_start: int) -> int | None:
    window_start = max(0, body_start - 260)
    best: int | None = None
    for idx in range(window_start, body_start):
        line = line_for_detection(lines[idx])
        if not line:
            continue
        if not TOC_START_RE.match(line):
            continue
        score = toc_density(lines, idx, min(body_start, idx + 70))
        if score >= 10:
            best = idx
            break
    if best is not None:
        return best

    for idx in range(window_start, body_start):
        if toc_density(lines, idx, min(body_start, idx + 50)) >= 16:
            return idx
    return None


def split_first_chunk(raw: str, body_start_re: re.Pattern[str]) -> tuple[str, str, str, dict[str, object]]:
    lines = raw.splitlines()
    body_start = find_body_start(lines, body_start_re)
    if body_start is None:
        return raw, "", "", {"body_start_line": None, "toc_start_line": None, "split": "none"}

    toc_start = find_toc_start(lines, body_start)
    if toc_start is None:
        return raw[:0], "", raw, {
            "body_start_line": body_start + 1,
            "toc_start_line": None,
            "split": "body-only",
        }

    front_raw = "\n".join(lines[:toc_start])
    toc_raw = "\n".join(lines[toc_start:body_start])
    body_raw = "\n".join(lines[body_start:])
    return front_raw, toc_raw, body_raw, {
        "body_start_line": body_start + 1,
        "toc_start_line": toc_start + 1,
        "split": "frontmatter-toc-body",
    }


def merge_chunk_boundary(left_blocks: list[str], right_blocks: list[str]) -> list[str]:
    if not left_blocks:
        return right_blocks[:]
    if not right_blocks:
        return left_blocks[:]
    merged = left_blocks[:]
    first = right_blocks[0]
    if should_merge_paragraph(merged[-1], first):
        merged[-1] = cjk_join(merged[-1], first)
        merged.extend(right_blocks[1:])
    else:
        merged.extend(right_blocks)
    return merged


def sanitize_toc_blocks(blocks: list[str]) -> list[str]:
    lines: list[str] = []
    for block in blocks:
        line = strip_md_heading(block)
        if not line:
            continue
        if image_markdown_path(line) or is_image_ocr_note(line):
            continue
        lines.append(line)
    return repair_toc_wrapped_lines(lines)


def repair_toc_wrapped_lines(lines: list[str]) -> list[str]:
    output: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        next_line = lines[i + 1] if i + 1 < len(lines) else ""
        if (
            i + 1 < len(lines)
            and not PAGE_NO_RE.search(line)
            and PAGE_NO_RE.search(next_line)
            and not re.match(r"^(?:第\s*[0-9一二三四五六七八九十]+\s*章|附录|致谢|注释)", line)
            and not re.match(
                r"^(?:第\s*[0-9一二三四五六七八九十]+\s*章|第[一二三四五六七八九十0-9]+部分|附录|致谢|注释|推荐序|译者序|中文版序)",
                next_line,
            )
        ):
            output.append(cjk_join(line, next_line))
            i += 2
            continue
        output.append(line)
        i += 1
    return output


def qa_for_blocks(chunk_name: str, raw: str, clean_blocks: list[str]) -> dict[str, object]:
    short_blocks = [block for block in clean_blocks if 0 < len(block) <= 6 and not image_markdown_path(block)]
    suspicious_noise = [
        line
        for line in raw.splitlines()
        if "$" in line or "\\times" in line or "ocr result should be empty" in line.lower()
    ]
    return {
        "chunk": chunk_name,
        "raw_chars": len(raw),
        "clean_chars": sum(len(block) for block in clean_blocks),
        "clean_blocks": len(clean_blocks),
        "images": sum(1 for block in clean_blocks if image_markdown_path(block)),
        "captions": sum(1 for block in clean_blocks if CAPTION_RE.match(block)),
        "headings": sum(1 for block in clean_blocks if re.match(r"^#{1,6}\s+", block)),
        "bullets": sum(1 for block in clean_blocks if block.startswith("- ") or BULLET_RE.match(block)),
        "short_blocks": len(short_blocks),
        "suspicious_noise_lines": len(suspicious_noise),
    }


def is_drop_exact(block: str, extra_drop_lines: set[str]) -> bool:
    line = strip_md_heading(block)
    if line in DROP_EXACT_LINES or line in extra_drop_lines:
        return True
    if line.startswith("扫码获取全部测试题及答案"):
        return True
    if line.startswith("代找各类相关书籍"):
        return True
    return False


def post_filter_blocks(blocks: list[str], extra_drop_lines: set[str]) -> list[str]:
    output: list[str] = []
    for block in blocks:
        if is_drop_exact(block, extra_drop_lines):
            continue
        output.append(block)
    return output


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument(
        "--body-start-regex",
        help="Optional regex for the first real body/chapter heading after the book TOC.",
    )
    parser.add_argument(
        "--drop-line",
        action="append",
        default=[],
        help="Exact extra line to remove after heading markup is stripped; may be repeated.",
    )
    args = parser.parse_args()
    body_start_re = re.compile(args.body_start_regex, re.IGNORECASE) if args.body_start_regex else DEFAULT_BODY_START_RE
    extra_drop_lines = set(args.drop_line)

    run_dir = args.run_dir
    chunks_dir = run_dir / "chunks"
    clean_chunks_dir = run_dir / "clean_chunks"
    clean_chunks_dir.mkdir(parents=True, exist_ok=True)

    chunk_paths = sorted(chunks_dir.glob("chunk_p*_p*-api.md"), key=chunk_sort_key)
    if not chunk_paths:
        raise SystemExit(f"no chunk api markdown files found in {chunks_dir}")

    toc_blocks: list[str] = []
    body_by_chunk: list[tuple[Path, list[str]]] = []
    qa_chunks: list[dict[str, object]] = []
    split_meta: dict[str, object] = {}

    for index, path in enumerate(chunk_paths):
        raw = path.read_text(encoding="utf-8")
        chunk_stem = path.name.removesuffix("-api.md")
        asset_prefix = f"assets/{chunk_stem}"

        if index == 0:
            front_raw, toc_raw, body_raw, meta = split_first_chunk(raw, body_start_re)
            split_meta[chunk_stem] = meta
            front_blocks = normalize_blocks(front_raw, asset_prefix) if front_raw.strip() else []
            toc_blocks = sanitize_toc_blocks(normalize_toc_blocks(toc_raw)) if toc_raw.strip() else []
            body_blocks = normalize_blocks(body_raw, asset_prefix) if body_raw.strip() else []
            clean_blocks = front_blocks + (["<!-- body-start -->"] if front_blocks and body_blocks else []) + body_blocks
        else:
            clean_blocks = normalize_blocks(raw, asset_prefix)

        clean_blocks = post_filter_blocks(clean_blocks, extra_drop_lines)
        clean_path = clean_chunks_dir / f"{chunk_stem}-clean.md"
        clean_path.write_text("\n\n".join(clean_blocks).strip() + "\n", encoding="utf-8")
        body_by_chunk.append((path, clean_blocks))
        qa_chunks.append(qa_for_blocks(chunk_stem, raw, clean_blocks))

    merged_body: list[str] = []
    for _path, blocks in body_by_chunk:
        merged_body = merge_chunk_boundary(merged_body, blocks)

    book_toc = run_dir / "book-toc.md"
    book_toc.write_text("\n\n".join(toc_blocks).strip() + "\n", encoding="utf-8")

    book_clean = run_dir / "book-clean.md"
    parts: list[str] = []
    if toc_blocks:
        parts.append("<!-- book-toc-start -->\n\n" + "\n\n".join(toc_blocks) + "\n\n<!-- book-toc-end -->")
    parts.append("\n\n".join(merged_body))
    book_clean.write_text("\n\n".join(part.strip() for part in parts if part.strip()) + "\n", encoding="utf-8")

    qa = {
        "run_dir": str(run_dir),
        "chunk_count": len(chunk_paths),
        "book_toc": str(book_toc),
        "book_clean": str(book_clean),
        "toc_entries": len(toc_blocks),
        "body_blocks": len(merged_body),
        "split_meta": split_meta,
        "chunks": qa_chunks,
    }
    qa_path = run_dir / "qa_report.json"
    qa_path.write_text(json.dumps(qa, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(qa, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
