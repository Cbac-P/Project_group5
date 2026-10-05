"""Cấu hình crawler: đổi seed, domain và giới hạn ở đây, không cần sửa logic."""

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Config:
    topic: str = "Movies & Entertainment"
    seeds: list = field(default_factory=lambda: ["https://w2w.vn/phim/"])   # main.py ghi đè bằng URL từ sitemap
    allowed_domains: list = field(default_factory=lambda: ["w2w.vn"])
    max_depth: int = 0       # seed đã là trang chi tiết phim, không đi tiếp theo link
    max_pages: int = 450
    max_pages_per_domain: int = 450
    timeout: float = 15      # giây
    delay: float = 2         # giây giữa hai request tới cùng một host
    user_agent: str = "VietnameseFilmCrawler/1.0 (educational HTML pilot)"
    db_path: Path = Path("data/crawler.db")
    output_dir: Path = Path("data")