#!/usr/bin/env python3
"""Submit a continuous PDF page range to MinerU and cache Markdown output."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
from pathlib import Path

import fitz

from mineru_to_weread_epub import (
    download_zip,
    parse_result_zip,
    poll_batch,
    request_upload_urls,
    upload_files,
)


def load_env_file(path: Path | None) -> None:
    if not path:
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'\"")
        if key and key not in os.environ:
            os.environ[key] = value


def require_token() -> str:
    token = os.getenv("MINERU_API_TOKEN", "").strip()
    if not token:
        raise SystemExit("MINERU_API_TOKEN is not set")
    return token


def export_page_chunks(
    pdf_path: Path,
    start_page: int,
    end_page: int,
    out_dir: Path,
    chunk_size: int,
) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    doc = fitz.open(pdf_path)
    if start_page < 1 or end_page > doc.page_count or start_page > end_page:
        raise ValueError(f"invalid page range {start_page}-{end_page}; PDF has {doc.page_count} pages")

    span = end_page - start_page + 1
    effective_chunk = span if chunk_size <= 0 else chunk_size
    chunks: list[Path] = []
    for chunk_start in range(start_page, end_page + 1, effective_chunk):
        chunk_end = min(chunk_start + effective_chunk - 1, end_page)
        target = out_dir / f"chunk_p{chunk_start:04d}_p{chunk_end:04d}.pdf"
        chunk = fitz.open()
        chunk.insert_pdf(doc, from_page=chunk_start - 1, to_page=chunk_end - 1)
        chunk.save(target)
        chunk.close()
        chunks.append(target)
    doc.close()
    return chunks


def chunk_sort_key(path: Path) -> int:
    match = re.search(r"p(\d+)_p\d+", path.stem)
    return int(match.group(1)) if match else 0


def write_assets(images: dict[str, bytes], assets_dir: Path) -> None:
    assets_dir.mkdir(parents=True, exist_ok=True)
    for name, data in images.items():
        target = assets_dir / Path(name).name
        target.write_bytes(data)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pdf", required=True, type=Path)
    parser.add_argument("--start-page", required=True, type=int)
    parser.add_argument("--end-page", required=True, type=int)
    parser.add_argument("--work-dir", required=True, type=Path)
    parser.add_argument("--combined-md", required=True, type=Path)
    parser.add_argument("--assets-dir", required=True, type=Path)
    parser.add_argument("--chunk-size", type=int, default=0, help="0 means submit the whole range as one file")
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--reuse", action="store_true")
    args = parser.parse_args()

    load_env_file(args.env_file)
    chunks_dir = args.work_dir / "chunks"
    raw_dir = args.work_dir / "raw_chunks"
    md_dir = args.work_dir / "markdown"
    raw_dir.mkdir(parents=True, exist_ok=True)
    md_dir.mkdir(parents=True, exist_ok=True)

    chunk_files = export_page_chunks(args.pdf, args.start_page, args.end_page, chunks_dir, args.chunk_size)
    md_parts: list[tuple[Path, str]] = []
    all_images: dict[str, bytes] = {}

    if args.reuse and all((raw_dir / f"{path.stem}.zip").exists() for path in chunk_files):
        for path in chunk_files:
            markdown, images = parse_result_zip((raw_dir / f"{path.stem}.zip").read_bytes())
            md_parts.append((path, markdown))
            all_images.update(images)
    else:
        token = require_token()
        batch_id, upload_urls = request_upload_urls(chunk_files, token)
        (raw_dir / "batch_id.txt").write_text(batch_id, encoding="utf-8")
        upload_files(chunk_files, upload_urls)
        results = poll_batch(batch_id, token)
        (raw_dir / "batch_results.json").write_text(
            json.dumps(results, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        by_name = {item.get("file_name") or item.get("name") or item.get("file"): item for item in results}
        for path in chunk_files:
            item = by_name.get(path.name)
            if item is None:
                matches = [candidate for candidate in results if path.name in json.dumps(candidate, ensure_ascii=False)]
                item = matches[0] if matches else None
            if item is None or not item.get("full_zip_url"):
                raise RuntimeError(f"no completed result for {path.name}")
            zip_bytes = download_zip(item["full_zip_url"], token)
            (raw_dir / f"{path.stem}.zip").write_bytes(zip_bytes)
            markdown, images = parse_result_zip(zip_bytes)
            md_parts.append((path, markdown))
            all_images.update(images)

    args.combined_md.parent.mkdir(parents=True, exist_ok=True)
    if args.assets_dir.exists():
        shutil.rmtree(args.assets_dir)
    write_assets(all_images, args.assets_dir)

    combined: list[str] = []
    for path, markdown in sorted(md_parts, key=lambda item: chunk_sort_key(item[0])):
        (md_dir / f"{path.stem}.md").write_text(markdown, encoding="utf-8")
        combined.append(f"<!-- source: {path.name} -->\n\n{markdown.strip()}")
    args.combined_md.write_text("\n\n".join(part for part in combined if part.strip()) + "\n", encoding="utf-8")

    manifest = {
        "pdf": str(args.pdf),
        "start_page": args.start_page,
        "end_page": args.end_page,
        "chunk_size": args.chunk_size,
        "chunks": [path.name for path in chunk_files],
        "combined_md": str(args.combined_md),
        "assets_dir": str(args.assets_dir),
        "image_count": len(all_images),
    }
    (args.work_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
