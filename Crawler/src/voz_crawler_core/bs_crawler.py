import argparse
import csv
import json
import os
import random
import re
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit

import requests
from bs4 import BeautifulSoup, Tag

from comment_crawler.paths import VOZ_COMMENTS_PREFIX

CSV_COLUMNS = [
    "title", "thread_url", "post_id", "username", "datetime", "comment",
    "page",
]
MAX_PAGES_PER_THREAD = 1


def clean_text(element: Tag, remove_quotes: bool = True) -> str:
    """
    Lấy nội dung comment.

    remove_quotes=True:
        Xóa phần trích dẫn comment của người khác để tránh nội dung bị lặp.
    """
    if remove_quotes:
        for quote in element.select(
            ".bbCodeBlock--quote, "
            ".bbCodeBlock-title, "
            "blockquote"
        ):
            quote.decompose()

    return element.get_text("\n", strip=True)


def is_cloudflare_challenge(response: requests.Response) -> bool:
    text = response.text.lower()

    return response.status_code in {403, 429, 503} and any(
        keyword in text
        for keyword in (
            "just a moment",
            "cf-chl-",
            "challenge-platform",
            "cloudflare ray id",
        )
    )


def crawl_voz_thread(
    thread_url: str,
    include_original_post: bool = False,
    remove_quotes: bool = True,
    max_pages: int | None = None,
) -> dict[str, Any]:
    max_pages = MAX_PAGES_PER_THREAD
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/126.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "vi-VN,vi;q=0.9,en;q=0.8",
            "Accept": (
                "text/html,application/xhtml+xml,application/xml;"
                "q=0.9,image/avif,image/webp,*/*;q=0.8"
            ),
        }
    )

    current_url = thread_url
    title = None
    comments: list[dict[str, Any]] = []
    visited_pages: set[str] = set()
    seen_posts: set[str] = set()

    page_number = 1

    while current_url and current_url not in visited_pages:
        if max_pages is not None and page_number > max_pages:
            break

        print(f"Crawling page {page_number}: {current_url}")
        visited_pages.add(current_url)

        response = session.get(current_url, timeout=30)

        if is_cloudflare_challenge(response):
            raise RuntimeError(
                f"Cloudflare challenge tại {current_url}. "
                "Dừng crawl và giảm tốc độ truy cập."
            )

        if response.status_code == 429:
            retry_after = response.headers.get("Retry-After", "không xác định")
            raise RuntimeError(
                f"Bị rate limit 429. Retry-After: {retry_after}"
            )

        response.raise_for_status()

        soup = BeautifulSoup(response.text, "html.parser")

        if title is None:
            title_element = soup.select_one("h1.p-title-value")

            if not title_element:
                raise RuntimeError("Không tìm thấy title của thread.")

            # Loại bỏ nhãn như “thảo luận” khỏi title nếu có
            for label in title_element.select(".labelLink, .label"):
                label.decompose()

            title = title_element.get_text(" ", strip=True)

        articles = soup.select("article.message")

        for article in articles:
            body_element = article.select_one(".message-body .bbWrapper")

            if not body_element:
                continue

            number_element = article.select_one(
                ".message-attribution-opposite a"
            )
            number_text = (
                number_element.get_text(" ", strip=True)
                if number_element
                else ""
            )

            number_match = re.search(r"#?(\d+)", number_text)
            post_number = (
                int(number_match.group(1))
                if number_match
                else None
            )

            # Bài số 1 là nội dung mở thread, chưa phải comment
            if post_number == 1 and not include_original_post:
                continue

            username_element = article.select_one(
                ".message-name .username"
            )
            time_element = article.select_one("time[datetime]")

            post_id = (
                article.get("data-content")
                or article.get("id")
                or f"{page_number}-{post_number}-{len(comments)}"
            )

            if post_id in seen_posts:
                continue

            seen_posts.add(post_id)

            comment = {
                "post_id": post_id,
                "post_number": post_number,
                "username": (
                    username_element.get_text(" ", strip=True)
                    if username_element
                    else None
                ),
                "datetime": (
                    time_element.get("datetime")
                    if time_element
                    else None
                ),
                "comment": clean_text(
                    body_element,
                    remove_quotes=remove_quotes,
                ),
                "page": page_number,
            }

            comments.append(comment)

        next_element = soup.select_one("a.pageNav-jump--next")

        if next_element and next_element.get("href"):
            next_url = urljoin(response.url, next_element["href"])

            if next_url in visited_pages:
                break

            current_url = next_url
            page_number += 1

            # Crawl chậm để tránh tạo tải lớn
            time.sleep(random.uniform(2.0, 4.0))
        else:
            current_url = None

    return {
        "url": thread_url,
        "title": title,
        "total_comments": len(comments),
        "comments": comments,
    }


def post_key(comment: dict[str, Any]) -> str:
    """Tạo khóa chống trùng; ưu tiên post_id do VOZ cung cấp."""
    post_id = str(comment.get("post_id") or "").strip()
    if post_id:
        return post_id
    return "|".join(
        str(comment.get(field) or "")
        for field in ("post_number", "username", "datetime", "page", "comment")
    )


def load_json_collection(path: Path) -> dict[str, Any]:
    """Đọc JSON cũ và chuyển định dạng một thread sang collection."""
    if not path.exists() or path.stat().st_size == 0:
        return {"total_threads": 0, "total_comments": 0, "threads": []}
    try:
        old = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"JSON cũ bị lỗi, không ghi đè: {path}: {exc}") from exc

    if isinstance(old, dict) and isinstance(old.get("threads"), list):
        return old
    if isinstance(old, dict) and "url" in old and isinstance(old.get("comments"), list):
        return {
            "total_threads": 1,
            "total_comments": len(old["comments"]),
            "threads": [old],
        }
    raise RuntimeError(f"JSON cũ không đúng cấu trúc hỗ trợ: {path}")


def merge_json_result(path: Path, result: dict[str, Any]) -> tuple[int, int]:
    """Merge thread/comment vào JSON cũ rồi thay file theo cách nguyên tử."""
    collection = load_json_collection(path)
    threads: list[dict[str, Any]] = collection["threads"]
    existing = next((x for x in threads if x.get("url") == result["url"]), None)
    added = 0

    if existing is None:
        threads.append(result)
        added = len(result["comments"])
    else:
        comments = existing.setdefault("comments", [])
        known = {post_key(comment) for comment in comments}
        for comment in result["comments"]:
            key = post_key(comment)
            if key not in known:
                known.add(key)
                comments.append(comment)
                added += 1
        existing["title"] = result.get("title") or existing.get("title")
        existing["total_comments"] = len(comments)

    collection["total_threads"] = len(threads)
    collection["total_comments"] = sum(
        len(thread.get("comments", [])) for thread in threads
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    with temp_path.open("w", encoding="utf-8") as file:
        json.dump(collection, file, ensure_ascii=False, indent=2)
        file.write("\n")
        file.flush()
        os.fsync(file.fileno())
    os.replace(temp_path, path)
    return added, collection["total_comments"]


def append_csv_result(path: Path, result: dict[str, Any]) -> int:
    """Chỉ append các bài chưa tồn tại trong CSV."""
    path.parent.mkdir(parents=True, exist_ok=True)
    new_file = not path.exists() or path.stat().st_size == 0
    known: set[tuple[str, str]] = set()

    if not new_file:
        with path.open("r", encoding="utf-8-sig", newline="") as file:
            reader = csv.DictReader(file)
            if reader.fieldnames != CSV_COLUMNS:
                raise RuntimeError(f"Header CSV cũ không đúng, không ghi đè: {path}")
            for row in reader:
                known.add((row.get("thread_url", ""), row.get("post_id", "")))

    rows: list[dict[str, Any]] = []
    for comment in result["comments"]:
        key = (result["url"], str(comment.get("post_id") or ""))
        if key in known:
            continue
        known.add(key)
        rows.append(
            {
                "title": result["title"],
                "thread_url": result["url"],
                "post_id": comment.get("post_id"),
                "username": comment.get("username"),
                "datetime": comment.get("datetime"),
                "comment": comment.get("comment"),
                "page": comment.get("page"),
            }
        )

    if not rows:
        return 0
    with path.open("a", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=CSV_COLUMNS)
        if new_file:
            writer.writeheader()
        writer.writerows(rows)
        file.flush()
        os.fsync(file.fileno())
    return len(rows)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Crawl một VOZ thread và cộng dồn vào JSON/CSV."
    )
    parser.add_argument("url", help="URL thread, dạng https://voz.vn/t/.../")
    parser.add_argument(
        "--max-pages",
        type=int,
        default=MAX_PAGES_PER_THREAD,
        help="Số trang mỗi thread; crawler này cố định là 1.",
    )
    parser.add_argument("--include-original-post", action="store_true")
    parser.add_argument("--keep-quotes", action="store_true")
    parser.add_argument("--output-prefix", default=str(VOZ_COMMENTS_PREFIX))
    args = parser.parse_args()

    parts = urlsplit(args.url)
    if parts.scheme != "https" or parts.hostname != "voz.vn" or not parts.path.startswith("/t/"):
        parser.error("URL phải là thread HTTPS trên voz.vn, dạng /t/...")
    if args.max_pages != MAX_PAGES_PER_THREAD:
        parser.error("--max-pages phải bằng 1; mỗi thread chỉ crawl trang đầu.")
    return args


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    args = parse_args()
    try:
        result = crawl_voz_thread(
            thread_url=args.url,
            include_original_post=args.include_original_post,
            remove_quotes=not args.keep_quotes,
            max_pages=args.max_pages,
        )
    except (requests.RequestException, RuntimeError) as exc:
        print(f"Không crawl được thread: {exc}")
        return 2

    json_path = Path(f"{args.output_prefix}.json")
    csv_path = Path(f"{args.output_prefix}.csv")
    try:
        # Xác thực JSON cũ trước khi append CSV để file JSON lỗi không làm hai
        # output lệch nhau.
        load_json_collection(json_path)
        csv_added = append_csv_result(csv_path, result)
        json_added, json_total = merge_json_result(json_path, result)
    except (OSError, RuntimeError) as exc:
        print(f"Không thể cập nhật output an toàn: {exc}")
        return 1

    print("Title:", result["title"])
    print("Số bài đọc được:", result["total_comments"])
    print(f"CSV thêm {csv_added} dòng; JSON thêm {json_added} bài, tổng {json_total} bài.")
    print(f"Đã cập nhật {json_path} và {csv_path}, không xóa dữ liệu cũ.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
