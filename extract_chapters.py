#!/usr/bin/env python3
"""
Extract chapter posts from a Patreon creator's page.

Usage:
    1. Log into patreon.com in your browser
    2. Open DevTools > Application > Cookies > patreon.com
    3. Copy the value of the 'session_id' cookie
    4. Run: python extract_chapters.py --session-id <YOUR_SESSION_ID> --campaign-id <CAMPAIGN_ID>

To find the campaign_id:
    - Go to the creator's page, open DevTools Network tab
    - Look for requests to /api/posts and find the filter[campaign_id] parameter
    - Or run: python extract_chapters.py --find-campaign --creator zogarth --session-id <SESSION_ID>
"""

import argparse
import json
import re
import sys
import time
from html import escape
from pathlib import Path
from urllib.parse import urlencode

try:
    import requests
except ImportError:
    print("Error: 'requests' library is required. Install with: pip install requests")
    sys.exit(1)


PATREON_API_POSTS = "https://www.patreon.com/api/posts"
PATREON_HEADERS = {
    "content-type": "application/vnd.api+json",
    "accept": "*/*",
    "accept-language": "en-US,en;q=0.9",
    "user-agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
}


# ---------------------------------------------------------------------------
# ProseMirror JSON -> HTML converter
# ---------------------------------------------------------------------------

def prosemirror_to_html(doc: dict) -> str:
    """Convert a ProseMirror JSON document to HTML."""
    if not doc or doc.get("type") != "doc":
        return ""
    return _render_nodes(doc.get("content", []))


def _render_nodes(nodes: list) -> str:
    parts = []
    for node in nodes:
        parts.append(_render_node(node))
    return "".join(parts)


def _render_node(node: dict) -> str:
    ntype = node.get("type", "")
    content = node.get("content", [])
    attrs = node.get("attrs", {})

    if ntype == "paragraph":
        inner = _render_nodes(content)
        return f"<p>{inner}</p>\n"
    elif ntype == "heading":
        level = attrs.get("level", 2)
        inner = _render_nodes(content)
        return f"<h{level}>{inner}</h{level}>\n"
    elif ntype == "text":
        text = escape(node.get("text", ""))
        marks = node.get("marks", [])
        for mark in marks:
            mtype = mark.get("type", "")
            mattrs = mark.get("attrs", {})
            if mtype == "bold" or mtype == "strong":
                text = f"<strong>{text}</strong>"
            elif mtype == "italic" or mtype == "em":
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
        return "<br>"
    elif ntype == "horizontal_rule":
        return "<hr>\n"
    elif ntype == "blockquote":
        inner = _render_nodes(content)
        return f"<blockquote>{inner}</blockquote>\n"
    elif ntype == "bullet_list" or ntype == "bulletList":
        inner = _render_nodes(content)
        return f"<ul>{inner}</ul>\n"
    elif ntype == "ordered_list" or ntype == "orderedList":
        inner = _render_nodes(content)
        return f"<ol>{inner}</ol>\n"
    elif ntype == "list_item" or ntype == "listItem":
        inner = _render_nodes(content)
        return f"<li>{inner}</li>\n"
    elif ntype == "code_block" or ntype == "codeBlock":
        inner = _render_nodes(content)
        return f"<pre><code>{inner}</code></pre>\n"
    elif ntype == "image":
        src = attrs.get("src", "")
        alt = attrs.get("alt", "")
        return f'<img src="{escape(src)}" alt="{escape(alt)}">\n'
    else:
        # Fallback: render children
        return _render_nodes(content)


# ---------------------------------------------------------------------------
# ProseMirror JSON -> plain text (for markdown)
# ---------------------------------------------------------------------------

def prosemirror_to_text(doc: dict) -> str:
    """Convert a ProseMirror JSON document to plain text."""
    if not doc or doc.get("type") != "doc":
        return ""
    return _text_nodes(doc.get("content", []))


def _text_nodes(nodes: list) -> str:
    parts = []
    for node in nodes:
        parts.append(_text_node(node))
    return "".join(parts)


def _text_node(node: dict) -> str:
    ntype = node.get("type", "")
    content = node.get("content", [])
    attrs = node.get("attrs", {})

    if ntype == "paragraph":
        inner = _text_nodes(content)
        return f"{inner}\n\n"
    elif ntype == "heading":
        level = attrs.get("level", 2)
        inner = _text_nodes(content)
        prefix = "#" * level
        return f"{prefix} {inner}\n\n"
    elif ntype == "text":
        text = node.get("text", "")
        marks = node.get("marks", [])
        for mark in marks:
            mtype = mark.get("type", "")
            if mtype in ("bold", "strong"):
                text = f"**{text}**"
            elif mtype in ("italic", "em"):
                text = f"*{text}*"
        return text
    elif ntype == "hard_break":
        return "\n"
    elif ntype == "horizontal_rule":
        return "\n---\n\n"
    elif ntype == "blockquote":
        inner = _text_nodes(content)
        lines = inner.strip().split("\n")
        return "\n".join(f"> {l}" for l in lines) + "\n\n"
    elif ntype in ("bullet_list", "bulletList"):
        items = []
        for child in content:
            inner = _text_nodes(child.get("content", [])).strip()
            items.append(f"- {inner}")
        return "\n".join(items) + "\n\n"
    elif ntype in ("ordered_list", "orderedList"):
        items = []
        for i, child in enumerate(content, 1):
            inner = _text_nodes(child.get("content", [])).strip()
            items.append(f"{i}. {inner}")
        return "\n".join(items) + "\n\n"
    elif ntype in ("list_item", "listItem"):
        return _text_nodes(content)
    else:
        return _text_nodes(content)


# ---------------------------------------------------------------------------
# Patreon API helpers
# ---------------------------------------------------------------------------

def build_list_params(campaign_id: str, cursor: str = "", tag: str = "") -> dict:
    """Build query parameters for the posts list endpoint."""
    params = {
        "sort": "-published_at",
        "filter[campaign_id]": campaign_id,
        "filter[is_draft]": "false",
        "filter[contains_exclusive_posts]": "true",
        "json-api-version": "1.0",
        "fields[post]": "title,published_at,url,current_user_can_view,post_type",
        "fields[user]": "full_name",
        "include": "user",
    }
    if tag:
        params["filter[tag]"] = tag
    if cursor:
        params["page[cursor]"] = cursor
    return params


def find_campaign_id(session: requests.Session, creator_slug: str) -> str | None:
    """Try to find campaign ID from creator page."""
    url = f"https://www.patreon.com/api/campaigns?filter%5Bvanity%5D={creator_slug}"
    resp = session.get(url, headers=PATREON_HEADERS)
    if resp.status_code == 200:
        data = resp.json()
        if data.get("data"):
            return data["data"][0]["id"]

    # Fallback: try alternate URL patterns
    for path in [f"/c/{creator_slug}/posts", f"/{creator_slug}/posts"]:
        resp2 = session.get(
            f"https://www.patreon.com{path}",
            headers={"user-agent": PATREON_HEADERS["user-agent"]},
        )
        if resp2.status_code == 200:
            match = re.search(r'"campaign_id"\s*:\s*(\d+)', resp2.text)
            if match:
                return match.group(1)
            match = re.search(r'campaign/(\d+)', resp2.text)
            if match:
                return match.group(1)
    return None


def fetch_post_list(
    session: requests.Session,
    campaign_id: str,
    tag: str = "",
    rate_limit_delay: float = 1.0,
) -> list[dict]:
    """Fetch all post metadata from a campaign (no content, just IDs/titles)."""
    all_posts = []
    cursor = ""
    page_num = 0

    while True:
        page_num += 1
        params = build_list_params(campaign_id, cursor=cursor, tag=tag)
        url = f"{PATREON_API_POSTS}?{urlencode(params)}"

        print(f"  Listing page {page_num}...", end=" ", flush=True)
        resp = session.get(url, headers=PATREON_HEADERS)

        if resp.status_code == 429:
            print("rate limited, waiting 30s...")
            time.sleep(30)
            continue

        if resp.status_code != 200:
            print(f"ERROR: HTTP {resp.status_code}")
            print(f"  Response: {resp.text[:500]}")
            break

        data = resp.json()
        posts = data.get("data", [])
        print(f"got {len(posts)} posts")

        if not posts:
            break

        all_posts.extend(posts)

        next_cursor = (
            data.get("meta", {})
            .get("pagination", {})
            .get("cursors", {})
            .get("next")
        )
        if not next_cursor:
            break

        cursor = next_cursor
        time.sleep(rate_limit_delay)

    return all_posts


def fetch_post_content(
    session: requests.Session,
    post_id: str,
) -> dict | None:
    """Fetch a single post's full content."""
    url = f"{PATREON_API_POSTS}/{post_id}?json-api-version=1.0"
    resp = session.get(url, headers=PATREON_HEADERS)
    if resp.status_code == 429:
        time.sleep(30)
        resp = session.get(url, headers=PATREON_HEADERS)
    if resp.status_code != 200:
        return None
    return resp.json().get("data", {})


def get_content_html(attrs: dict) -> str:
    """Extract HTML content from post attributes, trying multiple sources."""
    # Try content_json_string first (ProseMirror JSON)
    cjs = attrs.get("content_json_string", "")
    if cjs:
        try:
            doc = json.loads(cjs)
            html = prosemirror_to_html(doc)
            if html.strip():
                return html
        except (json.JSONDecodeError, TypeError):
            pass

    # Fall back to content field (sometimes has HTML)
    content = attrs.get("content", "")
    if content:
        return content

    return ""


def get_content_text(attrs: dict) -> str:
    """Extract plain text content from post attributes."""
    cjs = attrs.get("content_json_string", "")
    if cjs:
        try:
            doc = json.loads(cjs)
            text = prosemirror_to_text(doc)
            if text.strip():
                return text
        except (json.JSONDecodeError, TypeError):
            pass

    content = attrs.get("content", "")
    if content:
        text = re.sub(r"<br\s*/?>", "\n", content)
        text = re.sub(r"<p[^>]*>", "\n", text)
        text = re.sub(r"</p>", "\n", text)
        text = re.sub(r"<strong>(.*?)</strong>", r"**\1**", text)
        text = re.sub(r"<em>(.*?)</em>", r"*\1*", text)
        text = re.sub(r"<[^>]+>", "", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text

    return ""


# ---------------------------------------------------------------------------
# Output generators
# ---------------------------------------------------------------------------

def posts_to_html(posts: list[dict], title: str = "Chapters") -> str:
    """Convert posts to a single HTML document, ordered oldest-first."""
    sorted_posts = sorted(
        posts,
        key=lambda p: p.get("attributes", {}).get("published_at", ""),
    )

    html_parts = [
        "<!DOCTYPE html>",
        "<html lang='en'>",
        "<head>",
        f"<title>{escape(title)}</title>",
        "<meta charset='utf-8'>",
        "<meta name='viewport' content='width=device-width, initial-scale=1'>",
        "<style>",
        "  body { max-width: 800px; margin: 0 auto; padding: 20px;"
        " font-family: Georgia, serif; line-height: 1.8; color: #333;"
        " background: #fafafa; }",
        "  h1 { text-align: center; border-bottom: 2px solid #333;"
        " padding-bottom: 10px; }",
        "  .chapter { margin-bottom: 60px; page-break-before: always; }",
        "  .chapter-title { font-size: 1.5em; margin-bottom: 5px; }",
        "  .chapter-date { color: #666; font-size: 0.9em; margin-bottom: 20px; }",
        "  .chapter-content { text-align: justify; }",
        "  .chapter-content p { margin: 0.8em 0; }",
        "  .chapter-content img { max-width: 100%; height: auto; }",
        "  .locked { color: #999; font-style: italic; padding: 20px;"
        " background: #f0f0f0; border-radius: 4px; }",
        "  .toc { margin: 20px 0; padding: 20px; background: #fff;"
        " border: 1px solid #ddd; }",
        "  .toc a { text-decoration: none; color: #336; }",
        "  .toc a:hover { text-decoration: underline; }",
        "  .toc li { margin: 4px 0; }",
        "  hr { border: none; border-top: 1px solid #ccc; margin: 40px 0; }",
        "</style>",
        "</head>",
        "<body>",
        f"<h1>{escape(title)}</h1>",
    ]

    # Table of contents
    html_parts.append("<div class='toc'><h2>Table of Contents</h2><ol>")
    for i, post in enumerate(sorted_posts):
        attrs = post.get("attributes", {})
        post_title = attrs.get("title", "Untitled")
        html_parts.append(
            f'<li><a href="#chapter-{i}">{escape(post_title)}</a></li>'
        )
    html_parts.append("</ol></div><hr>")

    # Chapters
    for i, post in enumerate(sorted_posts):
        attrs = post.get("attributes", {})
        post_title = attrs.get("title", "Untitled")
        published = attrs.get("published_at", "")[:10]
        can_view = attrs.get("current_user_can_view", False)

        html_parts.append(f'<div class="chapter" id="chapter-{i}">')
        html_parts.append(f'<h2 class="chapter-title">{escape(post_title)}</h2>')
        html_parts.append(f'<div class="chapter-date">{published}</div>')

        content_html = get_content_html(attrs)
        if content_html and can_view:
            html_parts.append(f'<div class="chapter-content">{content_html}</div>')
        elif not can_view:
            teaser = attrs.get("teaser_text", "")
            html_parts.append(
                '<div class="locked">[Content locked - '
                "requires higher subscription tier]</div>"
            )
            if teaser:
                html_parts.append(
                    f'<div class="chapter-content"><p>{escape(teaser)}</p></div>'
                )
        else:
            html_parts.append(
                '<div class="chapter-content">'
                "<p><em>No content available.</em></p></div>"
            )

        html_parts.append("</div><hr>")

    html_parts.extend([
        f"<p style='text-align:center;color:#999;'>"
        f"Extracted {len(sorted_posts)} chapters</p>",
        "</body></html>",
    ])

    return "\n".join(html_parts)


def posts_to_markdown(posts: list[dict], title: str = "Chapters") -> str:
    """Convert posts to a single Markdown document, ordered oldest-first."""
    sorted_posts = sorted(
        posts,
        key=lambda p: p.get("attributes", {}).get("published_at", ""),
    )

    parts = [f"# {title}\n"]

    # TOC
    parts.append("## Table of Contents\n")
    for i, post in enumerate(sorted_posts):
        attrs = post.get("attributes", {})
        post_title = attrs.get("title", "Untitled")
        parts.append(f"{i + 1}. [{post_title}](#chapter-{i})")
    parts.append("")

    # Chapters
    for i, post in enumerate(sorted_posts):
        attrs = post.get("attributes", {})
        post_title = attrs.get("title", "Untitled")
        published = attrs.get("published_at", "")[:10]
        can_view = attrs.get("current_user_can_view", False)

        parts.append("\n---\n")
        parts.append(f"## {post_title} {{#chapter-{i}}}\n")
        parts.append(f"*{published}*\n")

        content_text = get_content_text(attrs)
        if content_text and can_view:
            parts.append(content_text.strip())
        elif not can_view:
            parts.append("*[Content locked - requires higher subscription tier]*")
            teaser = attrs.get("teaser_text", "")
            if teaser:
                parts.append(f"\n{teaser}")
        else:
            parts.append("*No content available.*")

    parts.append(f"\n---\n*Extracted {len(sorted_posts)} chapters*\n")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Extract chapter posts from Patreon",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--session-id", required=True,
        help="Your Patreon session_id cookie value",
    )
    parser.add_argument(
        "--campaign-id",
        help="Campaign ID (auto-detected if --creator is set)",
    )
    parser.add_argument(
        "--find-campaign", action="store_true",
        help="Find and print the campaign ID, then exit",
    )
    parser.add_argument(
        "--creator", default="Zogarth",
        help="Creator slug/vanity name (default: Zogarth)",
    )
    parser.add_argument(
        "--tag", default="",
        help='Filter by tag (e.g., "Chapters"). Empty = no filter.',
    )
    parser.add_argument(
        "--output", default="chapters.html",
        help="Output file path (default: chapters.html)",
    )
    parser.add_argument(
        "--format", choices=["html", "markdown"], default="html",
        help="Output format (default: html)",
    )
    parser.add_argument(
        "--json", dest="json_output",
        help="Also save raw JSON data to this file",
    )
    parser.add_argument(
        "--delay", type=float, default=0.5,
        help="Delay between API requests in seconds (default: 0.5)",
    )
    args = parser.parse_args()

    # Set up session with auth cookie
    session = requests.Session()
    session.cookies.set("session_id", args.session_id, domain=".patreon.com")

    # Find campaign ID if needed
    if args.find_campaign or not args.campaign_id:
        print(f"Looking up campaign ID for '{args.creator}'...")
        cid = find_campaign_id(session, args.creator)
        if cid:
            print(f"Found campaign ID: {cid}")
            if args.find_campaign:
                return
            args.campaign_id = cid
        else:
            print("Could not find campaign ID automatically.")
            print("Please provide it with --campaign-id")
            sys.exit(1)

    # Step 1: Get post list (IDs + titles)
    tag_desc = f" with tag '{args.tag}'" if args.tag else ""
    print(f"\nStep 1: Listing posts from campaign {args.campaign_id}{tag_desc}...")
    post_list = fetch_post_list(
        session, args.campaign_id, tag=args.tag, rate_limit_delay=args.delay
    )

    if not post_list:
        print("No posts found. Check your session_id and subscription.")
        sys.exit(1)

    # Filter to viewable posts only for content fetching
    viewable = [
        p for p in post_list
        if p.get("attributes", {}).get("current_user_can_view", False)
    ]
    not_viewable = [
        p for p in post_list
        if not p.get("attributes", {}).get("current_user_can_view", False)
    ]

    print(f"\nFound {len(post_list)} posts total "
          f"({len(viewable)} viewable, {len(not_viewable)} locked)")

    # Step 2: Fetch full content for each viewable post
    print(f"\nStep 2: Fetching content for {len(viewable)} viewable posts...")
    full_posts = []
    for idx, post in enumerate(viewable):
        post_id = post["id"]
        title = post.get("attributes", {}).get("title", "?")
        print(f"  [{idx + 1}/{len(viewable)}] {title}...", end=" ", flush=True)

        full_post = fetch_post_content(session, post_id)
        if full_post:
            print("ok")
            full_posts.append(full_post)
        else:
            print("FAILED")
            # Keep the metadata-only version
            full_posts.append(post)

        time.sleep(args.delay)

    # Add locked posts (metadata only)
    full_posts.extend(not_viewable)

    print(f"\nTotal posts with content: {len(full_posts)}")

    # Save raw JSON if requested
    if args.json_output:
        Path(args.json_output).write_text(json.dumps(full_posts, indent=2))
        print(f"Raw JSON saved to: {args.json_output}")

    # Generate output
    creator_title = f"{args.creator} - Chapters"
    if args.format == "html":
        output = posts_to_html(full_posts, title=creator_title)
    else:
        output = posts_to_markdown(full_posts, title=creator_title)
        if args.output == "chapters.html":
            args.output = "chapters.md"

    Path(args.output).write_text(output, encoding="utf-8")
    print(f"Output saved to: {args.output}")
    print("Done!")


if __name__ == "__main__":
    main()
