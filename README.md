Báo cáo thu thập dữ liệu: w2w.vn
1. Mục tiêu và nguồn dữ liệu

Thu thập dữ liệu phim từ https://w2w.vn/, chỉ lấy nội dung tiếng Việt. Tên phim không phải tiếng Việt thì vẫn giữ. Dữ liệu được chuẩn hoá về 9 trường chung của nhóm: source, source_url, title, release_year, synopsis, genre, director, cast, country. Kết quả lưu bằng SQLite.

2. Đặc điểm của w2w.vn so với các website giới thiệu phim thông thường

W2W không phải một cơ sở dữ liệu phim kiểu bách khoa. Nó là website cộng đồng đánh giá phim (người dùng gọi là "Weirdo"), nên có những khác biệt sau:
Trang phim lẫn nhiều nội dung ngoài thông tin phim. Dưới phần giới thiệu là đánh giá của người dùng, danh sách phim cùng chủ đề và bài viết liên quan. Parser phải cắt đúng phần đầu trang, vì nếu lấy cả trang thì mô tả sẽ lẫn bình luận.
Không có trường quốc gia và tên gốc. Trang chỉ hiển thị tên phim, mô tả, ngày khởi chiếu, thể loại, đạo diễn và diễn viên. Tôi đã tìm trong nội dung trang và không thấy quốc gia sản xuất, nên country được để [].
Phần lớn phim là phim nước ngoài, mô tả thường bằng tiếng Anh. Trang liệt kê khoảng 3.269 phim, gồm cả phim quốc tế rất nhỏ, nên một nửa số mô tả không phải tiếng Việt.
Danh sách phim phân trang không rõ URL. Có 182 trang danh sách, nhưng tôi không xác định được địa chỉ của trang 2 trở đi. Thay vào đó, site có sitemap (phim-sitemap1.xml đến phim-sitemap4.xml) liệt kê toàn bộ URL phim.
Các thông tin lộn xộn trong cùng một vùng chữ. Thể loại, ngày khởi chiếu, đạo diễn, diễn viên và mô tả đều là các đoạn chữ rời nhau, không có thẻ HTML riêng. Parser phải nhận dạng bằng nhãn ("Đạo diễn:", "Diễn viên:", "Khởi chiếu:").
Dữ liệu không đồng đều giữa các phim. Một số phim thiếu thể loại, ngày khởi chiếu hoặc mô tả.
Tên người có nhiều bảng chữ cái. Ví dụ có tên diễn viên viết bằng chữ Kirin, và vẫn được giữ nguyên.
Website cho phép cào các trang phim: robots.txt chỉ cấm /wp-admin/ và /feed/. Chương trình luôn đọc robots.txt trước khi tải, đặt khoảng cách 2 giây giữa hai request và khai báo User-Agent rõ ràng.

3. Các vấn đề gặp phải và cách xử lý


- Không biết URL phân trang của danh sách phim
Lấy URL phim từ sitemap, chọn đều theo thứ tự để tránh chỉ lấy toàn phim mới
- Cả 20 phim chạy thử đều báo parse_error
Truy ra nguyên nhân là thiếu hằng HIDDEN trong parser.py sau khi sửa code. Sửa lỗi rồi dùng lệnh reparse để parse lại từ HTML đã lưu, không phải tải lại
- 449/900 mô tả là tiếng Anh
Viết hàm is_vietnamese: đếm các ký tự chỉ có trong tiếng Việt. Nếu không đạt thì để synopsis = "", không tự dịch hay viết thêm
- 40/900 trang không có mô tả
Ghi error = no_synopsis, synopsis = ""
- Không có quốc gia sản xuất
Để country = [], do web không có country.
- Một số phim thiếu năm hoặc thể loại (1 phim thiếu năm, 5 phim thiếu thể loại trong 404 phim)
Đã mở trang web đối chiếu: website để trống. Giữ release_year = NULL và genre = [] theo quy ước schema
- Phim chưa chiếu chưa có nội dung đầy đủ
Parse ngày khởi chiếu (dd/mm/yyyy), nếu sau ngày cào thì xếp upcoming và loại khỏi tập kết quả. Có 7 phim bị loại
- Phân biệt thể loại với văn bản thường
Dùng danh sách thể loại của website làm bộ lọc

Mỗi phim không đạt đều được ghi lại trong cột error của bảng pages, nên toàn bộ vấn đề có thể thống kê lại bằng một câu SELECT.

4. Số lượng phim

Website có khoảng 3.269 phim tại thời điểm khảo sát.
Cào 900 phim (chọn đều từ sitemap): 900/900 trang tải thành công (HTTP 200), 0 request lỗi, 900 phim được parse.
Dùng được: 404 phim. Không trùng URL.
Không dùng được: 496 phim, gồm 449 phim mô tả tiếng Anh, 40 phim không có mô tả và 7 phim chưa chiếu.
Không cào: khoảng 2.400 phim. Đây là chủ động chọn mẫu, không phải do lỗi. Sitemap chứa toàn bộ URL, nhưng chốt 404 .
Chất lượng 404 phim: director và cast đầy đủ cho cả 404 phim, release_year thiếu 1, genre thiếu 5, country rỗng toàn bộ.
Trên 900 trang phim, chương trình ghi nhận 29.793 lượt liên kết bị bộ lọc loại: 14.911 lượt dẫn tới trang ngoài phạm vi phim (menu, blog, cộng đồng, bảng xếp hạng), 9.000 lượt dẫn tới tên miền khác (như mạng xã hội) và 5.882 lượt dẫn sang phim khác nhưng vượt max_depth = 0. Vì danh sách URL đã lấy từ sitemap nên chương trình không cần đi theo liên kết. Số URL được phát hiện nhưng không cào là khoảng 119, trên tổng 1.019 URL phát hiện được. . Đây là các liên kết tìm thấy trên trang phim nhưng bị bộ lọc loại (ngoài phạm vi /phim/ hoặc vượt max_depth = 0), vì danh sách URL đã lấy từ sitemap nên không cần đi theo liên kết.

5. Hàm loại trùng và làm sạch dữ liệu

Loại trùng lặp:
Frontier.add (frontier.py): tập seen đảm bảo một URL chỉ xếp hàng một lần.

normalize_url (url_rules.py): bỏ phần #fragment, chuẩn hoá tên miền (www.w2w.vn thành w2w.vn), thêm dấu / cuối, chuẩn hoá mã %. Nhờ vậy nhiều cách viết cùng một địa chỉ chỉ còn một.

Crawler.fetched (crawler.py): nhớ URL đã tải, kể cả URL sau chuyển hướng. Hai URL cùng trỏ về một trang sẽ không bị lưu hai lần.

Cơ sở dữ liệu: pages.url là UNIQUE, links có UNIQUE (source_url, target_url), movies.source_url và movies_clean.source_url là khoá chính.

sitemap_seeds (main.py) và names (parser.py): loại trùng URL và loại trùng tên trong danh sách thể loại, đạo diễn, diễn viên.

Làm sạch:

compact: gom khoảng trắng thừa. fold: bỏ dấu tiếng Việt để so khớp nhãn như "Đạo diễn".

names: tách danh sách "a, b, c", bỏ giá trị rỗng.

w2w_split_label: tách nhãn và giá trị. Danh sách thể loại của website dùng để nhận diện thể loại.

is_vietnamese: lọc mô tả tiếng Việt.

Cắt vùng đầu trang trước phần đánh giá, loại thẻ ẩn (script, style, [hidden]...) khỏi văn bản.

Chuyển ngày dd/mm/yyyy sang dạng ISO, lấy release_year. Hàm release_status đánh dấu phim chưa chiếu.

url_reason và Robots.allows: chỉ nhận URL thuộc w2w.vn, đúng đường dẫn phim và được robots cho phép.

6. Logic chạy của chương trình

main.py crawl --films N đọc 4 sitemap, lọc URL phim, chọn đều N URL làm danh sách khởi đầu.

Crawler.run kiểm tra hợp lệ các URL, đưa vào hàng đợi FIFO (Frontier).

Với mỗi URL lấy ra khỏi hàng đợi, chương trình kiểm tra đã tải chưa, còn trong giới hạn số trang chưa.

fetch kiểm tra phạm vi và robots.txt, chờ đủ khoảng cách 2 giây, rồi tải trang. Chuyển hướng được xử lý thủ công (tối đa 5 lần), mỗi bước đều kiểm tra lại phạm vi.

HTML được lưu vào data/html/, sau đó parse_page và w2w_movie trích xuất 9 trường.

save_page ghi trang, liên kết và phim trong một giao dịch SQLite (cùng thành công hoặc cùng không ghi).

Lỗi một trang (HTTP lỗi, hết thời gian chờ, lỗi parse) chỉ được ghi vào cột error, không dừng cả chương trình.
Cuối cùng export_all ghi các file CSV, crawl_summary.json, bảng movies_clean, và in bảng tóm tắt.

7. Vai trò từng file

config.py: cấu hình (tên miền, độ sâu, số trang tối đa, thời gian chờ, User-Agent).
main.py: dòng lệnh, đọc sitemap, điều phối các lệnh crawl, report, export, reparse.
crawler.py: vòng lặp cào, tải trang, robots, chuyển hướng, giới hạn tốc độ.
frontier.py: hàng đợi URL (FIFO) và tập URL đã thấy.
url_rules.py: chuẩn hoá URL, bộ lọc phạm vi, đọc robots.txt.
parser.py: đọc HTML, trích xuất 9 trường và làm sạch.
database.py: bảng SQLite (pages, links, movies, movies_clean), thống kê, xuất CSV.
tests/test_crawler.py: kiểm thử cho crawler, bộ lọc URL và parser.
README.md, requirements.txt, .gitignore: hướng dẫn, thư viện cần cài, file bỏ qua khi dùng git.

8. Lệnh chạy

python main.py crawl --films 900     # cào 900 URL chọn đều từ sitemap
python main.py report                # xem lại thống kê từ database
python main.py export                # ghi lại CSV, JSON và bảng movies_clean
python main.py reparse               # parse lại từ HTML đã lưu, không gọi mạng

