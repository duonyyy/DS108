#!/usr/bin/env python3
"""Crawler tuần tự các thread công khai trên VOZ bằng async Playwright."""

from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import json
import logging
import os
import random
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit, urlunsplit

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Locator, Page
from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from playwright.async_api import async_playwright

from comment_crawler.paths import (
    VOZ_PLAYWRIGHT_COMMENTS_CSV,
    VOZ_PLAYWRIGHT_RUNTIME_DIR,
)

BASE_URL = "https://voz.vn/"
OUTPUT_DIR = VOZ_PLAYWRIGHT_RUNTIME_DIR
CSV_FILE = VOZ_PLAYWRIGHT_COMMENTS_CSV
CHECKPOINT_FILE = OUTPUT_DIR / "checkpoint.json"
BROWSER_PROFILE_DIR = OUTPUT_DIR / "browser_profile"
MAX_RETRIES = 3
MAX_PAGES_PER_THREAD = 1
MANUAL_VERIFY_TIMEOUT_SECONDS = 300

CSV_COLUMNS = [
    "record_id", "title", "comment", "thread_index", "forum_name",
    "thread_title", "thread_url",
    "post_id", "post_url", "post_type", "post_number", "author",
    "author_url", "published_at", "content_text", "content_html",
    "page_number", "position_on_page", "collected_at",
]

VERIFY_TEXTS = [
    "just a moment", "checking your browser", "verify you are human",
    "captcha", "attention required", "cf-chl-",
]
NORMAL_CONTENT_SELECTOR = "article.message, .node, .structItem, a[href*='/f/']"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
LOG = logging.getLogger("voz_crawler")


# ---------------------------------------------------------------------------
# 1. DỮ LIỆU CƠ BẢN: URL, CHECKPOINT VÀ CSV
# ---------------------------------------------------------------------------


class CrawlError(RuntimeError):
    """Lỗi crawl có thể ghi vào checkpoint."""


class VerificationError(CrawlError):
    """Cloudflare hoặc CAPTCHA đã xuất hiện."""


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_voz_url(href: str | None, base: str = BASE_URL) -> str | None:
    """Đổi URL tương đối thành tuyệt đối và chỉ nhận hostname voz.vn."""
    if not href:
        return None
    url = urljoin(base, href)
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"} or parts.hostname != "voz.vn":
        return None
    return urlunsplit(("https", parts.netloc.lower(), parts.path, parts.query, ""))


def save_checkpoint_safely(data: dict[str, Any]) -> None:
    """Ghi JSON qua file tạm rồi thay thế file cũ."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    temp_file = CHECKPOINT_FILE.with_suffix(".json.tmp")
    with temp_file.open("w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)
        file.write("\n")
        file.flush()
        os.fsync(file.fileno())
    os.replace(temp_file, CHECKPOINT_FILE)


def load_checkpoint(target: int) -> dict[str, Any]:
    """Đọc checkpoint hoặc tạo checkpoint mới."""
    if not CHECKPOINT_FILE.exists():
        data = {
            "target": target,
            "selected_threads": [],
            "candidate_pool": [],
            "rejected_urls": [],
            "selection_complete": False,
            "last_error": None,
        }
        save_checkpoint_safely(data)
        return data

    try:
        with CHECKPOINT_FILE.open("r", encoding="utf-8") as file:
            data = json.load(file)
    except (OSError, json.JSONDecodeError) as exc:
        raise CrawlError(f"Checkpoint bị lỗi: {exc}") from exc

    if data.get("target") != target:
        raise CrawlError(
            f"Checkpoint đang dùng --target {data.get('target')}. "
            "Hãy dùng cùng target hoặc chạy --reset."
        )

    # Một lần chạy bị Ctrl+C không được tính là một lần retry thất bại.
    for thread in data["selected_threads"]:
        if thread["status"] == "running":
            thread["status"] = "pending"
            thread["attempts"] = max(0, thread["attempts"] - 1)
            thread["last_error"] = "Lần chạy trước bị gián đoạn."
    save_checkpoint_safely(data)
    return data


def load_record_ids() -> set[str]:
    """Chỉ đọc cột record_id để chống ghi trùng."""
    ids: set[str] = set()
    if not CSV_FILE.exists() or CSV_FILE.stat().st_size == 0:
        return ids

    with CSV_FILE.open("r", encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        if reader.fieldnames != CSV_COLUMNS:
            raise CrawlError("Header CSV cũ không đúng. Hãy kiểm tra hoặc chạy --reset.")
        for row in reader:
            if row.get("record_id"):
                ids.add(row["record_id"])
    return ids


def append_page_to_csv(rows: list[dict[str, Any]], known_ids: set[str]) -> int:
    """Ghi ngay một trang vào CSV, sau đó flush và fsync."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    CSV_FILE.parent.mkdir(parents=True, exist_ok=True)
    new_file = not CSV_FILE.exists() or CSV_FILE.stat().st_size == 0
    written = 0

    # Reader hỗ trợ BOM, nhưng append bằng utf-8 để không chèn BOM giữa file.
    with CSV_FILE.open("a", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=CSV_COLUMNS)
        if new_file:
            writer.writeheader()

        for row in rows:
            if row["record_id"] in known_ids:
                continue
            writer.writerow(row)
            known_ids.add(row["record_id"])
            written += 1

        file.flush()
        os.fsync(file.fileno())
    return written


async def find_first(parent: Page | Locator, selectors: list[str]) -> Locator | None:
    """Trả về selector đầu tiên tìm thấy."""
    for selector in selectors:
        item = parent.locator(selector)
        if await item.count():
            return item.first
    return None


async def get_first_text(parent: Page | Locator, selectors: list[str]) -> str:
    item = await find_first(parent, selectors)
    return (await item.inner_text()).strip() if item else ""


async def get_next_page_url(page: Page) -> str | None:
    item = await find_first(page, ["a.pageNav-jump--next", "a[rel='next']"])
    if not item:
        return None
    return normalize_voz_url(await item.get_attribute("href"), page.url)


class BrowserSession:
    """Giữ một Page duy nhất và đảm bảo request chạy tuần tự."""

    def __init__(self, page: Page, allow_manual_verification: bool = False) -> None:
        self.page = page
        self.allow_manual_verification = allow_manual_verification
        self.request_count = 0

    async def verification_marker(self) -> str | None:
        """Trả về dấu hiệu trang xác minh, nếu có."""
        title = (await self.page.title()).lower()
        try:
            body = (await self.page.locator("body").inner_text(timeout=5_000)).lower()
        except PlaywrightTimeoutError:
            body = ""

        current_url = self.page.url.lower()
        if "__cf_chl" in current_url:
            return "__cf_chl"

        challenge = self.page.locator(
            "iframe[src*='challenges.cloudflare.com'], "
            "#challenge-running, .cf-turnstile, [name='cf-turnstile-response']"
        )
        if await challenge.count():
            return "cloudflare challenge"

        # Không quét từ khóa chung chung trong nội dung VOZ thật: tiêu đề hoặc
        # bài viết của người dùng có thể chứa chính các từ như "captcha".
        normal_content = self.page.locator(NORMAL_CONTENT_SELECTOR)
        if await normal_content.count():
            return None
        for marker in VERIFY_TEXTS:
            if marker in title or marker in body:
                return marker
        return None

    async def wait_for_manual_verification(self, safe_url: str) -> None:
        """Chờ challenge tự kết thúc hoặc người dùng xử lý trong headful."""
        LOG.warning(
            "Cloudflare đang kiểm tra trình duyệt. Nếu có yêu cầu tương tác, "
            "hãy hoàn tất trong Chromium (tối đa %d giây).",
            MANUAL_VERIFY_TIMEOUT_SECONDS,
        )
        deadline = asyncio.get_running_loop().time() + MANUAL_VERIFY_TIMEOUT_SECONDS
        while asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(1)
            if self.page.is_closed():
                raise VerificationError("Cửa sổ Chromium đã bị đóng khi đang xác minh")
            if await self.verification_marker() is None:
                try:
                    await self.page.locator(NORMAL_CONTENT_SELECTOR).first.wait_for(
                        state="attached", timeout=1_000
                    )
                except PlaywrightTimeoutError:
                    # Challenge vừa biến mất nhưng trang VOZ đích chưa render xong.
                    continue
                LOG.info("Cloudflare kiểm tra xong, tiếp tục crawl: %s", safe_url)
                return
        raise VerificationError(
            f"Hết {MANUAL_VERIFY_TIMEOUT_SECONDS} giây chờ xác minh tại {safe_url}"
        )

    async def open_url(self, url: str) -> int:
        """Mở một URL VOZ sau khi nghỉ ngẫu nhiên 3–6 giây."""
        safe_url = normalize_voz_url(url)
        if not safe_url:
            raise CrawlError(f"URL ngoài voz.vn: {url}")

        if self.request_count:
            delay = random.uniform(3, 6)
            LOG.info("Nghỉ %.1f giây", delay)
            await asyncio.sleep(delay)
        self.request_count += 1

        try:
            response = await self.page.goto(
                safe_url, wait_until="domcontentloaded", timeout=45_000
            )
        except PlaywrightTimeoutError as exc:
            raise CrawlError(f"Timeout: {safe_url}") from exc

        if response is None:
            raise CrawlError(f"Không nhận được HTTP response: {safe_url}")

        marker = await self.verification_marker()
        if marker:
            if not self.allow_manual_verification:
                raise VerificationError(f"Phát hiện trang xác minh tại {safe_url}")
            await self.wait_for_manual_verification(safe_url)
            # response là response 403 ban đầu của challenge. Sau khi người dùng
            # xác minh, nội dung hiện tại đã được tải bằng một navigation khác.
            return 200

        status = response.status
        if status in {403, 404, 429} or status >= 500:
            raise CrawlError(f"HTTP {status}: {safe_url}")
        if status < 200 or status >= 400:
            raise CrawlError(f"HTTP {status}: {safe_url}")
        return status


async def block_unneeded_resources(route: Any) -> None:
    """Không tải image, font và media."""
    if route.request.resource_type in {"image", "font", "media"}:
        await route.abort()
    else:
        await route.continue_()


async def discover_public_forums(session: BrowserSession) -> list[dict[str, str]]:
    """Đọc trang chủ và trả về các chuyên mục công khai."""
    await session.open_url(BASE_URL)
    links = session.page.locator("a[href*='/f/']")
    forums: list[dict[str, str]] = []
    seen_urls: set[str] = set()

    for index in range(await links.count()):
        link = links.nth(index)
        url = normalize_voz_url(await link.get_attribute("href"), session.page.url)
        name = (await link.inner_text()).strip()

        is_forum_url = bool(url and re.search(r"/f/.+\.\d+/?$", url))
        if is_forum_url and name and url not in seen_urls:
            seen_urls.add(url)
            forums.append({"name": name, "url": url})

    random.shuffle(forums)
    return forums


async def read_candidates_on_current_page(
    page: Page,
    forum_name: str,
    excluded_urls: set[str],
) -> list[dict[str, str]]:
    """Đọc các thread không ghim trên trang danh sách đang mở."""
    result: list[dict[str, str]] = []
    rows = page.locator(".structItem--thread, .structItem[data-author]")

    for index in range(await rows.count()):
        row = rows.nth(index)
        if await row.locator(".structItem-status--sticky").count():
            continue

        link = await find_first(row, [
            ".structItem-title a[href*='/t/']",
            "a[data-tp-primary][href*='/t/']",
            "a[href*='/t/']",
        ])
        if not link:
            continue

        thread_url = normalize_voz_url(await link.get_attribute("href"), page.url)
        is_thread_url = bool(thread_url and "/t/" in urlsplit(thread_url).path)
        if not is_thread_url or thread_url in excluded_urls:
            continue

        result.append({
            "forum_name": forum_name,
            "thread_title": (await link.inner_text()).strip(),
            "thread_url": thread_url,
        })
        excluded_urls.add(thread_url)

    return result


# ---------------------------------------------------------------------------
# 2. CHỌN DANH SÁCH THREAD
# ---------------------------------------------------------------------------


async def collect_thread_candidates(
    session: BrowserSession,
    checkpoint: dict[str, Any],
    target: int,
) -> None:
    """Lấy candidate từ nhiều chuyên mục và lưu pool để có thể resume."""
    forums = await discover_public_forums(session)
    selected_urls = {x["thread_url"] for x in checkpoint["selected_threads"]}
    rejected_urls = set(checkpoint["rejected_urls"])
    candidates: list[dict[str, str]] = []
    excluded_urls = selected_urls | rejected_urls
    # Pool dự phòng vừa đủ để loại URL lỗi mà không quét quá nhiều forum khi
    # người dùng chỉ yêu cầu một mẫu nhỏ.
    wanted = max(target * 2, target + 20)
    # Giới hạn mỗi forum để mẫu không bị dồn vào một chuyên mục.
    per_forum = max(5, target // 8)

    for forum in forums:
        url: str | None = forum["url"]
        added_from_forum = 0
        visited_pages: set[str] = set()

        while url and url not in visited_pages and added_from_forum < per_forum:
            visited_pages.add(url)
            try:
                await session.open_url(url)
            except VerificationError:
                raise
            except CrawlError as exc:
                LOG.warning("Bỏ trang danh sách %s: %s", url, exc)
                break

            page_candidates = await read_candidates_on_current_page(
                session.page, forum["name"], excluded_urls
            )
            remaining_slots = per_forum - added_from_forum
            candidates.extend(page_candidates[:remaining_slots])
            added_from_forum += min(len(page_candidates), remaining_slots)

            url = await get_next_page_url(session.page)
        if len(candidates) >= wanted:
            break

    random.shuffle(candidates)
    checkpoint["candidate_pool"] = candidates
    save_checkpoint_safely(checkpoint)
    if len(candidates) + len(checkpoint["selected_threads"]) < target:
        raise CrawlError(f"Chỉ tìm được {len(candidates)} candidate, không đủ target={target}.")


async def select_threads(
    session: BrowserSession,
    checkpoint: dict[str, Any],
    target: int,
) -> None:
    """Kiểm tra candidate truy cập được rồi chốt danh sách thread."""
    selected = checkpoint["selected_threads"]
    if checkpoint["selection_complete"]:
        return

    while len(selected) < target:
        if not checkpoint["candidate_pool"]:
            await collect_thread_candidates(session, checkpoint, target)

        candidate = checkpoint["candidate_pool"].pop()
        save_checkpoint_safely(checkpoint)
        try:
            await session.open_url(candidate["thread_url"])
            posts = session.page.locator("article.message--post, article.message[data-content]")
            if not await posts.count():
                raise CrawlError("Không tìm thấy bài viết")
        except VerificationError:
            raise
        except CrawlError as exc:
            LOG.warning("Loại %s: %s", candidate["thread_url"], exc)
            checkpoint["rejected_urls"].append(candidate["thread_url"])
            save_checkpoint_safely(checkpoint)
            continue

        live_title = await get_first_text(
            session.page, ["h1.p-title-value", ".p-title-value"]
        )
        selected.append({
            "thread_index": len(selected) + 1,
            "forum_name": candidate["forum_name"],
            "thread_title": live_title or candidate["thread_title"],
            "thread_url": candidate["thread_url"],
            "status": "pending",
            "attempts": 0,
            "last_completed_page": 0,
            "next_page_url": candidate["thread_url"],
            "completed_at": None,
            "last_error": None,
        })
        save_checkpoint_safely(checkpoint)
        LOG.info("Đã chọn %d/%d thread", len(selected), target)

    checkpoint["selection_complete"] = True
    checkpoint.pop("candidate_pool", None)
    checkpoint.pop("rejected_urls", None)
    save_checkpoint_safely(checkpoint)


# ---------------------------------------------------------------------------
# 3. ĐỌC NỘI DUNG MỘT BÀI VIẾT
# ---------------------------------------------------------------------------


def extract_post_id(article_data: str, article_id: str) -> str:
    for value in (article_data, article_id):
        match = re.search(r"(?:post-|js-post-)(\d+)", value)
        if match:
            return match.group(1)
    return ""


async def parse_post(
    article: Locator,
    thread: dict[str, Any],
    page_number: int,
    position: int,
) -> dict[str, Any] | None:
    """Đọc một bài viết từ article XenForo."""
    body = await find_first(article, [
        ".message-body .bbWrapper",
        ".message-content .bbWrapper",
    ])
    if not body:
        return None

    # Không strip hoặc chuẩn hóa hai trường nội dung.
    content_text = await body.inner_text()
    content_html = await body.inner_html()

    article_data = await article.get_attribute("data-content") or ""
    article_id = await article.get_attribute("id") or ""
    post_id = extract_post_id(article_data, article_id)

    author_item = await find_first(article, [
        ".message-name a.username", "a.username[data-user-id]", ".message-name",
    ])
    author = (await author_item.inner_text()).strip() if author_item else ""
    author_href = await author_item.get_attribute("href") if author_item else None
    author_url = normalize_voz_url(author_href, thread["thread_url"]) or ""

    time_item = await find_first(article, ["time.u-dt", "time[datetime]"])
    published_at = ""
    if time_item:
        published_at = (
            await time_item.get_attribute("datetime")
            or await time_item.get_attribute("data-time")
            or (await time_item.inner_text()).strip()
        )

    number_item = await find_first(article, [
        ".message-attribution-opposite a[href*='/post-']",
        ".message-attribution-opposite a[href*='/posts/']",
        "a.message-attribution-main",
    ])
    post_number = (await number_item.inner_text()).strip().lstrip("#") if number_item else ""
    post_href = await number_item.get_attribute("href") if number_item else None
    post_url = normalize_voz_url(post_href, thread["thread_url"])
    if not post_url:
        post_url = f"{thread['thread_url']}#post-{post_id}" if post_id else thread["thread_url"]

    record_id = post_id
    if not record_id:
        value = f"{thread['thread_url']}{page_number}{position}{content_text}"
        record_id = hashlib.sha256(value.encode("utf-8")).hexdigest()

    opening_post = page_number == 1 and position == 1
    return {
        "record_id": record_id,
        "title": thread["thread_title"],
        "comment": content_text,
        "thread_index": thread["thread_index"],
        "forum_name": thread["forum_name"],
        "thread_title": thread["thread_title"],
        "thread_url": thread["thread_url"],
        "post_id": post_id,
        "post_url": post_url,
        "post_type": "opening_post" if opening_post else "comment",
        "post_number": post_number or ("1" if opening_post else ""),
        "author": author,
        "author_url": author_url,
        "published_at": published_at,
        "content_text": content_text,
        "content_html": content_html,
        "page_number": page_number,
        "position_on_page": position,
        "collected_at": now_utc(),
    }


# ---------------------------------------------------------------------------
# 4. CRAWL VÀ RESUME
# ---------------------------------------------------------------------------


async def crawl_thread_pages(
    session: BrowserSession,
    checkpoint: dict[str, Any],
    thread: dict[str, Any],
    known_ids: set[str],
) -> None:
    """Crawl tối đa MAX_PAGES_PER_THREAD trang đầu của một thread."""
    completed_page = thread["last_completed_page"]
    if completed_page >= MAX_PAGES_PER_THREAD:
        return
    url = thread["thread_url"]
    visited: set[str] = set()

    while url:
        if url in visited:
            raise CrawlError(f"Phát hiện lặp trang: {url}")
        visited.add(url)
        await session.open_url(url)

        page_number = completed_page + 1
        posts = session.page.locator("article.message--post, article.message[data-content]")
        if not await posts.count():
            raise CrawlError(f"Không thấy bài viết tại {url}")

        rows: list[dict[str, Any]] = []
        for i in range(await posts.count()):
            row = await parse_post(posts.nth(i), thread, page_number, i + 1)
            if row:
                rows.append(row)
        if not rows:
            raise CrawlError(f"Không đọc được bài viết tại {url}")

        # Chủ ý không đi theo nút Next: mỗi thread chỉ lấy trang đầu tiên.
        following = None

        written = append_page_to_csv(rows, known_ids)
        # CSV đã fsync xong mới cho checkpoint tiến lên.
        completed_page = page_number
        thread["last_completed_page"] = completed_page
        thread["next_page_url"] = following
        thread["last_error"] = None
        save_checkpoint_safely(checkpoint)
        LOG.info("Thread %s, trang %d: ghi %d dòng", thread["thread_index"], page_number, written)
        url = following


async def crawl_selected_threads(
    session: BrowserSession,
    checkpoint: dict[str, Any],
    known_ids: set[str],
) -> None:
    """Crawl tuần tự và retry mỗi thread tối đa ba lần."""
    for thread in checkpoint["selected_threads"]:
        if thread["status"] == "done":
            continue

        while thread["attempts"] < MAX_RETRIES:
            thread["attempts"] += 1
            thread["status"] = "running"
            save_checkpoint_safely(checkpoint)
            try:
                await crawl_thread_pages(session, checkpoint, thread, known_ids)
            except VerificationError:
                raise
            except (CrawlError, PlaywrightError) as exc:
                LOG.error(
                    "Thread %s lỗi lần %d/%d: %s",
                    thread["thread_index"], thread["attempts"], MAX_RETRIES, exc,
                )
                thread["last_error"] = str(exc)
                thread["status"] = (
                    "failed" if thread["attempts"] >= MAX_RETRIES else "pending"
                )
                save_checkpoint_safely(checkpoint)
                continue
            else:
                thread["status"] = "done"
                thread["completed_at"] = now_utc()
                thread["last_error"] = None
                save_checkpoint_safely(checkpoint)
                break


# ---------------------------------------------------------------------------
# 5. MỞ PLAYWRIGHT VÀ CLI
# ---------------------------------------------------------------------------


async def run_crawler(target: int, headful: bool) -> int:
    checkpoint = load_checkpoint(target)
    known_ids = load_record_ids()

    async with async_playwright() as playwright:
        try:
            context = await playwright.chromium.launch_persistent_context(
                user_data_dir=str(BROWSER_PROFILE_DIR),
                channel="chrome",
                headless=not headful,
                locale="vi-VN",
            )
        except PlaywrightError as exc:
            raise CrawlError(
                "Không mở được Google Chrome với profile riêng. Hãy cài Chrome "
                "và đóng crawler khác đang dùng profile runtime."
            ) from exc
        LOG.info("Dùng profile Chrome bền vững: %s", BROWSER_PROFILE_DIR)
        # Challenge tương tác có thể cần đầy đủ resource. Chỉ tối ưu request khi
        # chạy headless, nơi crawler vẫn dừng ngay nếu gặp trang xác minh.
        if not headful:
            await context.route("**/*", block_unneeded_resources)
        page = context.pages[0] if context.pages else await context.new_page()
        session = BrowserSession(page, allow_manual_verification=headful)

        try:
            await select_threads(session, checkpoint, target)
            await crawl_selected_threads(session, checkpoint, known_ids)
        except VerificationError as exc:
            checkpoint["last_error"] = str(exc)
            for thread in checkpoint["selected_threads"]:
                if thread["status"] == "running":
                    thread["status"] = "failed"
                    thread["last_error"] = str(exc)
            save_checkpoint_safely(checkpoint)
            LOG.critical("%s. Dừng an toàn, không bypass.", exc)
            return 2
        finally:
            await context.close()
    return 0


def reset_output_files(yes: bool) -> int:
    if not yes:
        answer = input("Xóa CSV và checkpoint? [y/N]: ").strip().lower()
        if answer not in {"y", "yes"}:
            print("Đã hủy.")
            return 1
    for path in (CSV_FILE, CHECKPOINT_FILE):
        path.unlink(missing_ok=True)
    print("Đã reset dữ liệu.")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Crawl dữ liệu công khai từ VOZ.")
    parser.add_argument("--headful", action="store_true", help="Hiện trình duyệt")
    parser.add_argument("--reset", action="store_true", help="Xóa CSV và checkpoint")
    parser.add_argument("--yes", action="store_true", help="Không hỏi khi reset")
    parser.add_argument("--target", type=int, default=500, help="Số thread, mặc định 500")
    args = parser.parse_args()
    if args.target < 1:
        parser.error("--target phải lớn hơn 0")
    if args.yes and not args.reset:
        parser.error("--yes chỉ dùng cùng --reset")
    return args


def main() -> int:
    # Tránh lỗi chữ tiếng Việt trên Windows dùng code page cũ.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    args = parse_args()
    if args.reset:
        return reset_output_files(args.yes)
    try:
        return asyncio.run(run_crawler(args.target, args.headful))
    except KeyboardInterrupt:
        LOG.warning("Đã dừng bằng Ctrl+C. Chạy lại cùng lệnh để resume.")
        return 130
    except (CrawlError, OSError) as exc:
        LOG.error("Dừng crawler: %s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
