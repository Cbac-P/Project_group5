"""python main.py crawl | report | export | reparse"""

import argparse
import sys
from pathlib import Path

from config import Config
from crawler import Crawler
from database import connect, export_all, print_summary, save_movie, stats
from parser import parse_page


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


def main():
    parser = argparse.ArgumentParser(description="Focused BFS crawler và dataset phim tiếng Việt")
    parser.add_argument("command", choices=["crawl", "report", "export", "reparse"])
    parser.add_argument("--db", type=Path, default=Path("data/crawler.db"))
    parser.add_argument("--output", type=Path, default=Path("data"))
    parser.add_argument("--max-pages", type=int)
    parser.add_argument("--per-domain", type=int, dest="max_pages_per_domain")
    parser.add_argument("--max-depth", type=int)
    parser.add_argument("--delay", type=float)
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
