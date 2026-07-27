#!/usr/bin/env python3
"""Collect public top-level YouTube comments for explicit search topics."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from comment_crawler.paths import DEFAULT_KEYWORDS_FILE, YOUTUBE_COMMENTS_CSV
from comment_crawler.queries import load_search_queries
from comment_crawler.storage import append_comment_rows, prepare_comment_csv


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Crawl public YouTube comments by topic.")
    parser.add_argument("--keywords-file", type=Path, default=DEFAULT_KEYWORDS_FILE)
    parser.add_argument("--category", action="append", help="Category in keywords file; repeat to select several.")
    parser.add_argument("--keyword", action="append", help="Additional manual query; repeat as needed.")
    parser.add_argument("--list-categories", action="store_true")
    parser.add_argument("--api-key", default=os.getenv("YOUTUBE_API_KEY"), help="Defaults to YOUTUBE_API_KEY.")
    parser.add_argument("--max-videos", type=int, default=50, help="So video ung vien toi da can kiem tra cho moi query (1..50).")
    parser.add_argument("--target-open-videos", type=int, default=10, help="So video co binh luan cong khai toi da can lay cho moi query.")
    parser.add_argument("--comments-per-video", type=int, default=13, help="So binh luan toi da moi video (mac dinh: 13).")
    parser.add_argument("--max-comments", type=int, default=500, help="Tong so binh luan toi da cho ca lan chay.")
    parser.add_argument("--output", type=Path, default=YOUTUBE_COMMENTS_CSV)
    parser.add_argument("--all-languages", action="store_true", help="Do not filter comments to Vietnamese.")
    parser.add_argument("--show-skipped", action="store_true", help="In tung video tat binh luan thay vi chi in tong ket.")
    parser.add_argument("--max-queries", type=int, default=0, help="Limit configured queries; 0 means all selected queries.")
    args = parser.parse_args()
    if args.max_videos < 1 or args.max_videos > 50:
        parser.error("--max-videos phai nam trong 1..50")
    if args.target_open_videos < 1 or args.target_open_videos > args.max_videos:
        parser.error("--target-open-videos phai nam trong 1..--max-videos")
    if args.comments_per_video < 1 or args.comments_per_video > 100:
        parser.error("--comments-per-video phai nam trong 1..100")
    if args.max_comments < 1:
        parser.error("--max-comments phai lon hon 0")
    if args.max_queries < 0:
        parser.error("--max-queries khong duoc am")
    return args


def is_vietnamese(text: str) -> bool:
    try:
        from langdetect import LangDetectException, detect
        return detect(text) == "vi"
    except (LangDetectException, ValueError):
        return False


def http_error_reason(error: object) -> str:
    """Extract a stable reason code from a Google API HTTP error."""
    content = getattr(error, "content", b"")
    try:
        payload = json.loads(content.decode("utf-8"))
        errors = payload.get("error", {}).get("errors", [])
        if errors and isinstance(errors[0], dict):
            return str(errors[0].get("reason", ""))
    except (AttributeError, UnicodeDecodeError, json.JSONDecodeError):
        pass
    return ""


def collect_comments(
    args: argparse.Namespace,
    queries: list[object],
    seen: set[tuple[str, str]],
) -> list[dict[str, str]]:
    try:
        from googleapiclient.discovery import build
        from googleapiclient.errors import HttpError
    except ImportError as exc:
        raise RuntimeError("Chay `python -m pip install -r requirements.txt` truoc.") from exc

    youtube = build("youtube", "v3", developerKey=args.api_key)
    rows: list[dict[str, str]] = []
    for query_index, query in enumerate(queries, start=1):
        if len(rows) >= args.max_comments:
            break
        print(f"[{query_index}/{len(queries)}] Tim video cho: {query.query}")
        try:
            search = youtube.search().list(q=query.query, part="id,snippet", type="video", maxResults=args.max_videos, regionCode="VN").execute()
        except HttpError as exc:
            print(f"Khong tim duoc video cho {query.query!r}: {exc}", file=sys.stderr)
            continue
        videos = search.get("items", [])
        open_videos = disabled_videos = empty_videos = 0
        if not videos:
            print("  Khong tim thay video.")
        for video_index, item in enumerate(videos, start=1):
            if open_videos >= args.target_open_videos or len(rows) >= args.max_comments:
                break
            video_id = item.get("id", {}).get("videoId")
            if not video_id:
                continue
            title = str(item.get("snippet", {}).get("title", "")).strip()
            print(f"  Video {video_index}/{len(videos)}: {video_id}")
            try:
                response = youtube.commentThreads().list(part="snippet", videoId=video_id, maxResults=args.comments_per_video, textFormat="plainText").execute()
            except HttpError as exc:
                if http_error_reason(exc) == "commentsDisabled":
                    disabled_videos += 1
                    if args.show_skipped:
                        print(f"Bo video {video_id}: da tat binh luan.", file=sys.stderr)
                else:
                    print(f"Bo video {video_id}: {exc}", file=sys.stderr)
                continue
            kept_before = len(rows)
            for item in response.get("items", []):
                if len(rows) >= args.max_comments:
                    break
                comment = item.get("snippet", {}).get("topLevelComment", {}).get("snippet", {})
                text = str(comment.get("textDisplay", "")).strip()
                comment_id = str(item.get("id", ""))
                if not text or not comment_id or (not args.all_languages and not is_vietnamese(text)):
                    continue
                key = (video_id, comment_id)
                if key in seen:
                    continue
                seen.add(key)
                rows.append({"platform": "youtube", "category": query.category, "query_type": query.query_type, "keyword": query.query, "content_id": video_id, "video_id": video_id, "title": title, "comment_id": comment_id, "author": str(comment.get("authorDisplayName", "")), "published_at": str(comment.get("publishedAt", "")), "comment": text})
            if response.get("items", []):
                open_videos += 1
                print(f"    Giu lai {len(rows) - kept_before} comment tieng Viet.")
            else:
                empty_videos += 1
            time.sleep(1)
        print(f"  Tong ket: {open_videos} video co binh luan, {disabled_videos} video tat binh luan, {empty_videos} video khong co binh luan.")
    return rows


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    args = parse_args()
    try:
        config, queries = load_search_queries(args.keywords_file, args.category, args.keyword)
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 1
    if args.list_categories:
        print("\n".join(config["categories"]))
        return 0
    if args.max_queries:
        queries = queries[:args.max_queries]
    if not queries:
        print("Khong co tu khoa de crawl.", file=sys.stderr)
        return 1
    if len(queries) > 100:
        print(f"Dang chon {len(queries)} query, vuot gioi han mac dinh 100 search.list/ngay cua YouTube. Chay tung --category hoac dung --max-queries 100.", file=sys.stderr)
        return 1
    if not args.api_key:
        print("Thieu API key. Dat bien moi truong YOUTUBE_API_KEY hoac dung --api-key.", file=sys.stderr)
        return 1
    try:
        seen = prepare_comment_csv(args.output)
        rows = collect_comments(args, queries, seen)
    except RuntimeError as exc:
        print(exc, file=sys.stderr)
        return 1
    append_comment_rows(args.output, rows)
    print(f"Da them {len(rows)}/{args.max_comments} comment moi vao {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
