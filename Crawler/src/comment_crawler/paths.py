"""Repository paths used by crawlers and command-line entry points."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = ROOT / "config"
DATA_DIR = ROOT / "data"
FACEBOOK_DATA_DIR = DATA_DIR / "facebook"
THREAD_CITY_DATA_DIR = DATA_DIR / "thread_city"
TIKTOK_DATA_DIR = DATA_DIR / "tiktok"
VOZ_DATA_DIR = DATA_DIR / "voz"
YOUTUBE_DATA_DIR = DATA_DIR / "youtube"
REDDIT_DATA_DIR = DATA_DIR / "reddit"
DATASET_DIR = VOZ_DATA_DIR / "dataset"
RUNTIME_DIR = DATA_DIR / ".runtime"
DEFAULT_KEYWORDS_FILE = CONFIG_DIR / "keywords_vi.json"

VOZ_THREAD_URLS_FILE = VOZ_DATA_DIR / "thread_urls.txt"
VOZ_COMMENTS_PREFIX = VOZ_DATA_DIR / "voz_comments"
VOZ_COMMENTS_CSV = VOZ_COMMENTS_PREFIX.with_suffix(".csv")
VOZ_COMMENTS_JSON = VOZ_COMMENTS_PREFIX.with_suffix(".json")
VOZ_PLAYWRIGHT_RUNTIME_DIR = RUNTIME_DIR / "voz_playwright"
VOZ_PLAYWRIGHT_COMMENTS_CSV = VOZ_DATA_DIR / "voz_playwright_comments.csv"

YOUTUBE_COMMENTS_CSV = YOUTUBE_DATA_DIR / "youtube_comments_keywords.csv"
TIKTOK_COMMENTS_CSV = TIKTOK_DATA_DIR / "tiktok_comments.csv"
REDDIT_COMMENTS_CSV = REDDIT_DATA_DIR / "reddit_comments.csv"
