"""Crawl Facebook Group posts and comments into a VOZ-compatible JSON schema."""

from __future__ import annotations

import argparse
import hashlib
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

from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError, sync_playwright


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_GROUP_URL = (
    "https://www.facebook.com/groups/552267055810389/?sorting_setting=CHRONOLOGICAL&locale=vi_VN"
    
)

#142
#  https://www.facebook.com/groups/mixigaming
# https://www.facebook.com/groups/TruongNguoiTa
#  https://www.facebook.com/groups/1228169470849996
#  https://www.facebook.com/groups/thongtinchinhphu
#    https://www.facebook.com/groups/868210502423655
#    https://www.facebook.com/groups/congdongsinhvien.hcmuit
#    "https://www.facebook.com/groups/987761062274391/"

DEFAULT_OUTPUT = PROJECT_ROOT / "data" / "facebook" / "facebook_group" / "data.json"

POST_LINK_RE = re.compile(r"/(?:permalink|posts)/(\d+)")
UI_TEXT_RE = re.compile(r"(?:\s*(?:Ẩn bớt|Xem thêm|See more))+\s*$", re.IGNORECASE)
THREADS_KEY = "facebook_threads"
TOTAL_THREADS_KEY = "facebook_total_threads"
TOTAL_COMMENTS_KEY = "facebook_total_comments"


def configure_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def empty_dataset() -> dict:
    return {
        TOTAL_THREADS_KEY: 0,
        TOTAL_COMMENTS_KEY: 0,
        THREADS_KEY: [],
    }


def normalize_output_path(path: Path) -> Path:
    return path if path.is_absolute() else PROJECT_ROOT / path


def recalculate_totals(dataset: dict) -> None:
    threads = dataset.get(THREADS_KEY, [])
    for thread in threads:
        thread["total_comments"] = len(thread.get("comments", []))
    dataset[TOTAL_THREADS_KEY] = len(threads)
    dataset[TOTAL_COMMENTS_KEY] = sum(
        thread["total_comments"] for thread in threads
    )


def save_dataset_safely(path: Path, dataset: dict) -> None:
    recalculate_totals(dataset)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    backup_path = path.with_suffix(path.suffix + ".bak")

    with temp_path.open("w", encoding="utf-8") as f:
        json.dump(dataset, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())

    # Parse lại file tạm trước khi thay thế file chính.
    with temp_path.open("r", encoding="utf-8") as f:
        json.load(f)

    if path.exists() and path.stat().st_size > 0:
        shutil.copy2(path, backup_path)
    os.replace(temp_path, path)


def backup_for_reset(path: Path) -> Path | None:
    if not path.exists() or path.stat().st_size == 0:
        return None
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = path.with_name(f"{path.stem}.before-reset-{stamp}{path.suffix}")
    shutil.copy2(path, backup)
    return backup


def load_dataset(path: Path, reset: bool) -> dict:
    if reset:
        backup = backup_for_reset(path)
        if backup:
            print(f"Đã sao lưu dataset cũ: {backup}")
        return empty_dataset()

    if not path.exists():
        return empty_dataset()

    try:
        with path.open("r", encoding="utf-8") as f:
            dataset = json.load(f)
    except (json.JSONDecodeError, OSError) as error:
        backup = path.with_suffix(path.suffix + ".bak")
        if not backup.exists():
            raise RuntimeError(f"Không đọc được {path}: {error}") from error
        print(f"⚠️ File chính lỗi, khôi phục từ {backup}")
        with backup.open("r", encoding="utf-8") as f:
            dataset = json.load(f)

    if isinstance(dataset, list):
        raise RuntimeError(
            "data.json đang dùng schema Facebook cũ dạng array. "
            "Chạy lại với --reset; script sẽ sao lưu file cũ trước khi crawl."
        )
    if isinstance(dataset, dict) and isinstance(dataset.get("threads"), list):
        print("Chuyển schema cũ sang các trường có tiền tố facebook_.")
        dataset = {
            TOTAL_THREADS_KEY: dataset.get("total_threads", 0),
            TOTAL_COMMENTS_KEY: dataset.get("total_comments", 0),
            THREADS_KEY: dataset["threads"],
        }

    if (
        not isinstance(dataset, dict)
        or not isinstance(dataset.get(THREADS_KEY), list)
    ):
        raise RuntimeError(
            f"Schema JSON không hợp lệ: cần object có trường {THREADS_KEY}."
        )

    dataset.setdefault(TOTAL_THREADS_KEY, len(dataset[THREADS_KEY]))
    dataset.setdefault(TOTAL_COMMENTS_KEY, 0)
    recalculate_totals(dataset)
    return dataset


def clean_text(value: str | None) -> str:
    if not value:
        return ""
    lines: list[str] = []
    for raw_line in value.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = re.sub(r"[ \t\u00a0]+", " ", raw_line).strip()
        line = UI_TEXT_RE.sub("", line).strip()
        if line and (not lines or line != lines[-1]):
            lines.append(line)
    return "\n".join(lines).strip()


def derive_title(text: str, max_length: int = 180) -> str:
    first_line = next((line.strip() for line in text.splitlines() if line.strip()), "")
    if len(first_line) <= max_length:
        return first_line

    sentence = re.split(r"(?<=[.!?])\s+", first_line, maxsplit=1)[0].strip()
    if sentence and len(sentence) <= max_length:
        return sentence
    return first_line[: max_length - 1].rstrip() + "…"


def canonical_post_url(url: str | None) -> str | None:
    if not url:
        return None
    post_match = POST_LINK_RE.search(url)
    group_match = re.search(r"/groups/(\d+)", url)
    if not post_match or not group_match:
        return None

    clean_query = urlencode({"locale": "vi_VN"})
    clean_path = f"/groups/{group_match.group(1)}/posts/{post_match.group(1)}/"
    return urlunsplit(("https", "www.facebook.com", clean_path, clean_query, ""))


def source_post_id(url: str) -> str:
    match = POST_LINK_RE.search(url)
    if not match:
        raise ValueError(f"Không tìm thấy post ID trong URL: {url}")
    return match.group(1)


def fallback_comment_id(post_url: str, username: str | None, dt: str | None, text: str) -> str:
    raw = "\0".join((post_url, username or "", dt or "", text))
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:20]
    return f"facebook-comment-{digest}"


def expand_post_text(page: Page) -> None:
    try:
        page.evaluate(
            """() => {
                const buttons = document.querySelectorAll(
                    'div[role="button"], span[role="button"], button'
                );
                for (const button of buttons) {
                    const text = (button.innerText || '').trim();
                    if (text === 'Xem thêm' || text === 'See more') button.click();
                }
            }"""
        )
    except Exception:
        pass


def select_all_comments(page: Page) -> None:
    """Best effort: chuyển từ Phù hợp nhất sang Tất cả bình luận."""
    try:
        opened = page.evaluate(
            """() => {
                const controls = document.querySelectorAll(
                    'div[role="button"], span[role="button"], button'
                );
                for (const control of controls) {
                    const text = (control.innerText || '').trim();
                    if (text === 'Phù hợp nhất' || text === 'Most relevant') {
                        control.click();
                        return true;
                    }
                }
                return false;
            }"""
        )
        if not opened:
            return
        page.wait_for_timeout(500)
        page.evaluate(
            """() => {
                const controls = document.querySelectorAll(
                    '[role="menuitem"], div[role="button"], span[role="button"]'
                );
                for (const control of controls) {
                    const text = (control.innerText || '').trim();
                    if (text === 'Tất cả bình luận' || text === 'All comments') {
                        control.click();
                        return true;
                    }
                }
                return false;
            }"""
        )
        page.wait_for_timeout(700)
    except Exception:
        pass


def expand_all_comments(page: Page, max_clicks: int) -> int:
    clicks = 0
    for _ in range(max_clicks):
        try:
            clicked = page.evaluate(
                """() => {
                    const controls = document.querySelectorAll(
                        'div[role="button"], span[role="button"], button'
                    );
                    for (const control of controls) {
                        const text = (control.innerText || '').trim();
                        const isMore = /(xem thêm|view more|previous|more)/i.test(text);
                        const isComment = /(bình luận|comment|câu trả lời|repl)/i.test(text);
                        if (isMore && isComment) {
                            control.click();
                            return true;
                        }
                    }
                    return false;
                }"""
            )
            if not clicked:
                break
            clicks += 1
            page.wait_for_timeout(500)
        except Exception:
            break
    return clicks


def reveal_comment_section(page: Page, post_url: str) -> None:
    """Đưa vùng comment vào viewport để Facebook kích hoạt lazy-load."""
    post_id = source_post_id(post_url)
    try:
        page.evaluate(
            """({ postId }) => {
                const targetDialog = [...document.querySelectorAll('[role="dialog"]')]
                    .find(dialog => [...dialog.querySelectorAll('a[href]')].some(a => {
                        const href = a.href || '';
                        return href.includes(`/permalink/${postId}`)
                            || href.includes(`/posts/${postId}`);
                    }));
                const scope = targetDialog || document;
                const commentBox = [...scope.querySelectorAll('[aria-label]')]
                    .find(el => /^(viết bình luận|write a comment)/i.test(
                        (el.getAttribute('aria-label') || '').trim()
                    ));

                if (commentBox) {
                    commentBox.scrollIntoView({ block: 'center' });
                    let scrollable = commentBox.parentElement;
                    for (let depth = 0; scrollable && depth < 15; depth++) {
                        if (scrollable.scrollHeight > scrollable.clientHeight + 50) {
                            scrollable.scrollTop = scrollable.scrollHeight;
                            break;
                        }
                        scrollable = scrollable.parentElement;
                    }
                } else if (targetDialog) {
                    targetDialog.scrollTop = targetDialog.scrollHeight;
                } else {
                    window.scrollTo(0, document.body.scrollHeight);
                }
            }""",
            {"postId": post_id},
        )
        page.wait_for_timeout(1800)
    except Exception:
        pass


def resolve_group_id(page: Page, group_url: str) -> str:
    numeric_match = re.search(r"/groups/(\d+)", group_url)
    if numeric_match:
        return numeric_match.group(1)

    group_id = page.evaluate(
        """() => {
            const links = [...document.querySelectorAll('a[href*="/groups/"]')];
            const preferred = links.find(a =>
                /\\/groups\\/\\d+\\/(?:members|about)\\/?/.test(a.href || '')
            );
            const fallback = links.find(a =>
                /\\/groups\\/\\d+\\/user\\//.test(a.href || '')
            );
            const match = (preferred?.href || fallback?.href || '')
                .match(/\\/groups\\/(\\d+)/);
            return match ? match[1] : null;
        }"""
    )
    if not group_id:
        raise RuntimeError(
            "Không xác định được ID số của group từ URL dạng chữ."
        )
    return str(group_id)


def wait_for_extractable_posts(page: Page, timeout_ms: int = 15000) -> dict:
    """Chờ feed có ít nhất một dấu hiệu đủ để suy ra post ID."""
    deadline = time.monotonic() + timeout_ms / 1000
    last_status = {
        "messages": 0,
        "direct_links": 0,
        "media_ids": 0,
    }
    while time.monotonic() < deadline:
        try:
            last_status = page.evaluate(
                """() => {
                    const main = document.querySelector('[role="main"]')
                        || document;
                    const links = [...main.querySelectorAll('a[href]')];
                    const directLinks = links.filter(a =>
                        /\\/groups\\/[^/]+\\/(?:posts|permalink)\\/\\d+/.test(
                            a.href || ''
                        )
                    ).length;
                    const mediaIds = links.filter(a =>
                        /[?&]set=pcb\\.\\d+/.test(a.href || '')
                        || /[?&](?:story_fbid|multi_permalinks)=\\d+/.test(
                            a.href || ''
                        )
                        || /\\/videos\\/\\d+/.test(a.href || '')
                    ).length;
                    return {
                        messages: main.querySelectorAll(
                            '[data-ad-comet-preview="message"]'
                        ).length,
                        direct_links: directLinks,
                        media_ids: mediaIds
                    };
                }"""
            )
            if last_status["direct_links"] or last_status["media_ids"]:
                return last_status
        except Exception:
            pass
        page.wait_for_timeout(750)
    return last_status


def extract_visible_post_urls(page: Page, group_id: str) -> list[str]:
    expand_post_text(page)
    raw_urls = page.evaluate(
        """({ groupId }) => {
            const byUrl = new Map();
            const messages = [...document.querySelectorAll(
                '[data-ad-comet-preview="message"]'
            )];

            for (const message of messages) {
                let el = message;
                let found = null;
                for (let depth = 0; depth < 25; depth++) {
                    el = el.parentElement;
                    if (!el) break;
                    const link = [...el.querySelectorAll('a[href]')].find(a => {
                        const href = a.href || '';
                        return href.includes('/permalink/') || href.includes('/posts/');
                    });
                    if (link) found = link.href;

                    const share = [...el.querySelectorAll(
                        'div[role="button"], span[role="button"], button'
                    )].some(button => /(chia sẻ|share)/i.test(
                        `${button.innerText || ''} ${button.getAttribute('aria-label') || ''}`
                    ));
                    if (found && share) break;
                }
                // Facebook có thể render bài được share trước bài gốc; bản ghi
                // xuất hiện sau cùng cho cùng URL thường là message của bài gốc.
                if (found) byUrl.set(found, found);
            }

            // Fallback cho giao diện Facebook mới: đôi khi message được render
            // chậm hoặc không có data-ad-comet-preview, nhưng permalink đã có.
            if (groupId) {
                const groupPath = `/groups/${groupId}/`;
                const main = document.querySelector('[role="main"]') || document;
                for (const anchor of main.querySelectorAll('a[href]')) {
                    const href = anchor.href || '';
                    if (
                        href.includes(groupPath)
                        && (href.includes('/posts/') || href.includes('/permalink/'))
                    ) {
                        byUrl.set(href, href);
                    }

                    // Facebook UI mới không luôn đặt permalink trên feed.
                    // Với bài có media, post ID vẫn có trong set=pcb.<id>.
                    let postId = null;
                    try {
                        const url = new URL(href, location.href);
                        const mediaSet = url.searchParams.get('set') || '';
                        const pcbMatch = mediaSet.match(/^pcb\\.(\\d+)$/);
                        postId = pcbMatch?.[1]
                            || url.searchParams.get('story_fbid')
                            || url.searchParams.get('multi_permalinks');
                        if (!postId) {
                            const videoMatch = url.pathname.match(
                                /\\/videos\\/(\\d+)/
                            );
                            postId = videoMatch?.[1] || null;
                        }
                    } catch (_) {}

                    if (postId && /^\\d+$/.test(postId)) {
                        const postUrl = `https://www.facebook.com/groups/${
                            groupId
                        }/posts/${postId}/?locale=vi_VN`;
                        byUrl.set(postUrl, postUrl);
                    }
                }
            }
            return [...byUrl.values()];
        }""",
        {"groupId": group_id},
    )

    result: list[str] = []
    seen: set[str] = set()
    for raw_url in raw_urls:
        if f"/groups/{group_id}/" not in raw_url:
            continue
        url = canonical_post_url(raw_url)
        if url and url not in seen:
            seen.add(url)
            result.append(url)
    return result


def extract_original_post(page: Page, post_url: str) -> dict:
    post_id = source_post_id(post_url)
    raw = page.evaluate(
        r"""({ postId }) => {
            const isTargetLink = a => {
                const href = a.href || '';
                return href.includes(`/permalink/${postId}`)
                    || href.includes(`/posts/${postId}`);
            };
            const targetDialog = [...document.querySelectorAll('[role="dialog"]')]
                .find(dialog => [...dialog.querySelectorAll('a[href]')]
                    .some(isTargetLink));
            const pageScope = targetDialog || document;
            const messages = [...pageScope.querySelectorAll(
                '[data-ad-comet-preview="message"]'
            )];
            const candidates = [];

            messages.forEach((message, index) => {
                let el = message;
                let scope = message.closest('[role="article"]')
                    || message.parentElement
                    || message;
                let foundTarget = false;
                for (let depth = 0; depth < 20; depth++) {
                    el = el.parentElement;
                    if (!el) break;
                    const hasTarget = [...el.querySelectorAll('a[href]')]
                        .some(isTargetLink);
                    if (hasTarget) {
                        // Metadata (tác giả/thời gian) thường là sibling của
                        // message nên nằm cao hơn link permalink đúng một cấp.
                        scope = el.parentElement || el;
                        foundTarget = true;
                        break;
                    }
                }

                const groupAuthor = [...scope.querySelectorAll('a[href]')]
                    .find(a => /\/groups\/\d+\/user\//.test(a.href || '')
                        && (a.innerText || '').trim());
                const headingAuthor = [...scope.querySelectorAll(
                    'h1 a, h2 a, h3 a, h4 a, strong a'
                )]
                    .find(a => (a.innerText || '').trim());
                const author = groupAuthor || headingAuthor;

                const links = [...scope.querySelectorAll('a[href]')];
                const looksLikeAbsoluteTime = value => /(?:thứ\s+(?:hai|ba|tư|năm|sáu|bảy)|chủ nhật|\d{1,2}\s+tháng\s+\d{1,2}|lúc\s+\d{1,2}:\d{2}|monday|tuesday|wednesday|thursday|friday|saturday|sunday|january|february|march|april|may|june|july|august|september|october|november|december|\d{1,2}:\d{2}\s*(?:am|pm))/i.test(
                    value || ''
                );
                const absoluteTimeLink = links.find(a => {
                        const href = a.href || '';
                        const target = href.includes(`/permalink/${postId}`)
                            || href.includes(`/posts/${postId}`);
                        return target && !href.includes('comment_id=')
                            && looksLikeAbsoluteTime(
                                `${
                                    a.getAttribute('aria-label') || ''
                                } ${
                                    a.getAttribute('title') || ''
                                } ${
                                    a.innerText || ''
                                }`
                            );
                    }) || links.find(a => {
                        const href = a.href || '';
                        return !href.includes('comment_id=')
                            && looksLikeAbsoluteTime(
                                `${
                                    a.getAttribute('aria-label') || ''
                                } ${
                                    a.getAttribute('title') || ''
                                } ${
                                    a.innerText || ''
                                }`
                            );
                    });

                candidates.push({
                    index,
                    text: message.innerText || '',
                    username: author ? (author.innerText || '').trim() : null,
                    datetime: absoluteTimeLink
                        ? (
                            absoluteTimeLink.getAttribute('aria-label')
                            || absoluteTimeLink.getAttribute('title')
                        )
                        : null,
                    score: (foundTarget ? 1000 : 0)
                        + (groupAuthor ? 100 : 0)
                        + (absoluteTimeLink ? 10 : 0)
                });
            });

            // Fallback cho giao diện Facebook mới, không còn
            // data-ad-comet-preview="message" trên permalink.
            if (!candidates.length) {
                const commentArticles = [...pageScope.querySelectorAll(
                    'div[role="article"][aria-label]'
                )].filter(el => /^(bình luận dưới tên|comment by)/i.test(
                    (el.getAttribute('aria-label') || '').trim()
                ));
                const textCandidates = [...pageScope.querySelectorAll('[dir="auto"]')]
                    .filter(el => {
                        const text = (el.innerText || '').trim();
                        return text.length >= 20
                            && el.getClientRects().length > 0
                            && !/^(bài viết của|post by)\s+/i.test(text)
                            && !/^H[1-6]$/.test(el.tagName)
                            && !el.closest('a, button, [role="button"]')
                            && !commentArticles.some(comment => comment.contains(el));
                    })
                    .map((el, index) => ({
                        index,
                        text: el.innerText || ''
                    }))
                    .sort((a, b) => b.text.length - a.text.length);

                const content = textCandidates[0];
                if (content) {
                    const links = [...pageScope.querySelectorAll('a[href]')];
                    const groupAuthor = links.find(a =>
                        /\/groups\/\d+\/user\//.test(a.href || '')
                        && (a.innerText || '').trim()
                    );
                    const heading = [...pageScope.querySelectorAll('h1, h2, h3')]
                        .map(el => (el.innerText || '').trim())
                        .find(text => /^(bài viết của|post by)\s+/i.test(text));
                    const headingAuthor = heading
                        ? heading.replace(/^(bài viết của|post by)\s+/i, '').trim()
                        : null;

                    const looksLikeAbsoluteTime = value => /(?:thứ\s+(?:hai|ba|tư|năm|sáu|bảy)|chủ nhật|\d{1,2}\s+tháng\s+\d{1,2}|lúc\s+\d{1,2}:\d{2}|monday|tuesday|wednesday|thursday|friday|saturday|sunday|january|february|march|april|may|june|july|august|september|october|november|december|\d{1,2}:\d{2}\s*(?:am|pm))/i.test(
                        value || ''
                    );
                    const absoluteTimeLink = links.find(a =>
                        isTargetLink(a)
                        && looksLikeAbsoluteTime(
                            `${
                                a.getAttribute('aria-label') || ''
                            } ${
                                a.getAttribute('title') || ''
                            } ${
                                a.innerText || ''
                            }`
                        )
                    );

                    candidates.push({
                        index: content.index,
                        text: content.text,
                        username: groupAuthor
                            ? (groupAuthor.innerText || '').trim()
                            : headingAuthor,
                        datetime: absoluteTimeLink
                            ? (
                                absoluteTimeLink.getAttribute('aria-label')
                                || absoluteTimeLink.getAttribute('title')
                            )
                            : null,
                        score: 2000
                    });
                }
            }

            candidates.sort((a, b) => a.score - b.score || a.index - b.index);
            return candidates[candidates.length - 1] || null;
        }""",
        {"postId": post_id},
    )
    if not raw:
        raise RuntimeError("Không tìm thấy nội dung bài gốc trên permalink.")

    text = clean_text(raw.get("text"))
    if not text:
        raise RuntimeError("Nội dung bài gốc rỗng.")
    return {
        "post_id": f"post-{post_id}",
        "username": clean_text(raw.get("username")) or None,
        "datetime": clean_text(raw.get("datetime")) or None,
        "comment": text,
    }


def extract_comment_rows(page: Page, post_url: str) -> list[dict]:
    post_id = source_post_id(post_url)
    rows = page.evaluate(
        r"""({ postId }) => {
             const targetDialog = [...document.querySelectorAll('[role="dialog"]')]
                .find(dialog => [...dialog.querySelectorAll('a[href]')].some(a => {
                    const href = a.href || '';
                    return href.includes(`/permalink/${postId}`)
                        || href.includes(`/posts/${postId}`);
                }));
             const pageScope = targetDialog || document;
             const articles = [...pageScope.querySelectorAll(
                 'div[role="article"][aria-label]'
             )].filter(el => /^(bình luận dưới tên|comment by)/i.test(
                 (el.getAttribute('aria-label') || '').trim()
             ) && el.getClientRects().length > 0);
            const timePattern = /^(vừa xong|just now|\d+\s*(giây|phút|giờ|ngày|tuần|tháng|năm|s|m|h|d|w|y))$/i;

            return articles.map(el => {
                const aria = (el.getAttribute('aria-label') || '').trim();
                const authorNode = [...el.querySelectorAll('[dir="auto"]')]
                    .find(node => node.closest('a') && !timePattern.test(
                        (node.innerText || '').trim()
                    ));
                const timeLink = [...el.querySelectorAll('a[href*="comment_id="]')][0]
                    || [...el.querySelectorAll('a[href]')].find(a => timePattern.test(
                        (a.innerText || '').trim()
                    ));

                let commentId = null;
                if (timeLink) {
                    try {
                        commentId = new URL(timeLink.href, location.href)
                            .searchParams.get('comment_id');
                    } catch (_) {}
                }

                const viMatch = aria.match(/^Bình luận dưới tên (.+?) vào (.+)$/i);
                const enMatch = aria.match(/^Comment by (.+?)(?: at| about| on) (.+)$/i);
                const ariaMatch = viMatch || enMatch;
                const username = (authorNode?.innerText || ariaMatch?.[1] || '').trim();
                const datetime = (
                    timeLink?.getAttribute('aria-label')
                    || timeLink?.getAttribute('title')
                    || ariaMatch?.[2]
                    || timeLink?.innerText
                    || ''
                ).trim();

                const candidates = [...el.querySelectorAll('[dir="auto"]')]
                    .filter(node => !node.closest('a, button, [role="button"]'))
                    .map(node => (node.innerText || '').replace(/\s+/g, ' ').trim())
                    .filter(Boolean);
                const comment = [...new Set(candidates)]
                    .sort((a, b) => b.length - a.length)[0] || '';

                return { comment_id: commentId, username, datetime, comment };
            }).filter(row => row.comment);
        }""",
        {"postId": post_id},
    )

    result: list[dict] = []
    seen_ids: set[str] = set()
    for row in rows:
        text = clean_text(row.get("comment"))
        if not text:
            continue
        comment_id = row.get("comment_id") or fallback_comment_id(
            post_url,
            row.get("username"),
            row.get("datetime"),
            text,
        )
        comment_id = str(comment_id)
        if comment_id in seen_ids:
            continue
        seen_ids.add(comment_id)
        result.append(
            {
                "post_id": f"post-{comment_id}",
                "username": clean_text(row.get("username")) or None,
                "datetime": clean_text(row.get("datetime")) or None,
                "comment": text,
            }
        )
    return result


def crawl_thread(page: Page, post_url: str, max_expand_clicks: int) -> dict:
    page.goto(post_url, wait_until="domcontentloaded", timeout=30000)
    page.wait_for_timeout(1800)
    expand_post_text(page)
    reveal_comment_section(page, post_url)
    select_all_comments(page)
    expand_clicks = expand_all_comments(page, max_expand_clicks)
    reveal_comment_section(page, post_url)

    original = extract_original_post(page, post_url)
    replies = extract_comment_rows(page, post_url)
    all_rows = [original, *replies]

    comments = []
    for number, row in enumerate(all_rows, start=1):
        comments.append(
            {
                "post_id": row["post_id"],
                "post_number": number,
                "username": row["username"],
                "datetime": row["datetime"],
                "comment": row["comment"],
                "page": 1,
            }
        )

    print(
        f"      ↳ {len(replies)} reply, "
        f"{expand_clicks} lần mở thêm, post_id={source_post_id(post_url)}"
    )
    return {
        "url": post_url,
        "title": derive_title(original["comment"]),
        "total_comments": len(comments),
        "comments": comments,
    }


def scroll_feed(page: Page) -> None:
    for attempt in range(3):
        try:
            page.evaluate("window.scrollBy(0, window.innerHeight * 2.2)")
            page.wait_for_timeout(random.randint(1800, 3000))
            return
        except Exception:
            if attempt == 2:
                raise
            page.wait_for_timeout(1000)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Crawl Facebook Group theo schema của voz_comments.json."
    )
    parser.add_argument("--group-url", default=DEFAULT_GROUP_URL)
    parser.add_argument("--target", type=int, default=50, help="Tổng số thread cần có.")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--cdp-url", default="http://localhost:9222")
    parser.add_argument("--max-expand-clicks", type=int, default=40)
    parser.add_argument("--max-idle-rounds", type=int, default=15)
    parser.add_argument("--delay-min", type=float, default=1.0)
    parser.add_argument("--delay-max", type=float, default=2.0)
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Sao lưu output cũ rồi crawl lại từ đầu theo schema mới.",
    )
    return parser.parse_args()


def main() -> int:
    configure_console()
    args = parse_args()
    if args.target <= 0:
        raise SystemExit("--target phải lớn hơn 0.")
    if args.delay_min < 0 or args.delay_max < args.delay_min:
        raise SystemExit("Khoảng delay không hợp lệ.")

    output = normalize_output_path(args.output)
    dataset = load_dataset(output, args.reset)
    known_urls = {
        canonical_post_url(thread.get("url"))
        for thread in dataset[THREADS_KEY]
        if canonical_post_url(thread.get("url"))
    }

    print(f"Output: {output}")
    print(f"Tiến độ ban đầu: {len(dataset[THREADS_KEY])}/{args.target} thread")

    with sync_playwright() as playwright:
        browser = playwright.chromium.connect_over_cdp(args.cdp_url, timeout=15000)
        if not browser.contexts:
            raise RuntimeError("Chrome không có browser context.")
        context = browser.contexts[0]
        feed_page = context.new_page()
        detail_page = context.new_page()

        try:
            feed_page.goto(args.group_url, wait_until="domcontentloaded", timeout=30000)
            feed_page.wait_for_timeout(4000)
            group_id = resolve_group_id(feed_page, args.group_url)
            print(f"Facebook group ID: {group_id}")
            feed_status = wait_for_extractable_posts(feed_page)
            print(
                "Feed DOM: "
                f"{feed_status['messages']} message, "
                f"{feed_status['direct_links']} permalink, "
                f"{feed_status['media_ids']} media post ID"
            )
            idle_rounds = 0

            while len(dataset[THREADS_KEY]) < args.target:
                urls = extract_visible_post_urls(feed_page, group_id)
                new_this_round = 0
                print(f"Tìm thấy {len(urls)} permalink trong vùng đang hiển thị.")

                for post_url in urls:
                    if post_url in known_urls:
                        continue
                    try:
                        thread = crawl_thread(
                            detail_page,
                            post_url,
                            args.max_expand_clicks,
                        )
                    except (PlaywrightTimeoutError, RuntimeError, ValueError) as error:
                        print(f"⚠️ Bỏ qua {post_url}: {error}")
                        continue
                    except Exception as error:
                        print(f"⚠️ Lỗi không xác định tại {post_url}: {error}")
                        continue

                    dataset[THREADS_KEY].append(thread)
                    known_urls.add(post_url)
                    new_this_round += 1
                    save_dataset_safely(output, dataset)
                    print(
                        f"[{len(dataset[THREADS_KEY])}/{args.target}] "
                        f"{thread['title'][:100]}"
                    )

                    if len(dataset[THREADS_KEY]) >= args.target:
                        break
                    time.sleep(random.uniform(args.delay_min, args.delay_max))

                if len(dataset[THREADS_KEY]) >= args.target:
                    break

                if new_this_round == 0:
                    idle_rounds += 1
                    print(f"Không có thread mới ({idle_rounds}/{args.max_idle_rounds}).")
                    if idle_rounds >= args.max_idle_rounds:
                        break
                else:
                    idle_rounds = 0
                scroll_feed(feed_page)
        finally:
            detail_page.close()
            feed_page.close()

    if dataset[THREADS_KEY] or not args.reset:
        save_dataset_safely(output, dataset)
    else:
        print(
            "⚠️ Không crawl được thread mới; giữ nguyên file output cũ. "
            "Hãy kiểm tra Chrome/Facebook rồi chạy lại với --reset."
        )
    print(
        f"HOÀN THÀNH: {dataset[TOTAL_THREADS_KEY]} thread, "
        f"{dataset[TOTAL_COMMENTS_KEY]} post/comment."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
