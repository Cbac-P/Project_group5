"""Đọc HTML: tiêu đề, văn bản, liên kết và (nếu là trang phim) thông tin phim."""

import json
import re
import unicodedata
from datetime import datetime, timezone
from urllib.parse import unquote, urlsplit

from bs4 import BeautifulSoup, Comment

from url_rules import W2W_DETAIL

HIDDEN = ('script, style, noscript, template, [hidden], [aria-hidden="true"], '
          '[style*="display:none"], [style*="display: none"]')   # chỉ giữ văn bản nhìn thấy

def compact(text):
    return re.sub(r"\s+", " ", text or "").strip()


def fold(text):
    """Chữ thường, bỏ dấu tiếng Việt: dùng để so khớp nhãn."""
    text = unicodedata.normalize("NFD", compact(text).casefold().replace("đ", "d"))
    return "".join(c for c in text if unicodedata.category(c) != "Mn")


def names(value):
    """Chuỗi 'a, b' hoặc list (str / dict có Name) → list tên không trùng."""
    if isinstance(value, str):
        value = value.split(",")
    result = []
    for item in value if isinstance(value, list) else []:
        if isinstance(item, dict):
            item = item.get("Name") or item.get("name") or item.get("Title")
        if isinstance(item, str) and compact(item):
            result.append(compact(item))
    return list(dict.fromkeys(result))


def release_status(dates, now):
    """'upcoming' nếu ngày phát hành sớm nhất còn ở tương lai, ngược lại 'released'."""
    today = datetime.fromisoformat(now).astimezone(timezone.utc).date()
    return "upcoming" if min(dates) > today else "released"



def new_movie(source, url, now):
    return dict(
        source=source, source_url=url, title="", release_year=None, synopsis="",
        genre=[], director=[], cast=[], country=[],
        source_release_date=None, release_status="unknown", crawled_at=now)


# ---------- W2W ----------

W2W_GENRES = {fold(g) for g in (
    "Hành động", "Phiêu lưu", "Hoạt hình", "Hài hước", "Tội phạm", "Tài liệu", "Chính kịch", "Gia đình",
    "Tưởng tượng", "Lịch sử", "Kinh dị", "Âm nhạc", "Bí ẩn", "Tình cảm", "Viễn tưởng", "Truyền hình",
    "Giật gân", "Chiến tranh", "Miền Tây", "Tâm lý", "Nhạc kịch", "Siêu anh hùng", "Khoa học",
    "Cảnh quay thật", "Hài hành động", "Hài tình cảm", "Võ thuật", "Quái vật")}
W2W_LABELS = {"khoi chieu", "dao dien", "dien vien", "quoc gia", "quoc gia san xuat", "nuoc san xuat"}
W2W_STOP = ("Chưa xem", "Yêu thích", "Đánh giá cộng đồng", "Phim cùng chủ đề", "Bài viết liên quan")
COUNTRY_KEYS = {"country", "countries", "production_countries", "productioncountries"}
# Ký tự chỉ có trong tiếng Việt (bỏ các chữ có dấu dùng chung với Pháp/Bồ Đào Nha...).
VI_ONLY = set("ăơưđảạằắẳẵặầấẩẫậẻẽẹềếểễệỉịọỏồốổỗộờớởỡợụủừứửữựỳỷỹỵ")


def is_vietnamese(text):
    """Văn bản đủ dài và có tỉ lệ chữ đặc trưng tiếng Việt; tiếng Anh/Trung/Nhật bị loại."""
    letters = [c for c in text.casefold() if c.isalpha()]
    hits = sum(c in VI_ONLY for c in letters)
    return len(letters) >= 15 and hits >= 3 and hits / len(letters) >= 0.05


def find_values(obj, keys):
    """Duyệt JSON lồng nhau, trả về các giá trị có khóa nằm trong keys."""
    found = []
    if isinstance(obj, dict):
        for key, value in obj.items():
            if key.casefold() in keys:
                found.append(value)
            else:
                found.extend(find_values(value, keys))
    elif isinstance(obj, list):
        for value in obj:
            found.extend(find_values(value, keys))
    return found


def embedded_country(soup):
    for script in soup.select('script#__NEXT_DATA__, script[type="application/ld+json"]'):
        try:
            data = json.loads(script.get_text())
        except ValueError:
            continue
        for value in find_values(data, COUNTRY_KEYS):
            found = names(value)
            if found:
                return found
    return []


def w2w_split_label(line):
    """'Đạo diễn: A, B' -> ('dao dien', 'A, B'); 'Đạo diễn:' -> ('dao dien', ''); khác -> (None, line)."""
    head, sep, tail = line.partition(":")
    key = fold(head)
    if sep and key in W2W_LABELS:
        return key, compact(tail)
    return None, line


def w2w_movie(soup, url, now):
    heading = soup.select_one("h1")
    title = compact(heading.get_text(" ", strip=True)) if heading else ""
    if not title:
        node = soup.select_one("head > title")
        title = re.sub(r"\s*[-|–]\s*W2W\s*$", "", compact(node.get_text(" ", strip=True))) if node else ""
    if not title:
        return None, "no_title"

    # Văn bản nhìn thấy, mỗi node một dòng; chỉ xét phần đầu trang (trước khu đánh giá).
    lines = [compact(s) for s in soup.find_all(string=True)
             if s.parent.name not in ("script", "style", "noscript", "template", "title")
             and not isinstance(s, Comment)]
    lines = [s for s in lines if s]
    if title not in lines:
        return None, "no_film_header"
    first = lines.index(title)
    stop = next((i for i in range(first + 1, len(lines)) if lines[i].startswith(W2W_STOP)), first + 40)
    region = lines[first + 1:stop]

    values, genres, text_lines, key = {}, [], [], None
    for line in region:
        if line == title:
            continue
        label, value = w2w_split_label(line)
        if label:
            key = label
            values.setdefault(key, [])
            if value:
                values[key].append(value)
        elif key == "khoi chieu" and not values[key]:
            values[key].append(line)
        elif fold(line) in W2W_GENRES:
            genres.append(line)
        elif key in ("dao dien", "dien vien", "quoc gia", "quoc gia san xuat", "nuoc san xuat"):
            values[key].append(line)
        else:
            text_lines.append(line)

    def listed(*labels):
        return names(", ".join(v for label in labels for v in values.get(label, [])))

    movie = new_movie("w2w", url, now)
    movie.update(title=title, genre=list(dict.fromkeys(genres)), director=listed("dao dien"),
                 cast=listed("dien vien"),
                 country=listed("quoc gia", "quoc gia san xuat", "nuoc san xuat") or embedded_country(soup))

    error = None
    synopsis = max((s for s in text_lines if len(s) >= 30), key=len, default="")
    if not synopsis:
        error = "no_synopsis"
    elif not is_vietnamese(synopsis):
        error = "synopsis_not_vietnamese"   # không tự dịch/viết thêm: để rỗng
        synopsis = ""
    movie["synopsis"] = synopsis

    found = re.search(r"(\d{1,2})/(\d{1,2})/((?:18|19|20)\d{2})", " ".join(values.get("khoi chieu", [])))
    if found:
        day, month, year = map(int, found.groups())
        movie["release_year"] = year
        try:
            opening = datetime(year, month, day).date()
            movie["source_release_date"] = opening.isoformat()
            movie["release_status"] = release_status([opening], now)
        except ValueError:
            pass
    return movie, error

# ---------- Điểm vào ----------

def parse_page(html, url, now):
    """html: bytes (để BeautifulSoup tự nhận charset) hoặc str.

    Trả về dict(title, content, page_type, movie, hrefs, parse_error).
    """
    soup = BeautifulSoup(html, "html.parser")
    host, path = urlsplit(url).hostname, unquote(urlsplit(url).path)
    hrefs = list(dict.fromkeys(a["href"] for a in soup.select("a[href]")))

    movie, error, page_type = None, None, "other"
    if host == "w2w.vn":
        is_detail = bool(W2W_DETAIL.fullmatch(path))
        if is_detail:
            movie, error = w2w_movie(soup, url, now)
        page_type = "movie" if movie else ("movie_candidate" if is_detail else "listing")

    title_node = soup.select_one("head > title")
    for node in soup.select(HIDDEN):   # JSON nhúng đã đọc xong ở trên, giờ mới bỏ khỏi văn bản
        if not node.decomposed:
            node.decompose()
    return dict(title=compact(title_node.get_text(" ", strip=True)) if title_node else "",
                content=compact(soup.get_text(" ", strip=True)), page_type=page_type,
                movie=movie, hrefs=hrefs, parse_error=error)