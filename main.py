"""python main.py crawl | report | export | reparse"""

import argparse
import sys
from pathlib import Path
from config import Config

from crawler import Crawler
from database import connect, export_all, print_summary, save_movie, stats
from parser import parse_page

import re
import time
from urllib.parse import urlsplit

import requests

from url_rules import W2W_DETAIL, normalize_url, url_reason

def show_config(config):
    print("========== CRAWLER CONFIGURATION ==========\n")
    print(f"Topic           : {config.topic}")
    print(f"Seed URLs       : {len(config.seeds)}")
    print("Allowed Domains :")
    for domain in config.allowed_domains:
        print(f"    - {domain}")
    print(f"\nMaximum Depth   : {config.max_depth}")
    print(f"Maximum Pages   : {config.max_pages} (tối đa {config.max_pages_per_domain} mỗi domain)")
    print(f"Request Timeout : {config.timeout} seconds")
    print(f"Crawl Delay     : {config.delay} seconds")


def reparse(db):
    """Parse lại từ HTML đã lưu (không gọi mạng) sau khi sửa parser.py."""
    count = 0
    with db:
        for row in list(db.execute("SELECT * FROM pages WHERE html_path IS NOT NULL")):
            parsed = parse_page(Path(row["html_path"]).read_bytes(), row["final_url"], row["crawled_at"])
            db.execute("UPDATE pages SET title=?, content=?, page_type=?, error=?, crawl_status='ok' WHERE url=?",
                       (parsed["title"], parsed["content"], parsed["page_type"], parsed["parse_error"], row["url"]))
            db.execute("DELETE FROM movies WHERE source_url=?", (row["final_url"],))
            if parsed["movie"]:
                save_movie(db, parsed["movie"])
                count += 1
    print(f"Đã parse lại {count} phim từ HTML đã lưu.")


def sitemap_seeds(config, limit):
    """Đọc sitemap phim của w2w.vn, chọn đều `limit` URL chi tiết phim."""
    session = requests.Session()
    session.trust_env = False
    session.headers["User-Agent"] = config.user_agent
    locs = []
    for n in range(1, 5):   # phim-sitemap1.xml ... phim-sitemap4.xml
        resp = session.get(f"https://w2w.vn/phim-sitemap{n}.xml", timeout=config.timeout)
        resp.raise_for_status()
        locs += re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", resp.text)
        time.sleep(config.delay)
    films = []
    for loc in locs:
        url = normalize_url(loc)
        if url and url_reason(url, config.allowed_domains) is None and W2W_DETAIL.fullmatch(urlsplit(url).path):
            films.append(url)
    films = list(dict.fromkeys(films))
    print(f"Sitemap có {len(films)} URL phim; chọn đều {min(limit, len(films))} URL.")
    step = len(films) / limit
    return [films[int(i * step)] for i in range(min(limit, len(films)))]


def main():
    parser = argparse.ArgumentParser(description="Focused BFS crawler và dataset phim tiếng Việt")
    parser.add_argument("command", choices=["crawl", "report", "export", "reparse"])
    parser.add_argument("--db", type=Path, default=Path("data/crawler.db"))
    parser.add_argument("--output", type=Path, default=Path("data"))
    parser.add_argument("--max-pages", type=int)
    parser.add_argument("--per-domain", type=int, dest="max_pages_per_domain")
    parser.add_argument("--max-depth", type=int)
    parser.add_argument("--delay", type=float)
    parser.add_argument("--films", type=int, help="số phim cần cào (lấy mẫu đều từ sitemap)")
    parser.add_argument("--listing-pages", type=int, dest="listing_pages")
    parser.add_argument("--timeout", type=float)
    args = parser.parse_args()

    sys.stdout.reconfigure(encoding="utf-8")   # console Windows mặc định không in được tiếng Việt
    config = Config(db_path=args.db, output_dir=args.output)
    for name in ("max_pages", "max_pages_per_domain", "max_depth", "delay", "timeout"):
        if getattr(args, name) is not None:
            setattr(config, name, getattr(args, name))



    if args.command != "crawl" and not args.db.exists():
        parser.error("Chưa có database; hãy chạy crawl trước.")
    db = connect(args.db)
    if args.command == "crawl":
        if db.execute("SELECT 1 FROM pages").fetchone():
            parser.error(f"{args.db} đã có dữ liệu. Dùng --db và --output mới, hoặc xóa thư mục data để crawl lại.")
        if args.films:
            config.seeds = sitemap_seeds(config, args.films)
            config.max_pages = config.max_pages_per_domain = args.films
        show_config(config)
        print("\nStop reason:", Crawler(config, db).run())
    elif args.command == "reparse":
        reparse(db)
    if args.command in ("crawl", "export", "reparse"):
        summary = export_all(db, config)
    else:
        summary = stats(db, config)
    print_summary(summary)
    db.close()


if __name__ == "__main__":
    main()
