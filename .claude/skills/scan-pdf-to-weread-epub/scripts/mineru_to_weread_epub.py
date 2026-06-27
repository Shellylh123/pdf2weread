#!/usr/bin/env python3
"""Direct MinerU-to-EPUB experiment for WeRead-friendly scanned books."""

from __future__ import annotations

import argparse
import base64
import html
import io
import json
import mimetypes
import os
import re
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import fitz
import requests


API_BASE = os.getenv("MINERU_API_BASE", "https://mineru.net")
MODEL_VERSION = os.getenv("MINERU_MODEL_VERSION", "vlm")
LANGUAGE = os.getenv("MINERU_LANGUAGE", "chinese_cht")
POLL_INTERVAL = int(os.getenv("MINERU_POLL_INTERVAL", "5"))
MAX_POLL_TIME = int(os.getenv("MINERU_MAX_POLL_TIME", "1800"))
UPLOAD_WORKERS = max(1, int(os.getenv("MINERU_UPLOAD_WORKERS", "4")))


STYLE_CSS = """
body {
  font-family: "PingFang SC", "Songti SC", "Noto Serif CJK SC", serif;
  line-height: 1.72;
  text-align: justify;
  margin: 0.8em 0.9em;
}
.source-page {
  margin: 0;
}
.page-anchor {
  display: none;
  height: 0;
  overflow: hidden;
}
h1 {
  text-align: center;
  font-size: 1.45em;
  line-height: 1.35;
  margin: 1.2em 0 0.9em 0;
}
h2 {
  font-size: 1.18em;
  line-height: 1.45;
  margin: 1em 0 0.55em 0;
}
p {
  margin: 0 0 0.82em 0;
  text-indent: 2em;
}
.toc-page p,
p.no-indent,
figure p,
li p {
  text-indent: 0;
}
.toc-page {
  text-align: left;
  margin-bottom: 1.4em;
}
.toc-line {
  margin: 0.36em 0;
  text-indent: 0;
  text-align: left;
}
.toc-part {
  margin: 1.15em 0 0.35em 0;
  font-weight: 700;
  text-indent: 0;
}
.toc-chapter {
  margin: 0.75em 0 0.25em 0;
  font-weight: 700;
  text-indent: 0;
}
ul.bullet-list {
  list-style-type: disc;
  margin: 0.75em 0 1em 0;
  padding-left: 1.35em;
}
ul.bullet-list li {
  margin: 0.42em 0;
  line-height: 1.72;
}
figure {
  margin: 1.05em 0;
  text-align: center;
  page-break-inside: avoid;
}
figure img,
img {
  max-width: 100%;
  height: auto;
  display: block;
  margin: 0 auto;
}
figcaption {
  margin-top: 0.45em;
  font-size: 0.9em;
  line-height: 1.55;
  color: #555;
}
""".strip() + "\n"


@dataclass
class PageResult:
    pdf_page: int
    markdown: str
    images: dict[str, bytes]


def require_token() -> str:
    token = os.getenv("MINERU_API_TOKEN", "").strip()
    if not token:
        raise SystemExit("MINERU_API_TOKEN is not set")
    return token


def headers(token: str) -> dict[str, str]:
    return {"Content-Type": "application/json", "Authorization": f"Bearer {token}"}


def split_pages(pdf_path: Path, start_page: int, end_page: int, out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    doc = fitz.open(pdf_path)
    if start_page < 1 or end_page > doc.page_count or start_page > end_page:
        raise ValueError(f"invalid page range {start_page}-{end_page}; PDF has {doc.page_count} pages")
    paths: list[Path] = []
    for page_no in range(start_page, end_page + 1):
        target = out_dir / f"page_{page_no:04d}.pdf"
        single = fitz.open()
        single.insert_pdf(doc, from_page=page_no - 1, to_page=page_no - 1)
        single.save(target)
        single.close()
        paths.append(target)
    doc.close()
    return paths


def request_upload_urls(files: list[Path], token: str) -> tuple[str, list[str]]:
    resp = requests.post(
        f"{API_BASE}/api/v4/file-urls/batch",
        json={
            "files": [{"name": path.name} for path in files],
            "model_version": MODEL_VERSION,
            "is_ocr": True,
            "enable_formula": False,
            "enable_table": True,
            "language": LANGUAGE,
        },
        headers=headers(token),
        timeout=60,
    )
    resp.raise_for_status()
    payload = resp.json()
    if payload.get("code") != 0:
        raise RuntimeError(f"upload URL request failed: {payload}")
    data = payload["data"]
    return data["batch_id"], data["file_urls"]


def upload_files(files: list[Path], upload_urls: list[str]) -> None:
    if len(files) != len(upload_urls):
        raise RuntimeError("upload URL count does not match file count")

    def upload_one(path: Path, url: str) -> None:
        with path.open("rb") as f:
            resp = requests.put(url, data=f, timeout=300)
        if resp.status_code not in (200, 204):
            raise RuntimeError(f"upload failed for {path.name}: HTTP {resp.status_code}")

    workers = min(UPLOAD_WORKERS, len(files))
    if workers <= 1:
        for path, url in zip(files, upload_urls):
            upload_one(path, url)
        return

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [executor.submit(upload_one, path, url) for path, url in zip(files, upload_urls)]
        for future in as_completed(futures):
            future.result()


def poll_batch(batch_id: str, token: str) -> list[dict[str, Any]]:
    url = f"{API_BASE}/api/v4/extract-results/batch/{batch_id}"
    started = time.time()
    while True:
        if time.time() - started > MAX_POLL_TIME:
            raise TimeoutError(f"timed out waiting for batch {batch_id}")
        resp = requests.get(url, headers=headers(token), timeout=30)
        if resp.status_code == 401:
            raise RuntimeError("MinerU API unauthorized")
        if resp.status_code == 200:
            payload = resp.json()
            if payload.get("code") == 0 and isinstance(payload.get("data"), dict):
                results = payload["data"].get("extract_result", [])
                if results and all(item.get("state") in {"done", "failed"} for item in results):
                    failed = [item for item in results if item.get("state") == "failed"]
                    if failed:
                        raise RuntimeError(f"MinerU failed for {len(failed)} file(s): {failed[:2]}")
                    return results
        time.sleep(POLL_INTERVAL)


def download_zip(zip_url: str, token: str) -> bytes:
    resp = requests.get(zip_url, headers=headers(token), timeout=120)
    resp.raise_for_status()
    return resp.content


def parse_result_zip(zip_bytes: bytes) -> tuple[str, dict[str, bytes]]:
    images: dict[str, bytes] = {}
    markdown_parts: list[str] = []
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        for name in sorted(zf.namelist()):
            lower = name.lower()
            if lower.endswith(".md"):
                markdown_parts.append(zf.read(name).decode("utf-8", "replace"))
            elif lower.endswith((".jpg", ".jpeg", ".png", ".gif", ".webp")):
                images[name] = zf.read(name)
    return "\n\n".join(part.strip() for part in markdown_parts if part.strip()), images


def run_mineru_pages(pdf_path: Path, start_page: int, end_page: int, work_dir: Path) -> list[PageResult]:
    token = require_token()
    pages_dir = work_dir / "single_pages"
    raw_dir = work_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    files = split_pages(pdf_path, start_page, end_page, pages_dir)

    batch_id, upload_urls = request_upload_urls(files, token)
    (raw_dir / "batch_id.txt").write_text(batch_id, encoding="utf-8")
    upload_files(files, upload_urls)
    results = poll_batch(batch_id, token)
    (raw_dir / "batch_results.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    by_name = {item.get("file_name") or item.get("name") or item.get("file"): item for item in results}
    page_results: list[PageResult] = []
    for file_path in files:
        item = by_name.get(file_path.name)
        if item is None:
            matches = [candidate for candidate in results if file_path.name in json.dumps(candidate, ensure_ascii=False)]
            item = matches[0] if matches else None
        if item is None or not item.get("full_zip_url"):
            raise RuntimeError(f"no completed result for {file_path.name}")
        zip_bytes = download_zip(item["full_zip_url"], token)
        zip_path = raw_dir / f"{file_path.stem}.zip"
        zip_path.write_bytes(zip_bytes)
        markdown, images = parse_result_zip(zip_bytes)
        pdf_page = int(re.search(r"page_(\d+)", file_path.stem).group(1))
        (raw_dir / f"{file_path.stem}.md").write_text(markdown, encoding="utf-8")
        page_results.append(PageResult(pdf_page=pdf_page, markdown=markdown, images=images))

    return sorted(page_results, key=lambda page: page.pdf_page)


OCR_META_PATTERNS = (
    "the ocr result should be empty",
    "according to rule",
    "no text or placeholder characters should be output",
)
BULLET_RE = re.compile(r"^\s*(?:[-–—−•●·‧○◦▪◆◇]\s*)+(.+?)\s*$")
CAPTION_RE = re.compile(r"^\s*(?:图|表)\s*[0-9一二三四五六七八九十]+(?:\s*[-－—]\s*[0-9一二三四五六七八九十]+)?\b")
TERM_RE = re.compile(r"^\s*([^：:。！？!?]{2,48})[：:]\s*(.{3,})$")


def normalize_math_fragment(value: str) -> str:
    value = value.strip()
    value = value.replace("\\times", "×").replace("\\%", "%")
    value = re.sub(r"\s*/\s*", "/", value)
    value = re.sub(r"\^\{([^{}]+)\}", r"^\1", value)
    value = re.sub(r"\^([①-⑳])", r"\1", value)
    value = value.replace("{", "").replace("}", "")
    value = value.replace("\\", "")
    return value.strip()


def clean_line(line: str) -> str:
    line = line.strip()
    line = re.sub(r"\$\s*\^\{?(\d{1,3}|[①-⑳])\}?\s*\$", r"\1", line)
    line = re.sub(r"\$([^$]+)\$", lambda match: normalize_math_fragment(match.group(1)), line)
    line = line.replace("\\-", "-")
    return re.sub(r"\s+", " ", line).strip()


def is_noise(line: str) -> bool:
    lowered = line.lower()
    return any(pattern in lowered for pattern in OCR_META_PATTERNS)


def plain_text(html_text: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", html_text)).strip()


def is_term_list_item(text: str) -> bool:
    match = TERM_RE.match(text)
    if not match:
        return False
    label = match.group(1).strip()
    rest = match.group(2).strip()
    if not rest or len(label) > 42:
        return False
    if any(mark in label for mark in "“”《》？?。！!；;"):
        return False
    if label.startswith(("但", "这", "那个", "不妨", "如果", "想想")) or "问题" in label or "一句话" in label:
        return False
    label_keywords = ("神迹", "技术", "系统", "工具", "模型", "AI", "3D", "无人机", "网络战", "脑机接口", "全息")
    if any(key in label for key in label_keywords):
        return True
    return len(label) <= 28 and rest.startswith(("能", "能够", "可以", "可", "无须", "通过", "如"))


def is_image_ocr_note(text: str) -> bool:
    if len(text) > 180:
        return False
    mathish = "$" in text or "\\" in text or re.search(r"\b1e\b|\^\{|\^\d|\^n", text, re.IGNORECASE)
    return bool(mathish and any(word in text for word in ("表示", "科学", "计算", "浮点", "次方", "times")))


def is_toc_page(markdown: str, pdf_page: int) -> bool:
    text = markdown
    digit_lines = sum(1 for line in text.splitlines() if re.search(r"\b\d{3}\b\s*$", line.strip()))
    chapter_lines = sum(1 for line in text.splitlines() if re.search(r"第\s*\d+\s*章.+\b\d{3}\b\s*$", line.strip()))
    return digit_lines >= 5 or chapter_lines >= 2 or "目录" in text[:300]


def classify_toc_line(text: str) -> str:
    if re.match(r"^第\s*[0-9一二三四五六七八九十]+\s*章\b", text):
        return "toc-chapter"
    if "部分" in text or text in {"尾声", "致谢", "注释", "重磅赞誉"} or re.match(r"^附录\s*[A-ZＡ-Ｚ]", text):
        return "toc-part"
    return "toc-line"


def markdown_lines(markdown: str, strip_headings: bool = True) -> list[str]:
    lines: list[str] = []
    for raw in markdown.splitlines():
        line = clean_line(raw)
        if not line or is_noise(line):
            continue
        if strip_headings:
            line = re.sub(r"^#{1,6}\s*", "", line).strip()
        if line in {"natural_image", "text_image"}:
            continue
        lines.append(line)
    return lines


def image_markdown_path(text: str) -> str | None:
    match = re.match(r"^!\[[^\]]*\]\(([^)]+)\)\s*$", text.strip())
    return match.group(1).strip() if match else None


def join_wrapped_lines(lines: list[str]) -> str:
    text = ""
    for line in lines:
        if not text:
            text = line
            continue
        left = text[-1]
        right = line[0]
        if re.search(r"[\u4e00-\u9fff]", left) and re.search(r"[\u4e00-\u9fff]", right):
            text += line
        elif right in "，。！？；：、）】》”’」』" or left in "（【《“‘「『":
            text += line
        else:
            text += " " + line
    return text.strip()


def markdown_blocks(markdown: str, strip_headings: bool = True) -> list[str]:
    blocks: list[str] = []
    current: list[str] = []

    def flush() -> None:
        nonlocal current
        if current:
            blocks.append(join_wrapped_lines(current))
            current = []

    for raw in markdown.splitlines():
        line = clean_line(raw)
        if not line or is_noise(line):
            flush()
            continue
        if strip_headings:
            line = re.sub(r"^#{1,6}\s*", "", line).strip()
        if line in {"natural_image", "text_image"}:
            flush()
            continue
        if image_markdown_path(line) or BULLET_RE.match(line) or CAPTION_RE.match(line) or re.match(r"^#{1,6}\s+", line):
            flush()
            blocks.append(line)
            continue
        current.append(line)
    flush()
    return blocks


def paragraphize(lines: list[str]) -> list[str]:
    paragraphs: list[str] = []
    current = ""
    terminal = tuple("。！？；;.!?」』）】》")
    for line in lines:
        if line.startswith("!["):
            if current:
                paragraphs.append(current)
                current = ""
            paragraphs.append(line)
            continue
        if CAPTION_RE.match(line):
            if current:
                paragraphs.append(current)
                current = ""
            paragraphs.append(line)
            continue
        if not current:
            current = line
        elif current.endswith(terminal):
            paragraphs.append(current)
            current = line
        else:
            current += " " + line
    if current:
        paragraphs.append(current)
    return paragraphs


def line_to_html(text: str, toc: bool = False, image_refs: dict[str, str] | None = None) -> str:
    image_ref = image_markdown_path(text)
    if image_ref:
        href = (image_refs or {}).get(image_ref) or (image_refs or {}).get(Path(image_ref).name)
        if href:
            return f'<figure><img src="{html.escape(href)}" alt=""/></figure>'
        return ""
    escaped = html.escape(text)
    if toc:
        return f'<p class="{classify_toc_line(text)}">{escaped}</p>'
    if text.startswith("#"):
        return f"<h2>{html.escape(text.lstrip('#').strip())}</h2>"
    bullet = BULLET_RE.match(text)
    if bullet:
        return f"<li>{html.escape(bullet.group(1).strip())}</li>"
    if len(text) <= 34 and (text.isupper() or text.endswith("神迹") or "部分" in text):
        return f'<p class="no-indent">{escaped}</p>'
    return f"<p>{escaped}</p>"


def page_body_html(page: PageResult, image_refs: dict[str, str]) -> str:
    lines = markdown_lines(page.markdown)
    if is_toc_page(page.markdown, page.pdf_page):
        body = "\n".join(line_to_html(line, toc=True, image_refs=image_refs) for line in lines)
        return f'<section class="source-page toc-page" id="pdf-page-{page.pdf_page}">\n{body}\n</section>'

    paragraphs = paragraphize(lines)
    chunks: list[str] = []
    list_open = False
    for para in paragraphs:
        bullet = BULLET_RE.match(para)
        term_item = is_term_list_item(para)
        if bullet or term_item:
            if not list_open:
                chunks.append('<ul class="bullet-list">')
                list_open = True
            text = bullet.group(1).strip() if bullet else para
            chunks.append(f"<li>{html.escape(text)}</li>")
            continue
        if list_open:
            chunks.append("</ul>")
            list_open = False
        html_chunk = line_to_html(para, image_refs=image_refs)
        if html_chunk:
            chunks.append(html_chunk)
    if list_open:
        chunks.append("</ul>")
    return f'<section class="source-page" id="pdf-page-{page.pdf_page}">\n' + "\n".join(chunks) + "\n</section>"


def is_terminal_text(text: str) -> bool:
    text = re.sub(r"<[^>]+>", "", text).strip()
    return bool(text) and text[-1] in set("。！？；;.!?…」』）】》”")


def page_image_refs(page: PageResult, image_items: list[tuple[str, str, bytes, str]]) -> dict[str, str]:
    image_refs: dict[str, str] = {}
    for original_name, image_bytes in page.images.items():
        suffix = Path(original_name).suffix.lower() or ".jpg"
        safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", Path(original_name).name)
        epub_name = f"images/page_{page.pdf_page:04d}_{safe_name}"
        href_from_text = "../" + epub_name
        image_refs[original_name] = href_from_text
        image_refs[Path(original_name).name] = href_from_text
        media_type = mimetypes.types_map.get(suffix, "image/jpeg")
        image_items.append((f"img_{page.pdf_page}_{len(image_items)}", epub_name, image_bytes, media_type))
    return image_refs


def body_blocks(page: PageResult, image_refs: dict[str, str]) -> list[tuple[str, str]]:
    paragraphs = markdown_blocks(page.markdown, strip_headings=False)
    blocks: list[tuple[str, str]] = [("anchor", f'<span class="page-anchor" id="pdf-page-{page.pdf_page}"></span>')]
    i = 0
    while i < len(paragraphs):
        para = paragraphs[i]
        image_ref = image_markdown_path(para)
        if image_ref:
            href = image_refs.get(image_ref) or image_refs.get(Path(image_ref).name)
            if href:
                caption_parts: list[str] = []
                j = i + 1
                while j < len(paragraphs) and len(caption_parts) < 1:
                    candidate = paragraphs[j]
                    if is_image_ocr_note(candidate):
                        j += 1
                        continue
                    if CAPTION_RE.match(candidate):
                        caption_parts.append(candidate)
                        j += 1
                        continue
                    break
                caption_html = "".join(f"<figcaption>{html.escape(part)}</figcaption>" for part in caption_parts)
                blocks.append(("raw", f'<figure><img src="{html.escape(href)}" alt=""/>{caption_html}</figure>'))
                if caption_parts:
                    i = j
                    continue
            i += 1
            continue
        if re.match(r"^#{1,6}\s+", para):
            text = re.sub(r"^#{1,6}\s+", "", para).strip()
            blocks.append(("raw", f"<h2>{html.escape(text)}</h2>"))
            i += 1
            continue
        bullet = BULLET_RE.match(para)
        term_item = is_term_list_item(para)
        if bullet or term_item:
            text = bullet.group(1).strip() if bullet else para
            blocks.append(("li", html.escape(text)))
            i += 1
            continue
        if len(para) <= 34 and not para.endswith(("：", ":")) and (para.isupper() or para.endswith("神迹") or "部分" in para):
            blocks.append(("raw", f'<p class="no-indent">{html.escape(para)}</p>'))
            i += 1
            continue
        blocks.append(("p", html.escape(para)))
        i += 1
    return blocks


def merge_body_blocks(blocks: list[tuple[str, str]]) -> list[tuple[str, str]]:
    merged: list[tuple[str, str]] = []
    pending_anchors: list[str] = []
    for kind, value in blocks:
        if kind == "anchor":
            pending_anchors.append(value)
            continue
        if kind == "p":
            if pending_anchors and merged and merged[-1][0] == "p" and not is_terminal_text(merged[-1][1]):
                merged_value = merged[-1][1].rstrip() + "".join(pending_anchors) + value.lstrip()
                merged[-1] = ("li" if is_term_list_item(plain_text(merged_value)) else "p", merged_value)
                pending_anchors = []
                continue
            if pending_anchors:
                merged.extend(("raw", anchor) for anchor in pending_anchors)
                pending_anchors = []
            merged.append((kind, value))
            continue
        if pending_anchors:
            merged.extend(("raw", anchor) for anchor in pending_anchors)
            pending_anchors = []
        merged.append((kind, value))
    if pending_anchors:
        merged.extend(("raw", anchor) for anchor in pending_anchors)
    return merged


def render_blocks(blocks: list[tuple[str, str]]) -> str:
    chunks: list[str] = []
    list_open = False
    for kind, value in blocks:
        if kind == "li":
            if not list_open:
                chunks.append('<ul class="bullet-list">')
                list_open = True
            chunks.append(f"<li>{value}</li>")
            continue
        if list_open:
            chunks.append("</ul>")
            list_open = False
        if kind == "p":
            chunks.append(f"<p>{value}</p>")
        else:
            chunks.append(value)
    if list_open:
        chunks.append("</ul>")
    return "\n".join(chunks)


def xhtml_doc(title: str, css_href: str, body_html: str) -> str:
    return f"""<?xml version='1.0' encoding='utf-8'?>
<html xmlns="http://www.w3.org/1999/xhtml" lang="zh-Hans" xml:lang="zh-Hans">
  <head>
    <title>{html.escape(title)}</title>
    <link rel="stylesheet" type="text/css" href="{css_href}"/>
  </head>
  <body>
{body_html}
  </body>
</html>
"""


def write_epub(pages: list[PageResult], output: Path, title: str, author: str) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    text_items: list[tuple[str, str, str]] = []
    image_items: list[tuple[str, str, bytes, str]] = []
    toc_sections: list[str] = []
    body_all_blocks: list[tuple[str, str]] = []

    for page in pages:
        image_refs = page_image_refs(page, image_items)
        if is_toc_page(page.markdown, page.pdf_page):
            toc_sections.append(page_body_html(page, image_refs))
        else:
            body_all_blocks.extend(body_blocks(page, image_refs))

    if toc_sections:
        text_items.append(("toc_doc", "text/toc.xhtml", xhtml_doc("目录", "../style/nav.css", "\n".join(toc_sections))))
    body_html = render_blocks(merge_body_blocks(body_all_blocks))
    text_items.append(("body_doc", "text/body.xhtml", xhtml_doc(title, "../style/nav.css", body_html)))

    manifest_items = [
        '<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>',
        '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>',
        '<item id="style" href="style/nav.css" media-type="text/css"/>',
    ]
    spine_items = []
    nav_lis = []
    ncx_points = []
    nav_labels = {"toc_doc": "目录", "body_doc": "正文"}
    for idx, (item_id, href, _content) in enumerate(text_items, start=1):
        manifest_items.append(f'<item id="{item_id}" href="{href}" media-type="application/xhtml+xml"/>')
        spine_items.append(f'<itemref idref="{item_id}"/>')
        label = nav_labels.get(item_id, "正文")
        nav_lis.append(f'<li><a href="{href}">{label}</a></li>')
        ncx_points.append(
            f'''<navPoint id="{item_id}" playOrder="{idx}">
      <navLabel><text>{label}</text></navLabel>
      <content src="{href}"/>
    </navPoint>'''
        )
    for item_id, href, _image_bytes, media_type in image_items:
        manifest_items.append(f'<item id="{item_id}" href="{href}" media-type="{media_type}"/>')

    opf = f"""<?xml version='1.0' encoding='utf-8'?>
<package xmlns="http://www.idpf.org/2007/opf" unique-identifier="bookid" version="3.0">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="bookid">direct-mineru-{int(time.time())}</dc:identifier>
    <dc:title>{html.escape(title)}</dc:title>
    <dc:language>zh-Hans</dc:language>
    <dc:creator>{html.escape(author)}</dc:creator>
  </metadata>
  <manifest>
    {' '.join(manifest_items)}
  </manifest>
  <spine toc="ncx">
    {' '.join(spine_items)}
  </spine>
</package>
"""
    nav = f"""<?xml version='1.0' encoding='utf-8'?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" lang="zh-Hans" xml:lang="zh-Hans">
  <head><title>{html.escape(title)}</title></head>
  <body>
    <nav epub:type="toc" id="toc" role="doc-toc">
      <h2>{html.escape(title)}</h2>
      <ol>{''.join(nav_lis)}</ol>
    </nav>
  </body>
</html>
"""
    ncx = f"""<?xml version='1.0' encoding='utf-8'?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">
  <head>
    <meta name="dtb:uid" content="direct-mineru"/>
    <meta name="dtb:depth" content="1"/>
    <meta name="dtb:totalPageCount" content="0"/>
    <meta name="dtb:maxPageNumber" content="0"/>
  </head>
  <docTitle><text>{html.escape(title)}</text></docTitle>
  <navMap>{''.join(ncx_points)}</navMap>
</ncx>
"""

    with zipfile.ZipFile(output, "w") as zf:
        zf.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        zf.writestr(
            "META-INF/container.xml",
            """<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="EPUB/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
""",
        )
        zf.writestr("EPUB/content.opf", opf)
        zf.writestr("EPUB/nav.xhtml", nav)
        zf.writestr("EPUB/toc.ncx", ncx)
        zf.writestr("EPUB/style/nav.css", STYLE_CSS)
        for _item_id, href, content in text_items:
            zf.writestr(f"EPUB/{href}", content)
        for _item_id, href, image_bytes, _media_type in image_items:
            zf.writestr(f"EPUB/{href}", image_bytes)


def load_pages_from_raw(work_dir: Path) -> list[PageResult]:
    raw_dir = work_dir / "raw"
    pages: list[PageResult] = []
    for zip_path in sorted(raw_dir.glob("page_*.zip")):
        match = re.search(r"page_(\d+)", zip_path.stem)
        if not match:
            continue
        markdown, images = parse_result_zip(zip_path.read_bytes())
        pages.append(PageResult(pdf_page=int(match.group(1)), markdown=markdown, images=images))
    if not pages:
        raise RuntimeError(f"no raw page ZIPs found in {raw_dir}")
    return sorted(pages, key=lambda page: page.pdf_page)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pdf", required=True, type=Path)
    parser.add_argument("--start-page", required=True, type=int)
    parser.add_argument("--end-page", required=True, type=int)
    parser.add_argument("--work-dir", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--title", default="Converted Book")
    parser.add_argument("--author", default="")
    parser.add_argument("--reuse", action="store_true")
    args = parser.parse_args()

    args.work_dir.mkdir(parents=True, exist_ok=True)
    manifest = args.work_dir / "pages.json"
    if args.reuse and manifest.exists():
        pages = load_pages_from_raw(args.work_dir)
    else:
        pages = run_mineru_pages(args.pdf, args.start_page, args.end_page, args.work_dir)
        manifest.write_text(
            json.dumps(
                [{"pdf_page": page.pdf_page, "markdown": page.markdown} for page in pages],
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    write_epub(pages, args.output, args.title, args.author)
    print(f"pages={len(pages)} output={args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
