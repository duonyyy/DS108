#!/usr/bin/env python3
"""Prepare the crawled VOZ CSV as a reusable dataset."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import random
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from comment_crawler.paths import DATASET_DIR, VOZ_COMMENTS_CSV

DEFAULT_INPUT = VOZ_COMMENTS_CSV
DEFAULT_OUTPUT_DIR = DATASET_DIR
SOURCE_NAME = "voz.vn"
OUTPUT_COLUMNS = [
    "comment_id",
    "thread_id",
    "title",
    "thread_url",
    "post_id",
    "username",
    "datetime",
    "datetime_utc",
    "page",
    "comment",
    "comment_length_chars",
    "comment_length_words",
    "source",
]


def stable_id(value: str, length: int = 16) -> str:
    return hashlib.sha1(value.encode("utf-8")).hexdigest()[:length]


def clean_text(text: str) -> str:
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def parse_datetime_to_utc(value: str) -> str:
    value = (value or "").strip()
    if not value:
        return ""

    normalized = value
    if re.search(r"[+-]\d{4}$", normalized):
        normalized = f"{normalized[:-2]}:{normalized[-2:]}"
    try:
        dt = datetime.fromisoformat(normalized)
    except ValueError:
        return ""
    if dt.tzinfo is None:
        return dt.isoformat()
    return dt.astimezone(timezone.utc).isoformat()


def load_raw_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        required = {"title", "thread_url", "post_id", "username", "datetime", "comment", "page"}
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise ValueError("CSV thieu cot: " + ", ".join(sorted(missing)))
        return list(reader)


def normalize_rows(raw_rows: list[dict[str, str]]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    stats = Counter()

    for raw in raw_rows:
        stats["raw_rows"] += 1
        comment = clean_text(raw.get("comment", ""))
        if not comment:
            stats["dropped_empty_comment"] += 1
            continue

        thread_url = clean_text(raw.get("thread_url", ""))
        post_id = clean_text(raw.get("post_id", ""))
        dedupe_key = f"{thread_url}|{post_id or comment}"
        if dedupe_key in seen:
            stats["dropped_duplicate"] += 1
            continue
        seen.add(dedupe_key)

        title = clean_text(raw.get("title", ""))
        username = clean_text(raw.get("username", ""))
        page = clean_text(raw.get("page", ""))
        dt = clean_text(raw.get("datetime", ""))
        thread_id = stable_id(thread_url, length=12)
        comment_id = stable_id(f"{thread_url}|{post_id}|{comment}", length=16)
        rows.append(
            {
                "comment_id": comment_id,
                "thread_id": thread_id,
                "title": title,
                "thread_url": thread_url,
                "post_id": post_id,
                "username": username,
                "datetime": dt,
                "datetime_utc": parse_datetime_to_utc(dt),
                "page": page,
                "comment": comment,
                "comment_length_chars": len(comment),
                "comment_length_words": len(re.findall(r"\S+", comment)),
                "source": SOURCE_NAME,
            }
        )

    stats["kept_rows"] = len(rows)
    stats["threads"] = len({row["thread_id"] for row in rows})
    return rows, dict(stats)


def write_csv(path: Path, rows: list[dict[str, Any]], columns: list[str]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as file:
        for row in rows:
            file.write(json.dumps(row, ensure_ascii=False))
            file.write("\n")


def build_threads(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(row["thread_id"], []).append(row)

    threads: list[dict[str, Any]] = []
    for thread_id, comments in grouped.items():
        first = comments[0]
        datetimes = [row["datetime_utc"] for row in comments if row["datetime_utc"]]
        threads.append(
            {
                "thread_id": thread_id,
                "title": first["title"],
                "thread_url": first["thread_url"],
                "comment_count": len(comments),
                "first_datetime_utc": min(datetimes, default=""),
                "last_datetime_utc": max(datetimes, default=""),
            }
        )
    return sorted(threads, key=lambda row: row["thread_id"])


def split_by_thread(
    rows: list[dict[str, Any]],
    train_ratio: float,
    val_ratio: float,
    seed: int,
) -> dict[str, list[dict[str, Any]]]:
    thread_ids = sorted({row["thread_id"] for row in rows})
    random.Random(seed).shuffle(thread_ids)

    total = len(thread_ids)
    train_end = round(total * train_ratio)
    val_end = train_end + round(total * val_ratio)
    if total >= 3:
        train_end = max(1, min(train_end, total - 2))
        val_end = max(train_end + 1, min(val_end, total - 1))

    split_for_thread: dict[str, str] = {}
    for index, thread_id in enumerate(thread_ids):
        if index < train_end:
            split_for_thread[thread_id] = "train"
        elif index < val_end:
            split_for_thread[thread_id] = "val"
        else:
            split_for_thread[thread_id] = "test"

    splits = {"train": [], "val": [], "test": []}
    for row in rows:
        splits[split_for_thread[row["thread_id"]]].append(row)
    return splits


def write_dataset_card(path: Path, metadata: dict[str, Any]) -> None:
    text = f"""# VOZ Comments Dataset

Dataset prepared from public VOZ thread comments crawled into `data/voz/voz_comments.csv`.

## Files

- `comments.csv`: cleaned tabular dataset.
- `comments.jsonl`: same records in JSON Lines format.
- `threads.csv`: one row per thread.
- `splits/train.jsonl`, `splits/val.jsonl`, `splits/test.jsonl`: deterministic split by thread.
- `metadata.json`: generation summary.

## Schema

- `comment_id`: stable hash id for a comment.
- `thread_id`: stable hash id for a thread.
- `title`, `thread_url`: thread metadata.
- `post_id`: VOZ post id when available.
- `username`: public display name.
- `datetime`: original datetime string.
- `datetime_utc`: parsed UTC datetime when parseable.
- `page`: crawled page number. This project is configured to keep only page 1 per thread.
- `comment`: cleaned comment text.
- `comment_length_chars`, `comment_length_words`: simple text length features.
- `source`: source hostname.

## Summary

- Raw rows: {metadata["stats"]["raw_rows"]}
- Kept rows: {metadata["stats"]["kept_rows"]}
- Threads: {metadata["stats"]["threads"]}
- Dropped empty comments: {metadata["stats"].get("dropped_empty_comment", 0)}
- Dropped duplicates: {metadata["stats"].get("dropped_duplicate", 0)}

## Caveats

This dataset is unlabeled. Do not treat it as sentiment, topic, toxicity, or quality training data until labels are added and audited.
Usernames and public post URLs are retained, so consider anonymization before sharing outside your own analysis workflow.
"""
    path.write_text(text, encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Prepare crawled VOZ comments as a dataset.")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--train-ratio", type=float, default=0.8)
    parser.add_argument("--val-ratio", type=float, default=0.1)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    if not args.input.is_file():
        parser.error(f"Khong tim thay input CSV: {args.input}")
    if args.train_ratio <= 0 or args.val_ratio < 0:
        parser.error("Ty le train/val khong hop le")
    if args.train_ratio + args.val_ratio >= 1:
        parser.error("train-ratio + val-ratio phai nho hon 1")
    return args


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    args = parse_args()
    raw_rows = load_raw_rows(args.input)
    rows, stats = normalize_rows(raw_rows)
    threads = build_threads(rows)
    splits = split_by_thread(rows, args.train_ratio, args.val_ratio, args.seed)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    split_dir = args.output_dir / "splits"
    split_dir.mkdir(parents=True, exist_ok=True)

    write_csv(args.output_dir / "comments.csv", rows, OUTPUT_COLUMNS)
    write_jsonl(args.output_dir / "comments.jsonl", rows)
    write_csv(
        args.output_dir / "threads.csv",
        threads,
        ["thread_id", "title", "thread_url", "comment_count", "first_datetime_utc", "last_datetime_utc"],
    )
    for name, split_rows in splits.items():
        write_jsonl(split_dir / f"{name}.jsonl", split_rows)

    metadata = {
        "source_csv": str(args.input),
        "output_dir": str(args.output_dir),
        "seed": args.seed,
        "split_strategy": "by_thread",
        "split_counts": {name: len(split_rows) for name, split_rows in splits.items()},
        "split_thread_counts": {
            name: len({row["thread_id"] for row in split_rows})
            for name, split_rows in splits.items()
        },
        "stats": stats,
    }
    (args.output_dir / "metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    write_dataset_card(args.output_dir / "DATASET_CARD.md", metadata)

    print(f"Da tao dataset tai: {args.output_dir}")
    print(f"Rows giu lai: {stats['kept_rows']}/{stats['raw_rows']}")
    print("Split:", metadata["split_counts"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
