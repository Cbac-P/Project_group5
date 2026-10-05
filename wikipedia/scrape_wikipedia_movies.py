#!/usr/bin/env python3
"""
Cào phim từ Wikipedia tiếng Việt -> JSONL schema movie-v2 (+ cột country).

Cấu trúc depth:
  depth 0: Thể loại:Danh sách phim  -> CHỈ lấy "Trang trong thể loại" (cmtype=page),
           bỏ qua phần "Thể loại con" (subcategory).
  depth 1: các trang "Danh sách phim ..." -> lấy link tới từng bài phim
  depth 2: bài viết phim -> đọc infobox + cốt truyện
"""
import argparse
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote, unquote

import requests
from bs4 import BeautifulSoup

API = "https://vi.wikipedia.org/w/api.php"
WIKI = "https://vi.wikipedia.org/wiki/"
ROOT_CATEGORY = "Thể loại:Danh sách phim"

SESSION = requests.Session()
SESSION.headers["User-Agent"] = "MovieDatasetBot/1.0 (student project; contact: buihuuloc2006@gmail.com)"


def api(params, retries=4):
    params = {**params, "format": "json", "formatversion": 2}
    for i in range(retries):
        try:
            r = SESSION.get(API, params=params, timeout=30)
            r.raise_for_status()
            return r.json()
        except Exception:
            time.sleep(1.5 * (i + 1))
    return {}


# ---------------------------------------------------------------- depth 0
def get_category_pages(category):
    """Chỉ trang (không lấy thể loại con) = mục 'Trang trong thể loại'."""
    titles, cont = [], {}
    while True:
        d = api({"action": "query", "list": "categorymembers", "cmtitle": category,
                 "cmtype": "page", "cmlimit": 500, **cont})
        titles += [m["title"] for m in d.get("query", {}).get("categorymembers", [])]
        if "continue" not in d:
            return titles
        cont = d["continue"]


# ---------------------------------------------------------------- fetch html
def fetch_page(title):
    d = api({"action": "parse", "page": title, "prop": "text|categories",
             "redirects": 1, "disableeditsection": 1, "disabletoc": 1})
    p = d.get("parse")
    if not p:
        return None
    soup = BeautifulSoup(p["text"], "lxml")
    cats = [c["category"] for c in p.get("categories", [])]
    return p["title"], soup, cats


# ---------------------------------------------------------------- depth 1
def good_link(a):
    if not a:
        return None
    href = a.get("href", "")
    if not href.startswith("/wiki/") or "new" in (a.get("class") or []):
        return None
    t = unquote(href[6:].split("#")[0]).replace("_", " ")
    if ":" in t:  # Tập tin:, Thể loại:, Wikipedia: ...
        return None
    return t


def expand_table(table):
    """Trải bảng thành lưới, xử lý rowspan/colspan để chỉ số cột không bị lệch."""
    grid, pending = [], {}  # pending: col -> (cell, remaining_rows)
    for tr in table.find_all("tr"):
        cells = tr.find_all(["td", "th"], recursive=False)
        row, col, ci = [], 0, 0
        while ci < len(cells) or col in pending:
            if col in pending:
                cell, left = pending[col]
                row.append((cell, True))  # True = ô lặp từ rowspan
                if left > 1:
                    pending[col] = (cell, left - 1)
                else:
                    del pending[col]
                col += 1
                continue
            cell = cells[ci]
            ci += 1
            rs = int(re.sub(r"\D", "", cell.get("rowspan", "1")) or 1)
            cs = int(re.sub(r"\D", "", cell.get("colspan", "1")) or 1)
            for _ in range(cs):
                row.append((cell, False))
                if rs > 1:
                    pending[col] = (cell, rs - 1)
                col += 1
        grid.append(row)
    return grid


BAD_HDR = re.compile(r"đạo diễn|diễn viên|hãng|studio|năm|thứ hạng|hạng|chú|nguồn|thể loại|quốc gia|"
                     r"kinh phí|doanh thu|ghi chú|stt|^#|rank|director|cast|year|note|source|genre|notes")
GOOD_HDR = re.compile(r"^(tên\s*)?(phim|bộ phim|tựa phim|tên phim|film|title|tên|tiêu đề)\b")


def title_column(header, body, strict=False):
    hs = [re.sub(r"\s+", " ", c.get_text(" ")).strip().lower() for c, _ in header]
    for i, h in enumerate(hs):
        if GOOD_HDR.search(h):
            return i
    if strict:  # bảng không có class wikitable: chỉ nhận khi có tiêu đề cột phim rõ ràng
        return None
    # fallback: cột không phải đạo diễn/năm/hãng... có nhiều link nhất
    best, best_n = None, 0
    for i, h in enumerate(hs):
        if len(h) <= 25 and BAD_HDR.search(h):  # tiêu đề dài (vd "Danh sách của phiên bản ... năm") không coi là cột phụ
            continue
        n = sum(1 for r in body if i < len(r) and not r[i][1] and good_link(r[i][0].find("a", href=True) or {}) )
        if n > best_n:
            best, best_n = i, n
    return best


def links_from_list_page(soup):
    """Chỉ lấy link ở CỘT TÊN PHIM của các bảng wikitable (+ <li> ở các trang dạng danh sách gạch đầu dòng)."""
    found = []
    tables = [(t, False) for t in soup.select("table.wikitable")]
    tables += [(t, True) for t in soup.select("div.mw-parser-output > table")
               if "wikitable" not in (t.get("class") or [])]
    for table, strict in tables:
        grid = expand_table(table)
        if len(grid) < 2:
            continue
        hdr_idx = next((i for i, r in enumerate(grid) if r and all(c.name == "th" for c, _ in r)), 0)
        header, body = grid[hdr_idx], grid[hdr_idx + 1:]
        col = title_column(header, body, strict)
        if col is None:
            continue
        for r in body:
            if col >= len(r) or r[col][1]:  # bỏ ô rowspan lặp
                continue
            cell = r[col][0]
            a = cell.find("a", href=True)
            t = good_link(a) if a else None
            if t:
                found.append(t)
    if not found:  # trang không có bảng: lấy <li> dạng "Tên phim (năm)" (ul hoặc ol)
        for ul in soup.select("div.mw-parser-output > ul, div.mw-parser-output > ol"):
            for li in ul.find_all("li", recursive=False):
                a = li.find("a", href=True)
                t = good_link(a) if a else None
                if t:
                    found.append(t)
    return found


# ---------------------------------------------------------------- depth 2
def clean(el):
    for s in el.select("sup, style, .reference"):
        s.decompose()
    for br in el.find_all("br"):
        br.replace_with("\n")
    for li in el.find_all("li"):
        li.append("\n")
    return el.get_text(" ")


def split_list(text):
    out = []
    for part in re.split(r"[\n,;•·]", text):
        part = re.sub(r"\s+", " ", part).strip()
        if part and part not in out:
            out.append(part)
    return out


def infobox(soup):
    box = soup.select_one("table.infobox")
    data = {}
    if not box:
        return data
    for tr in box.find_all("tr"):
        th, td = tr.find("th"), tr.find("td")
        if th and td:
            data[re.sub(r"\s+", " ", th.get_text(" ")).strip().lower()] = clean(td)
    return data


def pick(box, *keys):
    """Ưu tiên theo thứ tự keys (không theo thứ tự dòng trong infobox)."""
    for key in keys:
        for k, v in box.items():
            if key in k:
                return v
    return ""


def synopsis(soup):
    root = soup.select_one("div.mw-parser-output") or soup
    # ưu tiên mục Cốt truyện / Nội dung / Tóm tắt
    for h in root.select("div.mw-heading, h2, h3"):
        if re.search(r"cốt truyện|nội dung|tóm tắt|tình tiết", h.get_text(" ").lower()):
            paras = []
            for sib in h.find_next_siblings():
                if sib.name == "div" and "mw-heading" in (sib.get("class") or []) or sib.name in ("h2", "h3"):
                    break
                if sib.name == "p":
                    paras.append(clean(sib))
            txt = re.sub(r"\s+", " ", " ".join(paras)).strip()
            if txt:
                return txt
    # fallback: đoạn mở đầu
    for p in root.find_all("p", recursive=False):
        txt = re.sub(r"\s+", " ", clean(p)).strip()
        if len(txt) > 80:
            return txt
    return ""


def release_year(box, cats):
    raw = pick(box, "công chiếu", "ngày phát hành", "ra mắt")
    raw = re.sub(r"\(\s*\d{4}-\d{2}-\d{2}\s*\)", " ", raw)  # bỏ ISO date
    raw = re.sub(r"\d+\s*năm trước", " ", raw)                 # bỏ "102 năm trước"
    m = re.findall(r"\b(1[89]\d{2}|20\d{2})\b", raw)
    if m:
        return int(m[0])
    for c in cats:  # "Phim_năm_2010" hoặc "Phim_kinh_dị_năm_2010" (tên thể loại dùng dấu _)
        m = re.search(r"^Phim_(?:.+_)?năm_(\d{4})$", c)
        if m:
            return int(m.group(1))
    return None


GENRE_SKIP = re.compile(r"^(năm|tiếng|do|của|về|lấy bối cảnh|dựa|có|được|thập niên|đầu tay|chuyển thể|"
                        r"phát hành|sản xuất|quay|đoạt|giành|hãng|liên quan|trong|tại|bị|không|xuất hiện)\b")


COUNTRY_WORDS = {"Việt Nam", "Mỹ", "Pháp", "Anh", "Nhật Bản", "Hàn Quốc", "Trung Quốc", "Hồng Kông", "Đức", "Ý",
                 "Nga", "Ấn Độ", "Thái Lan", "Đài Loan", "Canada", "Úc", "Tây Ban Nha", "Liên Xô", "Hoa Kỳ",
                 "Philippines", "Singapore", "Na Uy", "Thụy Điển", "Đan Mạch", "Ba Lan", "Brasil", "Mexico",
                 "Argentina", "Iran", "Thổ Nhĩ Kỳ", "Hà Lan", "Bỉ", "Tiệp Khắc", "Hungary", "Ireland",
                 "New Zealand", "Indonesia", "Malaysia"}


def genres_from_cats(cats, countries):
    """Suy ra thể loại từ category kiểu 'Phim_kinh_dị_năm_2010' / 'Phim_chính_kịch_Mỹ'."""
    out = []
    for c in cats:
        n = c.replace("_", " ")
        m = re.match(r"^Phim (.+?) năm \d{4}$", n)
        if m:
            g = m.group(1)
        else:
            m = re.match(r"^Phim (.+)$", n)
            if not m or GENRE_SKIP.match(m.group(1)):
                continue
            g = m.group(1)
            # bỏ hậu tố quốc gia/vùng: "chính kịch Mỹ", "kinh dị Việt Nam", "... của Mỹ"
            g = re.sub(r"\s+của\s+.+$", "", g)
            for suf in ("Việt Nam", "Mỹ", "Pháp", "Anh", "Nhật Bản", "Hàn Quốc", "Trung Quốc", "Hồng Kông",
                        "Đức", "Ý", "Nga", "Ấn Độ", "Thái Lan", "Đài Loan", "Canada", "Úc", "Tây Ban Nha",
                        "Liên Xô", "Hoa Kỳ", "Philippines", "Singapore", "Na Uy", "Thụy Điển", "Đan Mạch",
                        "Ba Lan", "Brasil", "Mexico", "Argentina", "Iran", "Thổ Nhĩ Kỳ", "Hà Lan", "Bỉ",
                        "Tiệp Khắc", "Hungary", "Ireland", "New Zealand", "Indonesia", "Malaysia") + tuple(countries):
                if g.endswith(" " + suf):
                    g = g[: -len(suf) - 1].strip()
                    break
        g = g.strip()
        g = g.lower() if g[:1].isupper() and g not in COUNTRY_WORDS and not g.isupper() else g
        if GENRE_SKIP.match(g) or g in ("trắng đen", "màu", "độc lập", "câm", "lấy bối cảnh") or g.startswith("ở "):
            continue
        if g in COUNTRY_WORDS or g in countries or re.search(r"đạo diễn|diễn viên|nhà làm phim|hãng", g):
            continue
        if g and len(g.split()) <= 4 and g not in out and not re.search(r"\d", g):
            out.append(g)
    return out


def parse_movie(title):
    res = fetch_page(title)
    if not res:
        return None
    real_title, soup, cats = res
    box = infobox(soup)
    is_film = any(k in " ".join(box) for k in ("đạo diễn", "diễn viên", "quốc gia"))
    is_film = is_film and ("đạo diễn" in " ".join(box) or any("phim" in c.lower() for c in cats))
    if not is_film:
        return None
    countries = split_list(pick(box, "quốc gia", "nước"))
    genre = split_list(pick(box, "thể loại")) or genres_from_cats(cats, countries)
    cast = [x for x in split_list(pick(box, "diễn viên")) if not re.match(r"^(xem )?danh sách", x.lower())]
    return {
        "source": "wikipedia",
        "source_url": WIKI + quote(real_title.replace(" ", "_")),
        "title": real_title,
        "release_year": release_year(box, cats),
        "synopsis": synopsis(soup),
        "genre": genre,
        "director": split_list(pick(box, "đạo diễn")),
        "cast": cast,
        "country": countries,
    }


# ---------------------------------------------------------------- chuẩn hóa
COUNTRY_ALIASES = {  # so khớp không phân biệt hoa/thường
    "hoa kỳ": "Mỹ", "hoa kì": "Mỹ", "hợp chủng quốc hoa kỳ": "Mỹ",
}


def dedupe(items):
    out = []
    for x in items:
        if x and x not in out:
            out.append(x)
    return out


def normalize_records(recs):
    """- genre: về chữ thường (giữ nguyên từ viết hoa toàn bộ như IMAX).
    - country: Hoa Kỳ -> Mỹ; các biến thể chỉ khác hoa/thường gộp về dạng xuất hiện nhiều nhất."""
    from collections import Counter
    for r in recs:
        r["genre"] = dedupe([g if (g.isupper() and len(g) <= 5) else g.lower() for g in r["genre"]])
        r["country"] = dedupe([COUNTRY_ALIASES.get(c.casefold(), c) for c in r["country"]])
    freq = Counter(c for r in recs for c in r["country"])
    canon = {}
    for c, n in freq.most_common():       # most_common: dạng nhiều nhất đứng trước
        canon.setdefault(c.casefold(), c)
    for r in recs:
        r["country"] = dedupe([canon[c.casefold()] for c in r["country"]])
    return recs


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-o", "--out", default="wikipedia.jsonl")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--limit-lists", type=int, default=0, help="debug: chỉ chạy N trang danh sách")
    args = ap.parse_args()

    # depth 0
    list_pages = get_category_pages(ROOT_CATEGORY)
    if args.limit_lists:
        list_pages = list_pages[: args.limit_lists]
    print(f"[depth0] {len(list_pages)} trang trong thể loại")

    # depth 1
    film_titles, seen = [], set(list_pages)
    for lp in list_pages:
        res = fetch_page(lp)
        if not res:
            continue
        links = [t for t in links_from_list_page(res[1]) if t not in seen]
        seen.update(links)
        film_titles += links
        print(f"[depth1] {lp}: +{len(links)} link")
        time.sleep(0.2)
    print(f"[depth1] tổng {len(film_titles)} ứng viên")

    # depth 2
    recs, urls = [], set()
    with ThreadPoolExecutor(args.workers) as ex:
        for rec in ex.map(parse_movie, film_titles):
            if not rec or rec["source_url"] in urls:
                continue
            urls.add(rec["source_url"])
            recs.append(rec)
            if len(recs) % 50 == 0:
                print(f"[depth2] {len(recs)} phim")
    recs = normalize_records(recs)
    with open(args.out, "w", encoding="utf-8", newline="\n") as f:  # utf-8 (không BOM)
        for rec in recs:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print(f"Xong: {len(recs)} phim -> {args.out}")


if __name__ == "__main__":
    main()
