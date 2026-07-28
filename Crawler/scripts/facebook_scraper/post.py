"""Crawl one Facebook Page/Group post into a VOZ-compatible JSON object."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit

from playwright.sync_api import Page, sync_playwright

import group as group_scraper


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = (
    PROJECT_ROOT / "data" / "facebook" / "facebook_single_post" / "data.json"
)
POST_ID_RE = re.compile(r"/(?:permalink|posts)/([A-Za-z0-9]+)", re.IGNORECASE)
GROUP_POST_RE = re.compile(
    r"^/groups/([^/?#]+)/(?:posts|permalink)/([A-Za-z0-9]+)",
    re.IGNORECASE,
)
PAGE_POST_RE = re.compile(
    r"^/([^/?#]+)/posts/([A-Za-z0-9]+)",
    re.IGNORECASE,
)
TIME_VALUE_RE = re.compile(
    r"(?:"
    r"vừa xong|just now|"
    r"\d+\s*(?:giây|phút|giờ|ngày|tuần|tháng|năm|s|m|h|d|w|y)"
    r"(?:\s+trước)?|"
    r"thứ\s+(?:hai|ba|tư|năm|sáu|bảy)|chủ nhật|"
    r"\d{1,2}\s+tháng\s+\d{1,2}|"
    r"lúc\s+\d{1,2}:\d{2}|"
    r"monday|tuesday|wednesday|thursday|friday|saturday|sunday|"
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}"
    r")",
    re.IGNORECASE,
)


def configure_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def normalize_output_path(path: Path) -> Path:
    return path if path.is_absolute() else PROJECT_ROOT / path


def normalize_input_url(value: str) -> str:
    url = value.strip().strip("\"'")
    if not re.match(r"^https?://", url, re.IGNORECASE):
        url = "https://" + url
    parts = urlsplit(url)
    host = parts.netloc.lower().split(":", 1)[0]
    if host not in {"facebook.com", "www.facebook.com", "m.facebook.com"}:
        raise ValueError("URL phải thuộc facebook.com.")
    return urlunsplit(("https", "www.facebook.com", parts.path, parts.query, ""))


def canonical_post_url(url: str | None) -> tuple[str, str] | None:
    if not url:
        return None
    try:
        parts = urlsplit(url)
    except ValueError:
        return None

    group_match = GROUP_POST_RE.match(parts.path)
    if group_match:
        owner, post_id = group_match.groups()
        path = f"/groups/{owner}/posts/{post_id}/"
        canonical = urlunsplit(
            ("https", "www.facebook.com", path, urlencode({"locale": "vi_VN"}), "")
        )
        return "group", canonical

    page_match = PAGE_POST_RE.match(parts.path)
    if page_match and page_match.group(1).lower() != "groups":
        owner, post_id = page_match.groups()
        path = f"/{owner}/posts/{post_id}"
        canonical = urlunsplit(
            ("https", "www.facebook.com", path, urlencode({"locale": "vi_VN"}), "")
        )
        return "page", canonical

    if parts.path.lower() in {"/permalink.php", "/story.php"}:
        query = parse_qs(parts.query)
        post_id = (query.get("story_fbid") or [None])[0]
        owner = (query.get("id") or [None])[0]
        if owner and post_id:
            path = f"/{owner}/posts/{post_id}"
            canonical = urlunsplit(
                (
                    "https",
                    "www.facebook.com",
                    path,
                    urlencode({"locale": "vi_VN"}),
                    "",
                )
            )
            return "page", canonical
    return None


def resolve_post_url(page: Page, supplied_url: str) -> tuple[str, str]:
    direct = canonical_post_url(supplied_url)
    if direct:
        return direct

    page.goto(supplied_url, wait_until="domcontentloaded", timeout=30000)
    page.wait_for_timeout(2500)
    candidates = page.evaluate(
        r"""() => {
            const result = [location.href];
            const canonical = document.querySelector('link[rel="canonical"]')?.href;
            const ogUrl = document.querySelector('meta[property="og:url"]')?.content;
            if (canonical) result.push(canonical);
            if (ogUrl) result.push(ogUrl);
            for (const anchor of document.querySelectorAll(
                'a[href*="/posts/"], a[href*="/permalink/"]'
            )) {
                result.push(anchor.href);
            }
            return [...new Set(result)];
        }"""
    )
    for candidate in candidates:
        resolved = canonical_post_url(candidate)
        if resolved:
            return resolved
    raise ValueError(
        "Không tìm thấy permalink bài viết. Hãy dùng URL dạng /posts/... "
        "hoặc /groups/.../posts/...."
    )


def fill_original_metadata(page: Page, thread: dict, post_url: str) -> None:
    if not thread.get("comments"):
        return
    original = thread["comments"][0]
    if original.get("username") and original.get("datetime"):
        return

    post_id = group_scraper.source_post_id(post_url)
    metadata = page.evaluate(
        r"""({ postId }) => {
            const target = href => href.includes(`/posts/${postId}`)
                || href.includes(`/permalink/${postId}`);
            const messages = [...document.querySelectorAll(
                '[data-ad-comet-preview="message"]'
            )];
            const message = messages.find(node => {
                let scope = node;
                for (let i = 0; i < 18 && scope; i++, scope = scope.parentElement) {
                    if ([...scope.querySelectorAll('a[href]')]
                        .some(a => target(a.href || ''))) return true;
                }
                return false;
            }) || messages[0];
            if (!message) return {};

            let scope = message.closest('[role="article"]') || message.parentElement;
            for (let node = message, i = 0; i < 18 && node; i++, node = node.parentElement) {
                if ([...node.querySelectorAll('a[href]')]
                    .some(a => target(a.href || ''))) {
                    scope = node.parentElement || node;
                    break;
                }
            }

            const author = [...scope.querySelectorAll(
                'h1 a, h2 a, h3 a, h4 a, strong a, a[role="link"]'
            )].find(a => {
                const text = (a.innerText || '').trim();
                const href = a.href || '';
                return text && !target(href) && !href.includes('comment_id=');
            });
            let candidateNumber = 0;
            for (const link of document.querySelectorAll('a[href]')) {
                const href = link.href || '';
                if (!target(href) || href.includes('comment_id=')) continue;
                if (!link.getClientRects().length) continue;
                link.setAttribute(
                    'data-facebook-post-time-candidate',
                    String(candidateNumber++)
                );
            }
            return {
                username: (author?.innerText || '').trim() || null,
                candidateCount: candidateNumber
            };
        }""",
        {"postId": post_id},
    )

    tooltip_datetime = None
    for index in range(metadata.get("candidateCount", 0)):
        candidate = page.locator(
            f'[data-facebook-post-time-candidate="{index}"]'
        )
        try:
            if not candidate.is_visible():
                continue
            candidate.hover(timeout=3000)
            page.wait_for_timeout(1000)
            tooltips = page.locator('[role="tooltip"]')
            for tooltip_index in range(tooltips.count()):
                tooltip = tooltips.nth(tooltip_index)
                if not tooltip.is_visible():
                    continue
                value = group_scraper.clean_text(tooltip.inner_text())
                if valid_datetime(value):
                    tooltip_datetime = value
                    break
        except Exception:
            continue
        if tooltip_datetime:
            break

    if not original.get("username"):
        original["username"] = metadata.get("username")
    if not original.get("datetime"):
        original["datetime"] = tooltip_datetime


def valid_datetime(value: object) -> bool:
    return isinstance(value, str) and bool(TIME_VALUE_RE.search(value.strip()))


def validate_thread(thread: dict, allow_missing_metadata: bool) -> None:
    if allow_missing_metadata:
        return
    missing = [
        row.get("post_number")
        for row in thread.get("comments", [])
        if not row.get("username") or not valid_datetime(row.get("datetime"))
    ]
    if missing:
        raise RuntimeError(
            f"{len(missing)} post/comment thiếu username hoặc datetime; "
            "không ghi output. Có thể chạy lại hoặc dùng --allow-missing-metadata."
        )


def load_posts(path: Path) -> tuple[list[dict], bool]:
    if not path.exists():
        return [], False

    try:
        with path.open("r", encoding="utf-8") as file:
            payload = json.load(file)
    except (json.JSONDecodeError, OSError) as error:
        backup_path = path.with_suffix(path.suffix + ".bak")
        if not backup_path.exists():
            raise RuntimeError(f"Không đọc được {path}: {error}") from error
        print(f"⚠️ File chính lỗi, khôi phục từ {backup_path}")
        with backup_path.open("r", encoding="utf-8") as file:
            payload = json.load(file)

    if isinstance(payload, dict) and isinstance(payload.get("comments"), list):
        print("Chuyển output một object cũ thành JSON array để ghi nối tiếp.")
        return [payload], True
    if isinstance(payload, list) and all(isinstance(item, dict) for item in payload):
        return payload, False
    raise RuntimeError(
        "Schema output không hợp lệ: cần một post object hoặc JSON array các post."
    )


def post_identity(url: str | None) -> str | None:
    canonical = canonical_post_url(url)
    return canonical[1] if canonical else None


def find_existing_post(posts: list[dict], post_url: str) -> dict | None:
    identity = post_identity(post_url)
    if not identity:
        return None
    return next(
        (
            post
            for post in posts
            if post_identity(post.get("url")) == identity
        ),
        None,
    )


def save_json_safely(path: Path, payload: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    backup_path = path.with_suffix(path.suffix + ".bak")

    with temp_path.open("w", encoding="utf-8") as file:
        json.dump(payload, file, ensure_ascii=False, indent=2)
        file.flush()
        os.fsync(file.fileno())
    with temp_path.open("r", encoding="utf-8") as file:
        json.load(file)

    if path.exists() and path.stat().st_size > 0:
        shutil.copy2(path, backup_path)
    os.replace(temp_path, path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Crawl một bài Facebook Page/Group rồi ghi nối tiếp vào "
            "JSON array theo schema VOZ."
        )
    )
    parser.add_argument("post_url", help="Permalink của một bài Facebook.")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--cdp-url", default="http://localhost:9222")
    parser.add_argument("--max-expand-clicks", type=int, default=40)
    parser.add_argument(
        "--allow-missing-metadata",
        action="store_true",
        help="Cho phép ghi dù Facebook không cung cấp username/datetime.",
    )
    return parser.parse_args()


def main() -> int:
    configure_console()
    args = parse_args()
    supplied_url = normalize_input_url(args.post_url)
    output = normalize_output_path(args.output)
    posts, needs_migration = load_posts(output)
    group_scraper.POST_LINK_RE = POST_ID_RE

    with sync_playwright() as playwright:
        browser = playwright.chromium.connect_over_cdp(args.cdp_url, timeout=15000)
        if not browser.contexts:
            raise RuntimeError("Chrome không có browser context.")
        context = browser.contexts[0]
        detail_page = context.new_page()
        try:
            source_type, post_url = resolve_post_url(detail_page, supplied_url)
            print(f"Loại nguồn: Facebook {source_type.title()}")
            print(f"Permalink: {post_url}")
            if find_existing_post(posts, post_url):
                if needs_migration:
                    save_json_safely(output, posts)
                print(f"Đã tồn tại trong output, bỏ qua: {post_url}")
                print(f"Output: {output}")
                print(f"HOÀN THÀNH: tổng {len(posts)} bài, không thêm trùng.")
                return 0
            thread = group_scraper.crawl_thread(
                detail_page,
                post_url,
                args.max_expand_clicks,
            )
            fill_original_metadata(detail_page, thread, post_url)
            validate_thread(thread, args.allow_missing_metadata)
        finally:
            detail_page.close()

    posts.append(thread)
    save_json_safely(output, posts)
    print(f"Output: {output}")
    print(
        f"Đã thêm 1 bài với "
        f"{max(len(thread.get('comments', [])) - 1, 0)} comment. "
        f"Tổng trong file: {len(posts)} bài."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
