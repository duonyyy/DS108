#!/usr/bin/env python3
"""Collect public TikTok comments for explicit search topics."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from comment_crawler.paths import DEFAULT_KEYWORDS_FILE, TIKTOK_COMMENTS_CSV
from comment_crawler.queries import load_search_queries
from comment_crawler.storage import append_comment_rows, prepare_comment_csv


class TikTokAccessBlocked(RuntimeError):
    """TikTok rejected the automated session or returned no search response."""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Crawl public TikTok comments by topic.")
    parser.add_argument("--keywords-file", type=Path, default=DEFAULT_KEYWORDS_FILE)
    parser.add_argument("--category", action="append", help="Category in keywords file; repeat to select several.")
    parser.add_argument("--keyword", action="append", help="Additional manual query; repeat as needed.")
    parser.add_argument("--list-categories", action="store_true")
    parser.add_argument("--max-videos", type=int, default=20)
    parser.add_argument("--comments-per-video", type=int, default=100)
    parser.add_argument("--headful", action="store_true", help="Show the browser while TikTokApi creates its session.")
    parser.add_argument("--output", type=Path, default=TIKTOK_COMMENTS_CSV)
    parser.add_argument("--max-queries", type=int, default=0, help="Limit configured queries; 0 means all selected queries.")
    args = parser.parse_args()
    if args.max_videos < 1 or args.comments_per_video < 1:
        parser.error("Cac gioi han phai lon hon 0")
    if args.max_queries < 0:
        parser.error("--max-queries khong duoc am")
    return args


async def collect_comments(
    args: argparse.Namespace,
    queries: list[object],
    seen: set[tuple[str, str]],
) -> list[dict[str, str]]:
    try:
        from TikTokApi import TikTokApi
        from langdetect import LangDetectException, detect
    except ImportError as exc:
        raise RuntimeError("Chay `python -m pip install -r requirements.txt` truoc.") from exc
    rows: list[dict[str, str]] = []
    async with TikTokApi() as api:
        try:
            await api.create_sessions(num_sessions=1, headless=not args.headful)
        except Exception as exc:
            raise RuntimeError(
                "TikTok khong chap nhan phien trinh duyet. "
                "Hay dung crawl va thu lai sau theo dieu khoan nen tang. "
                f"({type(exc).__name__}: {exc})"
            ) from exc
        for query in queries:
            try:
                videos = api.search.search_type(query.query, "item", count=args.max_videos)
                video_count = 0
                async for video in videos:
                    if video_count >= args.max_videos:
                        break
                    video_count += 1
                    title = str(getattr(video, "desc", "") or "").strip()
                    comments = video.comments(count=args.comments_per_video)
                    comment_count = 0
                    async for comment in comments:
                        if comment_count >= args.comments_per_video:
                            break
                        comment_count += 1
                        text = str(comment.text).strip()
                        try:
                            vietnamese = detect(text) == "vi"
                        except (LangDetectException, ValueError):
                            vietnamese = False
                        if not text or not vietnamese:
                            continue
                        comment_id = str(getattr(comment, "id", ""))
                        key = (str(video.id), comment_id or text)
                        if key in seen:
                            continue
                        seen.add(key)
                        rows.append({"platform": "tiktok", "category": query.category, "query_type": query.query_type, "keyword": query.query, "content_id": str(video.id), "video_id": str(video.id), "title": title, "comment_id": comment_id, "author": "", "published_at": "", "comment": text})
            except Exception as exc:
                if type(exc).__name__ == "EmptyResponseException":
                    raise TikTokAccessBlocked(
                        f"TikTok tu choi truy van {query.query!r} "
                        "(phan hoi rong/co the phat hien tu dong)."
                    ) from exc
                else:
                    print(f"Khong truy van duoc TikTok cho {query.query!r}: {type(exc).__name__}: {exc}", file=sys.stderr)
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
    try:
        seen = prepare_comment_csv(args.output)
        rows = asyncio.run(collect_comments(args, queries, seen))
    except TikTokAccessBlocked as exc:
        print(f"Dung crawl TikTok: {exc}", file=sys.stderr)
        return 2
    except RuntimeError as exc:
        print(exc, file=sys.stderr)
        return 1
    append_comment_rows(args.output, rows)
    print(f"Da them {len(rows)} comment moi vao {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
