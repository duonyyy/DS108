#!/usr/bin/env python3
"""Collect Vietnamese TikTok comments through two Apify Actors."""

from __future__ import annotations

import argparse
import hashlib
import math
import os
import sys
from collections import Counter
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from comment_crawler.paths import DEFAULT_KEYWORDS_FILE, TIKTOK_COMMENTS_CSV
from comment_crawler.queries import SearchQuery, load_search_queries
from comment_crawler.storage import append_comment_rows, prepare_comment_csv

APIFY_API_BASE = "https://api.apify.com/v2"
DEFAULT_SEARCH_ACTOR = "clockworks~tiktok-scraper"
DEFAULT_COMMENTS_ACTOR = "clockworks~tiktok-comments-scraper"


class ApifyRunError(RuntimeError):
    """An Apify Actor did not return a usable dataset."""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Tim video TikTok theo keyword va lay comment qua Apify Actors. "
            "Actor co the phat sinh chi phi."
        )
    )
    parser.add_argument("--keywords-file", type=Path, default=DEFAULT_KEYWORDS_FILE)
    parser.add_argument(
        "--category",
        action="append",
        help="Category trong file keyword; lap lai de chon nhieu category.",
    )
    parser.add_argument(
        "--keyword",
        action="append",
        help="Query bo sung; lap lai neu can.",
    )
    parser.add_argument("--list-categories", action="store_true")
    parser.add_argument(
        "--max-queries",
        type=int,
        default=1,
        help="So query toi da; mac dinh 1, dung 0 de chay tat ca query da chon.",
    )
    parser.add_argument(
        "--max-videos",
        type=int,
        default=5,
        help="So video toi da cho moi query (mac dinh: 5).",
    )
    parser.add_argument(
        "--search-suffix",
        default="",
        help='Chuoi them vao moi query, vi du "Viet Nam".',
    )
    parser.add_argument(
        "--comments-per-video",
        type=int,
        default=13,
        help="So comment cap cao nhat moi video (mac dinh: 13).",
    )
    parser.add_argument(
        "--max-comments",
        type=int,
        default=3000,
        help="Tong comment moi toi da cho ca lan chay.",
    )
    parser.add_argument("--output", type=Path, default=TIKTOK_COMMENTS_CSV)
    parser.add_argument(
        "--search-actor",
        default=DEFAULT_SEARCH_ACTOR,
        help="Apify Actor ID dung de tim video.",
    )
    parser.add_argument(
        "--comments-actor",
        default=DEFAULT_COMMENTS_ACTOR,
        help="Apify Actor ID dung de lay comment.",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=310,
        help="HTTP timeout giay cho moi Actor dong bo (mac dinh: 310).",
    )
    parser.add_argument(
        "--all-languages",
        action="store_true",
        help="Khong loc comment theo tieng Viet.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Chi hien ke hoach, khong goi Actor va khong phat sinh chi phi.",
    )
    args = parser.parse_args()

    if args.max_queries < 0:
        parser.error("--max-queries khong duoc am")
    if args.max_videos < 1 or args.max_videos > 50:
        parser.error("--max-videos phai nam trong 1..50")
    if args.comments_per_video < 1 or args.comments_per_video > 36:
        parser.error("--comments-per-video phai nam trong 1..36")
    if args.max_comments < 1:
        parser.error("--max-comments phai lon hon 0")
    if args.timeout < 60 or args.timeout > 360:
        parser.error("--timeout phai nam trong 60..360 giay")
    return args


def normalize_video_url(value: str) -> str:
    parts = urlsplit((value or "").strip())
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        return ""
    return urlunsplit(("https", parts.netloc.lower(), parts.path.rstrip("/"), "", ""))


def video_id_from_url(url: str) -> str:
    parts = urlsplit(url)
    path_parts = [part for part in parts.path.split("/") if part]
    if "video" in path_parts:
        index = path_parts.index("video")
        if index + 1 < len(path_parts):
            return path_parts[index + 1]
    return ""


def stable_comment_id(video_id: str, text: str, author: str) -> str:
    raw = f"{video_id}|{author}|{text}".encode("utf-8")
    return "generated-" + hashlib.sha1(raw).hexdigest()[:20]


def is_vietnamese(text: str) -> bool:
    try:
        from langdetect import LangDetectException, detect

        return detect(text) == "vi"
    except (LangDetectException, ValueError):
        return False


def run_actor(
    token: str,
    actor_id: str,
    actor_input: dict[str, Any],
    timeout: int,
) -> list[dict[str, Any]]:
    try:
        import requests
    except ImportError as exc:
        raise ApifyRunError("Chay `python -m pip install -e .` truoc.") from exc

    url = (
        f"{APIFY_API_BASE}/actors/{actor_id}/"
        "run-sync-get-dataset-items"
    )
    try:
        response = requests.post(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json=actor_input,
            timeout=timeout,
        )
    except requests.Timeout as exc:
        raise ApifyRunError(
            f"Actor {actor_id} vuot HTTP timeout. Run co the van tiep tuc "
            "tren Apify Console va co the phat sinh chi phi."
        ) from exc
    except requests.RequestException as exc:
        raise ApifyRunError(f"Khong ket noi duoc Apify: {exc}") from exc

    if response.status_code >= 400:
        try:
            payload = response.json()
            message = (
                payload.get("error", {}).get("message")
                if isinstance(payload, dict)
                else None
            )
        except ValueError:
            message = None
        raise ApifyRunError(
            f"Actor {actor_id} tra HTTP {response.status_code}: "
            f"{message or response.reason}"
        )

    try:
        data = response.json()
    except ValueError as exc:
        raise ApifyRunError(f"Actor {actor_id} khong tra JSON hop le.") from exc
    if not isinstance(data, list):
        raise ApifyRunError(
            f"Actor {actor_id} tra dinh dang {type(data).__name__}, can list."
        )
    return [item for item in data if isinstance(item, dict)]


def submitted_search_term(query: SearchQuery, suffix: str) -> str:
    return " ".join(part for part in (query.query, suffix.strip()) if part)


def build_search_input(
    queries: list[SearchQuery],
    max_videos: int,
    search_suffix: str,
) -> dict[str, Any]:
    return {
        "searchQueries": [
            submitted_search_term(query, search_suffix) for query in queries
        ],
        "searchSection": "/video",
        "resultsPerPage": max_videos,
        "commentsPerPost": 0,
        "scrapeRelatedVideos": False,
        "scrapeRelatedSearchWords": False,
        "shouldDownloadVideos": False,
        "shouldDownloadCovers": False,
        "shouldDownloadSubtitles": False,
        "shouldDownloadAvatars": False,
        "shouldDownloadMusicCovers": False,
        "shouldDownloadSlideshowImages": False,
    }


def resolve_videos(
    items: list[dict[str, Any]],
    queries: list[SearchQuery],
    max_comments: int,
    comments_per_video: int,
    search_suffix: str,
) -> tuple[list[str], dict[str, dict[str, str]]]:
    by_query = {
        submitted_search_term(query, search_suffix).casefold(): query
        for query in queries
    }
    fallback = queries[0]
    max_urls = max(1, math.ceil(max_comments / comments_per_video))
    urls: list[str] = []
    metadata: dict[str, dict[str, str]] = {}

    for item in items:
        if item.get("errorCode"):
            print(
                f"Bo ket qua search loi: {item.get('errorCode')}: "
                f"{item.get('error', '')}",
                file=sys.stderr,
            )
            continue
        url = normalize_video_url(
            str(item.get("webVideoUrl") or item.get("videoWebUrl") or "")
        )
        if not url or url in metadata:
            continue
        search_query = str(item.get("searchQuery") or "").strip()
        query = by_query.get(search_query.casefold(), fallback)
        video_id = str(item.get("id") or video_id_from_url(url))
        metadata[url] = {
            "category": query.category,
            "query_type": query.query_type,
            "keyword": search_query or query.query,
            "video_id": video_id,
            "title": str(item.get("text") or "").strip(),
        }
        urls.append(url)
        if len(urls) >= max_urls:
            break
    return urls, metadata


def build_comments_input(
    video_urls: list[str],
    comments_per_video: int,
) -> dict[str, Any]:
    return {
        "postURLs": video_urls,
        "commentsPerPost": comments_per_video,
        "maxRepliesPerComment": 0,
        "resultsPerPage": comments_per_video,
    }


def normalize_comments(
    items: list[dict[str, Any]],
    video_metadata: dict[str, dict[str, str]],
    seen: set[tuple[str, str]],
    max_comments: int,
    all_languages: bool,
) -> tuple[list[dict[str, str]], dict[str, int]]:
    metadata_by_id = {
        metadata["video_id"]: metadata
        for metadata in video_metadata.values()
        if metadata["video_id"]
    }
    rows: list[dict[str, str]] = []
    stats: Counter[str] = Counter(total_items=len(items))

    for item in items:
        if len(rows) >= max_comments:
            break
        if item.get("errorCode"):
            stats["error_items"] += 1
            print(
                f"Bo comment item loi: {item.get('errorCode')}: "
                f"{item.get('error', '')}",
                file=sys.stderr,
            )
            continue
        text = str(item.get("text") or "").strip()
        if not text:
            stats["empty_text"] += 1
            continue
        if not all_languages and not is_vietnamese(text):
            stats["language_filtered"] += 1
            continue

        url = normalize_video_url(str(item.get("videoWebUrl") or ""))
        video_id = video_id_from_url(url)
        metadata = video_metadata.get(url) or metadata_by_id.get(video_id)
        if metadata is None:
            metadata = {
                "category": "Unknown",
                "query_type": "apify_video_url",
                "keyword": "",
                "video_id": video_id,
                "title": "",
            }
        video_id = metadata["video_id"] or video_id
        author = str(item.get("uniqueId") or "").strip()
        comment_id = str(item.get("cid") or "").strip()
        if not comment_id:
            comment_id = stable_comment_id(video_id, text, author)
        key = (video_id, comment_id)
        if not video_id:
            stats["missing_video_id"] += 1
            continue
        if key in seen:
            stats["duplicate"] += 1
            continue
        seen.add(key)
        rows.append(
            {
                "platform": "tiktok",
                "category": metadata["category"],
                "query_type": metadata["query_type"],
                "keyword": metadata["keyword"],
                "content_id": video_id,
                "video_id": video_id,
                "title": metadata["title"],
                "comment_id": comment_id,
                "author": author,
                "published_at": str(item.get("createTimeISO") or ""),
                "comment": text,
            }
        )
        stats["kept"] += 1
    return rows, dict(stats)


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    args = parse_args()

    try:
        config, queries = load_search_queries(
            args.keywords_file,
            args.category,
            args.keyword,
        )
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 1
    if args.list_categories:
        print("\n".join(config["categories"]))
        return 0
    if args.max_queries:
        queries = queries[: args.max_queries]
    if not queries:
        print("Khong co query de chay.", file=sys.stderr)
        return 1

    planned_videos = len(queries) * args.max_videos
    print(
        f"Ke hoach: {len(queries)} query, toi da {planned_videos} video, "
        f"{args.comments_per_video} comment/video, "
        f"toi da {args.max_comments} comment moi."
    )
    print("Luu y: Apify Actors co the phat sinh chi phi.")
    if args.dry_run:
        for query in queries:
            print(
                f"- [{query.category}/{query.query_type}] "
                f"{submitted_search_term(query, args.search_suffix)}"
            )
        print("Dry run: khong goi Apify.")
        return 0
    token = os.getenv("APIFY_TOKEN")
    if not token:
        print(
            "Thieu APIFY_TOKEN. Dat bien moi truong APIFY_TOKEN truoc khi chay.",
            file=sys.stderr,
        )
        return 1

    try:
        seen = prepare_comment_csv(args.output)
        print(f"Chay Actor tim video: {args.search_actor}")
        search_items = run_actor(
            token,
            args.search_actor,
            build_search_input(
                queries,
                args.max_videos,
                args.search_suffix,
            ),
            args.timeout,
        )
        video_urls, video_metadata = resolve_videos(
            search_items,
            queries,
            args.max_comments,
            args.comments_per_video,
            args.search_suffix,
        )
        if not video_urls:
            print("Apify khong tra video hop le; khong goi Actor comment.")
            return 2
        print(f"Tim thay {len(video_urls)} video hop le.")
        print(f"Chay Actor lay comment: {args.comments_actor}")
        comment_items = run_actor(
            token,
            args.comments_actor,
            build_comments_input(video_urls, args.comments_per_video),
            args.timeout,
        )
        print(f"Actor comment tra {len(comment_items)} item.")
        rows, stats = normalize_comments(
            comment_items,
            video_metadata,
            seen,
            args.max_comments,
            args.all_languages,
        )
        print(
            "Thong ke chuan hoa: "
            + ", ".join(
                f"{name}={value}" for name, value in sorted(stats.items())
            )
        )
        if not rows:
            if not comment_items:
                print(
                    "Comments Actor khong tra item. Hay mo run trong Apify "
                    "Console de xem log/error cua Actor.",
                    file=sys.stderr,
                )
            elif stats.get("language_filtered", 0):
                print(
                    "Tat ca/phan lon comment bi loc ngon ngu. Thu query Viet "
                    "hon bang --search-suffix \"Viet Nam\"; "
                    "--all-languages chi nen dung de chan doan.",
                    file=sys.stderr,
                )
        append_comment_rows(args.output, rows)
    except (ApifyRunError, OSError, RuntimeError) as exc:
        print(f"Dung crawl TikTok Apify: {exc}", file=sys.stderr)
        return 2

    print(f"Da them {len(rows)}/{args.max_comments} comment moi vao {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
