"""CLI cá nhân: crawl/report/export/reparse/verify/pack, chỉ XemAnime."""
import argparse
import json
import sqlite3
from contextlib import closing
from pathlib import Path
from dataclasses import asdict
from config import Config, MEMBER_ID
from crawler import Crawler
from database import connect, export_all, save_movie, summary
from dataset import pack_submission
from parser import parse_page

# Bắt đầu đọc ở main(): nhận lệnh -> cấu hình -> crawl/đọc DB -> xuất kết quả.
def main():  # Điểm điều phối chương trình; run.bat gọi hàm này qua main.py.
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["crawl", "report", "export", "reparse", "verify", "pack"])  # Chọn việc cần làm; report và verify không cào lại website.
    parser.add_argument("--profile", choices=["pilot", "expanded"], default="pilot")  # pilot mặc định 100 trang; expanded nâng ngân sách lên ít nhất 300 trang.
    parser.add_argument("--db", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--max-pages", type=int)  # Tổng số trang tối đa, không phải số phim và không phải số trang thêm mới.
    parser.add_argument("--max-depth", type=int)  # Độ sâu theo liên kết; khác hoàn toàn --max-pages.
    parser.add_argument("--timeout", type=float)
    parser.add_argument("--delay", type=float)
    parser.add_argument("--max-seconds", type=float)
    parser.add_argument("--member", help="Tên người thực hiện; giữ nguyên khi resume")
    parser.add_argument("--member-id", default=MEMBER_ID, help="Mã thành viên khi đóng gói nộp GitHub")
    args = parser.parse_args()
    output = args.output or (args.db.parent if args.db else Path("data"))  # Nếu chỉ truyền --db, xuất file cạnh DB đó.
    path = args.db or output / "crawler.db"
    try:
        if args.command != "crawl" and not path.is_file():  # Đọc/kiểm tra dữ liệu phải có DB trước; crawl mới được tạo DB.
            raise ValueError("Chưa có database. Chạy crawl trước.")
        if args.command == "verify":  # Audit offline: đối chiếu DB với HTML cache và các file dataset.
            from verify_dataset import verify
            result = verify(path, output)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            if not result["all_passed"]:
                parser.exit(1, "Audit chưa đạt.\n")
            return
        with closing(connect(path)) as db:  # closing bảo đảm đóng kết nối SQLite khi xong hoặc khi có lỗi.
            previous = db.execute("SELECT value FROM metadata WHERE key='config'").fetchone()  # Đọc cấu hình lượt trước để chạy tiếp đúng phạm vi.
            previous = json.loads(previous[0]) if previous else {}
            if args.command == "pack" and previous and args.member is not None and args.member != previous.get("member", ""):  # Không đổi tên người thu thập của dataset đã có.
                raise ValueError("Không đổi người thu thập khi đóng gói; dùng tên đã lưu trong DB.")
            config = Config(db_path=path, output_dir=output,
                            member=args.member if args.member is not None else previous.get("member", Config().member))
            for key in ("max_pages", "max_depth", "delay", "timeout", "max_seconds"):  # Ưu tiên giới hạn đã lưu trong DB khi resume.
                if key in previous:
                    setattr(config, key, previous[key])
            if args.profile == "expanded":  # Tăng ngân sách, giữ mức lớn hơn nếu người dùng đã cấu hình.
                config.max_pages = max(config.max_pages, 300)
                config.max_seconds = max(config.max_seconds, 1800)
            for key in ("max_pages", "max_depth", "delay", "timeout", "max_seconds"):  # Tham số người dùng truyền trực tiếp được ưu tiên cuối cùng.
                if getattr(args, key) is not None:
                    setattr(config, key, getattr(args, key))
            config.max_pages_per_domain = config.max_pages  # Một domain nên giới hạn domain bằng giới hạn trang tổng.
            config.validate()
            if args.command == "crawl":  # Có gửi request: chạy BFS rồi xuất dataset/báo cáo.
                print("CRAWLER CONFIGURATION")
                print(json.dumps(dict(topic="Movies & Entertainment", **asdict(config)), ensure_ascii=False, default=str, indent=2))
                print("Stop reason:", Crawler(config, db).run())
                result = export_all(db, output)
            elif args.command == "reparse":  # Chỉ đọc HTML đã lưu và trích lại; không tải website.
                with db:
                    for row in list(db.execute("SELECT * FROM pages WHERE html_path IS NOT NULL")):
                        parsed = parse_page(Path(row["html_path"]).read_bytes(), row["final_url"], row["crawled_at"])
                        db.execute("UPDATE pages SET title=?,content=?,page_type=?,error=?,crawl_status='ok' WHERE url=?",
                                   (parsed["title"], parsed["content"], parsed["page_type"], parsed["parse_error"], row["url"]))
                        db.execute("DELETE FROM movies WHERE source_url=?", (row["final_url"],))  # Xóa bản phim cũ của URL trước khi lưu kết quả parse mới.
                        if parsed["movie"]:
                            save_movie(db, parsed["movie"])
                result = export_all(db, output)
            elif args.command == "pack":  # Đóng gói JSONL và manifest; chưa tự đẩy lên GitHub.
                export_all(db, output)
                if not args.member_id or not config.member:
                    raise ValueError("pack cần --member-id và --member họ tên thật (hoặc tên đã lưu trong DB).")
                folder = pack_submission(output / "movies.jsonl", args.member_id, config.member)
                print("Đã đóng gói:", folder)
                return
            elif args.command == "export":  # Xuất lại dữ liệu từ DB, không cào thêm.
                result = export_all(db, output)
            else:  # Lệnh report: tính thống kê bằng SQL trên DB hiện có.
                result = summary(db)
            print("CRAWLING SUMMARY")
            print(json.dumps(result, ensure_ascii=False, indent=2))
    except (ValueError, TypeError, OSError, sqlite3.Error) as exc:  # Báo lỗi dễ đọc và trả mã thoát 1 để BAT nhận biết thất bại.
        parser.exit(1, f"Lỗi: {exc}\n")


if __name__ == "__main__":
    main()
