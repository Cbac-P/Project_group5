"""Entry point for the FPT Play movie crawler.

  python main.py                       # crawl from the seed URL (BFS, MAX_DEPTH / MAX_PAGES)
  python main.py --probe <movie-url>   # diagnose ONE page: what does each extraction strategy see?
  python main.py --analyze             # data-quality report of the saved database
  python main.py --export-csv out.csv  # export movies to CSV
"""
import argparse
import json
import sys
import re

# Windows consoles often default to a legacy code page that cannot encode every
# Vietnamese character in a movie title. Force UTF-8 so one odd character never
# crashes a long crawl.
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from pathlib import Path

import requests

import config
import parser as movie_parser
from crawler import MovieCrawler
from database import MovieDatabase


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="FPT Play movie crawler.")
    ap.add_argument("--probe", metavar="URL", help="fetch one page and print what each strategy extracts")
    ap.add_argument("--analyze", action="store_true", help="print data-quality report of the database")
    ap.add_argument("--export-csv", metavar="PATH", help="export movies table to CSV")
    ap.add_argument("--max-pages", type=int, default=None, help="override config.MAX_PAGES")
    ap.add_argument("--max-depth", type=int, default=None, help="override config.MAX_DEPTH")
    ap.add_argument("--keep-db", action="store_true",
                    help="do not reset the database at start (override RESET_DATABASE_ON_START)")
    return ap.parse_args()


def probe(url: str) -> None:
    try:
        response = requests.get(
            url,
            headers={"User-Agent": config.USER_AGENT, "Accept-Language": "vi,en;q=0.8"},
            timeout=config.REQUEST_TIMEOUT,
        )
    except requests.RequestException as exc:
        print(f"Request failed: {exc}")
        return

    print(f"HTTP {response.status_code} | Content-Type: {response.headers.get('Content-Type')}")
    print(f"HTML length : {len(response.text)}")
    print(f"Has JSON-LD : {'application/ld+json' in response.text}")
    print(f"Has __NEXT_DATA__ : {'__NEXT_DATA__' in response.text}")
    print(f"Matches DETAIL_URL_REGEX : {movie_parser.is_detail_url(movie_parser.normalize_url(response.url))}")

    soup = movie_parser.parse_html(response.text)
    for name, rec in movie_parser.parse_all_strategies(soup).items():
        print(f"\n== {name} ==")
        print(json.dumps(rec, ensure_ascii=False, indent=2) if rec else "(empty)")

    record, reason = movie_parser.extract_movie_information(soup, url)
    print("\n== MERGED RESULT ==")
    print(json.dumps(record, ensure_ascii=False, indent=2) if record else f"Rejected: {reason}")

    links = movie_parser.extract_links(response.url, soup)
    print(f"\nLinks found in HTML: {len(links)}")
    for link in links[:10]:
        print("  ", link)



def write_summary_file(summary: dict, path: Path) -> None:
    lines = [
        "=" * 46,
        " CRAWLING SUMMARY",
        "=" * 46,
        f"Topic                  : {summary['topic']}",
        f"Seed URLs              : {summary['seed_urls']}",
        f"Pages Crawled          : {summary['pages_crawled']}",
        f"Unique URLs Discovered : {summary['unique_urls_discovered']}",
        f"Skipped URLs           : {summary['skipped_urls']}",
        f"Failed Requests        : {summary['failed_requests']}",
        f"Maximum Depth          : {summary['maximum_depth']}",
        f"Movie Detail Pages     : {summary['detail_pages']}",
        f"Movies Saved           : {summary['movies_saved']}",
    ]
    for depth, count in summary["depth_counts"].items():
        lines.append(f"Depth {depth:<2}                : {count}")
    for status, count in summary["status_counts"].items():
        lines.append(f"HTTP {status:<3}               : {count}")
    lines.append("")
    lines.append("Skip reasons breakdown:")
    for reason, count in summary["skip_reasons"].items():
        lines.append(f"  {reason:<24}: {count}")
    lines.append(f"Frontier Remaining     : {summary['frontier_remaining']}")
    lines.append("=" * 46)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    args = parse_args()

    if args.probe:
        probe(args.probe)
        return

    if args.analyze or args.export_csv:
        db = MovieDatabase(config.DATABASE_PATH, reset=False)
        try:
            if args.analyze:
                print(db.quality_report())
            if args.export_csv:
                print(f"Exported {db.export_csv(Path(args.export_csv))} rows to {args.export_csv}")
        finally:
            db.close()
        return

    if args.max_pages is not None:
        config.MAX_PAGES = args.max_pages
    if args.max_depth is not None:
        config.MAX_DEPTH = args.max_depth
    if args.keep_db:
        config.RESET_DATABASE_ON_START = False

    summary_path = Path(config.DATA_DIR) / "crawl_summary_fptplay.txt"
    crawler = MovieCrawler()
    summary = crawler.run()
    write_summary_file(summary, summary_path)

    print(f"Database saved to : {config.DATABASE_PATH}")
    print(f"Summary saved to  : {summary_path}")


if __name__ == "__main__":
    main()
