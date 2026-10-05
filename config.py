"""Configuration for the FPT Play movie crawler (content-based movie search assignment)."""
from pathlib import Path

TOPIC = "Movies"
SOURCE_NAME = "fptplay"

SEED_URLS = [
    "https://fptplay.vn/",
]

ALLOWED_DOMAINS = [
    "fptplay.vn",
]

# ---------- Crawl limits ----------
MAX_DEPTH = 5          # seed = depth 0
MAX_PAGES = 1000        # every completed HTTP response counts as one crawled page
REQUEST_TIMEOUT = 10
CRAWL_DELAY = 1.5

# The crawler reads robots.txt dynamically before crawling each host.
RESPECT_ROBOTS_TXT = True
ROBOTS_FAIL_CLOSED = True

USER_AGENT = "MovieCrawler-FPTPlay/1.0 (+educational-assignment)"

# ---------- URL discovery flags ----------
# Many streaming sites render menus client-side, so plain <a href> BFS may find
# few links. When True, URLs listed in sitemaps that look like movie detail
# pages are also queued (at depth 1).
SEED_FROM_SITEMAP = True
SITEMAP_URLS = ["https://fptplay.vn/sitemap.xml"]
MAX_SITEMAP_FETCHES = 20     # safety cap on nested sitemap files per run

# A page is parsed as a movie ONLY if its URL matches this regex.
# Adjust after running:  python main.py --probe <movie-detail-url>
DETAIL_URL_REGEX = r"^https?://(www\.)?fptplay\.vn/(galaxy-play/)?xem-video/[^/?#]+/?$"
SKIP_URL_REGEX = r"(dang-nhap|dang-ky|thanh-toan|goi-cuoc)"

# ---------- Data-quality flags ----------
REQUIRE_TITLE = True        # no title    -> movie is not stored
REQUIRE_SYNOPSIS = True     # no synopsis -> movie is not stored
MIN_SYNOPSIS_LENGTH = 20    # shorter synopsis is treated as missing
DEDUP_BY_SYNOPSIS = True    # also drop a movie whose synopsis was already stored

# ---------- Storage ----------
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DATABASE_PATH = DATA_DIR / "movies_fptplay.db"

# Rebuild the database on each run so the summary and DB describe one clean run.
RESET_DATABASE_ON_START = True

# Non-HTML resources that should normally not be crawled.
BLOCKED_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".gif", ".svg", ".webp", ".ico",
    ".css", ".js", ".mjs", ".zip", ".rar", ".7z", ".tar", ".gz",
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".mp3", ".wav", ".mp4", ".avi", ".mov", ".wmv", ".webm", ".m3u8",
    ".xml", ".rss", ".atom",
}
