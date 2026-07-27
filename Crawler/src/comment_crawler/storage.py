"""Safe append-only storage for canonical comment CSV files."""

from __future__ import annotations

import csv
import os
from pathlib import Path

from comment_crawler.schema import COMMENT_CSV_COLUMNS


def prepare_comment_csv(path: Path) -> set[tuple[str, str]]:
    """Return stored keys and upgrade compatible legacy headers without losing rows."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists() or path.stat().st_size == 0:
        return set()
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        old_columns = reader.fieldnames or []
        old_rows = list(reader)
    if old_columns != COMMENT_CSV_COLUMNS:
        unknown = set(old_columns) - set(COMMENT_CSV_COLUMNS)
        if unknown:
            raise RuntimeError(
                f"CSV cu co cot khong ho tro: {', '.join(sorted(unknown))}"
            )
        temp_path = path.with_suffix(path.suffix + ".tmp")
        with temp_path.open("w", encoding="utf-8-sig", newline="") as file:
            writer = csv.DictWriter(file, fieldnames=COMMENT_CSV_COLUMNS)
            writer.writeheader()
            writer.writerows(old_rows)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temp_path, path)
        print("Da giu du lieu cu va nang cap header CSV.")
    return {
        (str(row.get("content_id") or row.get("video_id") or ""), str(row.get("comment_id", "")))
        for row in old_rows
        if (row.get("content_id") or row.get("video_id")) and row.get("comment_id")
    }


def append_comment_rows(path: Path, rows: list[dict[str, str]]) -> None:
    """Append rows atomically enough for a single-process crawler run."""
    new_file = not path.exists() or path.stat().st_size == 0
    # Do not use utf-8-sig here: opening an existing file for append may emit a
    # second BOM in the middle of the CSV. Readers still accept legacy BOM files.
    with path.open("a", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=COMMENT_CSV_COLUMNS)
        if new_file:
            writer.writeheader()
        writer.writerows(rows)
        file.flush()
        os.fsync(file.fileno())
