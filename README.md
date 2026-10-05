# Focused Web Crawler – phim tiếng Việt

Crawler BFS bằng Python (Requests, BeautifulSoup, SQLite). Ngoài việc lưu trang và liên kết theo đề bài, crawler trích thông tin phim từ MoMo và Wikipedia tiếng Việt để làm dữ liệu cho đề tài tìm phim bằng mô tả cốt truyện.

## 1. Chủ đề và domain

Topic: **Movies & Entertainment**. Domain: `www.momo.vn`, `vi.wikipedia.org` (thay cho các domain gợi ý trong đề; đã được giảng viên chấp thuận).

## 2. Seed URLs

1. https://www.momo.vn/cinema
2. https://www.momo.vn/cinema/phim-chieu
3. https://vi.wikipedia.org/wiki/Thể_loại:Phim_kinh_dị

Seed Wikipedia là một thể loại cụ thể nên dữ liệu Wikipedia nghiêng về phim kinh dị.

## 3. Cấu hình (`config.py`)

| Tham số | Giá trị |
|---|---|
| Số trang tối đa | 60 (tối đa 30 mỗi domain) |
| Depth tối đa (seed = 0) | 3 |
| Timeout | 15 giây |
| Delay giữa hai request cùng host | 2 giây |

Delay 2 giây để không gây tải cho hai website; đề bài chỉ nêu 1 giây làm ví dụ.

Cài đặt và chạy:

```
pip install -r requirements.txt
python main.py crawl                  # crawl, rồi xuất CSV và in thống kê
python main.py report                 # in lại thống kê từ database
python main.py export                 # xuất lại CSV từ database
python main.py reparse                # parse lại từ HTML đã lưu (không gọi mạng)
python -m unittest discover -s tests  # chạy test
```

Có thể đổi giới hạn bằng `--max-pages`, `--per-domain`, `--max-depth`, `--delay`, `--timeout`. `crawl` từ chối chạy trên database đã có dữ liệu; dùng `--db` và `--output` mới (hoặc xóa thư mục `data/`) để crawl lại.

## 4. Chiến lược crawl

Luồng: seed → **URL Frontier** → kiểm tra robots.txt → tải trang → parse → lưu → lọc link → đưa link hợp lệ vào frontier với `depth + 1`.

- Frontier (`frontier.py`) là hàng đợi FIFO (`deque`) kèm tập URL đã thấy. FIFO nên các trang depth *n* luôn được tải xong trước depth *n + 1*, đúng BFS.
- BFS cho khoảng cách ngắn nhất từ seed và dễ kiểm soát depth; trang danh mục chỉ dùng để khám phá URL.
- Crawler dừng khi đủ `max_pages`, hết frontier, hoặc khi bấm Ctrl+C (dữ liệu đã lưu vẫn còn).
- Lỗi mạng, HTTP 4xx/5xx, trang không phải HTML hay lỗi parse đều được ghi vào bảng `pages` rồi crawl tiếp. Host trả 403/429 sẽ bị bỏ qua trong phần còn lại của lượt chạy.
- Mỗi trang được lưu trong một transaction; HTML gốc lưu ở `data/html/` để `reparse`.

## 5. Quy tắc lọc URL

Một link được đưa vào frontier khi vượt qua lần lượt các bước (lý do loại ghi ở cột `skip_reason` của bảng `links`):

1. Chỉ `http`/`https` (bỏ `mailto:`, `javascript:`, `tel:`). Link tương đối được ghép bằng `urljoin`; bỏ fragment; `momo.vn` đổi về `www.momo.vn`; ký tự mã hóa `%xx` được chuẩn hóa để `/a` và `/%61` là một URL.
2. Hostname phải khớp chính xác một domain được phép (`outside_domain`).
3. Bỏ đuôi không phải HTML: ảnh, CSS/JS, PDF, ZIP, video... (`non_html_extension`).
4. MoMo: chỉ các trang danh sách cinema và `/cinema/<slug>-<id>`, không có query.
5. Wikipedia: chỉ `/wiki/<bài>`, không query, bỏ namespace đặc biệt/thảo luận/tập tin/bản mẫu và trang "Danh sách...".
6. Không vượt `max_depth` (`max_depth`).
7. robots.txt không cấm (`robots_denied`). Parser robots hỗ trợ `*`, `$` và quy tắc dài nhất thắng; không đọc được robots.txt thì host đó không được crawl.
8. Chưa từng được thêm vào frontier (`duplicate`).

Redirect được theo dõi từng bước; mỗi bước đều qua bước 2–5 và robots.txt (bước 7), nên crawler không gửi request tới URL ngoài phạm vi.

## 6. Thiết kế database (`data/crawler.db`)

- `pages`: mỗi trang đã tải một dòng – `url`, `domain`, `title`, `content`, `depth`, `status_code`, `crawled_at` (theo đề bài), cộng `final_url` (sau redirect), `page_type`, `crawl_status`, `response_time`, `error`, `html_path`.
- `links`: mỗi cặp (`source_url`, `target_url`) tìm thấy, kèm `accepted` và `skip_reason`. Bảng này cho biết số URL phát hiện/bị loại và lý do.
- `movies`: bản ghi phim trích từ trang chi tiết (JSON trong cột `data`).

## 7. Kết quả

Lượt chạy ngày 04/10/2026 (`python main.py report` để xem số liệu hiện tại):

| | |
|---|---|
| Trang đã crawl | 60 (30 MoMo, 30 Wikipedia), tất cả HTTP 200 |
| URL phát hiện | 4.839 |
| URL bị loại bởi bộ lọc | 3.618 (nhiều nhất: Wikipedia có query 2.584, ngoài domain 1.101) |
| Request lỗi | 0 |
| Depth 0 / 1 | 3 / 57 trang (hết ngân sách 60 trang trước khi sang depth 2) |
| Phim trích được | 50 (MoMo 26, Wikipedia 24); 34 phim có cốt truyện và không phải sắp chiếu |

Dữ liệu xuất ra `data/`: `momo_movies_raw.csv`, `wikipedia_movies_raw.csv`, `movies_with_synopsis.csv`, `crawl_summary.json`. Hai nguồn được giữ riêng, chưa ghép thành phim duy nhất.
