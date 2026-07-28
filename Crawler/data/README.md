# Dữ liệu crawler

Dữ liệu được lưu theo nền tảng. Các crawler ghi nối tiếp vào file hiện có theo
cơ chế riêng của từng nguồn; không chuyển dữ liệu trở lại `data/raw/`.

```text
data/
├── facebook/
│   ├── facebook_group/data.json
│   ├── facebook_page/data.json
│   └── facebook_single_post/data.json
├── thread_city/
│   └── thread_city.csv
├── tiktok/
│   └── tiktok_comments.csv
├── voz/
│   ├── thread_urls.txt
│   ├── voz_comments.csv
│   ├── voz_comments.json
│   └── dataset/                         # chỉ xuất hiện khi prepare_dataset.py chạy
├── youtube/
│   ├── youtube_comments_keywords.csv
│   └── youtube_links/
│       ├── youtube_comments_link.csv
│       ├── comments.jsonl
│       ├── videos.csv
│       ├── errors.csv
│       └── summary.json
└── .runtime/                            # checkpoint và profile tạm
```

Các file `*.bak` trong thư mục Facebook là bản sao dự phòng trước lần ghi gần
nhất. Không dùng các file này làm input chính nếu `data.json` vẫn hợp lệ.

Không đổi tên file output mà không cập nhật đồng thời
`src/comment_crawler/paths.py`, script crawler tương ứng và tài liệu.
