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
        self.assertEqual(normalize_url("/a#part", "https://momo.vn"), "https://www.momo.vn/a")
        self.assertIsNone(normalize_url("javascript:void(0)"))
        self.assertNotEqual(normalize_url("https://example.com/a"), normalize_url("https://example.com/a/"))
        self.assertEqual(normalize_url("https://vi.wikipedia.org/wiki/Thể_loại:Phim"),
                         normalize_url("https://vi.wikipedia.org/wiki/Th%E1%BB%83_lo%E1%BA%A1i%3APhim"))
        self.assertIn("%2F", normalize_url("https://doi.org/10.1/a%2Fb"))   # %2F không thành dấu phân cách
        momo, wiki = ["www.momo.vn"], ["vi.wikipedia.org"]
        self.assertIsNone(url_reason("https://www.momo.vn/cinema/phim-a-1", momo))
        self.assertIsNotNone(url_reason("https://momo.vn.example.com/cinema/a-1", momo))   # không khớp theo hậu tố
        self.assertIsNotNone(url_reason("https://www.momo.vn/cinema/a-1?page=2", momo))
        self.assertIsNone(url_reason(normalize_url("https://vi.wikipedia.org/wiki/Backrooms:_Thực_thể"), wiki))
        self.assertIsNotNone(url_reason(normalize_url("https://vi.wikipedia.org/wiki/Bản_mẫu:Phim"), wiki))

    def test_robots_wildcard_and_longest_rule(self):
        rules = Robots("User-agent: *\nAllow: /\nDisallow: /*?\nDisallow: /_next/\nAllow: /_next/open$\n", "FilmBot/1.0")
        self.assertTrue(rules.allows("https://www.momo.vn/cinema/a-1"))
        self.assertFalse(rules.allows("https://www.momo.vn/cinema/a-1?page=2"))
        self.assertFalse(rules.allows("https://www.momo.vn/_next/a"))
        self.assertTrue(rules.allows("https://www.momo.vn/_next/open"))
        self.assertFalse(rules.allows("https://www.momo.vn/_next/open/more"))
        own_group = Robots("User-agent: *\nDisallow: /\nUser-agent: FilmBot\nAllow: /\n", "FilmBot/1")
        self.assertTrue(own_group.allows("https://example.com/a"))


def wiki(body, name="Phim_mẫu"):
    return parse_page(body, "https://vi.wikipedia.org/wiki/" + name, NOW)


class ParserTests(unittest.TestCase):
    def test_momo_movie_export_and_status(self):
        data = {"Id": 123, "Title": "Phim, có dấu", "TitleEn": "Original",
                "Synopsis": "Tình tiết bí ẩn.\nMột câu nữa.", "OpeningDate": "2036-12-18 00:00:00",
                "ApiCasts": [{"name": "Diễn viên", "character": "Nhân vật"}]}
        payload = json.dumps({"props": {"pageProps": {"FilmData": {"Data": data}}}}, ensure_ascii=False)
        html = f'<head><title>T</title></head><body>Quảng cáo<script id="__NEXT_DATA__">{payload}</script></body>'
        parsed = parse_page(html, "https://www.momo.vn/cinema/a-123", NOW)
        movie = parsed["movie"]
        self.assertNotIn("Quảng cáo", movie["synopsis"])
        self.assertIsNone(movie["release_year"])   # OpeningDate là ngày chiếu địa phương, không phải năm phát hành
        self.assertEqual(movie["release_status"], "upcoming")
        self.assertEqual((movie["cast"], movie["characters"]), (["Diễn viên"], ["Nhân vật"]))
        self.assertNotIn("FilmData", parsed["content"])

        with tempfile.TemporaryDirectory() as folder:
            config = Config(output_dir=Path(folder))
            db = connect(Path(folder) / "crawler.db")
            from database import save_page
            save_page(db, dict(url=movie["source_url"], domain="www.momo.vn", title="T", content="x", depth=0,
                               status_code=200, crawled_at=NOW), [], movie)
            summary = export_all(db, config)
            with (Path(folder) / "momo_movies_raw.csv").open(encoding="utf-8-sig", newline="") as file:
                row = next(csv.DictReader(file))
            db.close()
        self.assertEqual((row["title"], json.loads(row["cast"])), ("Phim, có dấu", ["Diễn viên"]))
        self.assertEqual((summary["movies"], summary["movies_with_synopsis"]), ({"momo": 1}, 0))   # sắp chiếu thì bị loại

    def test_wiki_synopsis_stops_before_next_section(self):
        movie = wiki('''<h1 id="firstHeading">Phim mẫu (phim)</h1><div class="mw-parser-output">
          <table class="infobox"><tr><th class="summary">Phim mẫu</th></tr>
          <tr><th>Đạo diễn</th><td><a>Người A</a></td></tr><tr><th>Công chiếu</th><td>2018</td></tr></table>
          <p>Phim mẫu là một bộ phim kinh dị.</p>
          <div class="mw-heading"><h2>Nội dung<span class="mw-editsection">[sửa]</span></h2></div>
          <p>Một người tìm kiếm sự thật.<sup class="reference"><a href="#r">[1]</a></sup></p>
          <h3>Hồi cuối</h3><p>Họ giải cứu người bạn.</p>
          <h2>Diễn viên</h2><p>Không lấy phần này.</p></div>''')["movie"]
        self.assertEqual((movie["title"], movie["release_year"]), ("Phim mẫu", 2018))
        self.assertIn("giải cứu", movie["synopsis"])
        self.assertNotIn("Không lấy", movie["synopsis"])
        self.assertNotIn("[1]", movie["synopsis"])

    def test_wiki_category_page_is_not_a_movie(self):
        parsed = wiki('<h1>Phim</h1><div class="mw-parser-output">Category</div>', "Thể_loại:Phim")
        self.assertEqual((parsed["movie"], parsed["page_type"]), (None, "category"))

    def test_wiki_original_title_genre_and_upcoming(self):
        movie = wiki('''<h1 id="firstHeading">Tên Việt</h1><div class="mw-parser-output">
          <table class="infobox"><tr><th class="summary">English Title<br/>Tên Việt</th></tr>
          <tr><th>Đạo diễn</th><td>Người A</td></tr>
          <tr><th>Công chiếu</th><td>15 tháng 1 năm 2030 (2030-01-15)</td></tr></table>
          <p>Tên Việt là một bộ phim.</p><h2>Cốt truyện</h2><p>Nhân vật tìm đường về nhà.</p>
          <link rel="mw:PageProp/Category" href="./Thể_loại:Phim_kinh_dị_Mỹ"/></div>''', "Tên_Việt")["movie"]
        self.assertEqual((movie["original_title"], movie["genre"], movie["release_status"]),
                         ("English Title", ["kinh dị"], "upcoming"))

    def test_wiki_infobox_date_overrides_stale_upcoming_category(self):
        movie = wiki('''<h1 id="firstHeading">Crawl 2</h1><div class="mw-parser-output">
          <table class="infobox"><tr><th>Đạo diễn</th><td>Người A</td></tr>
          <tr><th>Công chiếu</th><td>2025-01-01</td></tr></table>
          <p>Crawl 2 là một bộ phim.</p><h2>Cốt truyện</h2><p>Thành phố ngập nước.</p>
          <link rel="mw:PageProp/Category" href="./Thể_loại:Phim_chưa_ra_mắt"/></div>''', "Crawl_2")["movie"]
        self.assertEqual(movie["release_status"], "released")

    def test_wiki_disambig_link_does_not_exclude_film_but_tv_does(self):
        body = '''<h1 id="firstHeading">Phim thật</h1><div class="mw-parser-output">
          <table class="infobox"><tr><th>Đạo diễn</th><td>Người A</td></tr>
          <tr><th>Công chiếu</th><td>2024</td></tr><tr><th>Thời lượng</th><td>90 phút</td></tr></table>
          <p>Phim thật là một bộ phim có diễn viên <a class="mw-disambig" href="./Tên_trùng">A</a>.</p>
          <h2>Nội dung</h2><p>Nhân vật đi tìm gia đình.</p></div>'''
        self.assertIsNotNone(wiki(body, "Phim_thật")["movie"])
        self.assertIsNone(wiki(body.replace("<th>Công chiếu</th>", "<th>Số tập</th>"), "Phim_thật")["movie"])
        self.assertIsNone(wiki(body.replace("<h1", '<link rel="mw:PageProp/disambiguation"/><h1'), "Phim_thật")["movie"])


if __name__ == "__main__":
    unittest.main()
