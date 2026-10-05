"""Chuẩn hóa URL, giới hạn metadata HTML và tôn trọng robots.txt."""
import re
from pathlib import PurePosixPath
from urllib.parse import quote, unquote, urljoin, urlsplit, urlunsplit
# Chỉ cào trang metadata HTML; các file media và trang phát video nằm ngoài phạm vi.
NON_HTML_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".gif", ".svg", ".webp", ".ico", ".css", ".js",
    ".pdf", ".zip", ".rar", ".7z", ".gz", ".xml", ".json", ".mp4", ".mp3",
    ".avi", ".webm", ".m3u8", ".ts", ".woff", ".woff2", ".ttf", ".exe", ".docx", ".xlsx",
}
LISTINGS = {"phim-le", "doraemon", "conan", "shin-cau-be-but-chi"}  # Danh mục có thể đi theo phân trang /page/N.
OTHER_CATEGORIES = {"co-trang", "hai-huoc", "hanh-dong", "hien-dai", "huyen-ao", "kiem-hiep",
                    "phieu-luu", "tien-hiep", "trung-sinh", "vo-thuat", "phim-bo", "sieu-nhan",
                    "7-vien-ngoc-rong", "anime-trung-quoc", "home"}


def normalized_percent(match):  # Chuẩn hóa percent-encoding: %61 tương đương a, nhưng không giải mã mọi ký tự.
    value = chr(int(match[0][1:], 16))
    return value if value.isascii() and (value.isalnum() or value in "-._~") else match[0].upper()


def normalize_url(href, base=""):  # Ghép href tương đối với base, chuẩn hóa URL; không hợp lệ thì trả None.
    try:
        if not isinstance(href, str) or not href.strip():
            return None
        parts = urlsplit(urljoin(base, href.strip()))  # Ví dụ href='/phim-a' trên seed trở thành URL đầy đủ cùng website.
        if parts.scheme.lower() not in {"http", "https"} or not parts.hostname or parts.username or parts.password:  # Bỏ mailto/javascript và URL kèm tài khoản đăng nhập.
            return None
        host = parts.hostname.encode("idna").decode("ascii").lower()
        port = parts.port
        scheme = parts.scheme.lower()
        if host in {"xemanime.org", "www.xemanime.org"} and port in (None, 80, 443):  # Gộp alias http/www của XemAnime về HTTPS xemanime.org.
            host, scheme, port = "xemanime.org", "https", None
        netloc = f"[{host}]" if ":" in host else host
        if port and not (scheme == "http" and port == 80 or scheme == "https" and port == 443):
            netloc += f":{port}"
        path = quote(parts.path or "/", safe="/%:@!$&'()*+,;=-._~")
        path = re.sub(r"%[0-9a-fA-F]{2}", normalized_percent, path)
        if host == "xemanime.org" and path != "/":
            path = path.rstrip("/")
        query = re.sub(r"%[0-9a-fA-F]{2}", normalized_percent, quote(parts.query, safe="%=&;/:?@!$'()*+,-._~"))
        return urlunsplit((scheme, netloc, path, query, ""))  # Bỏ fragment #... vì không tạo một trang HTTP khác; vẫn giữ query để lọc.
    except (ValueError, UnicodeError):
        return None


def url_reason(url, allowed_domains):  # None = URL được nhận; chuỗi khác = lý do loại, lưu vào links.skip_reason.
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"}:
        return "invalid_protocol"
    if parts.hostname not in allowed_domains:  # So hostname thật, tránh nhận nhầm xemanime.org.example.com.
        return "outside_domain"
    path = unquote(parts.path).lower()
    if PurePosixPath(path).suffix in NON_HTML_EXTENSIONS:  # Loại theo đuôi file trước khi gửi request.
        return "non_html_extension"
    if parts.hostname == "xemanime.org":
        if parts.query:  # Không cào URL query, ví dụ trang tìm kiếm ?s=... của XemAnime.
            return "query_excluded"
        slug = path.strip("/")
        if slug.startswith(("watch-", "wp-")) or slug in {"robots.txt", "feed", "comments", "login", "register"}:  # Loại player watch- và khu vực hệ thống WordPress.
            return "player_or_system_excluded"
        if slug in OTHER_CATEGORIES:  # Giới hạn danh mục theo chủ đề/phạm vi đã chọn.
            return "outside_selected_categories"
        if path == "/" or re.fullmatch(r"/[a-z0-9][a-z0-9_-]*", path):  # Chấp nhận root hoặc slug metadata; parse mới quyết định đó có phải phim.
            return None
        if any(re.fullmatch(r"/" + category + r"/page/[1-9]\d*", path) for category in LISTINGS):  # Cho phép trang tiếp theo của đúng danh mục trong LISTINGS.
            return None
        return "outside_metadata_paths"
    return None  # Host khác chỉ dùng trong HTTP server local của tests, không phải scope crawl thật.


class RobotsRules:
    """Wildcard rules: longest match wins; Allow wins ties."""
    def __init__(self, text, user_agent):  # Đọc nhóm User-agent, Allow/Disallow và Crawl-delay của robots.
        groups, agents, rules, delay = [], [], [], 0.0
        has_rules = False
        for line in text.lstrip("\ufeff").splitlines():
            key, sep, value = line.split("#", 1)[0].partition(":")  # Bỏ phần chú thích # trong robots rồi tách tên luật và giá trị.
            if not sep:
                continue
            key, value = key.strip().lower(), value.strip()
            if key == "user-agent":
                if has_rules:
                    groups.append((agents, rules, delay))
                    agents, rules, delay, has_rules = [], [], 0.0, False
                agents.append(value.lower())
            elif agents and key in {"allow", "disallow", "crawl-delay"}:
                has_rules = True
                if key == "crawl-delay":
                    try:
                        delay = max(delay, float(value))
                    except ValueError:
                        pass
                elif value:
                    rules.append((value, key == "allow"))
        if agents:
            groups.append((agents, rules, delay))
        agent = user_agent.split("/", 1)[0].lower()
        matches = []
        for names_, entries, wait in groups:
            score = max((len(n) if n != "*" else 0 for n in names_ if n == "*" or n in agent), default=-1)  # Ưu tiên nhóm User-agent khớp cụ thể hơn nhóm *.
            if score >= 0:
                matches.append((score, entries, wait))
        best = max((m[0] for m in matches), default=-1)
        self.rules = [entry for score, entries, _ in matches if score == best for entry in entries]
        self.delay = max((wait for score, _, wait in matches if score == best), default=0)

    def allows(self, url):  # Kiểm tra một URL bằng các luật của nhóm đã chọn.
        parts = urlsplit(url)
        target = parts.path or "/"
        if parts.query:  # Query cũng là một phần đường cần so với luật robots.
            target += "?" + parts.query
        matches = []
        for pattern, allowed in self.rules:  # Dấu $ yêu cầu khớp cuối đường dẫn; * đại diện nhiều ký tự.
            anchored = pattern.endswith("$")
            plain = pattern[:-1] if anchored else pattern
            regex = "^" + re.escape(plain).replace(r"\*", ".*")
            if anchored:
                regex += "$"
            if re.search(regex, target):
                matches.append((len(plain.replace("*", "")), allowed))  # Tính độ dài phần cụ thể của luật để chọn luật khớp dài nhất.
        return max(matches, default=(0, True))[1]  # Luật dài nhất thắng; ngang độ dài thì Allow thắng; không khớp luật thì cho phép.
