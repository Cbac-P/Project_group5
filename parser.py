"""Đọc HTML: tiêu đề, văn bản, liên kết và (nếu là trang phim) thông tin phim."""

import json
import re
import unicodedata
from datetime import datetime, timezone
from urllib.parse import parse_qs, unquote, urlsplit

from bs4 import BeautifulSoup

from url_rules import MOMO_DETAIL, TV360_DETAIL

HIDDEN = ('script, style, noscript, template, [hidden], [aria-hidden="true"], '
          '[style*="display:none"], [style*="display: none"]')   # chỉ giữ văn bản nhìn thấy
PLOT_SECTIONS = {"cot truyen", "noi dung", "noi dung phim", "tom tat", "tom tat noi dung", "plot", "synopsis"}
TV_FIELDS = {"so mua", "so tap", "mang truyen hinh", "phat song"}
FILM_FIELDS = {"dao dien", "cong chieu", "phat hanh", "thoi luong", "kich ban"}
WIKI_GENRES = ("kinh dị", "hài", "hành động", "khoa học viễn tưởng", "kỳ ảo", "giả tưởng", "chính kịch",
               "tình cảm", "lãng mạn", "phiêu lưu", "hoạt hình", "giật gân", "tội phạm", "chiến tranh")


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
        source=source, source_id=None, source_url=url, title="", original_title=None,
        release_year=None, source_release_date=None, release_status="unknown",
        synopsis="", introduction="", genre=[], country=[], director=[], cast=[],
        characters=[], external_imdb_id=None, external_tmdb_id=None, crawled_at=now)


# ---------- MoMo: dữ liệu phim nằm trong JSON __NEXT_DATA__ ----------

def momo_movie(soup, url, now):
    script = soup.select_one("script#__NEXT_DATA__")
    if not script:
        return None, "no_film_json"
    try:
        payload = json.loads(script.get_text())
        data = payload.get("props", {}).get("pageProps", {}).get("FilmData", {}).get("Data")
    except (ValueError, AttributeError):
        return None, "invalid_film_json"
    if not isinstance(data, dict) or not data.get("Id") or not data.get("Title"):
        return None, "no_film_record"
    casts = data.get("ApiCasts") or []
    extras = data.get("Extras") if isinstance(data.get("Extras"), dict) else {}
    movie = new_movie("momo", url, now)
    movie.update(
        source_id=str(data["Id"]), title=compact(data["Title"]),
        original_title=data.get("TitleEn") or None,
        synopsis=compact(BeautifulSoup(data.get("Synopsis") or "", "html.parser").get_text(" ", strip=True)),
        source_release_date=data.get("OpeningDate"),
        genre=names(data.get("Projects") or data.get("ApiGenreName")),
        country=names(data.get("Countries")),
        director=names(data.get("ApiDirectors")), cast=names(casts),
        characters=names([c.get("character") for c in casts if isinstance(c, dict)]),
        external_imdb_id=extras.get("imdbId"), external_tmdb_id=extras.get("tmdbId"))
    # OpeningDate là ngày chiếu tại rạp địa phương, không được coi là năm phát hành toàn cầu.
    for key in ("ReleaseYear", "Year"):
        if re.fullmatch(r"\d{4}", str(data.get(key, ""))):
            movie["release_year"] = int(data[key])
            break
    try:
        opening = datetime.fromisoformat(movie["source_release_date"]).date()
        movie["release_status"] = release_status([opening], now)
    except (TypeError, ValueError):
        pass
    return movie, None


# ---------- TV360: tiêu đề "Tên Việt - Tên gốc (năm)", mô tả trong meta, nhãn Diễn viên/Đạo diễn/Quốc gia ----------

TV360_LABELS = {"dien vien", "dao dien", "quoc gia"}


def tv360_field(soup, label):
    """Nhãn 'Diễn viên:' → danh sách link ngay sau nhãn, dừng khi gặp chữ khác (nhãn kế tiếp, 'Bình luận'...)."""
    start = soup.find(string=lambda s: s and fold(s).rstrip(": ") == label)
    found = []
    for el in (start.next_elements if start else []):
        if isinstance(el, str):
            if el.parent.name == "a" or not compact(el).strip(", "):
                continue   # chữ bên trong link (đã lấy ở thẻ <a>) hoặc dấu phẩy ngăn cách
            break
        if el.name == "a" and compact(el.get_text(" ", strip=True)):
            found.append(compact(el.get_text(" ", strip=True)))
    return list(dict.fromkeys(found))


def tv360_movie(soup, url, now):
    heading = soup.select_one("h1")
    if heading is None:
        return None, "no_film_record"
    meta = lambda name: (soup.select_one(f'meta[property="{name}"], meta[name="{name}"]') or {}).get("content", "")
    full_title = compact(meta("og:title") or heading.get_text(" ", strip=True))
    year = re.search(r"\((\d{4})\)\s*$", full_title)
    names_part = re.sub(r"\s*\(\d{4}\)\s*$", "", full_title)
    parts = [compact(v) for v in names_part.split(" - ")]
    movie = new_movie("tv360", url, now)
    movie.update(
        source_id=(parse_qs(urlsplit(url).query).get("m") or [None])[0],
        title=parts[0], original_title=parts[1] if len(parts) == 2 else None,
        release_year=int(year[1]) if year else None, release_status="released",
        synopsis=compact(meta("og:description") or meta("description")),
        country=tv360_field(soup, "quoc gia"), director=tv360_field(soup, "dao dien"),
        cast=tv360_field(soup, "dien vien"))
    genres = []   # link thể loại nằm sau tiêu đề; "Phim Trung Quốc" là bộ sưu tập theo nước nên bỏ
    for a in heading.find_all_next("a", href=re.compile(r"/movies/[^?]*\?c=\d+")):
        name = compact(a.get_text(" ", strip=True))
        if name and not fold(name).startswith("phim "):
            genres.append(name)
    movie["genre"] = list(dict.fromkeys(genres))
    if not movie["title"] or not movie["synopsis"]:
        return None, "no_film_record"
    return movie, None


# ---------- Wikipedia: infobox + mục Cốt truyện ----------

def intro_and_plot(root):
    """Tối đa 3 đoạn mở đầu (trước tiêu đề mục đầu tiên) và nội dung mục cốt truyện."""
    intro, plot, plot_level, before_heading = [], [], None, True
    for node in root.find_all(["h2", "h3", "h4", "h5", "h6", "p", "ul", "ol"]):
        if node.find_parent(["table", "ul", "ol"]):
            continue
        text = compact(node.get_text(" ", strip=True))
        if node.name[0] == "h":
            before_heading = False
            level = int(node.name[1])
            if plot_level is not None and level <= plot_level:
                break
            if plot_level is None and re.sub(r"\[.*?\]", "", fold(text)).strip() in PLOT_SECTIONS:
                plot_level = level
        elif plot_level is not None:
            if text:
                plot.append(text)
        elif before_heading and node.name == "p" and len(intro) < 3 and text:
            intro.append(text)
    return "\n".join(intro), "\n".join(plot)


def infobox_fields(infobox):
    """Bảng thông tin → ({nhãn bỏ dấu: text}, {nhãn bỏ dấu: ô <td>})."""
    fields, cells = {}, {}
    for row in infobox.find_all("tr") if infobox else []:
        label, value = row.find("th"), row.find("td")
        if label and value:
            key = fold(label.get_text(" ", strip=True))
            fields[key], cells[key] = compact(value.get_text(" ", strip=True)), value
    return fields, cells


def wiki_original_title(infobox, title, intro, fields):
    original = None
    box_title = infobox.select_one(".summary, .fn, caption") if infobox else None
    if box_title:
        lines = [compact(v) for v in box_title.get_text("\n", strip=True).splitlines() if compact(v)]
        others = [v for v in lines if v != title]
        if len(lines) > 1 and title in lines and len(others) == 1 and "tam dich:" not in fold(intro):
            original = others[0]
    if not original:
        found = re.search(r"(?:tựa gốc|tên gốc|tiếng Anh)\s*:\s*([^;)]+)", intro, re.I)
        original = compact(found[1]) if found else None
    for label in ("ten goc", "tua goc", "phon the", "gian the"):
        if fields.get(label):
            return fields[label]
    return original


def wiki_movie(soup, url, now):
    path_title = unquote(urlsplit(url).path.removeprefix("/wiki/"))
    if path_title.startswith(("Thể_loại:", "Category:")):
        return None, None
    # Chỉ xét chính bài viết: mw-disambig trên một link chỉ mô tả trang đích của link đó.
    if soup.select_one('#disambigbox, body.mw-disambig, link[rel~="mw:PageProp/disambiguation"]'):
        return None, "disambiguation"
    root = soup.select_one(".mw-parser-output") or soup.select_one("#mw-content-text")
    heading = soup.select_one("h1#firstHeading") or soup.find("h1")
    if root is None or heading is None:
        return None, "no_article_body"
    for node in root.select("sup.reference, .mw-editsection"):
        node.decompose()
    intro, plot = intro_and_plot(root)
    infobox = root.select_one("table.infobox")
    fields, cells = infobox_fields(infobox)
    if TV_FIELDS & fields.keys():
        return None, "television_series"
    # Heuristic thận trọng: infobox phim, hoặc câu đầu nói đây là một bộ phim.
    is_film = len(FILM_FIELDS & fields.keys()) >= 2 or (
        plot and re.search(r"la .{0,120}(bo phim|phim dien anh)", fold(intro[:600])))
    if not is_film:
        return None, None

    title = re.sub(r"\s*\(phim[^)]*\)\s*$", "", compact(heading.get_text(" ", strip=True)), flags=re.I)
    movie = new_movie("wikipedia", url, now)
    movie.update(source_id=path_title, title=title, introduction=intro, synopsis=plot,
                 original_title=wiki_original_title(infobox, title, intro, fields))
    for key, labels in {"director": ["dao dien"], "cast": ["dien vien"],
                        "country": ["quoc gia", "nuoc"], "genre": ["the loai"]}.items():
        cell = next((cells[v] for v in labels if v in cells), None)
        if cell is not None:
            links = [compact(a.get_text(" ", strip=True)) for a in cell.select("a")]
            lines = [compact(v) for v in cell.get_text("\n", strip=True).splitlines() if compact(v)]
            movie[key] = list(dict.fromkeys(v for v in links if v)) or lines

    categories = [unquote(v.get("href", "")).split("Thể_loại:", 1)[-1].replace("_", " ")
                  for v in soup.select('link[rel~="mw:PageProp/Category"]')
                  if "Thể_loại:" in unquote(v.get("href", ""))]
    categories = list(dict.fromkeys(categories))
    if not movie["genre"]:   # không có trong infobox thì suy từ thể loại "Phim <thể loại>..."
        movie["genre"] = [g for g in WIKI_GENRES if any(
            c.casefold() == "phim " + g or c.casefold().startswith("phim " + g + " ") for c in categories)]

    label = next((v for v in ("cong chieu", "ngay phat hanh", "ra mat") if fields.get(v)), None)
    if label:
        movie["source_release_date"] = fields[label]
        years = re.findall(r"\b(?:18|19|20)\d{2}\b", fields[label])
        if years:
            movie["release_year"] = int(years[0])
        try:
            dates = [datetime.fromisoformat(v).date() for v in
                     re.findall(r"\b(?:18|19|20)\d{2}-\d{2}-\d{2}\b", fields[label])]
            if dates:
                movie["release_status"] = release_status(dates, now)
        except ValueError:
            pass
    # Ngày phát hành trong infobox ưu tiên hơn thể loại "Phim chưa ra mắt" (có thể đã cũ).
    if "Phim chưa ra mắt" in categories and movie["release_status"] != "released":
        movie["release_status"] = "upcoming"
    movie["synopsis"] = compact(movie["synopsis"])
    return movie, None


# ---------- Điểm vào ----------

def parse_page(html, url, now):
    """html: bytes (để BeautifulSoup tự nhận charset) hoặc str.

    Trả về dict(title, content, page_type, movie, hrefs, parse_error).
    """
    soup = BeautifulSoup(html, "html.parser")
    host, path = urlsplit(url).hostname, unquote(urlsplit(url).path)
    is_wiki = host == "vi.wikipedia.org"
    is_category = is_wiki and path.startswith(("/wiki/Thể_loại:", "/wiki/Category:"))

    # Lấy link trước khi parse phim vì wiki_movie xóa một số thẻ khỏi soup.
    if is_category:   # bài viết trước, thể loại con sau (cùng depth vẫn là FIFO)
        nodes = soup.select("#mw-pages .mw-category a[href]")
        nodes += soup.select("#mw-subcategories .mw-category a[href], #mw-subcategories a.CategoryTreeLabel[href]")
    elif is_wiki:     # chỉ link trong nội dung bài, bỏ thanh điều hướng
        body = soup.select_one(".mw-parser-output") or soup.select_one("#mw-content-text")
        nodes = body.select("a[href]") if body else []
    else:
        nodes = soup.select("a[href]")
    hrefs = list(dict.fromkeys(a["href"] for a in nodes))

    movie, error, page_type = None, None, "other"
    if host == "www.momo.vn":
        is_detail = bool(MOMO_DETAIL.fullmatch(path))
        if is_detail:
            movie, error = momo_movie(soup, url, now)
        page_type = "movie" if movie else ("movie_candidate" if is_detail else "listing")
    elif host == "tv360.vn":
        is_detail = bool(TV360_DETAIL.fullmatch(path))
        if is_detail:
            movie, error = tv360_movie(soup, url, now)
        page_type = "movie" if movie else ("movie_candidate" if is_detail else "listing")
    elif is_category:
        page_type = "category"
    elif is_wiki:
        movie, error = wiki_movie(soup, url, now)
        page_type = "movie" if movie else "article"

    if movie and not movie["synopsis"]:   # không có nội dung/cốt truyện thì bỏ phim luôn
        movie, error, page_type = None, "no_synopsis", "movie_candidate"

    title_node = soup.select_one("head > title")
    for node in soup.select(HIDDEN):   # JSON nhúng đã đọc xong ở trên, giờ mới bỏ khỏi văn bản
        if not node.decomposed:
            node.decompose()
    return dict(title=compact(title_node.get_text(" ", strip=True)) if title_node else "",
                content=compact(soup.get_text(" ", strip=True)), page_type=page_type,
                movie=movie, hrefs=hrefs, parse_error=error)
