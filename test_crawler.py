"""Chạy: python -m unittest discover -s tests -v
Crawl thử trên server HTTP cục bộ, không gọi website thật."""

import contextlib
import csv
import io
import json
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import Config
from crawler import Crawler
from database import connect, export_all, stats
from parser import parse_page
from url_rules import Robots, normalize_url, url_reason

NOW = "2026-10-04T00:00:00+00:00"


class Handler(BaseHTTPRequestHandler):
    routes, hits = {}, []   # path -> (status, headers, body, pause)

    def do_GET(self):
        type(self).hits.append(self.path)
        status, headers, body, pause = self.routes.get(self.path, (404, {}, "", 0))
        time.sleep(pause)
        if status == 0:   # server đóng kết nối mà không trả lời
            self.close_connection = True
            return
        self.send_response(status)
        for key, value in headers.items():
            self.send_header(key, value)
        self.end_headers()
        with contextlib.suppress(OSError):
            self.wfile.write(body.encode("utf-8"))

    def log_message(self, *args):
        pass


def page(body):
    return (200, {"Content-Type": "text/html; charset=utf-8"},
            f"<html><head><title>Phim</title></head><body>{body}</body></html>", 0)


class CrawlerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.origin = f"http://127.0.0.1:{cls.server.server_port}"
        cls.other = f"http://localhost:{cls.server.server_port}"   # "host thứ hai" trỏ về cùng server

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.folder = Path(temp.name)
        Handler.hits = []
        Handler.routes = {"/robots.txt": (200, {"Content-Type": "text/plain"}, "User-agent: *\nDisallow: /blocked\n", 0)}

    def crawl(self, seeds, **options):
        options.setdefault("allowed_domains", ["127.0.0.1"])
        config = Config(seeds=seeds, delay=0, db_path=self.folder / "crawler.db", output_dir=self.folder, **options)
        db = connect(config.db_path)
        self.addCleanup(db.close)
        with contextlib.redirect_stdout(io.StringIO()):
            self.stop_reason = Crawler(config, db).run()
        self.config = config
        return db

    def statuses(self, db):
        return [r[0] for r in db.execute("SELECT crawl_status FROM pages ORDER BY id")]

    # ---------- BFS, lọc URL, trùng lặp ----------

    def test_bfs_order_depth_filters_and_dedup(self):
        Handler.routes.update({
            "/seed": page('<a href="/a">A</a><a href="/b">B</a><a href="/a#part">dup</a><a href="/blocked">x</a>'
                          '<a href="/x.jpg">img</a><a href="mailto:p@example.com">mail</a>'),
            "/a": page('<a href="/a1">A1</a><a href="/b">B</a>'),
            "/b": (404, {}, "", 0),
            "/a1": page('<a href="/too-deep">no</a>'),
        })
        db = self.crawl([self.origin + "/seed"], max_depth=2)
        rows = [(r[0].removeprefix(self.origin), r[1]) for r in db.execute("SELECT url, depth FROM pages ORDER BY id")]
        self.assertEqual(rows, [("/seed", 0), ("/a", 1), ("/b", 1), ("/a1", 2)])   # FIFO = BFS
        self.assertEqual(Handler.hits.count("/a"), 1)
        self.assertNotIn("/too-deep", Handler.hits)
        self.assertNotIn("/x.jpg", Handler.hits)
        self.assertNotIn("/blocked", Handler.hits)   # bị robots.txt cấm: loại ngay khi lọc link, không tốn lượt tải
        self.assertEqual(stats(db, self.config)["failed_requests"], 1)   # 404 được ghi nhận, crawl vẫn tiếp tục
        reasons = {r[0]: r[1] for r in db.execute("SELECT skip_reason, COUNT(*) FROM links WHERE accepted=0 GROUP BY 1")}
        self.assertEqual(set(reasons), {"duplicate", "non_html_extension", "max_depth", "robots_denied"})

    def test_robots_disallow_is_recorded_as_blocked(self):
        Handler.routes["/seed"] = page('<a href="/blocked">x</a>')
        db = self.crawl([self.origin + "/seed", self.origin + "/blocked"])
        self.assertNotIn("/blocked", Handler.hits)
        self.assertEqual(self.statuses(db), ["ok", "blocked"])

    def test_failed_robots_fail_closed(self):
        Handler.routes.update({"/robots.txt": (503, {}, "", 0), "/seed": page("Không được tải")})
        db = self.crawl([self.origin + "/seed"])
        self.assertNotIn("/seed", Handler.hits)
        self.assertEqual(self.statuses(db), ["blocked"])

    def test_max_pages_stops_crawl(self):
        Handler.routes.update({"/seed": page('<a href="/a">A</a><a href="/b">B</a>'), "/a": page("A"), "/b": page("B")})
        db = self.crawl([self.origin + "/seed"], max_pages=2)
        self.assertEqual(stats(db, self.config)["pages_crawled"], 2)
        self.assertEqual(self.stop_reason, "max_pages")

    def test_per_domain_budget(self):
        Handler.routes.update({"/seed": page('<a href="/a">A</a><a href="/b">B</a>'), "/a": page("A"), "/b": page("B")})
        db = self.crawl([self.origin + "/seed"], max_pages_per_domain=2)
        self.assertEqual(stats(db, self.config)["pages_crawled"], 2)
        self.assertEqual(self.stop_reason, "frontier_empty")

    def test_percent_encoded_alias_is_not_requested_twice(self):
        Handler.routes.update({"/seed": page('<a href="/a">A</a><a href="/%61">alias</a>'), "/a": page("A")})
        db = self.crawl([self.origin + "/seed"])
        self.assertEqual(Handler.hits.count("/a"), 1)
        self.assertEqual(stats(db, self.config)["pages_crawled"], 2)

    # ---------- redirect ----------

    def test_external_redirect_is_never_requested(self):
        Handler.routes["/redirect"] = (302, {"Location": f"{self.other}/poison"}, "", 0)
        db = self.crawl([self.origin + "/redirect"])
        self.assertNotIn("/poison", Handler.hits)
        self.assertEqual(db.execute("SELECT crawl_status, error FROM pages").fetchone()[:], ("blocked", "outside_domain"))

    def test_redirect_target_is_not_saved_twice(self):
        Handler.routes.update({"/redirect": (302, {"Location": "/target"}, "", 0), "/target": page("Target")})
        db = self.crawl([self.origin + "/redirect", self.origin + "/target"])
        self.assertEqual(Handler.hits.count("/target"), 1)
        self.assertEqual(stats(db, self.config)["pages_crawled"], 1)

    # ---------- lỗi mạng / HTTP ----------

    def test_timeout_binary_and_disconnect_do_not_stop_crawl(self):
        Handler.routes.update({"/slow": (200, {"Content-Type": "text/html"}, "slow", 0.3),
                               "/binary": (200, {"Content-Type": "image/png"}, "bin", 0),
                               "/drop": (0, {}, "", 0), "/fine": page("Fine")})
        db = self.crawl([self.origin + p for p in ("/slow", "/binary", "/drop", "/fine")], timeout=0.1)
        self.assertEqual(self.statuses(db), ["request_error", "non_html", "request_error", "ok"])

    def test_http_500_continues_and_403_stops_only_its_host(self):
        Handler.routes.update({
            "/seed-a": page('<a href="/err">500</a><a href="/forbidden">403</a><a href="/after">after</a>'),
            "/seed-b": page('<a href="/b-fine">b</a>'),
            "/err": (500, {}, "", 0), "/forbidden": (403, {}, "", 0),
            "/after": page("Không được tải"), "/b-fine": page("OK"),
        })
        db = self.crawl([self.origin + "/seed-a", self.other + "/seed-b"], allowed_domains=["127.0.0.1", "localhost"])
        self.assertEqual(stats(db, self.config)["failed_requests"], 2)
        self.assertIn("/b-fine", Handler.hits)
        self.assertNotIn("/after", Handler.hits)

    def test_parse_error_keeps_html_and_continues(self):
        Handler.routes.update({"/bad": page("Cache phải còn"), "/good": page("Good")})
        good = parse_page(page("Good")[2], self.origin + "/good", NOW)
        with patch("crawler.parse_page", side_effect=[ValueError("markup đổi"), good]):
            db = self.crawl([self.origin + "/bad", self.origin + "/good"])
        rows = list(db.execute("SELECT crawl_status, html_path FROM pages ORDER BY id"))
        self.assertEqual([r[0] for r in rows], ["parse_error", "ok"])
        self.assertIn("Cache phải còn", Path(rows[0][1]).read_text(encoding="utf-8"))


class UrlRuleTests(unittest.TestCase):
    def test_normalize_and_scope(self):
        # --- Chuẩn hóa chung ---
        self.assertIsNone(normalize_url("javascript:void(0)"))
        self.assertIsNone(normalize_url("mailto:abc@example.com"))
        self.assertNotEqual(normalize_url("https://example.com/a"), normalize_url("https://example.com/a/"))
        self.assertEqual(normalize_url("https://example.com/a"), normalize_url("https://example.com/%61"))   # %61 = "a"
        self.assertIn("%2F", normalize_url("https://doi.org/10.1/a%2Fb"))   # %2F không thành dấu phân cách

        # --- Chuẩn hóa riêng cho w2w.vn ---
        self.assertEqual(normalize_url("https://www.w2w.vn/phim/abc"), "https://w2w.vn/phim/abc/")   # bỏ www, thêm "/"
        self.assertEqual(normalize_url("/phim/abc#part", "https://w2w.vn"), "https://w2w.vn/phim/abc/")   # bỏ fragment
        self.assertEqual(normalize_url("https://w2w.vn/phim/Phim-Việt/"),
                         normalize_url("https://w2w.vn/phim/Phim-Vi%E1%BB%87t/"))   # tiếng Việt có dấu = dạng mã hóa

        # --- Phạm vi crawl ---
        w2w = ["w2w.vn"]
        self.assertIsNone(url_reason("https://w2w.vn/phim/ten-phim/", w2w))                 # trang chi tiết phim
        self.assertIsNone(url_reason("https://w2w.vn/phim/", w2w))                          # trang danh sách
        self.assertIsNone(url_reason("https://w2w.vn/phim/page/2/", w2w))                   # phân trang danh sách
        self.assertIsNone(url_reason("https://w2w.vn/phim/?page=2", w2w))                   # query phân trang được phép
        self.assertEqual(url_reason("https://w2w.vn/blog/abc/", w2w), "outside_movie_paths")
        self.assertEqual(url_reason("https://w2w.vn/phim/ten-phim/?a=1", w2w), "w2w_query_blocked")
        self.assertEqual(url_reason("https://w2w.vn/phim/poster.jpg", w2w), "non_html_extension")
        self.assertEqual(url_reason("https://example.com/phim/a/", w2w), "outside_domain")
        self.assertEqual(url_reason("https://w2w.vn.example.com/phim/a/", w2w), "outside_domain")   # không khớp theo hậu tố

    def test_robots_wildcard_and_longest_rule(self):
        rules = Robots("User-agent: *\nAllow: /\nDisallow: /*?\nDisallow: /_next/\nAllow: /_next/open$\n", "FilmBot/1.0")
        self.assertTrue(rules.allows("https://www.momo.vn/cinema/a-1"))
        self.assertFalse(rules.allows("https://www.momo.vn/cinema/a-1?page=2"))
        self.assertFalse(rules.allows("https://www.momo.vn/_next/a"))
        self.assertTrue(rules.allows("https://www.momo.vn/_next/open"))
        self.assertFalse(rules.allows("https://www.momo.vn/_next/open/more"))
        own_group = Robots("User-agent: *\nDisallow: /\nUser-agent: FilmBot\nAllow: /\n", "FilmBot/1")
        self.assertTrue(own_group.allows("https://example.com/a"))



if __name__ == "__main__":
    unittest.main()
