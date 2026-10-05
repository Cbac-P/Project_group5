"""Chuẩn hóa URL, lọc phạm vi crawl và đọc robots.txt."""

import re
from pathlib import PurePosixPath
from urllib.parse import parse_qsl, quote, unquote, urlencode, urljoin, urlsplit, urlunsplit

NON_HTML_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".gif", ".svg", ".webp", ".ico", ".css", ".js",
    ".pdf", ".zip", ".rar", ".7z", ".gz", ".xml", ".json", ".mp4", ".mp3",
    ".avi", ".webm", ".woff", ".woff2", ".ttf", ".exe", ".docx", ".xlsx",
}
MOMO_LISTINGS = {"/cinema", "/cinema/", "/cinema/phim-chieu", "/cinema/top-phim", "/cinema/review"}
MOMO_DETAIL = re.compile(r"/cinema/[^/]+-\d+/?")   # /cinema/<slug>-<id>
TV360_DETAIL = re.compile(r"/movie/[^/]+")        # /movie/<slug>?m=<id>
TV360_LISTING = re.compile(r"/movies(/[^/]+)?")   # /movies hoặc /movies/<thể-loại>?c=<id>
WIKI_NAMESPACES = {
    "đặc biệt", "special", "thảo luận", "talk", "thành viên", "user",
    "thảo luận thành viên", "user talk", "wikipedia", "thảo luận wikipedia",
    "tập tin", "hình", "file", "image", "thảo luận tập tin", "bản mẫu",
    "template", "thảo luận bản mẫu", "trợ giúp", "help", "thảo luận trợ giúp",
    "cổng thông tin", "portal", "mediawiki", "mô đun", "module",
    "thảo luận thể loại", "category talk",
}


def normalize_url(href, base=""):
    """Ghép link tương đối, bỏ fragment, chuẩn hóa host/port/percent-encoding.

    Trả về None nếu không phải link web (mailto:, javascript:, tel:...).
    """
    if not isinstance(href, str) or not href.strip():
        return None
    try:
        parts = urlsplit(urljoin(base, href.strip()))
        host, port = parts.hostname, parts.port
    except ValueError:
        return None
    scheme = parts.scheme.lower()
    if scheme not in ("http", "https") or not host:
        return None
    if host == "momo.vn":
        host = "www.momo.vn"
    netloc = host if port in (None, 80 if scheme == "http" else 443) else f"{host}:{port}"
    # Từng đoạn path: unquote rồi quote lại, nên "/a" = "/%61" và "Thể" = "Th%E1%BB%83",
    # còn %2F trong một đoạn vẫn được giữ nguyên (không biến thành dấu phân cách).
    path = "/".join(quote(unquote(seg), safe="!$&'()*+,;=-._~:@") for seg in (parts.path or "/").split("/"))
    query = parts.query
    if host == "tv360.vn":   # bỏ tham số theo dõi (col, sect, page...), chỉ giữ id phim / id thể loại
        keep = ("m",) if path.startswith("/movie/") else ("c",)
        query = urlencode([(k, v) for k, v in parse_qsl(query) if k in keep])
    return urlunsplit((scheme, netloc, path, query, ""))


def url_reason(url, allowed_domains):
    """None nếu URL nằm trong phạm vi crawl; ngược lại trả về lý do loại."""
    parts = urlsplit(url)
    path = unquote(parts.path)
    if parts.hostname not in allowed_domains:
        return "outside_domain"
    if PurePosixPath(path.lower()).suffix in NON_HTML_EXTENSIONS:
        return "non_html_extension"
    if parts.hostname == "www.momo.vn":
        if parts.query:
            return "momo_query_blocked"
        if path in MOMO_LISTINGS or MOMO_DETAIL.fullmatch(path):
            return None
        return "outside_movie_paths"
    if parts.hostname == "tv360.vn":
        if TV360_DETAIL.fullmatch(path):
            return None if re.fullmatch(r"m=\d+", parts.query) else "tv360_missing_id"
        if TV360_LISTING.fullmatch(path.rstrip("/")) and re.fullmatch(r"(c=\d+)?", parts.query):
            return None
        return "outside_movie_paths"
    if parts.hostname == "vi.wikipedia.org":
        if parts.query:
            return "wiki_query_excluded"
        if not path.startswith("/wiki/"):
            return "outside_wiki_articles"
        title = path[len("/wiki/"):].replace("_", " ")
        if ":" in title and title.split(":", 1)[0].casefold() in WIKI_NAMESPACES:
            return "wiki_namespace"
        if title.casefold().startswith(("danh sách", "list of")):
            return "wiki_list_excluded"
    return None


class Robots:
    """robots.txt tối giản: chọn nhóm User-agent phù hợp (hoặc *), hỗ trợ * và $,
    quy tắc khớp dài nhất thắng. urllib.robotparser không đủ vì MoMo khai báo
    'Allow: /' rồi mới đến 'Disallow: /*?'."""

    def __init__(self, text, user_agent):
        agent = user_agent.split("/", 1)[0].lower()
        groups, names, rules, delay, has_rules = [], [], [], 0.0, False
        for line in text.lstrip("\ufeff").splitlines():
            key, _, value = line.split("#", 1)[0].partition(":")
            key, value = key.strip().lower(), value.strip()
            if key == "user-agent":
                if has_rules:
                    groups.append((names, rules, delay))
                    names, rules, delay, has_rules = [], [], 0.0, False
                names.append(value.lower())
            elif key == "crawl-delay" and names:
                has_rules = True
                try:
                    delay = float(value)
                except ValueError:
                    pass
            elif key in ("allow", "disallow") and names:
                has_rules = True
                if value:
                    rules.append((value, key == "allow"))
        groups.append((names, rules, delay))
        specific = [(r, d) for n, r, d in groups if any(x != "*" and x in agent for x in n)]
        general = [(r, d) for n, r, d in groups if "*" in n]
        chosen = specific or general
        self.rules = [rule for group, _ in chosen for rule in group]
        self.crawl_delay = max((d for _, d in chosen), default=0.0)   # giây; 0 nếu không khai báo

    def allows(self, url):
        parts = urlsplit(url)
        target = (parts.path or "/") + ("?" + parts.query if parts.query else "")
        matches = []
        for pattern, allowed in self.rules:
            plain = pattern.rstrip("$")
            regex = "^" + re.escape(plain).replace(r"\*", ".*") + ("$" if pattern.endswith("$") else "")
            if re.search(regex, target):
                matches.append((len(plain.replace("*", "")), allowed))
        return max(matches, default=(0, True))[1]   # hòa độ dài thì Allow thắng
