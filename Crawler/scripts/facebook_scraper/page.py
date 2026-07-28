"""Crawl Facebook Page posts and comments without downloading images."""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode, urlsplit, urlunsplit

from playwright.sync_api import (
    Page,
    TimeoutError as PlaywrightTimeoutError,
    sync_playwright,
)

import group as group_scraper


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PAGE_URL = "https://www.facebook.com/tintucvtv24"
#  https://www.facebook.com/hoinguoithucdungvietnam
#  https://www.facebook.com/topcomments.vn?locale=vi_VN
#  https://www.facebook.com/thongtinchinhphu?locale=vi_VN
#  https://www.facebook.com/trollgamecom
#  https://www.facebook.com/bongdatructuyentv
#  https://www.facebook.com/Theanh28?locale=vi_VN
DEFAULT_OUTPUT = PROJECT_ROOT / "data" / "facebook" / "facebook_page" / "data.json"
PAGE_POST_RE = re.compile(r"^/([^/?#]+)/posts/([A-Za-z0-9]+)", re.IGNORECASE)
POST_ID_RE = re.compile(r"/(?:permalink|posts)/([A-Za-z0-9]+)", re.IGNORECASE)


def configure_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def normalize_output_path(path: Path) -> Path:
    return path if path.is_absolute() else PROJECT_ROOT / path


def normalize_page_url(url: str) -> str:
    parts = urlsplit(url.strip())
    path = "/" + parts.path.strip("/")
    if path == "/":
        raise ValueError("Page URL không hợp lệ.")
    return urlunsplit(("https", "www.facebook.com", path, "", ""))


def page_slug(page_url: str) -> str:
    slug = urlsplit(page_url).path.strip("/").split("/", 1)[0]
    if not slug:
        raise ValueError(f"Không tìm thấy Page slug trong URL: {page_url}")
    return slug


def canonical_page_post_url(url: str | None, expected_slug: str) -> str | None:
    if not url:
        return None
    try:
        parts = urlsplit(url)
    except ValueError:
        return None

    match = PAGE_POST_RE.match(parts.path)
    if not match or match.group(1).lower() != expected_slug.lower():
        return None

    post_id = match.group(2)
    path = f"/{expected_slug}/posts/{post_id}"
    return urlunsplit(
        ("https", "www.facebook.com", path, urlencode({"locale": "vi_VN"}), "")
    )


def backup_for_reset(path: Path) -> Path | None:
    if not path.exists() or path.stat().st_size == 0:
        return None
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = path.with_name(f"{path.stem}.before-reset-{stamp}{path.suffix}")
    shutil.copy2(path, backup)
    return backup


def load_records(path: Path, reset: bool) -> list[dict]:
    if reset:
        backup = backup_for_reset(path)
        if backup:
            print(f"Đã sao lưu dataset cũ: {backup}")
        return []

    if not path.exists():
        return []

    try:
        with path.open("r", encoding="utf-8") as file:
            records = json.load(file)
    except (json.JSONDecodeError, OSError) as error:
        backup = path.with_suffix(path.suffix + ".bak")
        if not backup.exists():
            raise RuntimeError(f"Không đọc được {path}: {error}") from error
        print(f"⚠️ File chính lỗi, khôi phục từ {backup}")
        with backup.open("r", encoding="utf-8") as file:
            records = json.load(file)

    if not isinstance(records, list) or not all(
        isinstance(record, dict) for record in records
    ):
        raise RuntimeError("Schema page data.json không hợp lệ: cần một JSON array.")
    return records


def save_records_safely(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    backup_path = path.with_suffix(path.suffix + ".bak")

    with temp_path.open("w", encoding="utf-8") as file:
        json.dump(records, file, ensure_ascii=False, indent=2)
        file.flush()
        os.fsync(file.fileno())

    with temp_path.open("r", encoding="utf-8") as file:
        json.load(file)

    if path.exists() and path.stat().st_size > 0:
        shutil.copy2(path, backup_path)
    os.replace(temp_path, path)


def wait_for_post_links(
    page: Page,
    expected_slug: str,
    timeout_ms: int = 15000,
) -> list[str]:
    deadline = time.monotonic() + timeout_ms / 1000
    urls: list[str] = []
    while time.monotonic() < deadline:
        urls = extract_visible_post_urls(page, expected_slug)
        if urls:
            return urls
        page.wait_for_timeout(500)
    return urls


def extract_visible_post_urls(page: Page, expected_slug: str) -> list[str]:
    raw_urls = page.evaluate(
        r"""({ slug }) => {
            const prefix = `/${slug.toLowerCase()}/posts/`;
            const result = [];
            const seen = new Set();

            for (const anchor of document.querySelectorAll('a[href]')) {
                let url;
                try {
                    url = new URL(anchor.href, location.href);
                } catch (_) {
                    continue;
                }

                const path = url.pathname.toLowerCase();
                if (!path.startsWith(prefix)) continue;

                const key = `${url.origin}${url.pathname}`;
                if (!seen.has(key)) {
                    seen.add(key);
                    result.push(key);
                }
            }
            return result;
        }""",
        {"slug": expected_slug},
    )

    result: list[str] = []
    seen: set[str] = set()
    for raw_url in raw_urls:
        canonical = canonical_page_post_url(raw_url, expected_slug)
        if canonical and canonical not in seen:
            seen.add(canonical)
            result.append(canonical)
    return result


def scroll_feed(page: Page) -> None:
    for attempt in range(3):
        try:
            page.evaluate(
                """() => window.scrollBy({
                    top: Math.max(window.innerHeight * 2.2, 1800),
                    behavior: 'smooth'
                })"""
            )
            page.wait_for_timeout(random.randint(2200, 3400))
            return
        except Exception:
            if attempt == 2:
                raise
            page.wait_for_timeout(1000)


def patch_shared_post_id_parser() -> None:
    # Facebook Page dùng pfbid (chữ + số), trong khi Group thường dùng ID số.
    group_scraper.POST_LINK_RE = POST_ID_RE


def comments_are_structured(record: dict) -> bool:
    comments = record.get("comments")
    return isinstance(comments, list) and all(
        isinstance(comment, dict)
        and isinstance(comment.get("post_id"), str)
        and isinstance(comment.get("username"), str)
        and bool(comment["username"].strip())
        and isinstance(comment.get("datetime"), str)
        and bool(comment["datetime"].strip())
        and isinstance(comment.get("comment"), str)
        for comment in comments
    )


def thread_to_record(thread: dict, record_id: int) -> dict:
    rows = thread.get("comments", [])
    if not rows:
        raise RuntimeError("Bài viết không có nội dung sau khi trích xuất.")

    original = rows[0]
    replies = rows[1:]
    comments = [
        {
            "post_id": reply["post_id"],
            "post_number": number,
            "username": reply.get("username"),
            "datetime": reply.get("datetime"),
            "comment": reply["comment"],
            "page": reply.get("page", 1),
        }
        for number, reply in enumerate(replies, start=1)
        if reply.get("comment")
    ]
    return {
        "id": record_id,
        "url": thread["url"],
        "title": thread["title"],
        "text": original["comment"],
        "username": original.get("username"),
        "datetime": original.get("datetime"),
        "total_comments": len(comments),
        "comments": comments,
    }


def merge_record(existing: dict, incoming: dict) -> None:
    existing["url"] = incoming["url"]
    existing["title"] = incoming["title"]
    existing["text"] = incoming["text"]
    existing["username"] = incoming.get("username")
    existing["datetime"] = incoming.get("datetime")
    existing["total_comments"] = incoming["total_comments"]
    existing["comments"] = incoming["comments"]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Crawl Facebook Page posts và comments, không tải ảnh."
    )
    parser.add_argument("--page-url", default=DEFAULT_PAGE_URL)
    parser.add_argument(
        "--target",
        type=int,
        default=50,
        help="Tổng số bài cần có trong output, không phải số bài bổ sung.",
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--cdp-url", default="http://localhost:9222")
    parser.add_argument("--max-expand-clicks", type=int, default=40)
    parser.add_argument("--max-idle-rounds", type=int, default=15)
    parser.add_argument("--delay-min", type=float, default=1.0)
    parser.add_argument("--delay-max", type=float, default=2.0)
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Sao lưu output cũ rồi crawl lại từ đầu.",
    )
    return parser.parse_args()


def main() -> int:
    configure_console()
    args = parse_args()
    if args.target <= 0:
        raise SystemExit("--target phải lớn hơn 0.")
    if args.delay_min < 0 or args.delay_max < args.delay_min:
        raise SystemExit("Khoảng delay không hợp lệ.")

    page_url = normalize_page_url(args.page_url)
    expected_slug = page_slug(page_url)
    output = normalize_output_path(args.output)
    records = load_records(output, args.reset)
    next_id = max(
        (
            record.get("id", -1)
            for record in records
            if isinstance(record.get("id"), int)
        ),
        default=-1,
    ) + 1

    known_urls: set[str] = set()
    refresh_urls: set[str] = set()
    records_by_url: dict[str, dict] = {}
    for record in records:
        canonical = canonical_page_post_url(record.get("url"), expected_slug)
        if not canonical:
            continue
        records_by_url[canonical] = record
        if comments_are_structured(record):
            known_urls.add(canonical)
        else:
            refresh_urls.add(canonical)
    records_by_text = {
        group_scraper.clean_text(record.get("text")): record
        for record in records
        if group_scraper.clean_text(record.get("text"))
    }
    failed_attempts: dict[str, int] = {}
    patch_shared_post_id_parser()

    print(f"Page: {page_url}")
    print(f"Output: {output}")
    print(f"Tiến độ ban đầu: {len(records)}/{args.target} bài")
    if refresh_urls:
        print(
            f"Cần nâng cấp {len(refresh_urls)} bài có comments dạng chuỗi "
            "hoặc thiếu username/datetime."
        )

    with sync_playwright() as playwright:
        browser = playwright.chromium.connect_over_cdp(args.cdp_url, timeout=15000)
        if not browser.contexts:
            raise RuntimeError("Chrome không có browser context.")

        context = browser.contexts[0]
        feed_page = next(
            (
                page
                for page in context.pages
                if expected_slug.lower() in page.url.lower()
            ),
            None,
        )
        owns_feed_page = feed_page is None
        if feed_page is None:
            feed_page = context.new_page()
        detail_page = context.new_page()
        crawled_any = False

        try:
            feed_page.bring_to_front()
            feed_page.goto(page_url, wait_until="domcontentloaded", timeout=30000)
            feed_page.wait_for_timeout(4000)
            if feed_page.locator('input[name="email"]').count():
                raise RuntimeError("Chrome chưa đăng nhập Facebook.")

            initial_urls = wait_for_post_links(feed_page, expected_slug)
            print(f"Feed ban đầu: {len(initial_urls)} permalink.")
            idle_rounds = 0

            while len(records) < args.target or refresh_urls:
                urls = extract_visible_post_urls(feed_page, expected_slug)
                handled_this_round = 0
                print(f"Tìm thấy {len(urls)} permalink trong vùng đang hiển thị.")

                for post_url in urls:
                    if post_url in known_urls or failed_attempts.get(post_url, 0) >= 2:
                        continue

                    try:
                        thread = group_scraper.crawl_thread(
                            detail_page,
                            post_url,
                            args.max_expand_clicks,
                        )
                    except (PlaywrightTimeoutError, RuntimeError, ValueError) as error:
                        failed_attempts[post_url] = failed_attempts.get(post_url, 0) + 1
                        print(f"⚠️ Bỏ qua tạm {post_url}: {error}")
                        continue
                    except Exception as error:
                        failed_attempts[post_url] = failed_attempts.get(post_url, 0) + 1
                        print(f"⚠️ Lỗi không xác định tại {post_url}: {error}")
                        continue

                    incoming = thread_to_record(thread, next_id)
                    text_key = group_scraper.clean_text(incoming["text"])
                    existing = records_by_url.get(post_url)
                    if existing is None:
                        text_match = records_by_text.get(text_key)
                        if text_match is not None and not comments_are_structured(text_match):
                            existing = text_match
                    if incoming["comments"] and not comments_are_structured(incoming):
                        failed_attempts[post_url] = failed_attempts.get(post_url, 0) + 1
                        print(
                            "⚠️ Comment chưa đủ username/datetime; "
                            f"giữ dữ liệu cũ và thử lại: {post_url}"
                        )
                        continue
                    if (
                        existing is not None
                        and existing.get("comments")
                        and not incoming["comments"]
                    ):
                        failed_attempts[post_url] = failed_attempts.get(post_url, 0) + 1
                        print(
                            "⚠️ Lần recrawl chưa tải được reply; "
                            f"giữ dữ liệu cũ và thử lại: {post_url}"
                        )
                        continue
                    if existing is not None:
                        old_url = canonical_page_post_url(
                            existing.get("url"),
                            expected_slug,
                        )
                        merge_record(existing, incoming)
                        if old_url:
                            refresh_urls.discard(old_url)
                            records_by_url.pop(old_url, None)
                        records_by_url[post_url] = existing
                        print(f"Đã cập nhật bài cũ: {incoming['title'][:90]}")
                    else:
                        if len(records) >= args.target:
                            known_urls.add(post_url)
                            continue
                        records.append(incoming)
                        records_by_text[text_key] = incoming
                        records_by_url[post_url] = incoming
                        next_id += 1
                        print(f"[{len(records)}/{args.target}] {incoming['title'][:90]}")

                    known_urls.add(post_url)
                    refresh_urls.discard(post_url)
                    handled_this_round += 1
                    crawled_any = True
                    save_records_safely(output, records)

                    if len(records) >= args.target and not refresh_urls:
                        break
                    time.sleep(random.uniform(args.delay_min, args.delay_max))

                if len(records) >= args.target and not refresh_urls:
                    break

                if handled_this_round == 0:
                    idle_rounds += 1
                    print(f"Không có bài mới ({idle_rounds}/{args.max_idle_rounds}).")
                    if idle_rounds >= args.max_idle_rounds:
                        break
                else:
                    idle_rounds = 0

                scroll_feed(feed_page)
        finally:
            detail_page.close()
            if owns_feed_page:
                feed_page.close()

    if records and (crawled_any or not args.reset):
        save_records_safely(output, records)
    elif args.reset:
        print("⚠️ Không crawl được bài mới; giữ nguyên output cũ.")

    total_replies = sum(
        len(record.get("comments", []))
        for record in records
        if isinstance(record.get("comments"), list)
    )
    if refresh_urls:
        print(
            f"⚠️ Còn {len(refresh_urls)} bài legacy chưa xuất hiện trong feed "
            "để nâng cấp comment."
        )
    print(f"HOÀN THÀNH: {len(records)} bài, {total_replies} comment.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
