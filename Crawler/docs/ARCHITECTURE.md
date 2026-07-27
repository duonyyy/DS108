# Kiến trúc dự án

## Ranh giới module

```text
scripts/                         Workflow/CLI, chia theo nền tảng
    ├── youtube/                 YouTube API và crawler theo danh sách URL
    ├── tiktok/                  TikTok API/Apify crawler
    ├── reddit/                  Reddit API crawler
    └── voz/                     VOZ crawler và chuẩn bị dataset
    │
    ├──► src/comment_crawler/    Thành phần dùng chung
    │       paths.py             Một nguồn khai báo đường dẫn mặc định
    │       queries.py           Đọc/kiểm tra config và provenance truy vấn
    │       schema.py            Schema bình luận đa nền tảng
    │       storage.py           Nâng cấp header, append và chống trùng
    │
    ├──► src/voz_crawler_core/   Adapter HTTP/Playwright riêng cho VOZ
    │
    ├──► config/                 Cấu hình có version, không chứa secret
    └──► data/                   Input, raw output, dataset và runtime state
```

`scripts/` chứa orchestration và xử lý API riêng cho từng nền tảng.
`comment_crawler/` không phụ thuộc ngược vào script hoặc dữ liệu cụ thể.
`voz_crawler_core/` được tách riêng vì VOZ đang có schema lịch sử và cơ chế
resume khác các API crawler.

## Luồng dữ liệu

```text
config/keywords_vi.json
          │
          ▼
YouTube / TikTok workflows
          │
          ▼
data/raw/{platform}_comments.csv

30 YouTube URLs ───────────► data/raw/youtube_links/

Reddit OAuth workflow ─────────► data/raw/reddit_comments.csv

data/*.html ─► thread_urls.txt ─► data/raw/voz_comments.csv|json
                                         │
                                         ▼
                                  data/dataset/
```

Checkpoint và profile trình duyệt là trạng thái tạm trong
`data/.runtime/`; chúng không phải dataset.

## Hợp đồng CSV đa nền tảng

YouTube, TikTok và Reddit dùng các cột:

```text
platform, category, query_type, keyword, content_id, video_id, title,
comment_id, author, published_at, comment
```

- `title` và `comment` bắt buộc có mặt trong schema bình luận.
- `content_id` là ID chung; `video_id` được giữ để tương thích YouTube/TikTok.
- `category`, `query_type`, `keyword` là nguồn gốc truy xuất, không phải nhãn.
- Khóa chống trùng là `(content_id hoặc video_id, comment_id)`.

Hai luồng VOZ giữ schema giàu thông tin riêng nhưng vẫn có alias `title` và
`comment`. Việc ép dữ liệu VOZ lịch sử sang toàn bộ schema đa nền tảng ngay
trong crawler sẽ gây migration không cần thiết; `prepare_dataset.py` tạo bản
dẫn xuất trong `data/dataset/`.

## Chính sách ghi và migration

1. Crawler đa nền tảng ghi nối tiếp và không xóa CSV cũ.
2. Header cũ chỉ được tự nâng cấp khi mọi cột đều tương thích schema chuẩn.
3. Nếu gặp cột lạ, writer dừng để tránh mất dữ liệu.
4. Không tự động di chuyển `data/raw/voz_comments.*` hoặc nội dung `archive/`.
5. Thay đổi đường dẫn mặc định phải được khai báo trong
   `comment_crawler.paths`, không lặp hằng số ở từng script.

## Phần không thuộc runtime

- `.venv/`, `__pycache__/`: dependency/cache cục bộ.
- `data/.runtime/`: checkpoint và browser profile.
- `archive/`: tài sản lịch sử, không được import hoặc gọi bởi workflow hiện tại.
