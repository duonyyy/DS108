#!/usr/bin/env python3
"""Tạo thread_urls.txt từ các trang forum VOZ đã lưu bằng trình duyệt."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from urllib.parse import urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from comment_crawler.paths import VOZ_THREAD_URLS_FILE


def normalize_thread_url(href: str, base_url: str = "https://voz.vn/") -> str | None:
    """Chuẩn hóa URL thread và loại link ngoài hostname voz.vn."""
    parts = urlsplit(urljoin(base_url, href))
    if parts.scheme not in {"http", "https"} or parts.hostname != "voz.vn":
        return None

    path = re.sub(r"/unread/?$", "/", parts.path)
    if not path.startswith("/t/"):
        return None
    return urlunsplit(("https", "voz.vn", path, "", ""))


def extract_thread_urls_from_html(
    html: bytes,
    include_sticky: bool = False,
) -> list[str]:
    """Trích URL thread từ một trang danh sách forum đã lưu."""
    soup = BeautifulSoup(html, "html.parser")
    urls: list[str] = []
    seen: set[str] = set()

    rows = soup.select(".structItem--thread")
    for row in rows:
        if not include_sticky and row.select_one(".structItem-status--sticky"):
            continue
        link = row.select_one(".structItem-title a[href*='/t/']")
        if not link or not link.get("href"):
            continue

        url = normalize_thread_url(link["href"])
        if url and url not in seen:
            seen.add(url)
            urls.append(url)
    return urls


def collect_thread_urls(
    html_files: list[Path],
    max_threads: int,
    include_sticky: bool = False,
) -> list[str]:
    """Gộp URL từ nhiều file HTML, giữ thứ tự và bỏ trùng."""
    urls: list[str] = []
    seen: set[str] = set()

    for html_file in html_files:
        print(f"Đọc: {html_file}")
        for url in extract_thread_urls_from_html(
            html_file.read_bytes(), include_sticky=include_sticky
        ):
            if url in seen:
                continue
            seen.add(url)
            urls.append(url)
            if len(urls) >= max_threads:
                return urls
    return urls


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(
        description="Tạo thread_urls.txt từ HTML forum VOZ đã lưu."
    )
    parser.add_argument("html_files", nargs="+", type=Path, help="Các file HTML")
    parser.add_argument(
        "--output",
        type=Path,
        default=VOZ_THREAD_URLS_FILE,
        help="File đầu ra (mặc định: data/thread_urls.txt)",
    )
    parser.add_argument(
        "--max-threads",
        type=int,
        default=50,
        help="Số URL tối đa (mặc định: 50)",
    )
    parser.add_argument(
        "--include-sticky", action="store_true", help="Gồm cả thread ghim"
    )
    args = parser.parse_args()

    if args.max_threads < 1:
        parser.error("--max-threads phải lớn hơn 0")
    missing = [path for path in args.html_files if not path.is_file()]
    if missing:
        parser.error("Không tìm thấy file: " + ", ".join(str(x) for x in missing))
    empty = [path for path in args.html_files if path.stat().st_size == 0]
    if empty:
        parser.error(
            "File HTML rỗng (0 byte), hãy lưu lại từ Chrome: "
            + ", ".join(str(x) for x in empty)
        )

    urls = collect_thread_urls(
        args.html_files,
        max_threads=args.max_threads,
        include_sticky=args.include_sticky,
    )
    if not urls:
        print(
            "Không tìm thấy thread trong HTML; file đầu ra không được thay đổi. "
            "Hãy chắc rằng đã lưu trang forum sau khi nội dung tải xong."
        )
        return 1
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        "".join(f"{url}\n" for url in urls), encoding="utf-8"
    )
    print(f"Đã ghi {len(urls)} URL vào {args.output}")
    return 0 if urls else 1


if __name__ == "__main__":
    raise SystemExit(main())
