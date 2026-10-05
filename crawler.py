"""Focused BFS crawler that collects movie information from FPT Play."""
from __future__ import annotations

import gzip
import re
import time
from collections import Counter
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import requests

import config
from database import (
    MovieDatabase,
    make_dedup_key,
    make_synopsis_hash,
)
from parser import (
    extract_links,
    extract_movie_information,
    is_detail_url,
    is_valid_url,
    normalize_url,
    parse_html,
)
from url_frontier import URLFrontier

LOC_RE = re.compile(r"<loc>\s*(.*?)\s*</loc>", re.S | re.I)


class MovieCrawler:
    def __init__(self, seed_urls=None, database_path=None) -> None:
        self.seed_urls = list(seed_urls) if seed_urls is not None else list(config.SEED_URLS)

        self.frontier = URLFrontier()
        self.db = MovieDatabase(
            database_path if database_path is not None else config.DATABASE_PATH,
            reset=config.RESET_DATABASE_ON_START,
        )

        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": config.USER_AGENT,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "vi,en;q=0.8",
            }
        )

        self.robots_cache: dict[str, RobotFileParser] = {}
        self.robots_delay_cache: dict[str, float] = {}
        self.last_request_at: dict[str, float] = {}

        self.discovered_urls: set[str] = set()
        self.pages_crawled = 0
        self.request_attempts = 0
        self.failed_requests = 0
        self.skipped_urls = 0
        self.detail_pages = 0
        self.movies_saved = 0
        self.skip_reasons: Counter[str] = Counter()
        self.status_counts: Counter[int] = Counter()
        self.depth_counts: Counter[int] = Counter()

        for seed in self.seed_urls:
            normalized = normalize_url(seed)
            self.discovered_urls.add(normalized)
            self.frontier.add(normalized, 0)

    # ------------------------------------------------------------------
    # robots.txt + politeness
    # ------------------------------------------------------------------
    def _base_url(self, url: str) -> str:
        p = urlparse(url)
        return f"{p.scheme}://{p.netloc}"

    def _get_robots(self, url: str) -> RobotFileParser:
        base = self._base_url(url)
        if base in self.robots_cache:
            return self.robots_cache[base]

        robots_url = base + "/robots.txt"
        rp = RobotFileParser()
        rp.set_url(robots_url)

        try:
            response = self.session.get(robots_url, timeout=config.REQUEST_TIMEOUT)
            if response.status_code == 200:
                rp.parse(response.text.splitlines())
            elif response.status_code in {401, 403}:
                # Conservative interpretation: explicit access denial means no crawling.
                rp.parse(["User-agent: *", "Disallow: /"])
            elif response.status_code == 404:
                # No robots file found -> no robots restrictions discovered.
                rp.parse(["User-agent: *", "Disallow:"])
            elif 500 <= response.status_code < 600 and config.ROBOTS_FAIL_CLOSED:
                rp.parse(["User-agent: *", "Disallow: /"])
            else:
                rp.parse(["User-agent: *", "Disallow:"])
        except requests.RequestException:
            if config.ROBOTS_FAIL_CLOSED:
                rp.parse(["User-agent: *", "Disallow: /"])
            else:
                rp.parse(["User-agent: *", "Disallow:"])

        delay = rp.crawl_delay(config.USER_AGENT)
        if delay is None:
            delay = rp.crawl_delay("*")
        self.robots_delay_cache[base] = float(delay or 0.0)
        self.robots_cache[base] = rp
        return rp

    def _robots_allows(self, url: str) -> bool:
        if not config.RESPECT_ROBOTS_TXT:
            return True
        return self._get_robots(url).can_fetch(config.USER_AGENT, url)

    def _effective_delay(self, url: str) -> float:
        robots_delay = self.robots_delay_cache.get(self._base_url(url), 0.0)
        return max(float(config.CRAWL_DELAY), robots_delay)

    def _respect_delay(self, url: str) -> None:
        base = self._base_url(url)
        previous = self.last_request_at.get(base)
        if previous is not None:
            remaining = self._effective_delay(url) - (time.monotonic() - previous)
            if remaining > 0:
                time.sleep(remaining)

    def _mark_request_time(self, url: str) -> None:
        self.last_request_at[self._base_url(url)] = time.monotonic()

    def _skip(self, reason: str) -> None:
        self.skipped_urls += 1
        self.skip_reasons[reason] += 1

    # ------------------------------------------------------------------
    # Sitemap seeding (optional, see config.SEED_FROM_SITEMAP)
    # ------------------------------------------------------------------
    def _fetch_sitemap_text(self, url: str) -> str | None:
        if not self._robots_allows(url):
            return None
        self._respect_delay(url)
        try:
            response = self.session.get(url, timeout=config.REQUEST_TIMEOUT)
            self._mark_request_time(url)
        except requests.RequestException:
            self._mark_request_time(url)
            return None
        if response.status_code != 200:
            return None
        data = response.content
        if data[:2] == b"\x1f\x8b":
            data = gzip.decompress(data)
        return data.decode("utf-8", errors="replace")

    def _seed_from_sitemaps(self) -> int:
        """Queue movie-detail URLs found in sitemaps at depth 1. Returns how many were queued."""
        pending = list(config.SITEMAP_URLS)
        try:
            for line in self._get_robots(self.seed_urls[0]).site_maps() or []:
                pending.append(line)
        except (AttributeError, IndexError):
            pass

        fetched = 0
        queued = 0
        done: set[str] = set()
        while pending and fetched < config.MAX_SITEMAP_FETCHES:
            sitemap_url = pending.pop(0)
            if sitemap_url in done:
                continue
            done.add(sitemap_url)
            text = self._fetch_sitemap_text(sitemap_url)
            fetched += 1
            if not text:
                continue
            for loc in LOC_RE.findall(text):
                target = normalize_url(loc)
                if re.search(r"\.xml(\.gz)?$", target, re.I):
                    pending.append(target)          # nested sitemap
                elif is_detail_url(target):
                    self.discovered_urls.add(target)
                    if self.frontier.add(target, 1):
                        queued += 1
        print(f"[SITEMAP] fetched {fetched} file(s), queued {queued} movie URL(s) at depth 1")
        return queued

    # ------------------------------------------------------------------
    # Movie handling
    # ------------------------------------------------------------------
    def _handle_detail_page(self, soup, url: str, depth: int) -> str:
        """Extract, validate, de-duplicate and store one movie. Returns a status string."""
        self.detail_pages += 1
        record, reason = extract_movie_information(soup, url)
        if record is None:
            self._skip(reason)
            return f"skipped ({reason})"

        if self.db.url_exists(url):
            self._skip("duplicate_url")
            return "skipped (duplicate_url)"

        key = make_dedup_key(record["title"], record.get("release_year"))
        if self.db.dedup_key_exists(key):
            self._skip("duplicate_content")
            return "skipped (duplicate title+year)"

        if config.DEDUP_BY_SYNOPSIS and self.db.synopsis_hash_exists(make_synopsis_hash(record["synopsis"])):
            self._skip("duplicate_synopsis")
            return "skipped (duplicate synopsis)"

        self.db.save_movie(record, depth)
        self.movies_saved += 1
        return f"SAVED: {record['title']} ({record.get('release_year') or 'n/a'})"

    # ------------------------------------------------------------------
    def _print_configuration(self) -> None:
        print("=" * 46)
        print(" FPT PLAY MOVIE CRAWLER")
        print("=" * 46)
        print(f"Topic          : {config.TOPIC}")
        print(f"Seed URLs      : {len(self.seed_urls)}")
        for i, seed in enumerate(self.seed_urls, start=1):
            print(f"  {i}. {seed}")
        print("Allowed Domains:")
        for domain in config.ALLOWED_DOMAINS:
            print(f"  - {domain}")
        print(f"Maximum Depth  : {config.MAX_DEPTH}")
        print(f"Maximum Pages  : {config.MAX_PAGES}")
        print(f"Request Timeout: {config.REQUEST_TIMEOUT} seconds")
        print(f"Base Crawl Delay: {config.CRAWL_DELAY} second(s)")
        print(f"Respect robots.txt: {config.RESPECT_ROBOTS_TXT}")
        print(f"Seed from sitemap : {config.SEED_FROM_SITEMAP}")
        print("=" * 46)

    def run(self) -> dict:
        self._print_configuration()

        try:
            if config.SEED_FROM_SITEMAP:
                self._seed_from_sitemaps()

            while self.frontier and self.pages_crawled < config.MAX_PAGES:
                item = self.frontier.pop()
                if item is None:
                    break
                url, depth = item

                if url in self.frontier.visited:
                    self._skip("already_visited")
                    continue

                if depth > config.MAX_DEPTH:
                    self.frontier.mark_visited(url)
                    self._skip("max_depth")
                    continue

                valid, reason = is_valid_url(
                    url, config.ALLOWED_DOMAINS, config.BLOCKED_EXTENSIONS
                )
                if not valid:
                    self.frontier.mark_visited(url)
                    self._skip(reason)
                    continue

                if not self._robots_allows(url):
                    self.frontier.mark_visited(url)
                    self._skip("robots_disallowed")
                    print(f"[SKIP robots.txt] {url}")
                    continue

                # Mark before requesting so a failure cannot cause repeated re-queuing.
                self.frontier.mark_visited(url)
                self._respect_delay(url)

                self.request_attempts += 1
                started = time.perf_counter()
                try:
                    response = self.session.get(
                        url,
                        timeout=config.REQUEST_TIMEOUT,
                        allow_redirects=True,
                    )
                    elapsed = time.perf_counter() - started
                    self._mark_request_time(url)
                except requests.RequestException as exc:
                    self._mark_request_time(url)
                    self.failed_requests += 1
                    print(f"[FAILED] Depth {depth} | {url} | {exc}")
                    continue

                self.pages_crawled += 1
                self.status_counts[response.status_code] += 1
                self.depth_counts[depth] += 1

                final_url = normalize_url(response.url)
                content_type = response.headers.get("Content-Type", "").lower()
                soup = None
                extracted_links: list[str] = []

                if response.status_code == 200 and "html" in content_type:
                    soup = parse_html(response.text)
                    extracted_links = extract_links(final_url, soup)
                elif response.status_code == 200:
                    self._skip("non_html_response")

                # Only movie-detail pages are turned into records; every other
                # page (home, listings, genres) is used just to discover links.
                movie_status = "-"
                if soup is not None and is_detail_url(final_url):
                    movie_status = self._handle_detail_page(soup, final_url, depth)

                accepted = 0
                if soup is not None:
                    for target in extracted_links:
                        self.discovered_urls.add(target)

                        valid, reason = is_valid_url(
                            target, config.ALLOWED_DOMAINS, config.BLOCKED_EXTENSIONS
                        )
                        if not valid:
                            self._skip(reason)
                            continue

                        new_depth = depth + 1
                        if new_depth > config.MAX_DEPTH:
                            self._skip("max_depth")
                            continue

                        # Check robots before enqueueing so disallowed pages never enter the queue.
                        if not self._robots_allows(target):
                            self._skip("robots_disallowed")
                            continue

                        if self.frontier.add(target, new_depth):
                            accepted += 1
                        else:
                            self._skip("duplicate")

                print("-" * 46)
                print(f"[Crawl #{self.pages_crawled:03d}]")
                print(f"Depth : {depth}")
                print(f"URL   : {final_url}")
                print(f"Status: {response.status_code}")
                print(f"Movie : {movie_status}")
                print(f"Links : {len(extracted_links)} extracted / {accepted} queued")
                print(f"Time  : {elapsed:.2f} sec")
                print(f"Frontier waiting: {len(self.frontier)}")

            return self.summary()
        finally:
            self.db.close()

    def summary(self) -> dict:
        result = {
            "topic": config.TOPIC,
            "seed_urls": len(self.seed_urls),
            "pages_crawled": self.pages_crawled,
            "request_attempts": self.request_attempts,
            "unique_urls_discovered": len(self.discovered_urls),
            "skipped_urls": self.skipped_urls,
            "failed_requests": self.failed_requests,
            "maximum_depth": config.MAX_DEPTH,
            "detail_pages": self.detail_pages,
            "movies_saved": self.movies_saved,
            "depth_counts": dict(sorted(self.depth_counts.items())),
            "status_counts": dict(sorted(self.status_counts.items())),
            "skip_reasons": dict(self.skip_reasons.most_common()),
            "frontier_remaining": len(self.frontier),
        }

        print("\n" + "=" * 46)
        print(" CRAWLING SUMMARY")
        print("=" * 46)
        print(f"Topic                  : {result['topic']}")
        print(f"Seed URLs              : {result['seed_urls']}")
        print(f"Pages Crawled          : {result['pages_crawled']}")
        print(f"Unique URLs Discovered : {result['unique_urls_discovered']}")
        print(f"Skipped URLs           : {result['skipped_urls']}")
        print(f"Failed Requests        : {result['failed_requests']}")
        print(f"Maximum Depth          : {result['maximum_depth']}")
        print(f"Movie Detail Pages     : {result['detail_pages']}")
        print(f"Movies Saved           : {result['movies_saved']}")
        for depth, count in result["depth_counts"].items():
            print(f"Depth {depth:<2}                : {count}")
        for status, count in result["status_counts"].items():
            print(f"HTTP {status:<3}               : {count}")
        print("\nSkip reasons breakdown:")
        for reason, count in result["skip_reasons"].items():
            print(f"  {reason:<24}: {count}")
        print(f"Frontier Remaining     : {result['frontier_remaining']}")
        print("=" * 46)

        return result
