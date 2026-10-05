"""Single-thread BFS with durable frontier, bounded requests and per-host pacing."""
# Luồng: seed -> hàng đợi BFS -> tải HTML -> parse -> lưu DB -> thêm URL mới.
import hashlib
import json
import time
from collections import Counter, deque
from dataclasses import asdict
from datetime import datetime, timezone
from urllib.parse import urlsplit

import requests

from database import enqueue, request_log, save_page
from dataset import validate_movie
from parser import parse_page
from url_rules import RobotsRules, normalize_url, url_reason


def timestamp():  # Ghi thời điểm UTC để log có cùng chuẩn thời gian.
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class FetchBlocked(Exception):  # Lỗi chủ động dừng tải khi vượt giới hạn, bị robots chặn hoặc redirect sai.
    pass


class Crawler:
    def __init__(self, config, db):  # Kiểm tra lượt cũ, mở phiên HTTP và chuẩn bị bộ đếm.
        config.validate()
        if config.max_pages < db.execute("SELECT COUNT(*) FROM pages").fetchone()[0]:  # max_pages là tổng tích lũy; không được hạ dưới số trang DB đã lưu.
            raise ValueError("max_pages không được nhỏ hơn số trang đã lưu; dùng DB mới để chạy ngân sách nhỏ hơn.")
        baseline = db.execute("SELECT value FROM metadata WHERE key='config'").fetchone()
        if baseline:
            previous = json.loads(baseline[0])
            current = asdict(config)  # Resume giữ nguyên seed, domain, depth và người cào; muốn đổi thì dùng DB mới.
            for field in ("seeds", "allowed_domains", "max_depth", "member"):
                if previous.get(field, "" if field == "member" else None) != current[field]:
                    raise ValueError(f"Không đổi {field} khi resume; hãy dùng --db và --output mới.")
        self.config, self.db = config, db
        self.session = requests.Session()  # Dùng lại kết nối HTTP giữa các request.
        self.session.trust_env = False  # Không tự lấy thông tin đăng nhập hoặc proxy ngầm từ môi trường.
        self.session.headers["User-Agent"] = config.user_agent
        self.last_request, self.robots, self.blocked_hosts = {}, {}, set()  # Lưu lần gọi gần nhất, luật robots và các host đang bị chặn.
        self.request_count = 0
        self.started = time.monotonic()  # monotonic dùng đo thời gian trôi qua, không dùng làm ngày giờ báo cáo.
        self.stop_reason = None

    def remaining_seconds(self):  # Thời gian còn lại của lượt; khác timeout của từng request.
        return self.config.max_seconds - (time.monotonic() - self.started)

    def get(self, url, kind="html"):  # Gửi một request, đọc body có giới hạn và ghi log kể cả khi lỗi.
        host = urlsplit(url).hostname
        cap = self.config.max_pages * (self.config.max_redirects + 1) + 10  # Chặn số request quá lớn; một trang có thể cần robots và nhiều redirect.
        if self.request_count >= cap or self.remaining_seconds() <= 0:
            raise FetchBlocked("request_or_time_budget")
        rules = self.robots.get(host)
        delay = max(self.config.delay, rules.delay if rules else 0)  # Chọn khoảng nghỉ lớn hơn giữa cấu hình và Crawl-delay của robots.
        wait = max(0, delay - (time.monotonic() - self.last_request.get(host, 0)))  # Tính số giây còn phải chờ so với request trước cùng host.
        if wait >= self.remaining_seconds():
            raise FetchBlocked("time_budget")
        if wait:
            time.sleep(wait)  # Nghỉ để giảm tải cho website.
        start, now = time.monotonic(), timestamp()
        self.last_request[host] = start
        self.request_count += 1
        status, size, error = None, 0, None
        try:
            with self.session.get(url, timeout=min(self.config.timeout, self.remaining_seconds()),
                                  allow_redirects=False, stream=True) as response:
                status = response.status_code
                content_type = response.headers.get("Content-Type", "").lower()
                body = bytearray()
                # Chỉ đọc robots hoặc HTML HTTP 200; không tải video/ảnh để kiểm tra.
                read_body = kind == "robots" or (status == 200 and
                            ("text/html" in content_type or "application/xhtml+xml" in content_type))
                if read_body:
                    for chunk in response.iter_content(65536):  # Đọc từng khối thay vì nhận toàn bộ body lớn một lần.
                        body.extend(chunk)
                        size = len(body)
                        if size > self.config.max_response_bytes:  # Dừng khi body vượt giới hạn byte.
                            raise FetchBlocked("response_too_large")
                        if self.remaining_seconds() <= 0:
                            raise FetchBlocked("time_budget")
                return dict(status=status, body=bytes(body), headers=dict(response.headers),
                            content_type=content_type, elapsed=time.monotonic() - start)
        except (requests.RequestException, FetchBlocked) as exc:
            error = str(exc)
            raise
        finally:  # finally chạy cả khi request lỗi: vẫn ghi thời gian, status và nguyên nhân.
            request_log(self.db, url, kind, status, now, round(time.monotonic() - start, 4), size, error)

    def check_robots(self, url):  # Đọc robots mỗi host một lần trong lượt, rồi kiểm tra URL được phép hay không.
        parts = urlsplit(url)
        host = parts.hostname
        if host not in self.robots:  # Chưa có luật trong bộ nhớ thì tải robots.txt.
            origin = f"{parts.scheme}://{parts.netloc}"
            robot_url, error, status, body = origin + "/robots.txt", None, None, ""
            try:
                for hop in range(self.config.max_redirects + 1):
                    response = self.get(robot_url, "robots")
                    status = response["status"]
                    if status in {301, 302, 303, 307, 308}:
                        target = normalize_url(response["headers"].get("Location", ""), robot_url)  # Robots không được redirect ra ngoài host hoặc vượt số lần cho phép.
                        if not target or urlsplit(target).netloc != parts.netloc or hop == self.config.max_redirects:
                            raise FetchBlocked("robots_redirect_blocked")
                        robot_url = target
                        continue
                    if status == 200:
                        body = response["body"].decode("utf-8-sig", "replace")
                        if "text/html" in response["content_type"]:
                            raise FetchBlocked("robots_returned_html")
                        self.robots[host] = RobotsRules(body, self.config.user_agent)
                    elif status in {404, 410}:  # 404/410: robots không tồn tại; dùng tập luật rỗng.
                        self.robots[host] = RobotsRules("", self.config.user_agent)
                        error = "robots_missing"
                    else:
                        raise FetchBlocked(f"robots_http_{status}")
                    break  # Không xác định được robots thì chặn host, không tự cho phép tải tiếp.
            except (requests.RequestException, FetchBlocked) as exc:
                error = str(exc)  # Lưu snapshot robots để có bằng chứng khi audit offline.
                self.robots[host] = None
                self.blocked_hosts.add(host)
            self.db.execute("""INSERT OR REPLACE INTO robots(origin,url,status_code,checked_at,body,error)
                               VALUES(?,?,?,?,?,?)""", (origin, robot_url, status, timestamp(), body, error))
            self.db.commit()
        rules = self.robots.get(host)
        return rules is not None and rules.allows(url)

    def fetch_page(self, url):  # Tải trang và theo redirect thủ công để kiểm tra từng URL đích.
        current, redirected = url, set()
        elapsed = 0
        for hop in range(self.config.max_redirects + 1):
            if current in redirected:  # Một URL lặp trong chuỗi redirect tạo vòng lặp.
                raise FetchBlocked("redirect_loop")
            redirected.add(current)
            reason = url_reason(current, self.config.allowed_domains)  # Kiểm tra scope trước request, kể cả URL đích redirect.
            if reason:
                raise FetchBlocked("redirect_" + reason)
            if not self.check_robots(current):  # Có luật cho phép rồi mới tải HTML.
                raise FetchBlocked("robots_denied")
            response = self.get(current)
            elapsed += response["elapsed"]
            if response["status"] in {301, 302, 303, 307, 308}:
                if hop == self.config.max_redirects:
                    raise FetchBlocked("too_many_redirects")
                current = normalize_url(response["headers"].get("Location", ""), current)
                if current is None:
                    raise FetchBlocked("invalid_redirect")
                # Đích redirect đã có trong DB thì không tải lại.
                if self.db.execute("SELECT 1 FROM pages WHERE url=? OR final_url=?", (current, current)).fetchone():
                    raise FetchBlocked("redirect_already_fetched")
                continue
            response["elapsed"] = elapsed
            return response, current
        raise FetchBlocked("too_many_redirects")

    def run(self):  # Vòng BFS chính: xử lý lớp gần seed trước, commit từng trang.
        serialized = json.dumps(asdict(self.config), ensure_ascii=False, default=str)  # Lưu cấu hình để report và resume dùng lại.
        self.db.execute("INSERT OR REPLACE INTO metadata VALUES('config',?)", (serialized,))
        for seed in self.config.seeds:  # Seed là đầu vào khai báo sẵn, không phải mọi URL được khám phá.
            url = normalize_url(seed)
            if not url or url_reason(url, self.config.allowed_domains):
                raise ValueError(f"Seed không hợp lệ hoặc ngoài phạm vi: {seed}")
            enqueue(self.db, url, 0)  # Seed bắt đầu ở depth 0; INSERT OR IGNORE ngăn seed trùng.
        self.db.commit()
        frontier = deque((r["url"], r["depth"]) for r in self.db.execute(  # deque là hàng đợi trong RAM; bảng frontier là bản bền vững trong DB.
            "SELECT url,depth FROM frontier WHERE state='pending' ORDER BY depth,id"))  # Resume đọc URL chờ theo depth, rồi thứ tự được đưa vào DB.
        seen = {r[0] for r in self.db.execute("SELECT url FROM frontier")}  # seen: URL đã vào frontier; ngăn đưa vào hàng đợi lần nữa.
        done = {r[0] for r in self.db.execute("SELECT url FROM pages")}  # done: URL đã được xử lý và có dòng pages, kể cả kết quả lỗi.
        fetched_targets = {r[0] for r in self.db.execute("SELECT final_url FROM pages WHERE final_url IS NOT NULL")}  # Các URL đích cuối cùng đã xử lý, để tránh trùng sau redirect.
        domain_counts = Counter(dict(self.db.execute("SELECT domain,COUNT(*) FROM pages GROUP BY domain")))
        total = len(done)  # Đếm trang đã xử lý, không đếm dòng movies.
        deferred_depth = None
        self.config.output_dir.joinpath("html").mkdir(parents=True, exist_ok=True)
        try:
            while frontier:
                if total >= self.config.max_pages:  # Đủ max_pages thì dừng dù số phim hợp lệ có thể thấp hơn.
                    self.stop_reason = "max_pages"
                    break
                if self.remaining_seconds() <= 0:  # Hết thời gian lượt thì dừng và giữ phần đã commit.
                    self.stop_reason = "max_seconds"
                    break
                if deferred_depth is not None and frontier[0][1] > deferred_depth:  # Lớp đang bị hoãn phải được tiếp tục trước khi xuống lớp sâu hơn.
                    self.stop_reason = "bfs_layer_deferred"
                    break  # Dừng trước lớp sâu; lần sau resume lớp đang chờ.
                url, depth = frontier.popleft()  # FIFO: lấy đầu; URL mới được append ở cuối nên đi theo BFS.
                host = urlsplit(url).hostname
                if url in done:
                    continue
                if depth > self.config.max_depth:  # Depth là số bước theo link từ seed, không phải số dấu / trong URL.
                    continue
                if url in fetched_targets:
                    self.db.execute("UPDATE frontier SET state='redirect_duplicate' WHERE url=?", (url,))
                    self.db.commit()
                    continue
                if domain_counts[host] >= self.config.max_pages_per_domain or host in self.blocked_hosts:
                    deferred_depth = depth if deferred_depth is None else min(deferred_depth, depth)
                    continue  # Giữ pending trong DB; chỉ xử lý phần còn lại của lớp hiện tại.
                now, start = timestamp(), time.monotonic()
                record = dict(url=url, domain=host, title="", content="", depth=depth,  # Bản ghi TRANG luôn có trạng thái, dù có trích được PHIM hay không.
                              status_code=None, crawled_at=now, final_url=url, page_type="unknown",
                              crawl_status="request_error", response_time=0, error=None, html_path=None)
                movie, new_links = None, []  # Ban đầu chưa có phim; parse thành công mới có thể tạo movie.
                try:
                    response, final = self.fetch_page(url)
                    record.update(status_code=response["status"], final_url=final,
                                  response_time=round(response["elapsed"], 4))
                    if response["status"] != 200:  # HTTP khác 200 được ghi lỗi, không đem parse như trang phim.
                        record.update(crawl_status="http_error", error=f"HTTP {response['status']}")
                        if response["status"] in {403, 429}:  # 403/429: ngừng gửi thêm request tới host trong lượt này.
                            self.blocked_hosts.add(host)
                    elif not response["body"]:  # Body rỗng hoặc không phải HTML thì không parse phim.
                        record.update(crawl_status="non_html", error="empty_or_non_html_response")
                    else:
                        html = response["body"]
                        html_path = self.config.output_dir / "html" / (hashlib.sha256(url.encode()).hexdigest() + ".html")  # SHA-256 băm URL để đặt tên cache; chưa chống trùng nội dung.
                        html_path.write_bytes(html)  # Giữ HTML gốc để kiểm tra và reparse mà không cào lại.
                        record["html_path"] = str(html_path)
                        # Truyền bytes để BeautifulSoup xét charset trong HTML.
                        parsed = parse_page(html, final, now)
                        if parsed["movie"] is not None:  # Chỉ kiểm tra schema phim khi parser trả về một bản ghi.
                            validate_movie(parsed["movie"])
                        record.update(title=parsed["title"], content=parsed["content"],
                                      page_type=parsed["page_type"], crawl_status="ok", error=parsed["parse_error"])
                        movie = parsed["movie"]  # None nếu là danh mục hoặc phim bị loại; trang vẫn được lưu vào pages.
                        for href in parsed["hrefs"]:  # Mỗi href là ứng viên; thấy link chưa có nghĩa sẽ tải link đó.
                            target = normalize_url(href, final)  # Ghép URL tương đối với trang hiện tại rồi chuẩn hóa.
                            if target is None:
                                continue  # Bỏ mailto/javascript và giá trị không thành URL web; không lưu email.
                            reason = url_reason(target, self.config.allowed_domains)  # Loại domain khác, file media, trang player và đường dẫn ngoài phạm vi.
                            if reason is None and depth + 1 > self.config.max_depth:  # Link con có depth cha + 1; vượt trần thì không enqueue.
                                reason = "max_depth"
                            if reason is None and target in seen:  # Cùng URL có thể xuất hiện nhiều nơi; chỉ nhận vào queue lần đầu.
                                reason = "duplicate"
                            if reason is None:
                                rules = self.robots.get(urlsplit(target).hostname)
                                if rules is not None and not rules.allows(target):
                                    reason = "robots_denied"
                            new_links.append((url, target, int(reason is None), reason))  # Ghi cạnh link và lý do loại; accepted=1 khi chưa có reason.
                            if reason is None:
                                seen.add(target)  # Đánh dấu ngay lúc nhận link, không đợi tải xong mới đánh dấu.
                                enqueue(self.db, target, depth + 1, url)  # Lưu URL, depth và URL cha vào frontier trong DB.
                                frontier.append((target, depth + 1))  # Thêm cuối queue RAM để chờ lượt BFS tiếp theo.
                except requests.RequestException as exc:  # Timeout hoặc lỗi kết nối: ghi lỗi rồi chuyển sang URL khác.
                    record.update(crawl_status="request_error", error=str(exc))
                except FetchBlocked as exc:  # Lỗi do giới hạn/robots/scope: ghi trạng thái blocked.
                    record.update(crawl_status="blocked", error=str(exc))
                except (ValueError, TypeError, KeyError, AttributeError, IndexError) as exc:  # Lỗi đọc HTML hoặc schema: không lưu movie sai, vẫn lưu trang và cache.
                    movie = None
                    record.update(crawl_status="parse_error", error=f"{type(exc).__name__}: {exc}")
                if record["status_code"] is None:
                    record["response_time"] = round(time.monotonic() - start, 4)
                self.db.executemany("""INSERT OR IGNORE INTO links(source_url,target_url,accepted,skip_reason)
                                       VALUES(?,?,?,?)""", new_links)  # Chống trùng cặp nguồn/đích; vẫn giữ cạnh từ nguồn khác.
                save_page(self.db, record, movie)  # Lưu trang, lưu phim nếu có, đánh dấu URL done.
                self.db.commit()  # Chốt kết quả trang vừa xử lý vào SQLite.
                done.add(url)
                fetched_targets.add(record["final_url"])
                domain_counts[host] += 1
                total += 1  # Tăng một TRANG cho mỗi URL đã lưu; không phải tăng một PHIM.
                print(f"[{total:03d}] depth={depth} status={record['status_code']} "
                      f"{record['crawl_status']} links={len(new_links)} {url}", flush=True)
                print(f"    Title: {record['title']} | Response time: {record['response_time']:.4f}s", flush=True)
            if not self.stop_reason:
                self.stop_reason = "domain_budget_or_blocked" if self.db.execute(
                    "SELECT 1 FROM frontier WHERE state='pending'").fetchone() else "frontier_empty"
        except KeyboardInterrupt:  # Ctrl+C: dừng nhẹ, giữ kết quả các trang đã commit.
            self.db.rollback()  # Hủy phần giao dịch chưa commit, tránh lưu nửa kết quả.
            self.stop_reason = "interrupted"
        finally:  # Luôn đóng phiên HTTP và ghi lý do dừng để báo cáo.
            self.session.close()
            self.db.execute("INSERT OR REPLACE INTO metadata VALUES('last_stop_reason',?)", (self.stop_reason or "error",))
            self.db.commit()
        return self.stop_reason
