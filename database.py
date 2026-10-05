"""SQLite bắt buộc pages/links, frontier và dataset phim đúng chín cột."""
import csv
import json
import sqlite3
from pathlib import Path
from dataset import FIELDS, validate_movie
# 7 bảng: pages (trang), links (cạnh), frontier (queue), movies (phim), requests_log, robots, metadata.
SCHEMA = """
CREATE TABLE IF NOT EXISTS pages (
 id INTEGER PRIMARY KEY, url TEXT UNIQUE NOT NULL, domain TEXT NOT NULL,
 title TEXT, content TEXT, depth INTEGER NOT NULL, status_code INTEGER,
 crawled_at TEXT NOT NULL, final_url TEXT, page_type TEXT,
 crawl_status TEXT NOT NULL, response_time REAL, error TEXT, html_path TEXT
);
CREATE TABLE IF NOT EXISTS links (
 id INTEGER PRIMARY KEY, source_url TEXT NOT NULL, target_url TEXT NOT NULL,
 accepted INTEGER NOT NULL, skip_reason TEXT, UNIQUE(source_url,target_url)
);
CREATE TABLE IF NOT EXISTS frontier (
 id INTEGER PRIMARY KEY, url TEXT UNIQUE NOT NULL, depth INTEGER NOT NULL,
 discovered_from TEXT, state TEXT NOT NULL DEFAULT 'pending'
);
CREATE TABLE IF NOT EXISTS movies (
 source TEXT NOT NULL, source_url TEXT PRIMARY KEY, title TEXT NOT NULL,
 release_year INTEGER, synopsis TEXT NOT NULL, genre TEXT NOT NULL,
 country TEXT NOT NULL, director TEXT NOT NULL, cast TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS requests_log (
 id INTEGER PRIMARY KEY, url TEXT NOT NULL, request_kind TEXT NOT NULL,
 status_code INTEGER, requested_at TEXT NOT NULL, response_time REAL,
 bytes INTEGER, error TEXT
);
CREATE TABLE IF NOT EXISTS robots (
 origin TEXT PRIMARY KEY, url TEXT NOT NULL, status_code INTEGER,
 checked_at TEXT NOT NULL, body TEXT, error TEXT
);
CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""
# pages lưu mọi trang đã xử lý; movies chỉ lưu phim hợp lệ nên hai bảng không bằng số dòng.
# UNIQUE/PRIMARY KEY chặn trùng; các list phim được lưu thành TEXT chứa JSON.
def connect(path):  # Tạo thư mục/DB/bảng nếu chưa có, rồi trả kết nối SQLite.
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row  # Đọc hàng theo tên cột, ví dụ row['url'], dễ hiểu hơn chỉ số.
    db.execute("PRAGMA journal_mode=WAL")  # WAL hỗ trợ đọc/ghi; kết quả vẫn phải commit để hoàn tất giao dịch.
    db.executescript(SCHEMA)
    return db


def enqueue(db, url, depth, source=None):  # Thêm URL vào queue DB; URL trùng bị bỏ qua, trả True khi thêm mới.
    return db.execute("INSERT OR IGNORE INTO frontier(url,depth,discovered_from) VALUES(?,?,?)",
                      (url, depth, source)).rowcount == 1


def save_movie(db, movie):  # Kiểm tra 9 trường rồi lưu phim theo khóa source_url.
    validate_movie(movie)
    values = [json.dumps(movie[k], ensure_ascii=False) if isinstance(movie[k], list) else movie[k] for k in FIELDS]  # SQLite không lưu list trực tiếp: đổi list thành chuỗi JSON.
    db.execute(f"INSERT OR REPLACE INTO movies({','.join(FIELDS)}) VALUES({','.join('?' for _ in FIELDS)})", values)


def save_page(db, record, movie=None):  # Lưu trang luôn; chỉ lưu phim khi movie có dữ liệu; đánh dấu frontier done.
    columns = list(record)
    db.execute(f"INSERT INTO pages({','.join(columns)}) VALUES({','.join('?' for _ in columns)})", list(record.values()))
    if movie:  # movie=None thì chỉ có pages, không có thêm dòng movies.
        save_movie(db, movie)
    db.execute("UPDATE frontier SET state='done' WHERE url=?", (record["url"],))


def request_log(db, url, kind, status, now, elapsed, size=0, error=None):  # Log từng request, gồm cả robots; không đồng nghĩa một request = một phim.
    db.execute("INSERT INTO requests_log(url,request_kind,status_code,requested_at,response_time,bytes,error) VALUES(?,?,?,?,?,?,?)",
               (url, kind, status, now, elapsed, size, error))
    db.commit()


def read_movies(db):  # Đọc phim và đổi các chuỗi JSON trong DB trở lại list Python.
    result = []
    for row in db.execute("SELECT * FROM movies ORDER BY source_url"):
        movie = dict(row)
        for key in ("genre", "country", "director", "cast"):
            movie[key] = json.loads(movie[key])
        result.append(movie)
    return result


def grouped(db, table, column, where="1=1"):  # GROUP BY đếm số dòng theo depth, status hoặc lý do loại.
    return {str(r[0]): r[1] for r in db.execute(f"SELECT {column},COUNT(*) FROM {table} WHERE {where} GROUP BY {column}")}


def summary(db):  # Báo cáo tính từ DB thật; không tự nhập con số 100 hay 41.
    one = lambda sql: db.execute(sql).fetchone()[0]
    config_row = db.execute("SELECT value FROM metadata WHERE key='config'").fetchone()
    config = json.loads(config_row[0]) if config_row else {}
    depths = {str(depth): 0 for depth in range(config.get("max_depth", 0) + 1)}  # Hiển thị cả lớp depth có 0 trang để phân biệt trần với độ sâu đã đi tới.
    depths.update(grouped(db, "pages", "depth"))
    return dict(topic="Movies & Entertainment", source="xemanime", member=config.get("member", ""),
                seed_urls=config.get("seeds", []), seed_count=len(config.get("seeds", [])), config=config,
                pages_crawled=one("SELECT COUNT(*) FROM pages"),  # Số TRANG đã xử lý, gồm danh mục và trang bị loại khỏi movies.
                html_success=one("SELECT COUNT(*) FROM pages WHERE crawl_status='ok'"),
                failed_requests=one("SELECT COUNT(*) FROM pages WHERE crawl_status IN ('http_error','request_error')"),
                unique_urls_discovered=one("SELECT COUNT(*) FROM (SELECT url FROM frontier UNION SELECT target_url FROM links)"),  # UNION đếm URL duy nhất, khác tổng số cạnh links.
                skipped_urls=one("SELECT COUNT(DISTINCT target_url) FROM links WHERE accepted=0"),  # Có thể giao với pages: một URL đã cào vẫn bị skip ở cạnh trùng khác.
                skip_reasons=grouped(db, "links", "skip_reason", "accepted=0"),
                links=one("SELECT COUNT(*) FROM links"), pending_urls=one("SELECT COUNT(*) FROM frontier WHERE state='pending'"),
                max_depth=config.get("max_depth", 0), max_depth_observed=one("SELECT COALESCE(MAX(depth),0) FROM pages"),  # Trần độ sâu và độ sâu thực tế là hai con số khác nhau.
                by_depth=depths, by_domain=grouped(db, "pages", "domain"),
                by_status_code=grouped(db, "pages", "COALESCE(status_code,'no_response')"),
                by_crawl_status=grouped(db, "pages", "crawl_status"), by_page_type=grouped(db, "pages", "page_type"),
                extraction_exclusions=grouped(db, "pages", "error", "error IS NOT NULL AND crawl_status='ok'"),
                http_requests=one("SELECT COUNT(*) FROM requests_log"), robots_requests=one("SELECT COUNT(*) FROM requests_log WHERE request_kind='robots'"),
                movies_raw=one("SELECT COUNT(*) FROM movies"),  # Số PHIM hợp lệ; không bị buộc phải bằng max_pages.
                movies_with_synopsis=one("SELECT COUNT(*) FROM movies WHERE synopsis != ''"),
                last_stop_reason=one("SELECT COALESCE((SELECT value FROM metadata WHERE key='last_stop_reason'),'')"))


def write_csv(path, columns, rows):  # Xuất CSV UTF-8 BOM để Excel đọc tiếng Việt; list trong ô vẫn là JSON.
    with Path(path).open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({k: json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else v for k, v in dict(row).items()})


def export_all(db, directory):  # Xuất dataset 9 trường và các báo cáo từ DB; không gửi request website.
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    movies = read_movies(db)
    with (directory / "movies.jsonl").open("w", encoding="utf-8") as file:
        for movie in movies:
            validate_movie(movie)
            file.write(json.dumps(movie, ensure_ascii=False) + "\n")  # JSONL: mỗi dòng là một object phim đầy đủ 9 trường.
    write_csv(directory / "movies.csv", FIELDS, movies)
    write_csv(directory / "movies_with_synopsis.csv", FIELDS, [m for m in movies if m["synopsis"]])  # Bản phụ chỉ giữ phim có cốt truyện; bản chính vẫn giữ phim thiếu mô tả.
    write_csv(directory / "crawl_log.csv", [r[1] for r in db.execute("PRAGMA table_info(pages)")], db.execute("SELECT * FROM pages ORDER BY id"))
    write_csv(directory / "links.csv", ["source_url", "target_url", "accepted", "skip_reason"], db.execute("SELECT * FROM links ORDER BY id"))
    write_csv(directory / "requests_log.csv", ["url", "request_kind", "status_code", "requested_at", "response_time", "bytes", "error"], db.execute("SELECT * FROM requests_log ORDER BY id"))
    stats = summary(db)
    (directory / "crawl_summary.json").write_text(json.dumps(stats, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    quality = {k: sum(not m[k] for m in movies) for k in ("release_year", "synopsis", "genre", "country", "director", "cast")}  # Thống kê trường còn thiếu; không bù thông tin bằng cách tự đoán.
    (directory / "quality_report.json").write_text(json.dumps(dict(total=len(movies), missing=quality,
                                                                 short_synopsis=sum(0 < len(m["synopsis"]) < 200 for m in movies)), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return stats
