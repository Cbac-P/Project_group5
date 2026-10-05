"""Chỉ một website cho phần việc cá nhân; số trang là tổng tích lũy."""
from dataclasses import dataclass, field
from pathlib import Path
# Cấu hình là các giới hạn của lượt cào; không đặt mục tiêu số phim ở đây.
MEMBER_NAME = "Nguyễn Việt Phương"
MEMBER_ID = "ce190248"
STUDENT_ID = "CE190248"

@dataclass
class Config:
    seeds: list[str] = field(default_factory=lambda: ["https://xemanime.org/phim-le", "https://xemanime.org/doraemon"])  # Hai điểm xuất phát, cùng một website.
    allowed_domains: list[str] = field(default_factory=lambda: ["xemanime.org"])  # Chỉ nhận URL thuộc hostname này.
    max_depth: int = 3  # Seed ở depth 0; trần 3 cho phép 4 lớp: 0, 1, 2, 3.
    max_pages: int = 100  # Đếm TRANG đã xử lý, không đếm PHIM; 100 trang có thể chỉ có 41 phim.
    max_pages_per_domain: int = 100  # Giới hạn trang của từng domain; bản này chỉ có một domain.
    timeout: float = 15  # Thời gian chờ cho một request, tính bằng giây.
    delay: float = 2  # Nghỉ ít nhất 2 giây giữa các request cùng host; robots có thể yêu cầu lâu hơn.
    max_redirects: int = 5  # Tối đa 5 lần chuyển hướng cho một lần tải.
    max_response_bytes: int = 5_000_000  # Body tối đa khoảng 5 MB để tránh tải quá lớn.
    max_seconds: float = 900  # Ngân sách thời gian một lượt: 900 giây = 15 phút; không phải hạn nộp bài.
    user_agent: str = "XemAnimeStudentCrawler/1.0 (educational metadata crawler)"  # Tên crawler gửi cho website, cũng dùng để chọn nhóm robots.
    db_path: Path = Path("data/crawler.db")  # SQLite lưu trang, phim, hàng đợi và log.
    output_dir: Path = Path("data")  # Nơi xuất CSV, JSONL, báo cáo và cache HTML.
    member: str = MEMBER_NAME

    def validate(self):  # Kiểm tra cấu hình trước khi gửi request.
        if not self.seeds or not self.allowed_domains:
            raise ValueError("Phải khai báo seeds và allowed_domains.")
        if self.max_depth < 0 or self.max_pages < 1 or self.max_pages_per_domain < 1:
            raise ValueError("Depth >= 0, ngân sách trang > 0.")
        if self.delay < 0 or self.timeout <= 0 or self.max_seconds <= 0:
            raise ValueError("Delay >= 0, timeout/max_seconds > 0.")
        if self.max_response_bytes < 1 or self.max_redirects < 0:
            raise ValueError("Giới hạn response/redirect không hợp lệ.")
