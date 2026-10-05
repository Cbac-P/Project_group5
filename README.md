# Movie Crawler - FPT Play

## Thông tin cá nhân

| MSSV | Họ tên |
|---|---|
| CE200153 | Nguyễn Thị Thảo Ngân |

## 1. Chủ đề đã chọn

**Topic:** Movies (hệ thống tìm phim theo nội dung nhớ mang máng)

**Domain phụ trách:** FPT Play - `fptplay.vn`

**Seed URL:**

```text
https://fptplay.vn/
```

## 2. Cấu hình crawl

Cấu hình lấy từ `config.py`:

| Thiết lập | Giá trị |
|---|---:|
| Seed URLs | 1 |
| Allowed domain | 1 (`fptplay.vn`) |
| Maximum pages | 1000 |
| Maximum depth | 5 |
| Request timeout | 10 giây |
| Base crawl delay | 1.5 giây |
| robots.txt | Bật (fail-closed) |
| Seed từ sitemap | Bật |

Crawler đợi lâu hơn nếu `robots.txt` yêu cầu `Crawl-delay` lớn hơn mức cấu hình.

## 3. Chiến lược crawl (BFS)

Dùng **Breadth-First Search**, `URLFrontier` cài bằng `collections.deque` (FIFO). Mỗi phần tử là `(url, depth)`. Seed ở depth `0`; link tìm thấy từ trang depth `d` vào hàng đợi ở depth `d+1` cho tới `MAX_DEPTH`.

Hai set chống trùng URL: `queued` (đang chờ) và `visited` (đã thử).

**Sitemap seeding (`SEED_FROM_SITEMAP`):** các trang streaming thường render menu bằng JavaScript nên HTML thô có thể chứa rất ít thẻ `<a>`. Vì vậy crawler còn đọc sitemap (khai báo trong `robots.txt` và `config.SITEMAP_URLS`, hỗ trợ sitemap lồng nhau và `.gz`), lấy các URL khớp `DETAIL_URL_REGEX` và đưa vào hàng đợi ở depth 1. Tắt bằng `SEED_FROM_SITEMAP = False` nếu muốn crawl thuần theo link.

## 4. Quy tắc lọc URL

Một URL chỉ được chấp nhận khi:
1. Scheme là `http` hoặc `https`
2. Thuộc domain `fptplay.vn` (kể cả subdomain)
3. Không phải file bị chặn (ảnh, CSS, JS, archive, PDF, video, `.xml`...)
4. Không chứa từ khoá bị chặn (`SKIP_URL_REGEX`: đăng nhập, thanh toán, gói cước, trang xem video)
5. Độ sâu không vượt `MAX_DEPTH`
6. Chưa `visited` hoặc đang chờ trong frontier
7. `robots.txt` cho phép user-agent của crawler

`normalize_url()` lowercase scheme/host, bỏ fragment, bỏ port mặc định, bỏ trailing slash, bỏ query tracking (`utm_*`, `fbclid`...).

## 5. Trích xuất thông tin phim

Chỉ trang có URL khớp `DETAIL_URL_REGEX` mới được parse thành bản ghi phim; các trang khác (trang chủ, danh sách, thể loại) chỉ dùng để tìm link.

`parser.py` thử 3 chiến lược, trường nào thiếu thì lấy từ chiến lược sau:
1. **JSON-LD** (`schema.org/Movie`, `TVSeries`)
2. **`__NEXT_DATA__`** (JSON của Next.js): duyệt đệ quy, chọn object có nhiều khoá giống phim nhất (`title` + `description` bắt buộc)
3. **Thẻ `<meta>`** (`og:title`, `og:description`)

Làm sạch: bỏ thẻ HTML, giải mã entity, gộp khoảng trắng, bỏ hậu tố `| FPT Play` và tiền tố `Xem phim` trong title; `release_year` trích bằng regex năm 19xx/20xx; `genre`, `director`, `cast`, `country` lưu dạng chuỗi ngăn cách bằng dấu phẩy.

## 6. Kiểm soát chất lượng dữ liệu

- **Thiếu title** hoặc **thiếu synopsis** (hoặc synopsis < `MIN_SYNOPSIS_LENGTH` = 20 ký tự) -> **không lưu** (skip reason `missing_title` / `missing_synopsis`). Có thể tắt bằng `REQUIRE_TITLE`, `REQUIRE_SYNOPSIS`.
- **Chống trùng** (3 tầng, kiểm tra trước khi `INSERT`):
  1. `duplicate_url`: cùng `source_url`
  2. `duplicate_content`: cùng `dedup_key` = SHA-256(tên chuẩn hoá bỏ dấu/ký tự đặc biệt + năm phát hành)
  3. `duplicate_synopsis`: cùng SHA-256 của synopsis chuẩn hoá (bật/tắt bằng `DEDUP_BY_SYNOPSIS`)

## 7. Schema bảng `movies`

| Cột | Ý nghĩa |
|---|---|
| `id` | khoá chính tự tăng |
| `source` | `fptplay` |
| `source_url` | URL trang phim (UNIQUE) |
| `title`, `release_year`, `synopsis`, `genre`, `director`, `cast`, `country` | thông tin phim |
| `dedup_key`, `synopsis_hash` | phục vụ lọc trùng |
| `depth`, `crawled_at` | truy vết |

## 8. Cách chạy

```bash
pip install -r requirements.txt

python main.py --probe <url-trang-chi-tiet-phim>   # 1) chẩn đoán 1 trang trước
python main.py --max-pages 20                      # 2) chạy thử nhỏ
python main.py                                     # 3) chạy đầy đủ
python main.py --analyze                           # báo cáo chất lượng dữ liệu
python main.py --export-csv data/movies_fptplay.csv
```

Output: `data/movies_fptplay.db`, `data/crawl_summary_fptplay.txt`. `RESET_DATABASE_ON_START = True` nên mỗi lần chạy DB được tạo lại; thêm `--keep-db` để giữ dữ liệu cũ.
