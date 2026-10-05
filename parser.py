"""HaLim HTML: one feature film per detail page, excluding series/collections."""
import json
import re
import unicodedata
from bs4 import BeautifulSoup
from url_rules import normalize_url
# Tách hai loại dữ liệu: title/content của TRANG và 9 trường của PHIM.

def compact(value):  # Gom khoảng trắng liên tiếp, bỏ khoảng trắng thừa ở đầu/cuối.
    return re.sub(r"\s+", " ", value or "").strip()


def fold(value):  # Bỏ dấu và đổi chữ thường để so nhãn, ví dụ 'Quốc gia' -> 'quoc gia'.
    text = unicodedata.normalize("NFD", compact(value).casefold().replace("đ", "d"))
    return "".join(c for c in text if unicodedata.category(c) != "Mn")


def names(value):  # Chuẩn hóa tên thành list không trùng; nhận chuỗi, list hoặc object name.
    if isinstance(value, dict):
        value = value.get("name", "")
    if isinstance(value, list):
        return list(dict.fromkeys(s for item in value for s in names(item)))
    if not isinstance(value, str):
        return []
    return list(dict.fromkeys(compact(s) for s in re.split(r"[,;]", value) if compact(s)))


def extract_movie(soup, url):  # Chỉ trả movie khi trang chi tiết thỏa điều kiện phim lẻ; kèm lý do nếu loại.
    heading = soup.select_one(".movie-detail h1.entry-title")  # Selector nhận diện tiêu đề trong vùng chi tiết phim.
    if not heading:
        return None, None  # Danh mục không có tiêu đề chi tiết này thì không tạo movie.
    detail = heading.find_parent(class_="movie-detail")
    title = compact(heading.get_text(" ", strip=True))
    if not title:
        return None, "missing_movie_title"
    nodes = []
    for script in soup.select('script[type="application/ld+json"]'):
        try:  # JSON-LD là JSON nằm trong HTML, không phải gọi API khác.
            payload = json.loads(script.get_text())
        except (ValueError, TypeError):
            continue
        for node in payload if isinstance(payload, list) else [payload]:
            if isinstance(node, dict):
                nodes.extend([node] + (node.get("@graph") or []))
    typed = [n for n in nodes if isinstance(n, dict) and normalize_url(n.get("url")) == normalize_url(url)  # Chỉ dùng node có URL đúng trang đang đọc; bỏ phim gợi ý/liên quan.
             and any(t in names(n.get("@type")) for t in ("Movie", "TVSeries", "TVEpisode"))]
    # Theme có thể gắn Movie trên danh mục; chỉ node đó chưa đủ chứng minh là phim.
    # Cần h1 chi tiết; năm lấy từ HTML release, không lấy ngày đăng bài.
    if any(t in names(n.get("@type")) for n in typed for t in ("TVSeries", "TVEpisode")):  # Loại phim bộ/tập phim dù trang vẫn tải HTML thành công.
        return None, "excluded_series_or_episode"
    episode_labels = [compact(n.get_text(" ", strip=True)) for n in soup.select(".halim-episode .halim-btn")]  # Đọc nhãn tập để nhận diện trang chứa nhiều tập hoặc tuyển tập.
    title_fold = fold(title)
    if (any(re.search(r"\b\d+\s*/\s*\d+\b", s) for s in episode_labels)  # Tên và nhãn tập là quy tắc lọc; chưa phải mô hình phân loại AI.
            or any(s not in ("full", "hoan tat", "1") for s in map(fold, episode_labels))
            or re.search(r"(?:tuyen tap|tong hop|truyen hinh|\bseason\b|\btap\s+\d+|\bmovie\s+\d+\s*-\s*\d+)", title_fold)):
        return None, "excluded_collection_or_multiple_episodes"
    movie_data = next((n for n in typed if "Movie" in names(n.get("@type"))), None)  # Phải có Movie gắn đúng URL thì mới nhận vào dataset.
    if not movie_data:
        return None, "unconfirmed_movie_type"
    record = dict(source="xemanime", source_url=url, title=title, release_year=None,  # Đúng 9 trường; thiếu năm để None, thiếu mô tả để rỗng, thiếu list để [].
                  synopsis="", genre=[], country=[], director=[], cast=[])
    year_node = detail.select_one('a[href*="/release/"]')  # Lấy năm nguồn ghi trong link /release/, tránh nhầm datePublished.
    if year_node:
        found = re.fullmatch(r"(?:18|19|20)\d{2}", compact(year_node.get_text()))
        if found:
            record["release_year"] = int(found[0])
    for row in detail.select("p"):  # Đọc từng dòng metadata: Quốc gia, Thể loại, Đạo diễn, Diễn viên.
        label = fold(row.get_text(" ", strip=True).split(":", 1)[0])  # Lấy nhãn bên trái dấu : rồi bỏ dấu để tra tên trường.
        key = {"quoc gia": "country", "the loai": "genre", "dao dien": "director", "dien vien": "cast"}.get(label)
        if key:
            anchors = names([a.get_text(" ", strip=True) for a in row.select("a")])
            raw = row.get_text(" ", strip=True).partition(":")[2]
            record[key] = anchors or names(raw)  # Ưu tiên tên trong thẻ a; không có thì đọc chữ phía sau nhãn.
    # Doraemon/Conan là tên loạt phim, không phải thể loại; không tự đoán thể loại.
    record["genre"] = [g for g in record["genre"] if fold(g) not in {"doraemon", "conan", "shin cau be but chi", "phim le", "phim bo"}]
    if not record["director"]:
        # HaLim là tên theme, không phải đạo diễn; loại cả nhãn chưa cập nhật.
        record["director"] = [n for n in names(movie_data.get("director")) if fold(n) not in {"halim", "admin", "dang cap nhat", "n/a"}]
    if not record["cast"]:  # Thiếu diễn viên trong HTML thì thử actor trong Movie JSON-LD đúng URL.
        record["cast"] = names(movie_data.get("actor"))
    synopsis_node = soup.select_one(".entry-content article.item-content")  # Chỉ lấy vùng cốt truyện, không lấy toàn bộ chữ trên trang làm synopsis.
    if synopsis_node:
        for unwanted in synopsis_node.select("script,style,iframe,form,.ads,.advertisement,[hidden]"):
            unwanted.decompose()  # Xóa quảng cáo/thẻ không cần thiết khỏi vùng mô tả trước khi lấy chữ.
        record["synopsis"] = compact(synopsis_node.get_text(" ", strip=True))  # Lấy text cốt truyện, gom khoảng trắng; không tự viết thêm nội dung.
    return record, None


def parse_page(html, url, now):  # Trả thông tin trang, link tìm thấy, movie nếu có và lý do loại phim.
    soup = BeautifulSoup(html, "html.parser")  # BeautifulSoup đọc cấu trúc HTML; không gửi request và không chạy JavaScript.
    hrefs = list(dict.fromkeys(a["href"] for a in soup.select("a[href]") if a["href"]))  # Lấy href không rỗng và bỏ href giống hệt; crawler còn chuẩn hóa/lọc tiếp.
    movie, error = extract_movie(soup, url)
    detail = soup.select_one(".movie-detail h1.entry-title")
    page_type = "movie" if movie else "excluded_movie" if detail else "listing"  # movie được nhận; excluded_movie có chi tiết nhưng bị loại; listing là danh mục.
    title_node = soup.title  # Title trang lấy từ <title>; tên phim lấy từ h1 trong extract_movie.
    title = compact(title_node.get_text(" ", strip=True)) if title_node else ""
    for node in soup.select('script,style,noscript,template,iframe,[hidden],[aria-hidden="true"],[style*="display:none"],[style*="display: none"]'):  # Bỏ script/player/vùng ẩn trước khi lấy text trang; vẫn có thể còn chữ menu.
        if not node.decomposed:
            node.decompose()
    content = compact(soup.get_text(" ", strip=True))  # Content trang để lưu pages; khác synopsis cốt truyện trong movies.
    return dict(title=title, content=content, page_type=page_type, movie=movie, hrefs=hrefs, parse_error=error)
