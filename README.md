# XemAnime — crawler cá nhân và dataset 9 trường

Bản riêng chỉ cào **xemanime.org**, phục vụ phần crawling thứ 2 của nhóm.
**Sinh viên: Nguyễn Việt Phương — MSSV CE190248.** Mã thư mục nộp: `ce190248`.
Đây là phần việc **một người / một website** theo thông báo mới của thầy; nhóm vẫn cần bốn người
cào bốn nguồn còn lại. Không triển khai TF-IDF/BM25 trong bản này.

ZIP có code, run.bat, tests, schema và dữ liệu chạy thử thật ở `sample_data/`.
Thư mục `data/` chưa có trong ZIP: chạy chức năng 1 để bắt đầu lượt crawl của mình.
Kết quả mẫu do công cụ chạy để kiểm chứng, không được gán thành dữ liệu một sinh viên tự cào.

## 1. Chủ đề và phạm vi

**Movies & Entertainment**; dữ liệu phim lẻ và phim anime điện ảnh.
Domain duy nhất: `xemanime.org` (không mở rộng sang website khác).
Phim bộ, tập phim và trang tuyển tập được lưu như trang đã crawl nhưng bị loại khỏi bảng movies.

Thông báo mới yêu cầu mỗi bạn một website cùng đề tài, thống nhất model, lưu DB SQL/NoSQL.
Tài liệu PDF ban đầu nói 2–4 domain cho toàn bài; bản này đáp ứng phần cá nhân một domain,
không tự coi là đủ toàn bộ nguồn của nhóm. Đối chiếu kỹ thuật ở `DOI_CHIEU_YEU_CAU.md`.

## 2. Seed URLs

1. https://xemanime.org/phim-le
2. https://xemanime.org/doraemon

Crawler bắt đầu từ hai trang danh mục, theo hyperlink HTML thật.
Các trang phân trang/danh mục Conan, Shin và gợi ý phim được khám phá qua liên kết,
không sinh ID, gọi API video hoặc lấy danh sách phim từ nguồn khác.
Hai seed này khiến mẫu thiên lệch về phim lẻ/Doraemon; không đại diện mọi phim trên website.

## 3. Cấu hình và chạy trên Windows

Giải nén ZIP, vào thư mục `xemanime_crawler`, nhấp đúp **run.bat**.
Cần Python 3.10 trở lên, Internet cho lần cài Requests/BeautifulSoup đầu tiên và lúc crawl.
File BAT tạo `.venv`, cài `requirements.txt`, dùng đúng thư mục dự án kể cả mở từ nơi khác.

| Mục menu | Tác dụng |
|---|---|
| 1 | Crawl mới 100 trang / tiếp tục giữ cấu hình đã lưu |
| 2 | Mở rộng ngân sách tới ít nhất tổng 300 trang |
| 3 | Thống kê DB của mình |
| 4 | Xuất lại JSONL và CSV |
| 5 | Audit offline DB, BFS, robots, HTML và dataset |
| 6 | Mở data |
| 7 | Đóng gói dataset của Nguyễn Việt Phương / ce190248 để nộp GitHub |
| 8 | Chạy 25 tests offline |
| 9 | Mở README |
| M | Xem và audit dữ liệu mẫu ở sample_data |
| 0 | Thoát |

| Tham số | Mặc định | Mở rộng |
|---|---|---|
| Maximum pages / một domain | 100 | 300 |
| Maximum depth | 3 | 3 |
| Request timeout | 15 giây | 15 giây |
| Crawl delay | Ít nhất 2 giây/host | Ít nhất 2 giây/host |
| Giới hạn thời gian/lượt | 900 giây | 1800 giây |
| Body / redirects | 5 MB / 5 lần | 5 MB / 5 lần |

100 trang là ngân sách chạy mặc định, không phải quota tối thiểu được thầy xác nhận.
Delay 2 giây giảm tải cho website; nếu robots có Crawl-delay lớn hơn thì dùng giá trị lớn hơn.
Seed ở depth 0; link mới depth cha + 1. Tăng ngân sách không tải lại các trang đã lưu.
Khi chạy tiếp mà không truyền tham số, giữ ngân sách/depth/delay/timeout/thời gian đã lưu trong DB.
Mục 2 tăng ngân sách tối thiểu 300 trang / 1800 giây, giữ mức lớn hơn nếu đã cấu hình.
Không cho hạ max_pages dưới số trang đã lưu; muốn chạy ngân sách nhỏ hơn, tạo DB mới.

```powershell
# Chạy trong thư mục đã giải nén, sau khi run.bat chuẩn bị môi trường
.\.venv\Scripts\python.exe -X utf8 main.py crawl
.\.venv\Scripts\python.exe -X utf8 main.py crawl --profile expanded
.\.venv\Scripts\python.exe -X utf8 main.py crawl --max-pages 150 --max-depth 3 --delay 2
.\.venv\Scripts\python.exe -X utf8 main.py verify
.\.venv\Scripts\python.exe -X utf8 main.py report --db sample_data\crawler.db --output sample_data
```

Tên người cào mặc định là Nguyễn Việt Phương (config.py), mã nộp `ce190248`.
Muốn dùng bản này cho người khác, ghi tên bằng `--member "Họ tên"` khi bắt đầu và `--member-id` lúc đóng gói.
Chạy lại sẽ dùng tên đã lưu. Không đổi member/seed/domain/depth của một DB đã có; muốn thay, tạo DB mới
bằng `--db data\luot_moi\crawler.db --output data\luot_moi`.

## 4. Chiến lược BFS và xử lý lỗi

`deque` FIFO quản lý frontier, `set` chống trùng ngay khi enqueue; bảng frontier giữ URL/depth/cha/trạng thái
để tiếp tục sau khi dừng. Khi resume, pending được đọc theo depth/id. Không xử lý lớp sâu hơn một lớp bị hoãn.

Trước HTML, đọc robots.txt; kiểm tra scope/robots mỗi bước redirect. Không xác định được robots thì dừng host.
404/500/timeout/connection error được ghi vào DB; các URL khác vẫn tiếp tục. 403/429 dừng gửi request tới host,
không retry dồn dập. Crawler chỉ đọc response HTML, không tải video/player/ảnh/file nhị phân.

Dừng khi đủ tổng trang, frontier rỗng, hết thời gian/request budget, host bị chặn hoặc Ctrl+C.
SQLite commit từng trang giữ kết quả đã có. CLI in config và từng URL/depth/status/title/link count/response time.
Text trang bỏ script/style/player; chưa tokenization, stopwords hay tính TF-IDF/BM25.

## 5. Lọc URL và trích phim

Chỉ HTTP/HTTPS và hostname đúng; URL nội bộ canonical dùng HTTPS, bỏ fragment, trailing slash và alias percent
unreserved. Giữ query để nhận diện rồi loại query; không crawl trang tìm kiếm/booking/comment.
Loại mailto/tel/javascript, ảnh/CSS/JS/PDF/ZIP/video/audio, wp-admin/feed và đường `watch-*` phát video.
Chấp nhận root slug metadata và phân trang các danh mục đã chọn; mọi link bị loại có skip_reason trong links.

Movie cần `.movie-detail h1.entry-title` và JSON-LD Movie thuộc đúng URL. JSON-LD Movie trên danh mục không đủ
để tạo record. Chỉ xét loại Movie/TVSeries/TVEpisode gắn với URL đang đọc; bỏ metadata của phim liên quan
và metadata không xác định URL. TVSeries/TVEpisode, nhiều tập, tuyển tập/movie 1–45 bị loại;
các trường thiếu giữ null/[]/"". Record sai schema được ghi parse_error; lượt crawl tiếp tục.
Năm phim lấy từ metadata HTML release, không lấy datePublished của bài WordPress.
Synopsis chỉ lấy nội dung article.item-content; không gộp menu, “Mở rộng”, quảng cáo hay toàn bộ text trang.
Không coi nhãn theme HaLim/Admin là đạo diễn; không coi tên franchise Doraemon/Conan là thể loại.

Đã kiểm tra robots/truy cập ngày 04/10/2026: robots HTTP 200, nhóm User-agent * không có Disallow nội dung.
Đã kiểm tra liên kết công khai và tìm điều khoản riêng của nguồn nhưng chưa thấy trang đủ rõ;
không suy ra quyền tái phát hành văn bản từ robots hoặc từ chấp thuận của giảng viên.
Giữ URL nguồn, kiểm tra quyền chia sẻ trước khi đưa nội dung lên repository công khai.

## 6. Thiết kế DB và dataset nhóm

`data/crawler.db` dùng SQLite, có:

- **pages**: id, url unique, domain, title, content, depth, status_code, crawled_at; thêm final_url,
  crawl_status, response_time, error, html_path để kiểm chứng.
- **links**: id, source_url, target_url; thêm accepted/skip_reason, unique cặp link.
- **frontier**: URL chờ, depth, discovered_from, state.
- **movies**: đúng 9 cột dataset dưới đây; list lưu thành chuỗi JSON trong SQLite.
- **requests_log / robots / metadata**: log HTTP, snapshot robots, cấu hình và lý do dừng.

| Trường phim | Kiểu | Nghĩa |
|---|---|---|
| source | string | Luôn xemanime |
| source_url | string | URL gốc/khóa bản ghi |
| title | string | Tên phim |
| release_year | integer/null | Năm phim, thiếu null |
| synopsis | string | Cốt truyện, thiếu "" |
| genre | list[string] | Thể loại, thiếu [] |
| country | list[string] | Quốc gia sản xuất, thiếu [] |
| director | list[string] | Đạo diễn, thiếu [] |
| cast | list[string] | Diễn viên, thiếu [] |

Output của mỗi lượt:

- movies.jsonl: UTF-8 không BOM, một record đủ 9 trường/dòng — bản nộp chính.
- movies.csv và movies_with_synopsis.csv: đúng 9 cột, UTF-8 BOM để Excel đọc tiếng Việt, list trong ô là JSON.
- crawl_log.csv, links.csv, requests_log.csv, crawl_summary.json, quality_report.json, verification_report.json.
- html/: HTML cache để audit/reparse offline. Không lưu cookie/token hay tải video.

Mục 7 menu, hoặc:

```powershell
.\.venv\Scripts\python.exe -X utf8 main.py pack
```

sẽ tạo `submissions/ce190248/xemanime/movies.jsonl` và manifest.json theo contract movie-v2 của nhóm.
Chỉ nộp dataset do mình cào. Mỗi thành viên commit thư mục riêng, tạo PR; nhóm gộp JSONL với công cụ chung.
Tên/mã đã cấu hình cho lần crawl mới; dữ liệu kiểm chứng trong sample_data vẫn để trống người cào.
Đóng gói không cho đổi tên người thu thập đã ghi trong DB.

## 7. Kết quả chạy thử thật

Snapshot trong sample_data được thu ngày **04/10/2026**, thống kê lấy từ SQLite, không nhập số tay.
Đây là kết quả kiểm chứng công cụ, không gán cho cá nhân nào.

| Chỉ số | Giá trị |
|---|---|
| Seed URLs / domain | 2 / 1 |
| Pages crawled / HTML thành công | 100 / 100 |
| HTTP requests / robots requests | 102 / 2 |
| URL HTTP(S) duy nhất phát hiện | 9920 |
| URL skip duy nhất / link edges | 9834 / 15495 |
| Failed requests | 0 |
| Maximum depth cấu hình / thực tế | 3 / 2 |
| Phim lẻ trong dataset | 41 |
| Có synopsis / thiếu synopsis | 40 / 1 |
| Còn pending | 262 |
| Lý do dừng | max_pages |
| Depth 0 | 2 |
| Depth 1 | 57 |
| Depth 2 | 41 |
| Depth 3 | 0 |
| HTTP 200 | 100 |

25 tests offline đạt, gồm local HTTP server kiểm tra BFS/depth/duplicate/resume/redirect/robots/
HTTP 200–404–403–500/timeout/connection error và tests parser, schema, CSV, đóng gói.
Audit sample_data: **22/22 đạt**, gồm DB integrity, đủ bảng/cột, một domain,
HTML cache, reparse, robots, BFS, CSV/JSONL/summary khớp DB.

Chất lượng/thiếu metadata xem sample_data/quality_report.json. Thiếu thông tin từ nguồn được giữ đúng,
không bù bằng nguồn khác. Số bản ghi là theo URL, chưa ghép phim cùng thực thể/phiên bản lồng tiếng.
URL skip là URL từng bị loại ở ít nhất một liên kết, gồm link trùng tới trang đã cào; vì vậy số skip
và số đã crawl có thể giao nhau, không cộng hai số này để tính tổng URL phát hiện.
Các lỗi đã sửa và phạm vi kiểm tra ngày 05/10/2026 ở REVIEW_05_10_2026.md.

Các file: main.py, config.py, crawler.py, url_rules.py, parser.py, database.py, dataset.py,
verify_dataset.py, requirements.txt, run.bat, schemas/, tests/, sample_data/.
Không có dependency GUI, database server, browser automation hoặc công cụ ranking.
