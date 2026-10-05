"""Cấu hình crawler: đổi seed, domain và giới hạn ở đây, không cần sửa logic."""

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Config:
    topic: str = "Movies & Entertainment"
    seeds: list = field(default_factory=lambda: [
        "https://tv360.vn/movies",
    ])
    allowed_domains: list = field(default_factory=lambda: ["tv360.vn"])
    sitemaps: list = field(default_factory=lambda: ["https://tv360.vn/sitemap.xml"])
    sitemap_limit: int = 1500    # số URL tối đa lấy từ sitemap
    max_depth: int = 3
    max_pages: int = 60
    max_pages_per_domain: int = 30
    timeout: float = 15      # giây
    delay: float = 2         # giây giữa hai request tới cùng một host
    user_agent: str = "VietnameseFilmCrawler/1.0 (educational HTML pilot)"
    db_path: Path = Path("data/crawler.db")
    output_dir: Path = Path("data")
