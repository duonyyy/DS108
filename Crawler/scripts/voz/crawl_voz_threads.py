#!/usr/bin/env python3
"""Run each pending VOZ thread URL and mark successful rows as DONE."""

from __future__ import annotations

import argparse
import os
import random
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]
MAX_PAGES_PER_THREAD = 1

if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from comment_crawler.paths import VOZ_COMMENTS_PREFIX, VOZ_THREAD_URLS_FILE


def is_voz_thread_url(url: str) -> bool:
    parts = urlsplit(url)
    return (
        parts.scheme == "https"
        and parts.hostname == "voz.vn"
        and parts.path.startswith("/t/")
    )


def read_pending_urls(path: Path) -> tuple[list[str], list[tuple[int, str]]]:
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    pending: list[tuple[int, str]] = []
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        parts = stripped.split()
        url = parts[0]
        if "DONE" in {part.upper() for part in parts[1:]}:
            continue
        if not is_voz_thread_url(url):
            print(f"Bo URL khong hop le dong {index + 1}: {stripped}")
            continue
        pending.append((index, url))
    return lines, pending


def mark_done(path: Path, lines: list[str], line_index: int, url: str) -> None:
    lines[line_index] = f"{url} DONE"
    temp_path = path.with_suffix(path.suffix + ".tmp")
    with temp_path.open("w", encoding="utf-8") as file:
        file.write("\n".join(lines) + "\n")
        file.flush()
        os.fsync(file.fileno())
    os.replace(temp_path, path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Crawl tung URL VOZ chua DONE.")
    parser.add_argument("--input", type=Path, default=VOZ_THREAD_URLS_FILE)
    parser.add_argument("--max-pages", type=int, default=MAX_PAGES_PER_THREAD)
    parser.add_argument("--include-original-post", action="store_true")
    parser.add_argument("--keep-quotes", action="store_true")
    parser.add_argument("--output-prefix", default=str(VOZ_COMMENTS_PREFIX))
    parser.add_argument("--delay-min", type=float, default=3.0)
    parser.add_argument("--delay-max", type=float, default=6.0)
    parser.add_argument("--stop-on-error", action="store_true")
    args = parser.parse_args()
    if args.max_pages != MAX_PAGES_PER_THREAD:
        parser.error("--max-pages phai bang 1; moi thread chi crawl trang dau.")
    if args.delay_min < 0 or args.delay_max < 0 or args.delay_min > args.delay_max:
        parser.error("delay khong hop le")
    if not args.input.is_file():
        parser.error(f"Khong tim thay file: {args.input}")
    return args


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    args = parse_args()
    lines, pending = read_pending_urls(args.input)
    if not pending:
        print("Khong co URL nao can chay. Tat ca co the da DONE.")
        return 0

    import requests
    from voz_crawler_core.bs_crawler import (
        append_csv_result,
        crawl_voz_thread,
        load_json_collection,
        merge_json_result,
    )

    json_path = Path(f"{args.output_prefix}.json")
    csv_path = Path(f"{args.output_prefix}.csv")
    try:
        load_json_collection(json_path)
    except (OSError, RuntimeError) as exc:
        print(f"JSON cu bi loi, dung de tranh output lech nhau: {exc}")
        return 1

    success = failed = 0
    print(f"Can chay {len(pending)} thread tu {args.input}")
    for ordinal, (line_index, url) in enumerate(pending, start=1):
        print(f"\nThread {ordinal}/{len(pending)} dong {line_index + 1}: {url}")
        try:
            result = crawl_voz_thread(
                thread_url=url,
                include_original_post=args.include_original_post,
                remove_quotes=not args.keep_quotes,
                max_pages=MAX_PAGES_PER_THREAD,
            )
            csv_added = append_csv_result(csv_path, result)
            json_added, json_total = merge_json_result(json_path, result)
        except (requests.RequestException, RuntimeError, OSError) as exc:
            failed += 1
            print(f"Loi: {exc}")
            if args.stop_on_error:
                break
        else:
            success += 1
            mark_done(args.input, lines, line_index, url)
            print(f"DONE. CSV them {csv_added} dong; JSON them {json_added} bai, tong {json_total} bai.")
        if ordinal < len(pending):
            delay = random.uniform(args.delay_min, args.delay_max)
            print(f"Nghi {delay:.1f} giay")
            time.sleep(delay)
    print(f"\nXong: {success} thanh cong, {failed} loi.")
    return 0 if failed == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
