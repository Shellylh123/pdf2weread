#!/usr/bin/env python3
"""Submit planned PDF page ranges to MinerU and cache chunk Markdown/assets."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import fitz

from mineru_range_markdown import load_env_file, require_token, write_assets
from mineru_to_weread_epub import (
    download_zip,
    parse_result_zip,
    poll_batch,
    request_upload_urls,
    upload_files,
)


RANGE_RE = re.compile(r"^\s*(\d+)\s*[-:]\s*(\d+)\s*$")
CHUNK_RE = re.compile(r"chunk_p(\d+)_p(\d+)")
DOWNLOAD_WORKERS = max(1, int(os.getenv("MINERU_DOWNLOAD_WORKERS", "4")))


def parse_ranges(value: str) -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []
    for part in value.split(","):
        if not part.strip():
            continue
        match = RANGE_RE.match(part)
        if not match:
            raise ValueError(f"invalid range: {part!r}")
        start, end = int(match.group(1)), int(match.group(2))
        if start > end:
            raise ValueError(f"invalid reversed range: {part!r}")
        ranges.append((start, end))
    if not ranges:
        raise ValueError("no ranges provided")
    return ranges


def chunk_name(start: int, end: int) -> str:
    return f"chunk_p{start:04d}_p{end:04d}"


def chunk_sort_key(path: Path) -> tuple[int, int]:
    match = CHUNK_RE.search(path.stem)
    if not match:
        return (10**9, 10**9)
    return (int(match.group(1)), int(match.group(2)))


def export_range_pdf(pdf_path: Path, start: int, end: int, target: Path) -> None:
    if target.exists():
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    doc = fitz.open(pdf_path)
    if start < 1 or end > doc.page_count:
        raise ValueError(f"invalid range {start}-{end}; PDF has {doc.page_count} pages")
    out = fitz.open()
    out.insert_pdf(doc, from_page=start - 1, to_page=end - 1)
    out.save(target)
    out.close()
    doc.close()


def write_chunk_outputs(
    run_dir: Path,
    pdf_path: Path,
    chunk_pdf: Path,
    zip_bytes: bytes,
) -> dict[str, object]:
    stem = chunk_pdf.stem
    markdown, images = parse_result_zip(zip_bytes)

    chunks_dir = run_dir / "chunks"
    assets_dir = run_dir / "assets" / stem
    manifests_dir = run_dir / "manifests"
    chunks_dir.mkdir(parents=True, exist_ok=True)
    manifests_dir.mkdir(parents=True, exist_ok=True)

    md_path = chunks_dir / f"{stem}-api.md"
    md_path.write_text(markdown.strip() + "\n", encoding="utf-8")
    if assets_dir.exists():
        shutil.rmtree(assets_dir)
    write_assets(images, assets_dir)

    start, end = chunk_sort_key(chunk_pdf)
    manifest = {
        "pdf": str(pdf_path),
        "chunk_pdf": str(chunk_pdf),
        "chunk": stem,
        "start_page": start,
        "end_page": end,
        "api_markdown": str(md_path),
        "assets_dir": str(assets_dir),
        "markdown_chars": len(markdown),
        "image_count": len(images),
    }
    (manifests_dir / f"{stem}.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return manifest


def rebuild_book_api(run_dir: Path) -> Path:
    chunks = sorted((run_dir / "chunks").glob("chunk_p*_p*-api.md"), key=chunk_sort_key)
    parts: list[str] = []
    for path in chunks:
        source = path.name.removesuffix("-api.md") + ".pdf"
        parts.append(f"<!-- source: {source} -->\n\n{path.read_text(encoding='utf-8').strip()}")
    target = run_dir / "book-api.md"
    target.write_text("\n\n".join(part for part in parts if part.strip()) + "\n", encoding="utf-8")
    return target


def find_result_for_chunk(chunk_pdf: Path, results: list[dict[str, object]]) -> dict[str, object]:
    by_name = {item.get("file_name") or item.get("name") or item.get("file"): item for item in results}
    item = by_name.get(chunk_pdf.name)
    if item is None:
        matches = [candidate for candidate in results if chunk_pdf.name in json.dumps(candidate, ensure_ascii=False)]
        item = matches[0] if matches else None
    if item is None or not item.get("full_zip_url"):
        raise RuntimeError(f"no completed result for {chunk_pdf.name}")
    return item


def download_and_write_chunk(
    run_dir: Path,
    pdf_path: Path,
    raw_zips_dir: Path,
    token: str,
    chunk_pdf: Path,
    item: dict[str, object],
) -> dict[str, object]:
    zip_bytes = download_zip(str(item["full_zip_url"]), token)
    (raw_zips_dir / f"{chunk_pdf.stem}.zip").write_bytes(zip_bytes)
    return write_chunk_outputs(run_dir, pdf_path, chunk_pdf, zip_bytes)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pdf", required=True, type=Path)
    parser.add_argument("--ranges", required=True, help="Comma-separated page ranges, e.g. 81-160,161-240")
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--reuse", action="store_true")
    args = parser.parse_args()

    load_env_file(args.env_file)
    ranges = parse_ranges(args.ranges)
    run_dir = args.run_dir
    pdf_chunks_dir = run_dir / "pdf_chunks"
    raw_zips_dir = run_dir / "raw_zips"
    logs_dir = run_dir / "logs"
    raw_zips_dir.mkdir(parents=True, exist_ok=True)
    logs_dir.mkdir(parents=True, exist_ok=True)

    chunk_pdfs: list[Path] = []
    for start, end in ranges:
        path = pdf_chunks_dir / f"{chunk_name(start, end)}.pdf"
        export_range_pdf(args.pdf, start, end, path)
        chunk_pdfs.append(path)

    manifests: list[dict[str, object]] = []
    missing: list[Path] = []
    for chunk_pdf in chunk_pdfs:
        zip_path = raw_zips_dir / f"{chunk_pdf.stem}.zip"
        if args.reuse and zip_path.exists():
            manifests.append(write_chunk_outputs(run_dir, args.pdf, chunk_pdf, zip_path.read_bytes()))
        else:
            missing.append(chunk_pdf)

    batch_meta: dict[str, object] = {
        "submitted_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "ranges": ranges,
        "missing": [path.name for path in missing],
        "reused": [path.name for path in chunk_pdfs if path not in missing],
    }

    if missing:
        token = require_token()
        batch_id, upload_urls = request_upload_urls(missing, token)
        batch_meta["batch_id"] = batch_id
        upload_files(missing, upload_urls)
        results = poll_batch(batch_id, token)
        batch_meta["results"] = results
        (logs_dir / f"batch_{batch_id}.json").write_text(
            json.dumps(batch_meta, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        items = {chunk_pdf: find_result_for_chunk(chunk_pdf, results) for chunk_pdf in missing}
        workers = min(DOWNLOAD_WORKERS, len(missing))
        if workers <= 1:
            for chunk_pdf in missing:
                manifests.append(
                    download_and_write_chunk(run_dir, args.pdf, raw_zips_dir, token, chunk_pdf, items[chunk_pdf])
                )
        else:
            with ThreadPoolExecutor(max_workers=workers) as executor:
                futures = {
                    executor.submit(
                        download_and_write_chunk,
                        run_dir,
                        args.pdf,
                        raw_zips_dir,
                        token,
                        chunk_pdf,
                        items[chunk_pdf],
                    ): chunk_pdf
                    for chunk_pdf in missing
                }
                completed: list[dict[str, object]] = []
                for future in as_completed(futures):
                    completed.append(future.result())
                manifests.extend(sorted(completed, key=lambda item: int(item["start_page"])))

    book_api = rebuild_book_api(run_dir)
    summary = {
        "pdf": str(args.pdf),
        "run_dir": str(run_dir),
        "ranges": ranges,
        "chunk_count": len(chunk_pdfs),
        "submitted_count": len(missing),
        "reused_count": len(chunk_pdfs) - len(missing),
        "book_api": str(book_api),
        "manifests": manifests,
    }
    (run_dir / "ocr_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
