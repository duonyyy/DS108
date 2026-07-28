#!/usr/bin/env python3
"""
Thu thập metadata video và bình luận công khai từ YouTube Data API v3.

Đầu ra:
- videos.csv: metadata của video
- youtube_comments_link.csv: mỗi bình luận là một dòng
- comments.jsonl: cùng dữ liệu ở định dạng JSON Lines
- errors.csv: video bị xóa, riêng tư, tắt bình luận hoặc lỗi API
- summary.json: thống kê lần chạy

Mặc định không lưu tên hoặc mã kênh của người bình luận để giảm dữ liệu
nhận diện cá nhân.

Yêu cầu:
    pip install requests

Thiết lập API key:
    Linux/macOS:
        export YOUTUBE_API_KEY="YOUR_API_KEY"
    Windows PowerShell:
        $env:YOUTUBE_API_KEY="YOUR_API_KEY"

Ví dụ:
    python scripts/youtube/youtube_comments_crawler.py

Lấy toàn bộ bình luận có thể truy cập:
    python scripts/youtube/youtube_comments_crawler.py --max-comments-per-video 0

Chỉ lấy tối đa 30 bình luận mỗi video:
    python scripts/youtube/youtube_comments_crawler.py --max-comments-per-video 30

Dùng danh sách URL riêng, mỗi dòng một URL hoặc video ID:
    python scripts/youtube/youtube_comments_crawler.py --urls-file urls.txt
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional
from urllib.parse import parse_qs, urlparse

import requests


API_ROOT = "https://www.googleapis.com/youtube/v3"
VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT_DIR = ROOT / "data" / "youtube" / "youtube_links"

DEFAULT_VIDEO_URLS = [
    # "https://www.youtube.com/watch?v=DZsLH7-Blns",
    # "https://www.youtube.com/watch?v=JtKAvuW53_E",
    # "https://www.youtube.com/watch?v=L19nxlo2nVM",
    # "https://www.youtube.com/watch?v=MYaDiToxAc8",
    # "https://www.youtube.com/watch?v=JBcLMw6GSZU",
    # "https://www.youtube.com/watch?v=YHGX1v5FibI",
    # "https://www.youtube.com/watch?v=GhvZrRyyeIs",
    # "https://www.youtube.com/watch?v=jWIxZnu9ysI",
    # "https://www.youtube.com/watch?v=ImTVgQ1upnY",
    # "https://www.youtube.com/watch?v=_EFZWitEync",
    # "https://www.youtube.com/watch?v=2y7cRNXtAV4",
    # "https://www.youtube.com/watch?v=KGu2ks_XmU0",
    # "https://www.youtube.com/watch?v=ChxKCVSOmgg",
    # "https://www.youtube.com/watch?v=bUd3BqDeHGs",
    # "https://www.youtube.com/watch?v=rLMGkE5VKrU",
    # "https://www.youtube.com/watch?v=bk2AfEm2xS0",
    # "https://www.youtube.com/watch?v=A-Cv_aa5dRs",
    # "https://www.youtube.com/watch?v=LHQodXrcfRo",
    # "https://www.youtube.com/watch?v=KD1CuWiX2Co",
    # "https://www.youtube.com/watch?v=Yd0o5oISSqM",
    # "https://www.youtube.com/watch?v=sRHFxiWp5rc",
    # "https://www.youtube.com/watch?v=mZWJFhxt7yw",
    # "https://www.youtube.com/watch?v=4ghbI5Y1mT8",
    # "https://www.youtube.com/watch?v=u_r7f_adfck",
    # "https://www.youtube.com/watch?v=wQS3_EEHfvM",
    # "https://www.youtube.com/watch?v=k9O2uOXXaNE",
    # "https://www.youtube.com/watch?v=S2lQ9BB76kI",
    # "https://www.youtube.com/watch?v=RP28H5JA3Zo",
    # "https://www.youtube.com/watch?v=pDFKpAFya1s",
    # "https://www.youtube.com/watch?v=XGEDGdSlmRQ",
    # " https://www.youtube.com/watch?v=vC1kxao-vKU"
    # "https://www.youtube.com/watch?v=n9DATHwDcqs",
    # "https://www.youtube.com/watch?v=aTlqH1djttg",
    # "https://www.youtube.com/watch?v=iKLrfxGwfys",
    # "https://www.youtube.com/watch?v=qltZn6sV5_o",
    # "https://www.youtube.com/watch?v=uK5USxRR7iI",
    # "https://www.youtube.com/watch?v=_r8rwew3DDY",
    # "https://www.youtube.com/watch?v=QWrLA_SoJGY",
    # "https://www.youtube.com/watch?v=eYwrg4VtscQ",
    # "https://www.youtube.com/watch?v=l3JdTYIHn-o",
    # "https://www.youtube.com/watch?v=oyEOohYtgvI",
    # "https://www.youtube.com/watch?v=7kO_ALcwNAw",
    # "https://www.youtube.com/watch?v=6aOSom-T6QA",
    # "https://www.youtube.com/watch?v=CtpkQQGPWn4",
    # "https://www.youtube.com/watch?v=Wv3QtKNnATY",
    # "https://www.youtube.com/watch?v=9N_Viiudrp0",
    # "https://www.youtube.com/watch?v=NrYrLV0oaQ0",
    # "https://www.youtube.com/watch?v=UB7qPhTQe-k",
    # "https://www.youtube.com/watch?v=N-AkBvnWhOo",
    # "https://www.youtube.com/watch?v=XXuRl7fhfJw"
    # 
    "https://www.youtube.com/watch?v=u-WC6EX1ahA",
    "https://www.youtube.com/watch?v=2-NFuX4x5jo",
    "https://www.youtube.com/watch?v=E0j17u1a260",
    "https://www.youtube.com/watch?v=gHguLIMFdQY"

]

VIDEO_FIELDS = [
    "video_id",
    "video_url",
    "title",
    "channel_title",
    "published_at",
    "view_count",
    "like_count",
    "comment_count",
]

COMMENT_FIELDS = [
    "video_id",
    "video_url",
    "title",
    "channel_title",
    "comment_id",
    "parent_id",
    "is_reply",
    "comment",
    "like_count",
    "published_at",
    "updated_at",
]

LEGACY_COMMENT_FIELDS = [
    "video_id",
    "video_url",
    "video_title",
    "video_channel",
    "comment_id",
    "parent_id",
    "is_reply",
    "comment_text",
    "like_count",
    "published_at",
    "updated_at",
]

ERROR_FIELDS = ["video_id", "video_url", "stage", "status_code", "reason", "message"]


class YouTubeAPIError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        status_code: Optional[int] = None,
        reason: str = "",
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.reason = reason


class CommentsDisabled(YouTubeAPIError):
    pass


@dataclass(frozen=True)
class VideoMetadata:
    video_id: str
    video_url: str
    title: str
    channel_title: str
    published_at: str
    view_count: Optional[int]
    like_count: Optional[int]
    comment_count: Optional[int]

    def as_csv_row(self) -> Dict[str, Any]:
        return {
            "video_id": self.video_id,
            "video_url": self.video_url,
            "title": self.title,
            "channel_title": self.channel_title,
            "published_at": self.published_at,
            "view_count": self.view_count,
            "like_count": self.like_count,
            "comment_count": self.comment_count,
        }


def parse_int(value: Any) -> Optional[int]:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def extract_video_id(value: str) -> Optional[str]:
    """Nhận URL YouTube phổ biến hoặc video ID 11 ký tự."""
    value = value.strip()
    if not value or value.startswith("#"):
        return None

    if VIDEO_ID_RE.fullmatch(value):
        return value

    parsed = urlparse(value)
    host = parsed.netloc.lower().split(":")[0]
    if host.startswith("www."):
        host = host[4:]

    if host == "youtu.be":
        candidate = parsed.path.strip("/").split("/")[0]
        return candidate if VIDEO_ID_RE.fullmatch(candidate) else None

    if host in {"youtube.com", "m.youtube.com", "music.youtube.com"}:
        if parsed.path.rstrip("/") == "/watch":
            candidate = parse_qs(parsed.query).get("v", [""])[0]
            return candidate if VIDEO_ID_RE.fullmatch(candidate) else None

        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) >= 2 and parts[0] in {"shorts", "embed", "live"}:
            candidate = parts[1]
            return candidate if VIDEO_ID_RE.fullmatch(candidate) else None

    return None


def unique_video_ids(values: Iterable[str]) -> List[str]:
    seen = set()
    result: List[str] = []
    for value in values:
        video_id = extract_video_id(value)
        if video_id and video_id not in seen:
            seen.add(video_id)
            result.append(video_id)
        elif value.strip() and not value.lstrip().startswith("#") and not video_id:
            print(f"[CẢNH BÁO] Không đọc được URL/video ID: {value.strip()}", file=sys.stderr)
    return result


def read_url_file(path: Path) -> List[str]:
    return path.read_text(encoding="utf-8-sig").splitlines()


def chunks(items: List[str], size: int) -> Iterator[List[str]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


def prepare_append_csv(
    path: Path,
    fields: List[str],
    *,
    id_field: str,
    legacy_fields: Optional[List[str]] = None,
) -> set[str]:
    """Validate/migrate a CSV and return IDs already stored in it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists() or path.stat().st_size == 0:
        return set()

    with path.open("r", encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        current_fields = reader.fieldnames or []
        rows = list(reader)

    if current_fields == legacy_fields:
        for row in rows:
            row["title"] = row.pop("video_title", "")
            row["channel_title"] = row.pop("video_channel", "")
            row["comment"] = row.pop("comment_text", "")
        temp_path = path.with_suffix(path.suffix + ".tmp")
        with temp_path.open("w", encoding="utf-8-sig", newline="") as file:
            writer = csv.DictWriter(file, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temp_path, path)
        print(f"Đã nâng cấp schema cũ, không xóa dữ liệu: {path}")
    elif current_fields != fields:
        raise RuntimeError(
            f"Header không tương thích trong {path}: {', '.join(current_fields)}"
        )

    return {row[id_field] for row in rows if row.get(id_field)}


def prepare_comments_jsonl(path: Path) -> None:
    """Upgrade legacy JSONL field names when an old output directory is reused."""
    if not path.exists() or path.stat().st_size == 0:
        return

    rows: List[Dict[str, Any]] = []
    changed = False
    with path.open("r", encoding="utf-8-sig") as file:
        for line_number, line in enumerate(file, start=1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise RuntimeError(
                    f"JSONL lỗi tại {path}, dòng {line_number}: {exc}"
                ) from exc
            if "video_title" in row or "comment_text" in row:
                row["title"] = row.pop("video_title", "")
                row["channel_title"] = row.pop("video_channel", "")
                row["comment"] = row.pop("comment_text", "")
                changed = True
            rows.append(row)

    if changed:
        temp_path = path.with_suffix(path.suffix + ".tmp")
        with temp_path.open("w", encoding="utf-8") as file:
            for row in rows:
                file.write(json.dumps(row, ensure_ascii=False) + "\n")
            file.flush()
            os.fsync(file.fileno())
        os.replace(temp_path, path)
        print(f"Đã nâng cấp schema JSONL cũ: {path}")


def parse_api_error(response: requests.Response) -> tuple[str, str]:
    reason = ""
    message = response.text[:1000]
    try:
        payload = response.json()
        error = payload.get("error", {})
        message = error.get("message", message)
        details = error.get("errors") or []
        if details:
            reason = details[0].get("reason", "")
    except (ValueError, TypeError, AttributeError):
        pass
    return reason, message


def api_get(
    session: requests.Session,
    api_key: str,
    endpoint: str,
    params: Dict[str, Any],
    *,
    retries: int = 5,
    timeout: int = 30,
    sleep_between_requests: float = 0.0,
) -> Dict[str, Any]:
    url = f"{API_ROOT}/{endpoint}"
    request_params = {**params, "key": api_key}

    for attempt in range(retries + 1):
        if sleep_between_requests > 0:
            time.sleep(sleep_between_requests)

        try:
            response = session.get(url, params=request_params, timeout=timeout)
        except requests.RequestException as exc:
            if attempt >= retries:
                raise YouTubeAPIError(f"Lỗi mạng sau {retries + 1} lần thử: {exc}") from exc
            time.sleep(min(2**attempt, 30))
            continue

        if response.ok:
            return response.json()

        reason, message = parse_api_error(response)

        if response.status_code == 403 and reason == "commentsDisabled":
            raise CommentsDisabled(
                message,
                status_code=response.status_code,
                reason=reason,
            )

        retryable_reason = reason in {
            "backendError",
            "processingFailure",
            "rateLimitExceeded",
            "userRateLimitExceeded",
        }
        retryable_status = response.status_code in {429, 500, 502, 503, 504}

        if attempt < retries and (retryable_status or retryable_reason):
            retry_after = response.headers.get("Retry-After")
            try:
                delay = float(retry_after) if retry_after else min(2**attempt, 30)
            except ValueError:
                delay = min(2**attempt, 30)
            time.sleep(delay)
            continue

        raise YouTubeAPIError(
            message,
            status_code=response.status_code,
            reason=reason,
        )

    raise AssertionError("Nhánh không thể xảy ra")


def fetch_video_metadata(
    session: requests.Session,
    api_key: str,
    video_ids: List[str],
    *,
    sleep_between_requests: float,
) -> Dict[str, VideoMetadata]:
    result: Dict[str, VideoMetadata] = {}

    # videos.list hỗ trợ tối đa 50 ID trong một yêu cầu.
    for batch in chunks(video_ids, 50):
        payload = api_get(
            session,
            api_key,
            "videos",
            {
                "part": "snippet,statistics",
                "id": ",".join(batch),
            },
            sleep_between_requests=sleep_between_requests,
        )

        for item in payload.get("items", []):
            video_id = item["id"]
            snippet = item.get("snippet", {})
            statistics = item.get("statistics", {})
            result[video_id] = VideoMetadata(
                video_id=video_id,
                video_url=f"https://www.youtube.com/watch?v={video_id}",
                title=snippet.get("title", ""),
                channel_title=snippet.get("channelTitle", ""),
                published_at=snippet.get("publishedAt", ""),
                view_count=parse_int(statistics.get("viewCount")),
                like_count=parse_int(statistics.get("likeCount")),
                comment_count=parse_int(statistics.get("commentCount")),
            )

    return result


def comment_row(
    video: VideoMetadata,
    comment: Dict[str, Any],
    *,
    parent_id: str,
    is_reply: bool,
) -> Dict[str, Any]:
    snippet = comment.get("snippet", {})
    text = snippet.get("textDisplay")
    if text is None:
        text = snippet.get("textOriginal", "")

    return {
        "video_id": video.video_id,
        "video_url": video.video_url,
        "title": video.title,
        "channel_title": video.channel_title,
        "comment_id": comment.get("id", ""),
        "parent_id": parent_id,
        "is_reply": is_reply,
        "comment": text,
        "like_count": parse_int(snippet.get("likeCount")) or 0,
        "published_at": snippet.get("publishedAt", ""),
        "updated_at": snippet.get("updatedAt", ""),
    }


def write_comment(
    row: Dict[str, Any],
    csv_writer: csv.DictWriter,
    jsonl_file: Any,
    known_comment_ids: set[str],
) -> bool:
    comment_id = str(row.get("comment_id", ""))
    if not comment_id or comment_id in known_comment_ids:
        return False
    csv_writer.writerow(row)
    jsonl_file.write(json.dumps(row, ensure_ascii=False) + "\n")
    known_comment_ids.add(comment_id)
    return True


def iter_all_replies(
    session: requests.Session,
    api_key: str,
    parent_id: str,
    *,
    sleep_between_requests: float,
) -> Iterator[Dict[str, Any]]:
    page_token: Optional[str] = None

    while True:
        params: Dict[str, Any] = {
            "part": "snippet",
            "parentId": parent_id,
            "maxResults": 100,
            "textFormat": "plainText",
        }
        if page_token:
            params["pageToken"] = page_token

        payload = api_get(
            session,
            api_key,
            "comments",
            params,
            sleep_between_requests=sleep_between_requests,
        )

        yield from payload.get("items", [])

        page_token = payload.get("nextPageToken")
        if not page_token:
            break


def collect_video_comments(
    session: requests.Session,
    api_key: str,
    video: VideoMetadata,
    csv_writer: csv.DictWriter,
    jsonl_file: Any,
    known_comment_ids: set[str],
    *,
    max_comments: int,
    order: str,
    include_replies: bool,
    sleep_between_requests: float,
) -> tuple[int, int]:
    """
    max_comments = 0 nghĩa là không giới hạn.
    Giới hạn tính cả bình luận cấp cao nhất và câu trả lời.
    Trả về (số comment đã duyệt, số comment mới đã ghi).
    """
    processed = 0
    written = 0
    page_token: Optional[str] = None

    while True:
        params: Dict[str, Any] = {
            "part": "snippet",
            "videoId": video.video_id,
            "maxResults": 100,
            "order": order,
            "textFormat": "plainText",
        }
        if page_token:
            params["pageToken"] = page_token

        payload = api_get(
            session,
            api_key,
            "commentThreads",
            params,
            sleep_between_requests=sleep_between_requests,
        )

        for thread in payload.get("items", []):
            thread_snippet = thread.get("snippet", {})
            top_level = thread_snippet.get("topLevelComment")
            if not top_level:
                continue

            top_level_id = top_level.get("id", "")
            row = comment_row(
                video,
                top_level,
                parent_id="",
                is_reply=False,
            )
            processed += 1
            if write_comment(row, csv_writer, jsonl_file, known_comment_ids):
                written += 1

            if max_comments > 0 and processed >= max_comments:
                return processed, written

            total_reply_count = parse_int(thread_snippet.get("totalReplyCount")) or 0
            if include_replies and total_reply_count > 0 and top_level_id:
                for reply in iter_all_replies(
                    session,
                    api_key,
                    top_level_id,
                    sleep_between_requests=sleep_between_requests,
                ):
                    row = comment_row(
                        video,
                        reply,
                        parent_id=top_level_id,
                        is_reply=True,
                    )
                    processed += 1
                    if write_comment(
                        row, csv_writer, jsonl_file, known_comment_ids
                    ):
                        written += 1

                    if max_comments > 0 and processed >= max_comments:
                        return processed, written

        page_token = payload.get("nextPageToken")
        if not page_token:
            break

    return processed, written


def error_row(
    video_id: str,
    stage: str,
    exc: BaseException,
) -> Dict[str, Any]:
    return {
        "video_id": video_id,
        "video_url": f"https://www.youtube.com/watch?v={video_id}",
        "stage": stage,
        "status_code": getattr(exc, "status_code", ""),
        "reason": getattr(exc, "reason", ""),
        "message": str(exc),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Lấy tiêu đề và bình luận công khai từ YouTube Data API v3."
    )
    parser.add_argument(
        "--api-key",
        default=os.getenv("YOUTUBE_API_KEY", ""),
        help="API key; mặc định đọc từ biến môi trường YOUTUBE_API_KEY.",
    )
    parser.add_argument(
        "--urls-file",
        type=Path,
        help="Tệp UTF-8, mỗi dòng là URL YouTube hoặc video ID. "
             "Nếu bỏ qua, script dùng sẵn 30 video.",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="Thư mục đầu ra; mặc định: data/youtube/youtube_links",
    )
    parser.add_argument(
        "--max-comments-per-video",
        type=int,
        default=0,
        help="Số bình luận tối đa mỗi video, tính cả replies; 0 = toàn bộ.",
    )
    parser.add_argument(
        "--order",
        choices=["time", "relevance"],
        default="time",
        help="Thứ tự commentThreads: time hoặc relevance.",
    )
    parser.add_argument(
        "--no-replies",
        action="store_true",
        help="Chỉ lấy bình luận cấp cao nhất, không lấy câu trả lời.",
    )
    parser.add_argument(
        "--sleep",
        type=float,
        default=0.05,
        help="Số giây nghỉ trước mỗi API request; mặc định 0.05.",
    )
    args = parser.parse_args()

    if args.max_comments_per_video < 0:
        parser.error("--max-comments-per-video phải >= 0")
    if args.sleep < 0:
        parser.error("--sleep phải >= 0")
    if args.urls_file and not args.urls_file.is_file():
        parser.error(f"Không tìm thấy file URL: {args.urls_file}")
    return args


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    args = parse_args()

    if not args.api_key:
        print(
            "Thiếu API key. Hãy đặt biến môi trường YOUTUBE_API_KEY "
            "hoặc truyền --api-key.",
            file=sys.stderr,
        )
        return 2

    try:
        raw_values = (
            read_url_file(args.urls_file)
            if args.urls_file
            else DEFAULT_VIDEO_URLS
        )
    except (OSError, UnicodeError) as exc:
        print(f"Không đọc được file URL: {exc}", file=sys.stderr)
        return 2
    video_ids = unique_video_ids(raw_values)
    if not video_ids:
        print("Không tìm thấy video ID hợp lệ.", file=sys.stderr)
        return 2

    args.out_dir.mkdir(parents=True, exist_ok=True)
    videos_path = args.out_dir / "videos.csv"
    comments_csv_path = args.out_dir / "youtube_comments_link.csv"
    comments_jsonl_path = args.out_dir / "comments.jsonl"
    errors_path = args.out_dir / "errors.csv"
    summary_path = args.out_dir / "summary.json"

    session = requests.Session()
    session.headers.update(
        {
            "Accept": "application/json",
            "User-Agent": "Vietnamese-Toxicity-Research/1.0",
        }
    )

    started_at = datetime.now(timezone.utc)
    processed_comments = 0
    completed_videos = 0
    disabled_videos = 0
    failed_videos = 0

    try:
        known_video_ids = prepare_append_csv(
            videos_path, VIDEO_FIELDS, id_field="video_id"
        )
        known_comment_ids = prepare_append_csv(
            comments_csv_path,
            COMMENT_FIELDS,
            id_field="comment_id",
            legacy_fields=LEGACY_COMMENT_FIELDS,
        )
        prepare_append_csv(errors_path, ERROR_FIELDS, id_field="video_id")
        prepare_comments_jsonl(comments_jsonl_path)
    except (OSError, RuntimeError) as exc:
        print(f"Không chuẩn bị được output: {exc}", file=sys.stderr)
        return 1

    initial_comment_count = len(known_comment_ids)

    try:
        metadata_by_id = fetch_video_metadata(
            session,
            args.api_key,
            video_ids,
            sleep_between_requests=args.sleep,
        )
    except YouTubeAPIError as exc:
        print(
            "Lỗi khi lấy metadata video; các output cũ được giữ nguyên: "
            f"{exc}",
            file=sys.stderr,
        )
        return 1

    new_videos_file = not videos_path.exists() or videos_path.stat().st_size == 0
    new_comments_file = (
        not comments_csv_path.exists() or comments_csv_path.stat().st_size == 0
    )
    new_errors_file = not errors_path.exists() or errors_path.stat().st_size == 0

    with (
        videos_path.open(
            "a",
            newline="",
            encoding="utf-8-sig" if new_videos_file else "utf-8",
        ) as videos_file,
        comments_csv_path.open(
            "a",
            newline="",
            encoding="utf-8-sig" if new_comments_file else "utf-8",
        ) as comments_csv_file,
        comments_jsonl_path.open("a", encoding="utf-8") as comments_jsonl_file,
        errors_path.open(
            "a",
            newline="",
            encoding="utf-8-sig" if new_errors_file else "utf-8",
        ) as errors_file,
    ):
        video_writer = csv.DictWriter(videos_file, fieldnames=VIDEO_FIELDS)
        comment_writer = csv.DictWriter(comments_csv_file, fieldnames=COMMENT_FIELDS)
        error_writer = csv.DictWriter(errors_file, fieldnames=ERROR_FIELDS)
        if new_videos_file:
            video_writer.writeheader()
        if new_comments_file:
            comment_writer.writeheader()
        if new_errors_file:
            error_writer.writeheader()

        # Ghi nhận URL không tồn tại, video riêng tư hoặc đã bị xóa.
        for video_id in video_ids:
            if video_id not in metadata_by_id:
                exc = YouTubeAPIError(
                    "Không nhận được metadata; video có thể đã bị xóa, "
                    "đặt riêng tư hoặc bị giới hạn truy cập.",
                    reason="videoUnavailable",
                )
                error_writer.writerow(error_row(video_id, "video_metadata", exc))
                failed_videos += 1

        videos = [
            metadata_by_id[video_id]
            for video_id in video_ids
            if video_id in metadata_by_id
        ]

        for index, video in enumerate(videos, start=1):
            if video.video_id not in known_video_ids:
                video_writer.writerow(video.as_csv_row())
                known_video_ids.add(video.video_id)
                videos_file.flush()

            print(f"[{index}/{len(videos)}] {video.title}")

            try:
                processed, written = collect_video_comments(
                    session,
                    args.api_key,
                    video,
                    comment_writer,
                    comments_jsonl_file,
                    known_comment_ids,
                    max_comments=args.max_comments_per_video,
                    order=args.order,
                    include_replies=not args.no_replies,
                    sleep_between_requests=args.sleep,
                )
                comments_csv_file.flush()
                comments_jsonl_file.flush()
                os.fsync(comments_csv_file.fileno())
                os.fsync(comments_jsonl_file.fileno())
                processed_comments += processed
                completed_videos += 1
                print(
                    f"    Đã duyệt {processed:,}; "
                    f"ghi thêm {written:,} bình luận mới."
                )
            except CommentsDisabled as exc:
                comments_csv_file.flush()
                comments_jsonl_file.flush()
                disabled_videos += 1
                error_writer.writerow(error_row(video.video_id, "comments", exc))
                errors_file.flush()
                print("    Video đã tắt bình luận.", file=sys.stderr)
            except YouTubeAPIError as exc:
                comments_csv_file.flush()
                comments_jsonl_file.flush()
                failed_videos += 1
                error_writer.writerow(error_row(video.video_id, "comments", exc))
                errors_file.flush()
                print(
                    f"    Lỗi API [{exc.status_code or '-'} / {exc.reason or '-'}]: {exc}",
                    file=sys.stderr,
                )

    # Tính theo khóa thực tế để gồm cả comment đã ghi trước một lỗi API giữa video.
    written_comments = len(known_comment_ids) - initial_comment_count
    finished_at = datetime.now(timezone.utc)
    summary = {
        "started_at_utc": started_at.isoformat(),
        "finished_at_utc": finished_at.isoformat(),
        "requested_video_count": len(video_ids),
        "metadata_video_count": len(metadata_by_id),
        "completed_video_count": completed_videos,
        "comments_disabled_video_count": disabled_videos,
        "failed_video_count": failed_videos,
        "processed_comment_count": processed_comments,
        "new_comment_count": written_comments,
        "stored_comment_count": len(known_comment_ids),
        "include_replies": not args.no_replies,
        "max_comments_per_video": args.max_comments_per_video,
        "order": args.order,
        "files": {
            "videos_csv": str(videos_path),
            "comments_csv": str(comments_csv_path),
            "comments_jsonl": str(comments_jsonl_path),
            "errors_csv": str(errors_path),
        },
    }
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print("\nHoàn tất.")
    print(f"Video thành công: {completed_videos}")
    print(f"Video tắt bình luận: {disabled_videos}")
    print(f"Video lỗi/không khả dụng: {failed_videos}")
    print(f"Bình luận đã duyệt: {processed_comments:,}")
    print(f"Bình luận mới đã ghi: {written_comments:,}")
    print(f"Tổng bình luận trong CSV: {len(known_comment_ids):,}")
    print(f"Thư mục đầu ra: {args.out_dir.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
