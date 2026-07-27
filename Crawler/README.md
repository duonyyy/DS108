# Vietnamese Comment Crawler

Bộ công cụ dòng lệnh để thu thập bình luận công khai bằng tiếng Việt từ
YouTube, TikTok, Reddit và VOZ. YouTube/TikTok dùng chung cấu hình chủ đề; các
crawler API dùng chung schema CSV và cơ chế ghi nối tiếp để chạy nhiều lần mà
không xóa dữ liệu cũ.

> Từ khóa chỉ quyết định nội dung được truy xuất. Chúng **không phải** nhãn
> `TOXIC`, `HATE` hay nhãn chủ đề của từng bình luận. Muốn dùng dữ liệu cho mô
> hình học máy, cần gán nhãn và kiểm định độc lập.

## Cấu trúc dự án

```text
voz_crawler/
├── config/
│   └── keywords_vi.json          # chủ đề, keyword và hashtag
├── scripts/                      # các workflow có thể chạy trực tiếp
│   ├── youtube/
│   │   ├── crawl_youtube.py
│   │   └── youtube_comments_crawler.py # toàn bộ comment của 30 URL cố định
│   ├── tiktok/
│   │   ├── crawl_tiktok_apify.py     # cách TikTok được khuyến nghị trong dự án
│   │   └── crawl_tiktok.py           # TikTokApi thử nghiệm, dễ bị chặn
│   ├── reddit/
│   │   └── crawl_reddit.py
│   └── voz/
│       ├── build_thread_urls.py
│       ├── crawl_voz_threads.py
│       ├── crawl_voz_playwright.py
│       └── prepare_dataset.py
├── src/
│   ├── comment_crawler/          # paths, query loader, schema, CSV storage
│   └── voz_crawler_core/         # implementation riêng cho VOZ
├── data/
│   ├── raw/                      # CSV thô đa nền tảng
│   ├── dataset/                  # dataset VOZ dẫn xuất
│   ├── .runtime/                 # checkpoint/profile, không commit
│   ├── thread_urls.txt
├── docs/
│   └── ARCHITECTURE.md
├── .env.example
├── pyproject.toml
└── requirements.txt
```

`archive/` không thuộc luồng chạy hiện tại. Nó được giữ nguyên vì có thể chứa
mã hoặc dữ liệu lịch sử; chỉ xóa sau khi đã kiểm tra và sao lưu. Chi tiết dữ
liệu nằm trong [data/README.md](data/README.md).

## Cài đặt

Yêu cầu Python 3.11 trở lên:

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e .
playwright install chromium
```

`requirements.txt` chỉ gọi cài đặt editable từ `pyproject.toml`; không cần chạy
cả hai lệnh cài dependency.

## Cấu hình bí mật

Các script đọc biến môi trường của tiến trình. File `.env` bị Git bỏ qua nhưng
**không được tự động nạp**. Trong PowerShell, đặt biến trước khi chạy:

```powershell
$env:YOUTUBE_API_KEY = "..."
$env:APIFY_TOKEN = "..."
$env:REDDIT_CLIENT_ID = "..."
$env:REDDIT_CLIENT_SECRET = "..."
$env:REDDIT_USER_AGENT = "windows:vn-comment-crawler:0.1 (by /u/ten_cua_ban)"
```

Không truyền token vào chat, source code hoặc commit. Nếu một key từng xuất
hiện trong log công khai, hãy thu hồi và tạo key mới.

## Cấu hình chủ đề

[config/keywords_vi.json](config/keywords_vi.json) có sáu nhóm:
`LGBTQ+`, `Vùng miền`, `Tôn giáo`, `Giới tính`, `Ngoại hình`, `Bệnh lý`.
Mỗi truy vấn được lưu kèm:

- `category`: nhóm dùng khi tìm kiếm;
- `query_type`: `neutral`, `toxic_enriched`, `hashtag` hoặc `manual`;
- `keyword`: chuỗi truy vấn thực tế.

Xem danh sách category:

```powershell
python scripts/youtube/crawl_youtube.py --list-categories
python scripts/tiktok/crawl_tiktok_apify.py --list-categories
```

Một số từ khóa có nhiều nghĩa, ví dụ `gay` có thể thuộc LGBTQ+ hoặc là từ không
dấu của “gầy”. Vì vậy cần kiểm tra mẫu và đo độ lệch truy xuất trước khi phân
tích.

## YouTube

YouTube cần API key của YouTube Data API v3. Lệnh đầy đủ theo giới hạn hiện tại
của dự án:

```powershell
python scripts/youtube/crawl_youtube.py --category "LGBTQ+" --max-queries 1 --max-videos 50 --target-open-videos 10 --comments-per-video 13 --max-comments 500
```

- `--max-videos 50`: số video ứng viên tối đa cần kiểm tra cho mỗi query.
- `--target-open-videos 10`: chỉ dừng sau khi tìm đủ 10 video mở bình luận hoặc
  hết ứng viên.
- `--comments-per-video 13`: tối đa 13 bình luận cấp cao nhất mỗi video.
- `--max-comments 500`: giới hạn tổng số bình luận mới của cả lần chạy.
- `--show-skipped`: hiện từng video bị tắt bình luận.
- `--all-languages`: tắt bộ lọc tiếng Việt.

Video tắt bình luận được bỏ qua; đây không phải lỗi dừng chương trình. Kết quả
được nối vào `data/raw/youtube_comments.csv`.

### Toàn bộ bình luận từ 30 link có sẵn

File `youtube_comments_crawler.py` phục vụ một luồng khác: không tìm kiếm theo
keyword mà dùng 30 URL đã khai báo trong file. Mặc định script lấy toàn bộ bình
luận cấp cao nhất và toàn bộ replies có thể truy cập, không có giới hạn tổng
500:

```powershell
python scripts/youtube/youtube_comments_crawler.py
```

Output nằm trong `data/raw/youtube_links/`:

- `videos.csv`: metadata của 30 video;
- `comments.csv` và `comments.jsonl`: bình luận;
- `errors.csv`: video tắt bình luận, riêng tư hoặc lỗi;
- `summary.json`: thống kê lần chạy gần nhất.

Chạy lại cùng lệnh sẽ ghi tiếp và bỏ `comment_id` đã tồn tại. Nếu chỉ muốn tối
đa 30 bình luận mỗi video:

```powershell
python scripts/youtube/youtube_comments_crawler.py --max-comments-per-video 30
```

Dùng `--no-replies` nếu chỉ cần bình luận cấp cao nhất. Luồng 30 link không lọc
ngôn ngữ vì mục tiêu là lấy toàn bộ comment có thể truy cập.

## TikTok

### Cách khuyến nghị: Apify

Pipeline gọi Actor tìm video rồi gọi Actor lấy bình luận. Chạy thử kế hoạch
trước để không phát sinh request/chi phí:

```powershell
python scripts/tiktok/crawl_tiktok_apify.py --category "LGBTQ+" --max-queries 1 --max-videos 5 --search-suffix "Viet Nam" --comments-per-video 13 --max-comments 500 --dry-run
```

Chạy thật:

```powershell
$env:APIFY_TOKEN = "..."
python scripts/tiktok/crawl_tiktok_apify.py --category "LGBTQ+" --max-queries 1 --max-videos 5 --search-suffix "Viet Nam" --comments-per-video 13 --max-comments 500
```

Actor có thể phát sinh chi phí; kiểm tra giá và giới hạn trong Apify Console
trước khi chạy. Token được gửi trong header `Authorization`, không nằm trong
URL hoặc log. Kết quả được nối vào `data/raw/tiktok_comments.csv`.

Nếu Actor tìm thấy video nhưng thêm `0` bình luận, đọc phần thống kê cuối lệnh:

- `actor_items=0`: Actor comment không trả item;
- `empty_text>0`: cấu trúc item không có nội dung comment nhận diện được;
- `language_filtered>0`: bình luận bị loại vì bộ nhận diện không xem là tiếng
  Việt; có thể chạy một mẫu nhỏ với `--all-languages` để chẩn đoán;
- `duplicate>0`: bình luận đã tồn tại trong CSV.

`--search-suffix "Viet Nam"` chỉ giúp hướng kết quả về ngữ cảnh Việt Nam, không
đảm bảo mọi video hoặc bình luận là tiếng Việt.

### Cách thử nghiệm: TikTokApi

```powershell
python scripts/tiktok/crawl_tiktok.py --category "LGBTQ+" --max-queries 1 --max-videos 5 --comments-per-video 13 --headful
```

Luồng này không cần API key, nhưng TikTok có thể trả phản hồi rỗng do phát hiện
truy cập tự động. `--headful` không bảo đảm giải quyết được vấn đề và dự án
không triển khai kỹ thuật vượt CAPTCHA hoặc cơ chế chống bot.

## Reddit

Reddit dùng OAuth application credentials:

```powershell
python scripts/reddit/crawl_reddit.py --subreddit VietNam --max-posts 50 --comments-per-post 13 --max-comments 500 --delay 2
```

Kết quả được nối vào `data/raw/reddit_comments.csv`. Hãy kiểm tra quyền truy cập,
rate limit và điều khoản dữ liệu hiện hành của Reddit trước khi thu thập.

## VOZ

Tạo hàng đợi URL từ một hoặc nhiều snapshot HTML:

```powershell
python scripts/voz/build_thread_urls.py data/diem_bao.html --max-threads 50
```

Crawl các URL chưa có hậu tố `DONE`; crawler requests chỉ lấy trang đầu:

```powershell
python scripts/voz/crawl_voz_threads.py --delay-min 5 --delay-max 10
```

Kết quả được ghi vào `data/raw/voz_comments.csv` và
`data/raw/voz_comments.json`. Dòng URL chỉ được đánh dấu `DONE` sau khi cả hai
output đã được cập nhật thành công.

Nếu requests gặp trang xác minh, dùng Playwright:

```powershell
python scripts/voz/crawl_voz_playwright.py --target 20 --headful
```

CSV Playwright nằm tại `data/raw/voz_playwright_comments.csv`; checkpoint và
profile trình duyệt nằm trong `data/.runtime/voz_playwright/`. Chạy lại cùng
`--target` để tiếp tục. `--reset` sẽ xóa CSV Playwright và checkpoint nên chỉ
dùng khi thực sự muốn bắt đầu lại.

Tạo lại dataset VOZ:

```powershell
python scripts/voz/prepare_dataset.py
```

## Quy tắc output

Các crawler YouTube, TikTok và Reddit dùng cùng schema:

```text
platform,category,query_type,keyword,content_id,video_id,title,
comment_id,author,published_at,comment
```

Mọi CSV ở cấp bình luận, kể cả hai luồng VOZ, đều có cột `title` và `comment`.
Writer đa nền tảng:

- giữ dữ liệu cũ và ghi nối tiếp;
- bổ sung các cột chuẩn còn thiếu cho CSV tương thích;
- bỏ bản ghi trùng theo `(content_id hoặc video_id, comment_id)`;
- không ghi đè toàn bộ file khi chạy lại.

VOZ requests vẫn giữ schema lịch sử riêng để tương thích với dữ liệu đang có.
`prepare_dataset.py` chuyển schema này sang dataset dẫn xuất trong
`data/dataset/`.

## Kiểm tra và xử lý lỗi

```powershell
python -m compileall -q src scripts
python scripts/youtube/crawl_youtube.py --help
python scripts/tiktok/crawl_tiktok_apify.py --help
python scripts/reddit/crawl_reddit.py --help
```

- `commentsDisabled` trên YouTube: bình thường; crawler tiếp tục tìm video khác.
- TikTok `EmptyResponseException`: nền tảng từ chối phiên tự động; ưu tiên luồng
  Apify hoặc API được cấp quyền.
- Apify trả `0` comment: dựa vào thống kê chẩn đoán, không mặc định kết luận
  rằng video không có bình luận.
- CSV header không tương thích: sao lưu file rồi kiểm tra schema; writer chủ ý
  dừng để tránh làm hỏng dữ liệu.

## Giới hạn phương pháp và pháp lý

- Chỉ thu thập nội dung công khai và tuân thủ điều khoản, quota/rate limit,
  `robots.txt` và quy định bảo vệ dữ liệu của từng nền tảng.
- `langdetect` chỉ là ước lượng; câu ngắn, tiếng lóng và văn bản không dấu dễ bị
  phân loại sai.
- Search ranking, video tắt bình luận và lựa chọn keyword đều tạo thiên lệch
  mẫu. Dataset không đại diện cho toàn bộ người dùng Việt Nam.
- `author`, URL và nội dung bình luận có thể là dữ liệu cá nhân. Cần ẩn danh,
  giới hạn truy cập và xác định thời hạn lưu trước khi chia sẻ.

Xem ranh giới module và chính sách dữ liệu tại
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).
