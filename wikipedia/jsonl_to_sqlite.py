#!/usr/bin/env python3
"""Nạp wikipedia.jsonl (movie-v2 + country) vào SQLite.
Dùng:  python jsonl_to_sqlite.py wikipedia.jsonl -o movies.db
"""
import argparse, json, sqlite3

SCHEMA = """
PRAGMA foreign_keys = ON;
DROP TABLE IF EXISTS movie_people; DROP TABLE IF EXISTS movie_genres;
DROP TABLE IF EXISTS movie_countries; DROP TABLE IF EXISTS movies;

CREATE TABLE movies (
  id           INTEGER PRIMARY KEY,
  source       TEXT NOT NULL,
  source_url   TEXT NOT NULL UNIQUE,
  title        TEXT NOT NULL,
  release_year INTEGER,
  synopsis     TEXT,
  genre        TEXT NOT NULL DEFAULT '[]',     -- JSON array, giữ nguyên thứ tự gốc
  director     TEXT NOT NULL DEFAULT '[]',
  cast         TEXT NOT NULL DEFAULT '[]',
  country      TEXT NOT NULL DEFAULT '[]'
);
-- bảng phụ để truy vấn/lọc theo từng giá trị
CREATE TABLE movie_genres    (movie_id INTEGER NOT NULL REFERENCES movies(id) ON DELETE CASCADE, genre   TEXT NOT NULL, PRIMARY KEY (movie_id, genre));
CREATE TABLE movie_countries (movie_id INTEGER NOT NULL REFERENCES movies(id) ON DELETE CASCADE, country TEXT NOT NULL, PRIMARY KEY (movie_id, country));
CREATE TABLE movie_people    (movie_id INTEGER NOT NULL REFERENCES movies(id) ON DELETE CASCADE, name    TEXT NOT NULL,
                              role TEXT NOT NULL CHECK (role IN ('director','cast')), PRIMARY KEY (movie_id, name, role));
CREATE INDEX idx_movies_year ON movies(release_year);
CREATE INDEX idx_movies_title ON movies(title);
CREATE INDEX idx_genres ON movie_genres(genre);
CREATE INDEX idx_countries ON movie_countries(country);
CREATE INDEX idx_people ON movie_people(name);
"""

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("jsonl"); ap.add_argument("-o", "--out", default="movies.db")
    a = ap.parse_args()
    db = sqlite3.connect(a.out); db.executescript(SCHEMA)
    n = 0
    with open(a.jsonl, encoding="utf-8") as f:
        for line in f:
            r = json.loads(line)
            cur = db.execute(
                "INSERT INTO movies(source,source_url,title,release_year,synopsis,genre,director,cast,country) VALUES (?,?,?,?,?,?,?,?,?)",
                (r["source"], r["source_url"], r["title"], r["release_year"], r["synopsis"],
                 *(json.dumps(r[k], ensure_ascii=False) for k in ("genre", "director", "cast", "country"))))
            mid = cur.lastrowid
            db.executemany("INSERT OR IGNORE INTO movie_genres VALUES (?,?)", [(mid, g) for g in r["genre"]])
            db.executemany("INSERT OR IGNORE INTO movie_countries VALUES (?,?)", [(mid, c) for c in r["country"]])
            db.executemany("INSERT OR IGNORE INTO movie_people VALUES (?,?,?)",
                           [(mid, p, "director") for p in r["director"]] + [(mid, p, "cast") for p in r["cast"]])
            n += 1
    db.commit(); print(f"Đã nạp {n} phim -> {a.out}")

if __name__ == "__main__":
    main()
