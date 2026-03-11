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

Options:
    --tag TAG           Filter posts by tag (default: "Chapters")
    --output FILE       Output file path (default: chapters.html)
    --format FORMAT     Output format: html or markdown (default: html)
    --json FILE         Also save raw JSON data to this file
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
    "user-agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
}


def build_posts_params(campaign_id: str, cursor: str = "", tag: str = "") -> dict:
    """Build query parameters for the Patreon posts API."""
    params = {
        "sort": "-published_at",
        "filter[campaign_id]": campaign_id,
        "filter[is_draft]": "false",
        "filter[contains_exclusive_posts]": "true",
        "json-api-version": "1.0",
        "fields[post]": ",".join([
            "title", "content", "published_at", "post_type", "url",
            "current_user_can_view", "embed", "image", "is_paid",
            "teaser_text",
        ]),
        "fields[user]": "full_name,image_url,url",
        "fields[campaign]": "name,url",
        "include": "user",
    }
    if tag:
        params["filter[tag]"] = tag
    if cursor:
        params["page[cursor]"] = cursor
    return params


def find_campaign_id(session: requests.Session, creator_slug: str) -> str | None:
    """Try to find campaign ID from creator page."""
    url = f"https://www.patreon.com/api/campaigns?filter[creator_vanity]={creator_slug}"
    resp = session.get(url, headers=PATREON_HEADERS)
    if resp.status_code != 200:
        # Fallback: scrape the page for campaign ID
        resp2 = session.get(
            f"https://www.patreon.com/c/{creator_slug}/posts",
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
    data = resp.json()
    if data.get("data"):
        return data["data"][0]["id"]
    return None


def fetch_all_posts(
    session: requests.Session,
    campaign_id: str,
    tag: str = "",
    rate_limit_delay: float = 1.0,
) -> list[dict]:
    """Fetch all posts from a campaign, handling pagination."""
    all_posts = []
    cursor = ""
    page_num = 0

    while True:
        page_num += 1
        params = build_posts_params(campaign_id, cursor=cursor, tag=tag)
        url = f"{PATREON_API_POSTS}?{urlencode(params)}"

        print(f"  Fetching page {page_num}...", end=" ", flush=True)
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

        # Check for next page
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


def posts_to_html(posts: list[dict], title: str = "Chapters") -> str:
    """Convert posts to a single HTML document, ordered oldest-first for reading."""
    # Sort by published_at ascending (oldest first for reading order)
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
        "  body { max-width: 800px; margin: 0 auto; padding: 20px; font-family: Georgia, serif; line-height: 1.6; color: #333; background: #fafafa; }",
        "  h1 { text-align: center; border-bottom: 2px solid #333; padding-bottom: 10px; }",
        "  .chapter { margin-bottom: 40px; page-break-before: always; }",
        "  .chapter-title { font-size: 1.5em; margin-bottom: 5px; }",
        "  .chapter-date { color: #666; font-size: 0.9em; margin-bottom: 20px; }",
        "  .chapter-content { text-align: justify; }",
        "  .chapter-content img { max-width: 100%; height: auto; }",
        "  .locked { color: #999; font-style: italic; }",
        "  .toc { margin: 20px 0; padding: 20px; background: #fff; border: 1px solid #ddd; }",
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
        content = attrs.get("content", "")
        can_view = attrs.get("current_user_can_view", False)

        html_parts.append(f'<div class="chapter" id="chapter-{i}">')
        html_parts.append(f'<h2 class="chapter-title">{escape(post_title)}</h2>')
        html_parts.append(f'<div class="chapter-date">{published}</div>')

        if content and can_view:
            html_parts.append(f'<div class="chapter-content">{content}</div>')
        elif not can_view:
            teaser = attrs.get("teaser_text", "")
            html_parts.append('<div class="locked">[Content locked - requires subscription access]</div>')
            if teaser:
                html_parts.append(f'<div class="chapter-content"><p>{escape(teaser)}</p></div>')
        else:
            html_parts.append('<div class="chapter-content"><p><em>No content available.</em></p></div>')

        html_parts.append("</div><hr>")

    html_parts.extend([
        f"<p style='text-align:center;color:#999;'>Extracted {len(sorted_posts)} chapters</p>",
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
        content = attrs.get("content", "")
        can_view = attrs.get("current_user_can_view", False)

        parts.append(f"\n---\n")
        parts.append(f"## {post_title} {{#chapter-{i}}}\n")
        parts.append(f"*{published}*\n")

        if content and can_view:
            # Strip HTML tags for markdown (basic conversion)
            text = re.sub(r"<br\s*/?>", "\n", content)
            text = re.sub(r"<p[^>]*>", "\n", text)
            text = re.sub(r"</p>", "\n", text)
            text = re.sub(r"<strong>(.*?)</strong>", r"**\1**", text)
            text = re.sub(r"<em>(.*?)</em>", r"*\1*", text)
            text = re.sub(r"<h[1-6][^>]*>(.*?)</h[1-6]>", r"### \1\n", text)
            text = re.sub(r"<[^>]+>", "", text)
            text = re.sub(r"\n{3,}", "\n\n", text)
            parts.append(text.strip())
        elif not can_view:
            parts.append("*[Content locked - requires subscription access]*")
            teaser = attrs.get("teaser_text", "")
            if teaser:
                parts.append(f"\n{teaser}")
        else:
            parts.append("*No content available.*")

    parts.append(f"\n---\n*Extracted {len(sorted_posts)} chapters*\n")
    return "\n".join(parts)


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
        help="Campaign ID to fetch posts from",
    )
    parser.add_argument(
        "--find-campaign", action="store_true",
        help="Find and print the campaign ID for a creator",
    )
    parser.add_argument(
        "--creator", default="Zogarth",
        help="Creator slug/vanity name (default: Zogarth)",
    )
    parser.add_argument(
        "--tag", default="",
        help='Filter by tag (e.g., "Chapters"). Empty means no tag filter.',
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
        "--delay", type=float, default=1.0,
        help="Delay between API requests in seconds (default: 1.0)",
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
            print("(Check DevTools Network tab on the creator's Patreon page)")
            sys.exit(1)

    # Fetch posts
    tag_desc = f" with tag '{args.tag}'" if args.tag else ""
    print(f"Fetching posts from campaign {args.campaign_id}{tag_desc}...")
    posts = fetch_all_posts(
        session, args.campaign_id, tag=args.tag, rate_limit_delay=args.delay
    )

    if not posts:
        print("No posts found. Check your session_id is valid and you're subscribed.")
        sys.exit(1)

    print(f"\nTotal posts fetched: {len(posts)}")

    # Count viewable
    viewable = sum(
        1 for p in posts
        if p.get("attributes", {}).get("current_user_can_view", False)
    )
    print(f"Viewable posts: {viewable}/{len(posts)}")

    # Save raw JSON if requested
    if args.json_output:
        Path(args.json_output).write_text(json.dumps(posts, indent=2))
        print(f"Raw JSON saved to: {args.json_output}")

    # Generate output
    title = f"{args.creator} - Chapters"
    if args.format == "html":
        output = posts_to_html(posts, title=title)
    else:
        output = posts_to_markdown(posts, title=title)
        if args.output == "chapters.html":
            args.output = "chapters.md"

    Path(args.output).write_text(output, encoding="utf-8")
    print(f"Output saved to: {args.output}")
    print("Done!")


if __name__ == "__main__":
    main()
