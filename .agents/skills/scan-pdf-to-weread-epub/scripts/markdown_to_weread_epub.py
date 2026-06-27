#!/usr/bin/env python3
"""Build a WeRead-friendly EPUB from normalized Markdown."""

from __future__ import annotations

import argparse
import html
import mimetypes
import re
import time
import zipfile
from pathlib import Path


STYLE_CSS = """
body {
  font-family: "PingFang SC", "Songti SC", "Noto Serif CJK SC", serif;
  line-height: 1.72;
  text-align: justify;
  margin: 0.8em 0.9em;
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
h3 {
  font-size: 1.05em;
  line-height: 1.45;
  margin: 0.9em 0 0.5em 0;
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

CAPTION_RE = re.compile(r"^\s*(?:图|表)\s*[0-9一二三四五六七八九十]+(?:\s*[-－—]\s*[0-9一二三四五六七八九十]+)?\b")
TERMINAL_CHARS = set("。！？；;.!?…」』）】》”")
TOC_PAGE_RE = re.compile(r"(?:^|[\s　])\d{3}\s*$")
BODY_SECTION_RE = re.compile(
    r"^(?:"
    r"第\s*[0-9一二三四五六七八九十]+\s*章\b|"
    r"第[一二三四五六七八九十0-9]+部分\b|"
    r"chapter\s+\d+\b"
    r")",
    re.IGNORECASE,
)
STRUCTURAL_TOC_RE = re.compile(
    r"^(?:"
    r"推荐序[一二三四五六七八九十0-9]*|"
    r"译者序|中文版序|前言|导言|"
    r"第\s*[0-9一二三四五六七八九十]+\s*章|"
    r"(?:第)?[一二三四五六七八九十0-9]+部分|"
    r"附录|致谢|注释|重磅赞誉"
    r")"
)


def markdown_blocks(markdown: str) -> list[str]:
    return [block.strip() for block in re.split(r"\n\s*\n", markdown.strip()) if block.strip()]


def image_markdown_path(text: str) -> str | None:
    match = re.match(r"^!\[[^\]]*\]\(([^)]+)\)\s*$", text.strip())
    return match.group(1).strip() if match else None


def classify_toc_line(text: str) -> str:
    if re.match(r"^第\s*[0-9一二三四五六七八九十]+\s*章\b", text):
        return "toc-chapter"
    if "部分" in text or text in {"尾声", "致谢", "注释", "重磅赞誉"} or re.match(r"^附录\s*[A-ZＡ-Ｚ]", text):
        return "toc-part"
    return "toc-line"


def normalize_title(text: str) -> str:
    text = re.sub(r"^#{1,6}\s*", "", text).strip()
    text = re.sub(r"\s+\d{3}\s*$", "", text).strip()
    text = re.sub(r"^第\s*[0-9一二三四五六七八九十]+\s*章\s*", "", text).strip()
    text = text.lower()
    return re.sub(r"[\s,，.。:：;；、!！?？\-—－_《》“”\"'‘’（）()·]+", "", text)


def candidate_title_keys(text: str) -> list[str]:
    base = re.sub(r"^#{1,6}\s*", "", text).strip()
    base = re.sub(r"\s+\d{3}\s*$", "", base).strip()
    candidates = [base]
    candidates.append(re.sub(r"^第\s*[0-9一二三四五六七八九十]+\s*章\s*", "", base).strip())
    candidates.append(re.sub(r"^推荐序[一二三四五六七八九十0-9]*\s*", "", base).strip())
    candidates.append(re.sub(r"^(?:译者序|中文版序|前言|导言)\s*", "", base).strip())
    candidates.append(re.sub(r"^附录\s*[A-ZＡ-Ｚ]\s*", "", base).strip())
    candidates.append(re.sub(r"^(附录)\s*([A-ZＡ-Ｚ])\s*", r"\1\2 ", base).strip())

    keys: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = normalize_title(candidate)
        if key and key not in seen:
            keys.append(key)
            seen.add(key)
    return keys


def visible_text(block: str) -> str:
    heading = re.match(r"^(#{1,6})\s+(.+)$", block)
    if heading:
        return heading.group(2).strip()
    if block.startswith("- "):
        return block[2:].strip()
    return block.strip()


def is_anchor_candidate(block: str) -> bool:
    if block.startswith("<!--") and block.endswith("-->"):
        return False
    if image_markdown_path(block) or block.startswith("- "):
        return False
    heading = re.match(r"^(#{1,6})\s+(.+)$", block)
    if heading:
        return True
    text = visible_text(block)
    return 2 <= len(text) <= 90 and text[-1] not in TERMINAL_CHARS


def build_body_targets(blocks: list[str], href: str, anchor_prefix: str) -> tuple[dict[int, str], dict[str, str]]:
    ids_by_index: dict[int, str] = {}
    targets: dict[str, str] = {}
    counter = 1
    for idx, block in enumerate(blocks):
        if not is_anchor_candidate(block):
            continue
        keys = candidate_title_keys(visible_text(block))
        if not keys:
            continue
        anchor_id = f"{anchor_prefix}-{counter:03d}"
        counter += 1
        ids_by_index[idx] = anchor_id
        for key in keys:
            targets.setdefault(key, f"{href}#{anchor_id}")
    return ids_by_index, targets


def toc_link_for(block: str, toc_targets: dict[str, str]) -> str | None:
    if not is_linkable_toc_line(block):
        return None
    for key in candidate_title_keys(block):
        if key in toc_targets:
            return toc_targets[key]
    return None


def is_linkable_toc_line(text: str) -> bool:
    text = text.strip()
    return bool(TOC_PAGE_RE.search(text) or STRUCTURAL_TOC_RE.match(text))


def is_frontmatter_toc_line(text: str) -> bool:
    return bool(re.match(r"^(?:推荐序|译者序|中文版序|前言|导言)", text.strip()))


def nav_href_from_text_href(href: str) -> str:
    if href.startswith("text/"):
        return href
    if href.startswith(("frontmatter.xhtml", "book_toc.xhtml", "body.xhtml")):
        return "text/" + href
    return href


def toc_nav_label(text: str) -> str:
    text = re.sub(r"\s+\d{3}\s*$", "", text.strip())
    return re.sub(r"\s+", " ", text).strip()


def toc_nav_level(text: str) -> int:
    stripped = toc_nav_label(text)
    if re.match(r"^(?:推荐序|译者序|中文版序|前言|导言|重磅赞誉|附录|致谢|注释)", stripped):
        return 1
    if re.match(r"^(?:第)?[一二三四五六七八九十0-9]+部分", stripped):
        return 1
    if re.match(r"^第\s*[0-9一二三四五六七八九十]+\s*章", stripped):
        return 2
    return 3


def build_toc_nav_entries(toc_blocks: list[str], toc_targets: dict[str, str]) -> list[tuple[int, str, str]]:
    entries: list[tuple[int, str, str]] = []
    for block in toc_blocks:
        href = toc_link_for(block, toc_targets)
        if not href:
            continue
        label = toc_nav_label(block)
        if not label:
            continue
        entries.append((toc_nav_level(block), label, nav_href_from_text_href(href)))
    return entries


def render_nav_ol(entries: list[tuple[int, str, str]]) -> str:
    if not entries:
        return ""
    roots: list[dict[str, object]] = []
    stack: list[tuple[int, list[dict[str, object]]]] = [(0, roots)]
    for raw_level, label, href in entries:
        level = max(1, min(raw_level, 3))
        while stack and stack[-1][0] >= level:
            stack.pop()
        parent = stack[-1][1] if stack else roots
        node: dict[str, object] = {"label": label, "href": href, "children": []}
        parent.append(node)
        stack.append((level, node["children"]))  # type: ignore[arg-type]

    def render_nodes(nodes: list[dict[str, object]]) -> str:
        lis: list[str] = []
        for node in nodes:
            children = node["children"]  # type: ignore[assignment]
            child_html = render_nodes(children) if children else ""  # type: ignore[arg-type]
            lis.append(
                f'<li><a href="{html.escape(str(node["href"]))}">'
                f'{html.escape(str(node["label"]))}</a>{child_html}</li>'
            )
        return "<ol>" + "".join(lis) + "</ol>"

    return render_nodes(roots)


def id_attr(anchor_id: str | None) -> str:
    return f' id="{html.escape(anchor_id)}"' if anchor_id else ""


def resolve_image(path_text: str, markdown_path: Path, image_map: dict[str, str], image_items: list[tuple[str, str, bytes, str]]) -> str:
    if path_text in image_map:
        return image_map[path_text]
    source = (markdown_path.parent / path_text).resolve()
    if not source.exists():
        source = (markdown_path.parent / Path(path_text).name).resolve()
    if not source.exists():
        raise FileNotFoundError(f"image not found: {path_text}")

    safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", source.name)
    epub_name = f"images/{safe_name}"
    href = "../" + epub_name
    media_type = mimetypes.types_map.get(source.suffix.lower(), "image/jpeg")
    image_items.append((f"img_{len(image_items) + 1}", epub_name, source.read_bytes(), media_type))
    image_map[path_text] = href
    image_map[source.name] = href
    return href


def render_blocks(
    blocks: list[str],
    markdown_path: Path,
    image_items: list[tuple[str, str, bytes, str]],
    toc: bool = False,
    body_anchor_ids: dict[int, str] | None = None,
    toc_targets: dict[str, str] | None = None,
) -> str:
    chunks: list[str] = []
    image_map: dict[str, str] = {}
    list_open = False
    i = 0

    def close_list() -> None:
        nonlocal list_open
        if list_open:
            chunks.append("</ul>")
            list_open = False

    while i < len(blocks):
        block = blocks[i]
        if block.startswith("<!--") and block.endswith("-->"):
            i += 1
            continue
        image_ref = image_markdown_path(block)
        if image_ref:
            close_list()
            href = resolve_image(image_ref, markdown_path, image_map, image_items)
            caption_html = ""
            if i + 1 < len(blocks) and CAPTION_RE.match(blocks[i + 1]):
                caption_html = f"<figcaption>{html.escape(blocks[i + 1])}</figcaption>"
                i += 1
            chunks.append(f'<figure><img src="{html.escape(href)}" alt=""/>{caption_html}</figure>')
            i += 1
            continue

        heading = re.match(r"^(#{1,6})\s+(.+)$", block)
        if heading:
            close_list()
            level = min(len(heading.group(1)), 3)
            anchor_id = (body_anchor_ids or {}).get(i)
            chunks.append(f"<h{level}{id_attr(anchor_id)}>{html.escape(heading.group(2).strip())}</h{level}>")
            i += 1
            continue

        if block.startswith("- "):
            if not list_open:
                chunks.append('<ul class="bullet-list">')
                list_open = True
            chunks.append(f"<li>{html.escape(block[2:].strip())}</li>")
            i += 1
            continue

        close_list()
        if toc:
            href = toc_link_for(block, toc_targets or {})
            escaped = html.escape(block)
            content = f'<a href="{html.escape(href)}">{escaped}</a>' if href else escaped
            chunks.append(f'<p class="{classify_toc_line(block)}">{content}</p>')
        elif len(block) <= 34 and (block.isupper() or block.endswith("部分") or block.endswith("神迹")):
            anchor_id = (body_anchor_ids or {}).get(i)
            chunks.append(f'<p class="no-indent"{id_attr(anchor_id)}>{html.escape(block)}</p>')
        else:
            anchor_id = (body_anchor_ids or {}).get(i)
            chunks.append(f"<p{id_attr(anchor_id)}>{html.escape(block)}</p>")
        i += 1

    close_list()
    return "\n".join(chunks)


def xhtml_doc(title: str, css_href: str, body_html: str, body_class: str = "") -> str:
    class_attr = f' class="{body_class}"' if body_class else ""
    return f"""<?xml version='1.0' encoding='utf-8'?>
<html xmlns="http://www.w3.org/1999/xhtml" lang="zh-Hans" xml:lang="zh-Hans">
  <head>
    <title>{html.escape(title)}</title>
    <link rel="stylesheet" type="text/css" href="{css_href}"/>
  </head>
  <body{class_attr}>
{body_html}
  </body>
</html>
"""


def split_markdown(markdown: str) -> tuple[str, str]:
    toc_start = "<!-- book-toc-start -->"
    toc_end = "<!-- book-toc-end -->"
    if toc_start in markdown and toc_end in markdown:
        before, rest = markdown.split(toc_start, 1)
        toc_md, after = rest.split(toc_end, 1)
        body_md = "\n\n".join(part.strip() for part in [before, after] if part.strip())
        return toc_md.strip(), body_md.strip()

    return "", markdown.strip()


def split_frontmatter_blocks(blocks: list[str]) -> tuple[list[str], list[str]]:
    for idx, block in enumerate(blocks):
        if block.strip() == "<!-- body-start -->":
            return blocks[:idx], blocks[idx + 1 :]
        if BODY_SECTION_RE.search(visible_text(block)):
            return blocks[:idx], blocks[idx:]
    return [], blocks


def write_epub(markdown_path: Path, output: Path, title: str, author: str) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    markdown = markdown_path.read_text(encoding="utf-8")
    toc_md, body_md = split_markdown(markdown)
    image_items: list[tuple[str, str, bytes, str]] = []

    text_items: list[tuple[str, str, str]] = []
    all_body_blocks = markdown_blocks(body_md)
    front_blocks, body_blocks = split_frontmatter_blocks(all_body_blocks)
    toc_targets: dict[str, str] = {}
    front_targets: dict[str, str] = {}

    if front_blocks:
        front_anchor_ids, front_targets = build_body_targets(front_blocks, "frontmatter.xhtml", "front-sec")
        toc_targets.update(front_targets)
        front_html = render_blocks(front_blocks, markdown_path, image_items, toc=False, body_anchor_ids=front_anchor_ids)
        text_items.append(("frontmatter", "text/frontmatter.xhtml", xhtml_doc("前置内容", "../style/nav.css", front_html)))

    body_anchor_ids, body_targets = build_body_targets(body_blocks, "body.xhtml", "body-sec")
    toc_targets.update(body_targets)
    toc_blocks = markdown_blocks(toc_md) if toc_md else []
    for block in toc_blocks:
        if not is_frontmatter_toc_line(block):
            continue
        full_key = normalize_title(block)
        for key in candidate_title_keys(block)[1:]:
            if key in front_targets:
                toc_targets[full_key] = front_targets[key]
                break
    if toc_md:
        toc_html = render_blocks(toc_blocks, markdown_path, image_items, toc=True, toc_targets=toc_targets)
        text_items.append(("book_toc", "text/book_toc.xhtml", xhtml_doc("本书目录", "../style/nav.css", toc_html, "toc-page")))

    body_html = render_blocks(body_blocks, markdown_path, image_items, toc=False, body_anchor_ids=body_anchor_ids)
    text_items.append(("body", "text/body.xhtml", xhtml_doc("正文", "../style/nav.css", body_html)))

    manifest_items = [
        '<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>',
        '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>',
        '<item id="style" href="style/nav.css" media-type="text/css"/>',
    ]
    spine_items: list[str] = []
    labels = {"frontmatter": "前置内容", "book_toc": "本书目录", "body": "正文"}
    for item_id, href, _content in text_items:
        label = labels.get(item_id, item_id)
        manifest_items.append(f'<item id="{item_id}" href="{href}" media-type="application/xhtml+xml"/>')
        spine_items.append(f'<itemref idref="{item_id}"/>')
    for item_id, href, _image_bytes, media_type in image_items:
        manifest_items.append(f'<item id="{item_id}" href="{href}" media-type="{media_type}"/>')

    side_nav_entries: list[tuple[int, str, str]] = []
    if toc_md:
        side_nav_entries.append((1, "本书目录", "text/book_toc.xhtml"))
        side_nav_entries.extend(build_toc_nav_entries(toc_blocks, toc_targets))
    if not side_nav_entries:
        side_nav_entries = [(1, labels.get(item_id, item_id), href) for item_id, href, _content in text_items]
    nav_ol = render_nav_ol(side_nav_entries)
    ncx_points: list[str] = []
    for idx, (_level, label, href) in enumerate(side_nav_entries, start=1):
        ncx_points.append(
            f'''<navPoint id="nav-{idx:03d}" playOrder="{idx}">
      <navLabel><text>{html.escape(label)}</text></navLabel>
      <content src="{html.escape(href)}"/>
    </navPoint>'''
        )
    ncx_depth = max((level for level, _label, _href in side_nav_entries), default=1)

    opf = f"""<?xml version='1.0' encoding='utf-8'?>
<package xmlns="http://www.idpf.org/2007/opf" unique-identifier="bookid" version="3.0">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="bookid">markdown-flow-{int(time.time())}</dc:identifier>
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
      {nav_ol}
    </nav>
  </body>
</html>
"""
    ncx = f"""<?xml version='1.0' encoding='utf-8'?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">
  <head>
    <meta name="dtb:uid" content="markdown-flow"/>
    <meta name="dtb:depth" content="{ncx_depth}"/>
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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--markdown", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--title", default="Converted Book")
    parser.add_argument("--author", default="")
    args = parser.parse_args()
    write_epub(args.markdown, args.output, args.title, args.author)
    print(f"output={args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
