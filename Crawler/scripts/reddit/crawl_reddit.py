#!/usr/bin/env python3
"""Collect public top-level Vietnamese Reddit comments through OAuth Data API."""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from comment_crawler.paths import REDDIT_COMMENTS_CSV
from comment_crawler.storage import append_comment_rows, prepare_comment_csv

TOKEN_URL = "https://www.reddit.com/api/v1/access_token"
OAUTH_BASE_URL = "https://oauth.reddit.com"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Crawl public Vietnamese Reddit comments through OAuth.")
    parser.add_argument("--subreddit", default="VietNam")
    parser.add_argument("--client-id", default=os.getenv("REDDIT_CLIENT_ID"))
    parser.add_argument("--client-secret", default=os.getenv("REDDIT_CLIENT_SECRET"))
    parser.add_argument("--user-agent", default=os.getenv("REDDIT_USER_AGENT", "windows:vn-comment-crawler:0.1 (by /u/your_reddit_username)"))
    parser.add_argument("--max-posts", type=int, default=50)
    parser.add_argument("--comments-per-post", type=int, default=13)
    parser.add_argument("--max-comments", type=int, default=500)
    parser.add_argument("--delay", type=float, default=2.0)
    parser.add_argument("--output", type=Path, default=REDDIT_COMMENTS_CSV)
    args = parser.parse_args()
    if not args.client_id or not args.client_secret:
        parser.error("Thieu REDDIT_CLIENT_ID hoac REDDIT_CLIENT_SECRET.")
    if args.max_posts < 1 or args.max_posts > 100:
        parser.error("--max-posts phai nam trong 1..100")
    if args.comments_per_post < 1 or args.max_comments < 1 or args.delay < 0:
        parser.error("Cac gioi han comment va delay khong hop le")
    return args


def is_vietnamese(text: str) -> bool:
    try:
        from langdetect import LangDetectException, detect
        return detect(text) == "vi"
    except (LangDetectException, ValueError):
        return False


def create_session(args: argparse.Namespace) -> Any:
    import requests

    session = requests.Session()
    response = session.post(TOKEN_URL, auth=(args.client_id, args.client_secret), data={"grant_type": "client_credentials"}, headers={"User-Agent": args.user_agent}, timeout=30)
    response.raise_for_status()
    token = response.json().get("access_token")
    if not token:
        raise RuntimeError("Reddit khong tra ve access_token.")
    session.headers.update({"Authorization": f"bearer {token}", "User-Agent": args.user_agent})
    return session


def get_json(session: Any, url: str, **params: object) -> Any:
    response = session.get(url, params=params, timeout=30)
    if response.status_code == 429:
        raise RuntimeError("Reddit rate limit. Hay tang --delay va chay lai sau.")
    response.raise_for_status()
    return response.json()


def collect_comments(args: argparse.Namespace, seen: set[tuple[str, str]]) -> list[dict[str, str]]:
    import requests

    session = create_session(args)
    listing = get_json(session, f"{OAUTH_BASE_URL}/r/{args.subreddit}/new", limit=args.max_posts, raw_json=1)
    posts = listing.get("data", {}).get("children", [])
    rows: list[dict[str, str]] = []
    for index, item in enumerate(posts, start=1):
        if len(rows) >= args.max_comments:
            break
        post = item.get("data", {})
        post_id = str(post.get("id", ""))
        title = str(post.get("title", "")).strip()
        if not post_id:
            continue
        print(f"[{index}/{len(posts)}] Post {post_id}: {title[:80]}")
        try:
            payload = get_json(session, f"{OAUTH_BASE_URL}/r/{args.subreddit}/comments/{post_id}", limit=args.comments_per_post, raw_json=1)
            children = payload[1].get("data", {}).get("children", []) if isinstance(payload, list) and len(payload) > 1 else []
        except (RuntimeError, requests.RequestException, ValueError) as exc:
            print(f"  Bo post: {exc}", file=sys.stderr)
            continue
        kept = 0
        for child in children:
            if len(rows) >= args.max_comments or kept >= args.comments_per_post:
                break
            if child.get("kind") != "t1":
                continue
            comment = child.get("data", {})
            text = str(comment.get("body", "")).strip()
            comment_id = str(comment.get("id", ""))
            if not text or not comment_id or not is_vietnamese(text):
                continue
            key = (post_id, comment_id)
            if key in seen:
                continue
            seen.add(key)
            kept += 1
            rows.append({"platform": "reddit", "category": args.subreddit, "query_type": "subreddit_new", "keyword": f"r/{args.subreddit}", "content_id": post_id, "video_id": "", "title": title, "comment_id": comment_id, "author": str(comment.get("author", "")), "published_at": str(comment.get("created_utc", "")), "comment": text})
        print(f"  Giu lai {kept} comment tieng Viet.")
        if index < len(posts):
            time.sleep(args.delay)
    return rows


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    args = parse_args()
    try:
        seen = prepare_comment_csv(args.output)
        rows = collect_comments(args, seen)
    except (RuntimeError, OSError) as exc:
        print(f"Khong crawl duoc Reddit: {exc}", file=sys.stderr)
        return 1
    append_comment_rows(args.output, rows)
    print(f"Da them {len(rows)}/{args.max_comments} comment moi vao {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
