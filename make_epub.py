#!/usr/bin/env python3
"""Convert extracted Patreon chapters JSON into an EPUB ebook."""

import json
import sys
from html import escape
from pathlib import Path

from ebooklib import epub


def prosemirror_to_html(doc: dict) -> str:
    if not doc or doc.get("type") != "doc":
        return ""
    return _render_nodes(doc.get("content", []))


def _render_nodes(nodes: list) -> str:
    return "".join(_render_node(n) for n in nodes)


def _render_node(node: dict) -> str:
    ntype = node.get("type", "")
    content = node.get("content", [])
    attrs = node.get("attrs", {})

    if ntype == "paragraph":
        return f"<p>{_render_nodes(content)}</p>\n"
    elif ntype == "heading":
        level = attrs.get("level", 2)
        return f"<h{level}>{_render_nodes(content)}</h{level}>\n"
    elif ntype == "text":
        text = escape(node.get("text", ""))
        for mark in node.get("marks", []):
            mtype = mark.get("type", "")
            mattrs = mark.get("attrs", {})
            if mtype in ("bold", "strong"):
                text = f"<strong>{text}</strong>"
            elif mtype in ("italic", "em"):
                text = f"<em>{text}</em>"
            elif mtype == "underline":
                text = f"<u>{text}</u>"
            elif mtype == "strike":
                text = f"<s>{text}</s>"
            elif mtype == "link":
                href = escape(mattrs.get("href", ""))
                text = f'<a href="{href}">{text}</a>'
            elif mtype == "code":
                text = f"<code>{text}</code>"
        return text
    elif ntype == "hard_break":
        return "<br/>"
    elif ntype == "horizontal_rule":
        return "<hr/>\n"
    elif ntype == "blockquote":
        return f"<blockquote>{_render_nodes(content)}</blockquote>\n"
    elif ntype in ("bullet_list", "bulletList"):
        return f"<ul>{_render_nodes(content)}</ul>\n"
    elif ntype in ("ordered_list", "orderedList"):
        return f"<ol>{_render_nodes(content)}</ol>\n"
    elif ntype in ("list_item", "listItem"):
        return f"<li>{_render_nodes(content)}</li>\n"
    elif ntype in ("code_block", "codeBlock"):
        return f"<pre><code>{_render_nodes(content)}</code></pre>\n"
    elif ntype == "image":
        src = attrs.get("src", "")
        alt = attrs.get("alt", "")
        return f'<img src="{escape(src)}" alt="{escape(alt)}"/>\n'
    else:
        return _render_nodes(content)


CHAPTER_CSS = """
body { font-family: Georgia, serif; line-height: 1.8; color: #333; margin: 1em; }
h1, h2 { margin-top: 1em; }
p { margin: 0.6em 0; text-align: justify; }
.date { color: #666; font-size: 0.9em; margin-bottom: 1em; }
.locked { color: #999; font-style: italic; padding: 1em; }
blockquote { margin: 1em 2em; font-style: italic; border-left: 3px solid #ccc; padding-left: 1em; }
"""


def main():
    json_path = sys.argv[1] if len(sys.argv) > 1 else "chapters.json"
    output_path = sys.argv[2] if len(sys.argv) > 2 else "zogarth_chapters.epub"

    print(f"Loading {json_path}...")
    posts = json.loads(Path(json_path).read_text())

    # Sort oldest first
    sorted_posts = sorted(
        posts,
        key=lambda p: p.get("attributes", {}).get("published_at", ""),
    )

    book = epub.EpubBook()
    book.set_identifier("zogarth-primal-hunter-patreon")
    book.set_title("Zogarth - Primal Hunter (Patreon Chapters)")
    book.set_language("en")
    book.add_author("Zogarth")

    # Add CSS
    style = epub.EpubItem(
        uid="style", file_name="style/default.css",
        media_type="text/css", content=CHAPTER_CSS.encode("utf-8"),
    )
    book.add_item(style)

    chapters = []
    toc = []

    for i, post in enumerate(sorted_posts):
        attrs = post.get("attributes", {})
        title = attrs.get("title", "Untitled")
        published = attrs.get("published_at", "")[:10]
        can_view = attrs.get("current_user_can_view", False)

        # Get content
        content_html = ""
        cjs = attrs.get("content_json_string", "")
        if cjs and can_view:
            try:
                doc = json.loads(cjs)
                content_html = prosemirror_to_html(doc)
            except (json.JSONDecodeError, TypeError):
                pass

        if not content_html:
            content = attrs.get("content", "")
            if content and can_view:
                content_html = content
            elif not can_view:
                content_html = (
                    '<p class="locked">[Content locked - '
                    "requires higher subscription tier]</p>"
                )
            else:
                content_html = "<p><em>No content available.</em></p>"

        # Build chapter XHTML
        chapter_body = (
            f"<h1>{escape(title)}</h1>\n"
            f'<p class="date">{published}</p>\n'
            f"{content_html}"
        )

        ch = epub.EpubHtml(
            title=title,
            file_name=f"chapter_{i:04d}.xhtml",
            lang="en",
        )
        ch.content = (
            '<?xml version="1.0" encoding="utf-8"?>\n'
            '<!DOCTYPE html>\n'
            '<html xmlns="http://www.w3.org/1999/xhtml">\n'
            "<head>\n"
            f"<title>{escape(title)}</title>\n"
            '<link rel="stylesheet" href="style/default.css" type="text/css"/>\n'
            "</head>\n"
            f"<body>\n{chapter_body}\n</body>\n</html>"
        ).encode("utf-8")
        ch.add_item(style)

        book.add_item(ch)
        chapters.append(ch)
        toc.append(ch)

        if (i + 1) % 50 == 0:
            print(f"  Processed {i + 1}/{len(sorted_posts)} chapters...")

    # Set book metadata
    book.toc = toc
    book.spine = ["nav"] + chapters

    # Add navigation
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())

    print(f"Writing {output_path}...")
    epub.write_epub(output_path, book)
    size_mb = Path(output_path).stat().st_size / (1024 * 1024)
    print(f"Done! {output_path} ({size_mb:.1f} MB, {len(chapters)} chapters)")


if __name__ == "__main__":
    main()
