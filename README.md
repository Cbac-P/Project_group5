# Movie Crawler - TV360

## Thông tin cá nhân

| MSSV | Họ tên |
| --- | --- |
|CE201234 | Phạm Quỳnh Hương |

Crawler BFS bằng Python (Requests, BeautifulSoup, SQLite). Ngoài việc lưu trang và liên kết theo đề bài, crawler trích thông tin phim từ **TV360** (`tv360.vn`) để làm dữ liệu cho đề tài tìm phim bằng mô tả cốt truyện. Phim không có phần nội dung bị loại ngay khi crawl.

## 1. Chủ đề và domain

Topic: **Movies & Entertainment**. Domain được phép: `tv360.vn`.

Mã nguồn vẫn giữ bộ đọc cho MoMo và Wikipedia tiếng Việt, nhưng cấu hình hiện tại (`config.py`) chỉ crawl TV360.

## 2. Seed URLs

1. https://tv360.vn/movies

Ngoài seed, danh sách phim được lấy từ sitemap https://tv360.vn/sitemap.xml (tối đa `sitemap_limit` URL, mỗi phim chỉ lấy một URL). Các URL lấy từ sitemap được coi là **seed bổ sung** nên cũng ở depth 0; vì vậy bảng thống kê ghi `Seed URLs: 1` (seed khai báo trong `config.py`) nhưng có 1.000 trang ở depth 0. Lý do: phần phim gợi ý trên trang TV360 được tải bằng JavaScript nên không có trong HTML, đi theo liên kết gần như không tìm thêm được phim.

## 3. Cấu hình (`config.py`)

| Tham số | Giá trị |
|---|---|
| Số trang tối đa | đặt bằng `--max-pages` (mặc định 60) |
| Số trang tối đa mỗi domain | đặt bằng `--per-domain` (mặc định 30) |
| Depth tối đa (seed = 0) | 3 |
| Timeout | 15 giây |
| Delay cấu hình | 2 giây |
| Số URL lấy từ sitemap | 1.500 (`sitemap_limit`) |

**Delay thực tế với TV360 là 5 giây.** robots.txt của TV360 khai báo `Crawl-delay: 5`; crawler đọc giá trị này và dùng số lớn hơn giữa `delay` trong cấu hình và `Crawl-delay`, nên không thể vô tình crawl nhanh hơn mức website cho phép.

Cài đặt và chạy:

```
pip install -r requirements.txt
python main.py crawl --max-pages 1000 --per-domain 1000 --db data/tv360.db --output data/tv360
python main.py report --db data/tv360.db --output data/tv360    # in lại thống kê từ database
python main.py export --db data/tv360.db --output data/tv360    # xuất lại file từ database
python main.py reparse --db data/tv360.db --output data/tv360   # parse lại từ HTML đã lưu (không gọi mạng)
python -m unittest discover -s tests                            # chạy test
```

Có thể đổi giới hạn bằng `--max-pages`, `--per-domain`, `--max-depth`, `--delay`, `--timeout`. `crawl` từ chối chạy trên database đã có dữ liệu; dùng `--db` và `--output` mới (hoặc xóa dữ liệu cũ) để crawl lại. Bấm Ctrl+C để dừng giữa chừng, dữ liệu đã lưu vẫn còn.

## 4. Chiến lược crawl

Luồng: seed và sitemap → **URL Frontier** → kiểm tra robots.txt → tải trang → parse → lưu → lọc link → đưa link hợp lệ vào frontier với `depth + 1`.

- Frontier (`frontier.py`) là hàng đợi FIFO (`deque`) kèm tập URL đã thấy. FIFO nên các trang depth *n* luôn được tải xong trước depth *n + 1*, đúng BFS.
- Sitemap liệt kê từng tập của một phim bằng nhiều URL khác slug nhưng cùng mã `m`. Crawler chỉ giữ một URL cho mỗi mã phim (`film_id` trong `crawler.py`), vừa tránh cào trùng vừa tiết kiệm thời gian chờ.
- Crawler dừng khi đủ `max_pages`, hết frontier, hoặc khi bấm Ctrl+C.
- Lỗi mạng, HTTP 4xx/5xx, trang không phải HTML hay lỗi parse đều được ghi vào bảng `pages` rồi crawl tiếp. Host trả 403/429 sẽ bị bỏ qua trong phần còn lại của lượt chạy.
- Mỗi trang được lưu trong một transaction; HTML gốc lưu ở `html/` trong thư mục output để `reparse`.

## 5. Quy tắc lọc URL

Một link được đưa vào frontier khi vượt qua lần lượt các bước (lý do loại ghi ở cột `skip_reason` của bảng `links`):

1. Chỉ `http`/`https` (bỏ `mailto:`, `javascript:`, `tel:`). Link tương đối được ghép bằng `urljoin`; bỏ fragment; ký tự mã hóa `%xx` được chuẩn hóa để `/a` và `/%61` là một URL.
2. Với TV360, các tham số theo dõi (`col`, `sect`, `page`...) bị bỏ; chỉ giữ `m` (mã phim) trên trang phim và `c` (mã thể loại) trên trang danh mục. Nhờ vậy cùng một phim qua nhiều link vẫn cho một URL.
3. Hostname phải khớp chính xác `tv360.vn` (`outside_domain`).
4. Bỏ đuôi không phải HTML: ảnh, CSS/JS, PDF, ZIP, video... (`non_html_extension`).
5. Chỉ nhận trang phim `/movie/<slug>?m=<id>` và trang danh mục `/movies`, `/movies/<thể-loại>?c=<id>`; các đường dẫn khác bị loại (`outside_movie_paths`, `tv360_missing_id`).
6. Không vượt `max_depth` (`max_depth`).
7. robots.txt không cấm (`robots_denied`). Parser robots hỗ trợ `*`, `$`, `Crawl-delay` và quy tắc dài nhất thắng; không đọc được robots.txt thì host đó không được crawl.
8. Chưa từng được thêm vào frontier (`duplicate`). Dòng `duplicate` trong bảng `links` không phải lỗi: nó nghĩa là URL đã nằm trong hàng đợi từ một trang khác.

Redirect được theo dõi từng bước; mỗi bước đều qua kiểm tra phạm vi và robots.txt, nên crawler không gửi request tới URL ngoài phạm vi.

## 6. Trích xuất dữ liệu phim (`parser.py`)

Với trang phim TV360, BeautifulSoup đọc HTML như sau:

| Trường | Nguồn |
|---|---|
| `title`, `release_year` | thẻ meta `og:title`, dạng `Tên Việt - Tên gốc (năm)` |
| `synopsis` | thẻ meta `og:description` |
| `cast`, `director`, `country` | các liên kết đứng sau nhãn "Diễn viên:", "Đạo diễn:", "Quốc gia:" |
| `genre` | các liên kết thể loại `/movies/...?c=...` sau tiêu đề |

Phim **không có phần nội dung bị bỏ** (không lưu vào bảng `movies`, trang vẫn được ghi trong `pages` với lý do `no_synopsis` hoặc `no_film_record`).

## 7. Thiết kế database

- `pages`: mỗi trang đã tải một dòng – `url`, `domain`, `title`, `content`, `depth`, `status_code`, `crawled_at`, cộng `final_url` (sau redirect), `page_type`, `crawl_status`, `response_time`, `error`, `html_path`.
- `links`: mỗi cặp (`source_url`, `target_url`) tìm thấy, kèm `accepted` và `skip_reason`. Bảng này cho biết số URL phát hiện/bị loại và lý do.
- `movies`: bản ghi phim trích từ trang chi tiết (JSON trong cột `data`), khóa chính là `source_url`.

## 8. Dữ liệu xuất ra

Trong thư mục `--output`:

- `tv360_movie_v2.jsonl`: **file nộp**, theo schema `movie-v2` của nhóm (UTF-8 không BOM, một object JSON mỗi dòng) gồm `source`, `source_url`, `title`, `release_year`, `synopsis`, `genre`, `director`, `cast` và thêm `country`. Chỉ gồm phim có tên và nội dung, mỗi phim một dòng (dedupe theo mã phim).
- `tv360_movies_raw.csv`: bản đầy đủ các trường để kiểm tra.
- `crawl_summary.json`: thống kê lượt chạy.

## 9. Kết quả

Lượt chạy 1.000 trang (`python main.py report` để xem lại số liệu từ database):

| | |
|---|---|
| Trang đã crawl | 1.000, tất cả depth 0 (lấy từ sitemap) |
| HTTP 200 | 386 |
| HTTP 404 | 412 |
| Không có phản hồi HTTP | 202 (lý do ghi ở cột `error` của bảng `pages`) |
| Request lỗi (tổng) | 614 |
| URL phát hiện | 1.595 |
| URL bị loại bởi bộ lọc | 401 |
| Phim trích được (có nội dung) | 378 |

Trong 386 trang tải thành công, 378 trang cho ra phim có nội dung (khoảng 98%); các trang còn lại bị loại vì không có phần nội dung. Toàn bộ trang ở depth 0 vì URL phim được nạp từ sitemap (seed bổ sung) với số lượng (khoảng 1.500) lớn hơn ngân sách 1.000 trang. Hàng đợi là FIFO nên crawler phải cào hết depth 0 rồi mới sang depth 1, và đã dừng trước khi tới lượt. Các liên kết tìm thấy trên trang (khoảng 90 URL mới, thể hiện ở số URL phát hiện 1.595 so với 1.501 URL ban đầu) được xếp ở depth 1 nhưng chưa được cào. Ngoài ra, phần phim gợi ý trên trang TV360 tải bằng JavaScript nên mỗi trang phim dẫn tới rất ít URL mới. Khi sitemap có ít URL hơn ngân sách (lượt chạy 200 trang với `sitemap_limit = 100`), crawler đã đi tiếp sang depth 1 và depth 2.

## 10. Hạn chế

- Bình luận và mục phim gợi ý của TV360 được tải bằng JavaScript nên không có trong dữ liệu; `requests` chỉ tải HTML ban đầu.
- Sitemap của TV360 còn liệt kê nhiều URL không còn truy cập được: 412/1.000 trang trả về HTTP 404 và 202 trang không nhận được phản hồi, nên số phim thu được (378) nhỏ hơn nhiều so với số trang đã crawl.
- Parser phụ thuộc vào cấu trúc trang hiện tại của TV360; trang đổi giao diện thì cần sửa `parser.py`.
- Dữ liệu chỉ phản ánh phim có trên nền tảng TV360, nên có thể lệch so với toàn bộ phim thực tế.
- Crawler tuân thủ robots.txt và `Crawl-delay`, chỉ lấy văn bản công khai, không đăng nhập và không tải video. Dữ liệu dùng cho mục đích học tập; điều khoản dịch vụ của TV360 có quy định về quyền sở hữu nội dung nên không công bố toàn văn mô tả.
