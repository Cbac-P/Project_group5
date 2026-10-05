"""Audit offline: DB, BFS, scope/robots, HTML cache và dataset 9 trường."""
import csv
import json
import sqlite3
from collections import Counter
from contextlib import closing
from pathlib import Path
from urllib.parse import urlsplit
from database import read_movies, summary
from dataset import FIELDS, read_dataset, validate_movie
from parser import parse_page
from url_rules import RobotsRules, url_reason

# Audit = kiểm tra các bằng chứng khớp nhau; không sửa phim và không tải website.
def verify(path, output):  # Đọc DB, cache HTML, JSONL/CSV và summary để tạo báo cáo đạt/chưa đạt.
    with closing(sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)) as db:  # mode=ro mở SQLite chỉ đọc; closing đóng kết nối khi hoàn tất.
        db.row_factory = sqlite3.Row
        config = json.loads(db.execute("SELECT value FROM metadata WHERE key='config'").fetchone()[0])
        pages = list(db.execute("SELECT * FROM pages ORDER BY id"))
        links = list(db.execute("SELECT * FROM links"))
        frontier = {r["url"]: dict(r) for r in db.execute("SELECT * FROM frontier")}
        requests = list(db.execute("SELECT * FROM requests_log ORDER BY id"))
        movies = read_movies(db)
        page_urls = {p["url"] for p in pages}
        checks = {"sqlite_integrity": db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"}  # Kiểm tra file SQLite có lỗi cấu trúc hay không.
        for table, needed in {"pages": {"id", "url", "domain", "title", "content", "depth", "status_code", "crawled_at"},
                              "links": {"id", "source_url", "target_url"}}.items():
            checks[f"schema_{table}"] = needed <= {r[1] for r in db.execute(f"PRAGMA table_info({table})")}
        checks["movies_table_exact_nine_fields"] = set(FIELDS) == {r[1] for r in db.execute("PRAGMA table_info(movies)")}  # Bảng movies phải đúng 9 cột của schema chung.
        checks["single_xemanime_scope"] = config["allowed_domains"] == ["xemanime.org"] and {p["domain"] for p in pages} <= {"xemanime.org"}  # Bản cá nhân chỉ được có một domain xemanime.org.
        checks["nonempty_successful_pages"] = bool(pages) and all(p["title"] and p["content"] and p["crawled_at"] for p in pages if p["crawl_status"] == "ok")
        checks["page_caps_and_depth"] = len(pages) <= config["max_pages"] and all(p["depth"] <= config["max_depth"] for p in pages)  # Giới hạn trang và depth được đối chiếu với cấu hình đã lưu.
        checks["pages_unique"] = len(page_urls) == len(pages)
        checks["bfs_depth_order"] = all(a["depth"] <= b["depth"] for a, b in zip(pages, pages[1:]))  # BFS: trang lớp sau không được xuất hiện trước trang lớp gần seed.
        checks["frontier_parent_depth"] = all(r["depth"] == frontier[r["discovered_from"]]["depth"] + 1 if r["discovered_from"] else r["depth"] == 0 for r in frontier.values())  # URL con có depth cha + 1; seed không có cha thì depth 0.
        checks["unique_links_with_saved_source"] = len({(r["source_url"], r["target_url"]) for r in links}) == len(links) and all(r["source_url"] in page_urls for r in links)
        checks["request_scope"] = all(urlsplit(r["url"]).hostname == "xemanime.org" and (r["request_kind"] == "robots" or url_reason(r["url"], config["allowed_domains"]) is None) for r in requests)  # Request thật phải đúng scope; robots là trường hợp được phép riêng.
        robots = {urlsplit(r["origin"]).hostname: RobotsRules(r["body"] or "", config["user_agent"]) for r in db.execute("SELECT * FROM robots WHERE status_code IN (200,404,410)")}
        checks["html_requests_respect_saved_robots"] = all(urlsplit(r["url"]).hostname in robots and robots[urlsplit(r["url"]).hostname].allows(r["url"]) for r in requests if r["request_kind"] == "html")  # Đối chiếu HTML request với snapshot robots đã lưu, không đọc robots mới trên mạng.
        checks["cached_successful_html"] = all(p["html_path"] and Path(p["html_path"]).is_file() for p in pages if p["crawl_status"] == "ok")
        checks["schema_valid_movies"] = bool(movies) and all(validate_movie(m) for m in movies)
        extracted = {}
        for page in pages:  # Parse lại HTML cache để so với movies; không cào lại nguồn.
            if page["html_path"] and Path(page["html_path"]).exists():
                record = parse_page(Path(page["html_path"]).read_bytes(), page["final_url"], page["crawled_at"])["movie"]
                if record:
                    extracted[record["source_url"]] = record
        checks["movies_match_cached_html"] = {m["source_url"]: m for m in movies} == extracted
        output = Path(output)
        checks["jsonl_equals_db"] = read_dataset(output / "movies.jsonl") == movies  # Bản JSONL nộp nhóm phải khớp dữ liệu đọc từ SQLite.
        with (output / "movies.csv").open(encoding="utf-8-sig", newline="") as file:
            reader = csv.DictReader(file)
            rows = list(reader)
            checks["csv_exact_nine_columns"] = reader.fieldnames == FIELDS
        recovered = []
        for row in rows:
            row["release_year"] = int(row["release_year"]) if row["release_year"] else None  # Khôi phục kiểu năm/list từ CSV trước khi so với DB.
            for key in ("genre", "country", "director", "cast"):
                row[key] = json.loads(row[key])
            recovered.append(row)
        checks["csv_equals_db"] = recovered == movies
        stats = summary(db)
        checks["summary_equals_db"] = json.loads((output / "crawl_summary.json").read_text(encoding="utf-8")) == stats  # Số liệu crawl_summary.json phải đúng SQL trên DB.
        html_hits = [r["url"] for r in requests if r["request_kind"] == "html"]
        checks["no_repeated_html_requests"] = len(html_hits) == len(set(html_hits))  # Không có cùng một URL HTML bị request nhiều lần trong log này.
        checks["movie_origin_successful_page"] = {m["source_url"] for m in movies} <= {p["final_url"] for p in pages if p["crawl_status"] == "ok" and p["page_type"] == "movie"}  # Mỗi phim phải có một trang tải thành công và được parser nhận là movie.
        result = dict(checks=checks, all_passed=all(checks.values()), passed=sum(checks.values()), total=len(checks),  # Tất cả tiêu chí True mới all_passed=True; số phim là một chỉ số riêng.
                      pages=len(pages), movies=len(movies), by_depth=dict(Counter(p["depth"] for p in pages)))
    (output / "verification_report.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")  # Chỉ ghi file báo cáo; kết nối DB phía trên đã đóng.
    return result
