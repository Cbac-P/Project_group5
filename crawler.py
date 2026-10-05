"""BFS crawler: lấy URL từ frontier → robots → tải → parse → lưu → lọc link → đưa vào frontier."""

import hashlib
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

import requests

from database import save_page
from frontier import Frontier
from parser import parse_page
from url_rules import Robots, normalize_url, url_reason

REDIRECT_CODES = {301, 302, 303, 307, 308}
MAX_REDIRECTS = 5


class Blocked(Exception):
    """URL không được phép tải: ngoài phạm vi, robots cấm, redirect lỗi..."""


class Crawler:
    def __init__(self, config, db):
        self.config, self.db = config, db
        self.frontier = Frontier()
        self.session = requests.Session()
        self.session.trust_env = False   # trang công khai: không lấy thông tin đăng nhập ngầm (netrc, proxy)
        self.session.headers["User-Agent"] = config.user_agent
        self.robots = {}            # host -> Robots; None nếu không đọc được robots.txt (chặn host đó)
        self.last_request = {}      # host -> thời điểm request gần nhất, để giữ delay
        self.blocked_hosts = set()  # host đã trả 403/429: dừng gọi tới host đó
        self.fetched = set()        # URL (và URL sau redirect) đã tải, để một trang không bị lưu hai lần
        self.per_domain = Counter()
        self.pages = 0

    # ---------- Tải trang ----------

    def get(self, url):
        """Mọi request (trang và robots.txt) đều đi qua đây để dùng chung delay."""
        host = urlsplit(url).hostname
        wait = self.config.delay - (time.monotonic() - self.last_request.get(host, float("-inf")))
        if wait > 0:
            time.sleep(wait)
        self.last_request[host] = time.monotonic()
        return self.session.get(url, timeout=self.config.timeout, allow_redirects=False, stream=True)

    def load_robots(self, origin):
        try:
            with self.get(origin + "/robots.txt") as resp:
                if resp.status_code == 200 and "html" not in resp.headers.get("Content-Type", ""):
                    return Robots(resp.content.decode("utf-8-sig", "replace"), self.config.user_agent)
                if resp.status_code in (404, 410):   # không có robots.txt = được phép
                    return Robots("", self.config.user_agent)
        except requests.RequestException:
            pass
        return None   # không đọc được robots.txt thì không crawl host này

    def robots_allow(self, url):
        parts = urlsplit(url)
        if parts.hostname not in self.robots:
            self.robots[parts.hostname] = self.load_robots(f"{parts.scheme}://{parts.netloc}")
        rules = self.robots[parts.hostname]
        return rules is not None and rules.allows(url)

    def fetch(self, url):
        """Tải URL, tự theo redirect để mỗi bước đều qua kiểm tra phạm vi và robots.

        Trả về (status, body, url_cuối, thời_gian). body rỗng nếu không phải HTML 200.
        """
        for _ in range(MAX_REDIRECTS + 1):
            reason = url_reason(url, self.config.allowed_domains)
            if reason:
                raise Blocked(reason)
            if not self.robots_allow(url):
                raise Blocked("robots_denied")
            with self.get(url) as resp:
                if resp.status_code in REDIRECT_CODES:
                    url = normalize_url(resp.headers.get("Location", ""), url)
                    if url is None:
                        raise Blocked("invalid_redirect")
                    if url in self.fetched:
                        raise Blocked("redirect_already_fetched")
                    continue
                is_html = "html" in resp.headers.get("Content-Type", "").lower()
                body = resp.content if resp.status_code == 200 and is_html else b""
                return resp.status_code, body, url, resp.elapsed.total_seconds()
        raise Blocked("too_many_redirects")

    # ---------- Xử lý một trang ----------

    def filter_links(self, source, base, depth, hrefs):
        """Mỗi link → (source, target, accepted, skip_reason). Link hợp lệ vào frontier ở depth + 1."""
        rows = []
        for href in hrefs:
            target = normalize_url(href, base)
            if target is None:   # mailto:, javascript:, tel:...
                continue
            reason = url_reason(target, self.config.allowed_domains)
            if reason is None and depth + 1 > self.config.max_depth:
                reason = "max_depth"
            rules = self.robots.get(urlsplit(target).hostname)   # chỉ biết nếu đã đọc robots.txt của host đó
            if reason is None and rules is not None and not rules.allows(target):
                reason = "robots_denied"
            if reason is None and not self.frontier.add(target, depth + 1):
                reason = "duplicate"
            rows.append((source, target, int(reason is None), reason))
        return rows

    def cache_html(self, url, body):
        path = Path(self.config.output_dir) / "html" / (hashlib.sha256(url.encode()).hexdigest() + ".html")
        path.write_bytes(body)
        return path.as_posix()

    def crawl_one(self, url, depth):
        host = urlsplit(url).hostname
        page = dict(url=url, domain=host, title="", content="", depth=depth, status_code=None,
                    crawled_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    final_url=url, page_type="other", crawl_status="ok",
                    response_time=0.0, error=None, html_path=None)
        parsed = None
        try:
            status, body, final, elapsed = self.fetch(url)
            page.update(status_code=status, final_url=final, response_time=round(elapsed, 3))
            if status != 200:
                page.update(crawl_status="http_error", error=f"HTTP {status}")
                if status in (403, 429):
                    self.blocked_hosts.add(host)
            elif not body:
                page.update(crawl_status="non_html", error="not_html")
            else:
                page["html_path"] = self.cache_html(url, body)
                try:
                    parsed = parse_page(body, final, page["crawled_at"])
                except Exception as exc:   # một trang lỗi không được dừng cả crawl; HTML đã lưu để reparse
                    page.update(crawl_status="parse_error", error=f"{type(exc).__name__}: {exc}")
        except Blocked as exc:
            page.update(crawl_status="blocked", error=str(exc))
        except requests.RequestException as exc:
            page.update(crawl_status="request_error", error=str(exc))

        links, movie = [], None
        if parsed:
            page.update(title=parsed["title"], content=parsed["content"],
                        page_type=parsed["page_type"], error=parsed["parse_error"])
            movie = parsed["movie"]
            links = self.filter_links(url, page["final_url"], depth, parsed["hrefs"])
        save_page(self.db, page, links, movie)
        self.fetched.update((url, page["final_url"]))
        self.report(page, links)

    def report(self, page, links):
        accepted = sum(link[2] for link in links)
        print(f"\n[Crawl #{self.pages + 1:03d}]\nDepth : {page['depth']}\nURL   : {page['url']}\n"
              f"Status: {page['status_code']} ({page['crawl_status']})\nTitle : {page['title']}\n"
              f"Links : {len(links)} ({accepted} mới)\nTime  : {page['response_time']:.2f} sec", flush=True)

    # ---------- Vòng lặp chính ----------

    def run(self):
        """Trả về lý do dừng: max_pages, frontier_empty hoặc interrupted."""
        for seed in self.config.seeds:
            url = normalize_url(seed)
            if url is None or url_reason(url, self.config.allowed_domains):
                raise ValueError(f"Seed không hợp lệ hoặc ngoài phạm vi: {seed}")
            self.frontier.add(url, 0)
        print("\n========== URL FRONTIER ==========")
        for i, (url, _) in enumerate(self.frontier.queue, 1):
            print(f"[{i}] {url}")
        (Path(self.config.output_dir) / "html").mkdir(parents=True, exist_ok=True)

        try:
            while self.frontier and self.pages < self.config.max_pages:
                url, depth = self.frontier.pop()
                host = urlsplit(url).hostname
                if url in self.fetched or host in self.blocked_hosts \
                        or self.per_domain[host] >= self.config.max_pages_per_domain:
                    continue
                self.crawl_one(url, depth)
                self.pages += 1
                self.per_domain[host] += 1
        except KeyboardInterrupt:
            return "interrupted"
        finally:
            self.session.close()
        return "max_pages" if self.pages >= self.config.max_pages else "frontier_empty"
