"""SQLite: lưu trang / liên kết / phim, tính thống kê và xuất CSV."""

import csv
import json
import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS pages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    url TEXT UNIQUE NOT NULL, domain TEXT, title TEXT, content TEXT,
    depth INTEGER, status_code INTEGER, crawled_at TEXT,
    final_url TEXT, page_type TEXT, crawl_status TEXT,
    response_time REAL, error TEXT, html_path TEXT
);
CREATE TABLE IF NOT EXISTS links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_url TEXT NOT NULL, target_url TEXT NOT NULL,
    accepted INTEGER, skip_reason TEXT,
    UNIQUE (source_url, target_url)
);
CREATE TABLE IF NOT EXISTS movies (
    source_url TEXT PRIMARY KEY, source TEXT, title TEXT, data TEXT
);

CREATE TABLE IF NOT EXISTS movies_clean (
    source TEXT NOT NULL,
    source_url TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    release_year INTEGER,
    synopsis TEXT NOT NULL,
    genre TEXT NOT NULL,
    director TEXT NOT NULL,
    "cast" TEXT NOT NULL,
    country TEXT NOT NULL
);

"""

SOURCES = ("w2w",)
MOVIE_COLUMNS = [
    "source", "source_url", "title", "release_year", "synopsis",
    "genre", "director", "cast", "country",
]


def connect(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.executescript(SCHEMA)
    return db


def save_movie(db, movie):
    db.execute("INSERT OR REPLACE INTO movies VALUES (?,?,?,?)",
               (movie["source_url"], movie["source"], movie["title"],
                json.dumps(movie, ensure_ascii=False)))


def save_page(db, page, links, movie=None):
    """Một trang = một transaction: trang, liên kết và phim cùng được lưu hoặc cùng không."""
    with db:
        db.execute(f"INSERT INTO pages ({','.join(page)}) VALUES ({','.join('?' * len(page))})",
                   list(page.values()))
        db.executemany("INSERT OR IGNORE INTO links (source_url, target_url, accepted, skip_reason) "
                       "VALUES (?,?,?,?)", links)
        if movie:
            save_movie(db, movie)

def save_clean(db, movies):
    """Ghi lại bảng movies_clean: đúng 9 trường của schema chung, list lưu dạng JSON."""
    rows = [(m["source"], m["source_url"], m["title"], m["release_year"], m["synopsis"],
             *(json.dumps(m[k], ensure_ascii=False) for k in ("genre", "director", "cast", "country")))
            for m in movies]
    with db:
        db.execute("DELETE FROM movies_clean")
        db.executemany(
            'INSERT INTO movies_clean (source, source_url, title, release_year, synopsis, '
            'genre, director, "cast", country) VALUES (?,?,?,?,?,?,?,?,?)', rows)


def count_by(db, table, column, where="1"):
    sql = f"SELECT {column}, COUNT(*) FROM {table} WHERE {where} GROUP BY {column}"
    return {str(key): n for key, n in db.execute(sql)}


def stats(db, config):
    """Mọi con số đều tính từ dữ liệu đã lưu, không nhập tay."""
    one = lambda sql: db.execute(sql).fetchone()[0]
    by_depth = {str(d): 0 for d in range(config.max_depth + 1)}
    by_depth.update(count_by(db, "pages", "depth"))
    return {
        "topic": config.topic,
        "seed_urls": one("SELECT COUNT(*) FROM pages WHERE depth = 0"),
        "pages_crawled": one("SELECT COUNT(*) FROM pages"),
        "unique_urls_discovered": one("SELECT COUNT(*) FROM "
                                      "(SELECT url FROM pages UNION SELECT target_url FROM links)"),
        # "Skipped" = URL bị bộ lọc loại; link trùng chỉ là link lặp lại, không tính.
        "skipped_urls": one("SELECT COUNT(DISTINCT target_url) FROM links "
                            "WHERE accepted = 0 AND skip_reason != 'duplicate' "
                            "AND target_url NOT IN (SELECT url FROM pages)"),
        "failed_requests": one("SELECT COUNT(*) FROM pages WHERE crawl_status != 'ok'"),
        "max_depth": config.max_depth,
        "by_depth": by_depth,
        "by_status_code": count_by(db, "pages", "COALESCE(status_code, 'no_response')"),
        "by_crawl_status": count_by(db, "pages", "crawl_status"),
        "by_domain": count_by(db, "pages", "domain"),
        "skip_reasons": count_by(db, "links", "skip_reason", "accepted = 0"),
        "movies": count_by(db, "movies", "source"),
    }

 
def print_summary(s):
    row = lambda label, value: print(f"{label:<24}: {value}")
    print("\n========== CRAWLING SUMMARY ==========")
    row("Topic", s["topic"])
    row("Seed URLs", s["seed_urls"])
    row("Pages Crawled", s["pages_crawled"])
    row("Unique URLs Discovered", s["unique_urls_discovered"])
    row("Skipped URLs", s["skipped_urls"])
    row("Failed Requests", s["failed_requests"])
    row("Maximum Depth", s["max_depth"])
    for depth, n in s["by_depth"].items():
        row(f"Depth {depth}", f"{n} pages")
    for code, n in s["by_status_code"].items():
        row(f"HTTP {code}", n)
    for source, n in s["movies"].items():
        row(f"Movies ({source})", n)
    print("=" * 38)


def write_csv(path, columns, rows):
    # utf-8-sig để Excel đọc đúng tiếng Việt; list/dict được ghi dưới dạng JSON.
    with Path(path).open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({k: json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else v
                             for k, v in row.items()})


def export_all(db, config):
    out = Path(config.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    movies = [json.loads(r[0]) for r in db.execute("SELECT data FROM movies ORDER BY source, source_url")]
    for source in SOURCES:
        write_csv(out / f"{source}_movies_raw.csv", MOVIE_COLUMNS, [m for m in movies if m["source"] == source])
    # Phim có cốt truyện và đã/đang chiếu: dùng làm tập mô tả.
    usable = [m for m in movies if m["synopsis"] and m["release_status"] != "upcoming"]
    
    save_clean(db, usable)

    write_csv(out / "movies_with_synopsis.csv", MOVIE_COLUMNS, usable)
    summary = stats(db, config)
    summary["movies_with_synopsis"] = len(usable)
    (out / "crawl_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary
