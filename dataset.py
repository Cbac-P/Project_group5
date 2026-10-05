"""Dataset nhóm: đúng chín trường, JSONL UTF-8 và manifest riêng."""
import hashlib
import json
import re
from pathlib import Path
from urllib.parse import urlsplit

SCHEMA = json.loads((Path(__file__).resolve().parent / "schemas/movie-v2.schema.json").read_text(encoding="utf-8"))  # Schema là quy ước chung để các thành viên gộp dataset cùng cấu trúc.
FIELDS = SCHEMA["required"]  # Danh sách 9 trường bắt buộc; không thêm tên/MSSV vào từng phim.


def validate_movie(record):  # Kiểm tra cấu trúc/kiểu; không tự xác minh metadata có đúng ngoài đời hay không.
    if not isinstance(record, dict) or set(record) != set(FIELDS):  # Phải có đúng tập 9 trường, không thiếu và không thừa.
        raise ValueError("Bản ghi cần đúng 9 trường theo schema chung.")
    for key, rule in SCHEMA["properties"].items():
        value = record[key]
        allowed = rule["type"] if isinstance(rule["type"], list) else [rule["type"]]
        types = {"string": str, "integer": int, "null": type(None), "array": list}
        if type(value) not in [types[t] for t in allowed]:  # Dùng type để True/False không bị nhận nhầm là năm dạng int.
            raise ValueError(f"{key}: sai kiểu dữ liệu.")
        if isinstance(value, str) and (value != value.strip() or len(value) < rule.get("minLength", 0)):
            raise ValueError(f"{key}: chuỗi rỗng/không được trim.")
        if isinstance(value, list) and (any(type(s) is not str or not s.strip() or s != s.strip() for s in value)  # Các list cần chuỗi sạch, không rỗng và không có phần tử trùng.
                                       or len(value) != len(set(value))):
            raise ValueError(f"{key}: cần danh sách chuỗi không rỗng, không trùng.")
        if type(value) is int and not rule.get("minimum", value) <= value <= rule.get("maximum", value):
            raise ValueError(f"{key}: ngoài khoảng hợp lệ.")
    parts = urlsplit(record["source_url"])  # Dataset cá nhân chỉ nhận nguồn xemanime và URL HTTPS đúng hostname.
    if (record["source"] != "xemanime" or parts.scheme != "https" or parts.hostname != "xemanime.org"
            or parts.username or parts.password or parts.fragment or parts.query or parts.port not in (None, 443)):
        raise ValueError("Nguồn phải là xemanime và URL HTTPS canonical của xemanime.org.")
    return record


def read_dataset(path):  # Đọc JSONL, kiểm tra từng phim và chống trùng source_url.
    records, urls = [], set()
    for number, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):  # Số dòng giúp báo chính xác vị trí bản ghi lỗi.
        try:
            record = validate_movie(json.loads(line))
            if record["source_url"] in urls:
                raise ValueError("URL trùng.")
            urls.add(record["source_url"])
            records.append(record)
        except (ValueError, TypeError) as exc:
            raise ValueError(f"{path}:{number}: {exc}") from exc
    return records


def pack_submission(input_path, member_id, member_name, root=Path("submissions")):  # Tạo bộ nộp movies.jsonl + manifest.json; không tự publish lên GitHub.
    if not re.fullmatch(r"[a-z][a-z0-9_-]{0,39}", member_id) or not member_name.strip():  # Giới hạn mã thành viên để tên thư mục không chứa đường dẫn tùy ý.
        raise ValueError("Cần mã thành viên ASCII chữ thường, bắt đầu bằng chữ và họ tên thật.")
    records = read_dataset(input_path)
    if not records:
        raise ValueError("Không đóng gói dataset rỗng.")
    body = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in sorted(records, key=lambda r: r["source_url"])).encode("utf-8")  # Sắp theo URL để thứ tự ổn định; mỗi phim một dòng JSON UTF-8.
    folder = Path(root) / member_id / "xemanime"  # Mỗi người một thư mục riêng, thuận tiện cho nhóm gộp dữ liệu.
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "movies.jsonl").write_bytes(body)
    manifest = dict(schema_version="movie-v2", source="xemanime", member_id=member_id,
                    member_name=member_name.strip(), record_count=len(records), sha256=hashlib.sha256(body).hexdigest())  # SHA-256 băm bytes file dataset để kiểm tra file thay đổi, không chống trùng phim.
    (folder / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return folder
