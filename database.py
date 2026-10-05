"""SQLite persistence for movies, with content-level de-duplication."""
from __future__ import annotations

import csv
import hashlib
import re
import sqlite3
import statistics
import unicodedata
from datetime import datetime, timezone
from pathlib import Path


def normalize_text(text: str | None) -> str:
    """Lowercase, strip Vietnamese diacritics/punctuation, collapse spaces.

    'Ký Sinh Trùng!' and 'ky sinh trung' normalize to the same string, so the
    same film written slightly differently is detected as a duplicate.
    """
    text = (text or "").replace("đ", "d").replace("Đ", "d")
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def sha256_of(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def make_dedup_key(title: str, release_year: int | None) -> str:
    return sha256_of(f"{normalize_text(title)}|{release_year or ''}")


def make_synopsis_hash(synopsis: str) -> str:
    return sha256_of(normalize_text(synopsis))


class MovieDatabase:
    def __init__(self, path: Path, reset: bool = False) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)

        self.path = path
        self.conn = sqlite3.connect(path)

        if reset:
            self.conn.executescript("DROP TABLE IF EXISTS movies;")
            self.conn.commit()

        self._create_schema()

    def _create_schema(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS movies (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                source        TEXT NOT NULL,
                source_url    TEXT NOT NULL UNIQUE,
                title         TEXT NOT NULL,
                release_year  INTEGER,
                synopsis      TEXT NOT NULL,
                genre         TEXT,
                director      TEXT,
                "cast"        TEXT,
                country       TEXT,
                dedup_key     TEXT NOT NULL,
                synopsis_hash TEXT NOT NULL,
                depth         INTEGER,
                crawled_at    TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_movies_dedup_key
            ON movies(dedup_key);

            CREATE INDEX IF NOT EXISTS idx_movies_synopsis_hash
            ON movies(synopsis_hash);
            """
        )
        self.conn.commit()

    # ---------- duplicate checks ----------
    def url_exists(self, source_url: str) -> bool:
        cur = self.conn.execute(
            "SELECT 1 FROM movies WHERE source_url = ? LIMIT 1", (source_url,)
        )
        return cur.fetchone() is not None

    def dedup_key_exists(self, dedup_key: str) -> bool:
        """Same normalized title + same year already stored."""
        cur = self.conn.execute(
            "SELECT 1 FROM movies WHERE dedup_key = ? LIMIT 1", (dedup_key,)
        )
        return cur.fetchone() is not None

    def synopsis_hash_exists(self, synopsis_hash: str) -> bool:
        """Same normalized synopsis already stored (e.g. re-listed under another title)."""
        cur = self.conn.execute(
            "SELECT 1 FROM movies WHERE synopsis_hash = ? LIMIT 1", (synopsis_hash,)
        )
        return cur.fetchone() is not None

    # ---------- write ----------
    def save_movie(self, record: dict, depth: int) -> None:
        self.conn.execute(
            """
            INSERT INTO movies
            (source, source_url, title, release_year, synopsis, genre, director,
             "cast", country, dedup_key, synopsis_hash, depth, crawled_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record["source"],
                record["source_url"],
                record["title"],
                record.get("release_year"),
                record["synopsis"],
                record.get("genre"),
                record.get("director"),
                record.get("cast"),
                record.get("country"),
                make_dedup_key(record["title"], record.get("release_year")),
                make_synopsis_hash(record["synopsis"]),
                depth,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        self.conn.commit()

    # ---------- read / reports ----------
    def count(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM movies").fetchone()[0]

    def export_csv(self, path: Path) -> int:
        rows = self.conn.execute(
            'SELECT id, source, source_url, title, release_year, synopsis, genre, '
            'director, "cast", country FROM movies ORDER BY id'
        ).fetchall()
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f)
            writer.writerow(["id", "source", "source_url", "title", "release_year",
                             "synopsis", "genre", "director", "cast", "country"])
            writer.writerows(rows)
        return len(rows)

    def quality_report(self) -> str:
        cur = self.conn.cursor()
        total = self.count()
        lines = [f"Tổng số phim lưu trong DB : {total}"]
        if total == 0:
            return "\n".join(lines)

        lengths = [len(r[0]) for r in cur.execute("SELECT synopsis FROM movies").fetchall()]
        lines.append(f"Độ dài synopsis trung bình: {statistics.mean(lengths):.0f} ký tự")
        lines.append(f"Độ dài synopsis nhỏ nhất  : {min(lengths)}")
        lines.append(f"Độ dài synopsis lớn nhất  : {max(lengths)}")

        lines.append("\nTỉ lệ thiếu từng trường (NULL/rỗng):")
        for col in ("release_year", "genre", "director", '"cast"', "country"):
            missing = cur.execute(
                f"SELECT COUNT(*) FROM movies WHERE {col} IS NULL OR TRIM({col}) = ''"
            ).fetchone()[0]
            lines.append(f"  {col.strip(chr(34)):<13}: {missing} ({missing / total * 100:.1f}%)")

        dup_key = cur.execute(
            "SELECT COUNT(*) FROM (SELECT dedup_key FROM movies GROUP BY dedup_key HAVING COUNT(*) > 1)"
        ).fetchone()[0]
        dup_syn = cur.execute(
            "SELECT COUNT(*) FROM (SELECT synopsis_hash FROM movies GROUP BY synopsis_hash HAVING COUNT(*) > 1)"
        ).fetchone()[0]
        lines.append(f"\nNhóm trùng (title+year) còn sót : {dup_key}")
        lines.append(f"Nhóm trùng synopsis còn sót     : {dup_syn}")

        lines.append("\nPhân bố theo depth:")
        for depth, count in cur.execute(
            "SELECT depth, COUNT(*) FROM movies GROUP BY depth ORDER BY depth"
        ).fetchall():
            lines.append(f"  Depth {depth}: {count}")
        return "\n".join(lines)

    def close(self) -> None:
        self.conn.close()
