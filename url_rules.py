"""Chuẩn hóa URL, lọc phạm vi crawl và đọc robots.txt."""

import re
from pathlib import PurePosixPath
from urllib.parse import quote, unquote, urljoin, urlsplit, urlunsplit

NON_HTML_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".gif", ".svg", ".webp", ".ico", ".css", ".js",
    ".pdf", ".zip", ".rar", ".7z", ".gz", ".xml", ".json", ".mp4", ".mp3",
    ".avi", ".webm", ".woff", ".woff2", ".ttf", ".exe", ".docx", ".xlsx",
}
W2W_LISTING = re.compile(r"/phim/?(page/\d+/?)?")
W2W_DETAIL = re.compile(r"/phim/(?!page/)[^/]+/?")
W2W_PAGE_QUERY = re.compile(r"(page|paged|trang)=\d+")

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
    if host == "www.w2w.vn":
        host = "w2w.vn"
    netloc = host if port in (None, 80 if scheme == "http" else 443) else f"{host}:{port}"
    # Từng đoạn path: unquote rồi quote lại, nên "/a" = "/%61" và "Thể" = "Th%E1%BB%83",
    # còn %2F trong một đoạn vẫn được giữ nguyên (không biến thành dấu phân cách).
    path = "/".join(quote(unquote(seg), safe="!$&'()*+,;=-._~:@") for seg in (parts.path or "/").split("/"))
    if host == "w2w.vn" and not path.endswith("/"):
        path += "/"   # /phim/abc và /phim/abc/ là một trang
    return urlunsplit((scheme, netloc, path, parts.query, ""))


def url_reason(url, allowed_domains):
    """None nếu URL nằm trong phạm vi crawl; ngược lại trả về lý do loại."""
    parts = urlsplit(url)
    path = unquote(parts.path)
    if parts.hostname not in allowed_domains:
        return "outside_domain"
    if PurePosixPath(path.lower()).suffix in NON_HTML_EXTENSIONS:
        return "non_html_extension"
    if parts.hostname == "w2w.vn":
        if parts.query and not (W2W_PAGE_QUERY.fullmatch(parts.query) and W2W_LISTING.fullmatch(path)):
            return "w2w_query_blocked"
        if W2W_LISTING.fullmatch(path) or W2W_DETAIL.fullmatch(path):
            return None
        return "outside_movie_paths"   # blog, cộng đồng, bảng xếp hạng...
    return None

class Robots:
    """robots.txt tối giản: chọn nhóm User-agent phù hợp (hoặc *), hỗ trợ * và $,
    quy tắc khớp dài nhất thắng. urllib.robotparser không đủ vì MoMo khai báo
    'Allow: /' rồi mới đến 'Disallow: /*?'."""

    def __init__(self, text, user_agent):
        agent = user_agent.split("/", 1)[0].lower()
        groups, names, rules, has_rules = [], [], [], False
        for line in text.lstrip("\ufeff").splitlines():
            key, _, value = line.split("#", 1)[0].partition(":")
            key, value = key.strip().lower(), value.strip()
            if key == "user-agent":
                if has_rules:
                    groups.append((names, rules))
                    names, rules, has_rules = [], [], False
                names.append(value.lower())
            elif key in ("allow", "disallow") and names:
                has_rules = True
                if value:
                    rules.append((value, key == "allow"))
        groups.append((names, rules))
        specific = [r for n, r in groups if any(x != "*" and x in agent for x in n)]
        general = [r for n, r in groups if "*" in n]
        self.rules = [rule for group in (specific or general) for rule in group]

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
