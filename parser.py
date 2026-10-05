"""HTML parsing, URL normalization, filtering, and movie-information extraction."""
from __future__ import annotations

import html as htmllib
import json
import re
from pathlib import PurePosixPath
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup

import config

TRACKING_QUERY_KEYS = {"fbclid", "gclid", "mc_cid", "mc_eid", "_rsc"}

DETAIL_RE = re.compile(config.DETAIL_URL_REGEX)
SKIP_RE = re.compile(config.SKIP_URL_REGEX, re.I)
YEAR_RE = re.compile(r"\b(19[0-9]{2}|20[0-9]{2})\b")

MOVIE_FIELDS = ["title", "synopsis", "release_year", "genre", "director", "cast", "country"]

# JSON key names seen in page data -> canonical field (keys are lowercased before lookup).
KEY_ALIASES = {
    "title": ["title_vie", "title", "name", "vie_title"],
    "synopsis": ["description", "synopsis", "overview", "plot", "summary", "detail"],
    "release_year": ["release_year", "year", "release_date", "publish_date", "date_published", "released", "release"],
    "genre": ["genres", "genre", "categories", "category"],
    "director": ["directors", "director"],
    "cast": ["actors", "actor", "cast", "casts", "artists"],
    "country": ["countries", "country", "nation", "origin_country"],
}
LD_TYPES = {"movie", "tvseries", "tvseason", "creativework", "videoobject"}


# ======================================================================
# URL helpers
# ======================================================================
def normalize_url(url: str) -> str:
    """Normalize a web URL to reduce duplicate crawling.

    - lowercase scheme + host
    - remove URL fragments (#...)
    - remove default ports (:80 for HTTP, :443 for HTTPS)
    - remove a trailing slash except for the root path
    - remove common tracking parameters such as utm_* and fbclid
    - keep all other query parameters because they may change page content
    """
    parsed = urlparse(url.strip())
    scheme = parsed.scheme.lower()
    hostname = (parsed.hostname or "").lower()

    if not scheme or not hostname:
        return url.strip()

    port = parsed.port
    if (scheme == "http" and port == 80) or (scheme == "https" and port == 443):
        port = None

    netloc = hostname if port is None else f"{hostname}:{port}"

    path = parsed.path or "/"
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")

    kept_query = []
    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        lower_key = key.lower()
        if lower_key.startswith("utm_") or lower_key in TRACKING_QUERY_KEYS:
            continue
        kept_query.append((key, value))

    query = urlencode(kept_query, doseq=True)
    return urlunparse((scheme, netloc, path, parsed.params, query, ""))


def domain_is_allowed(hostname: str, allowed_domains: list[str]) -> bool:
    """Allow exact root domains and their subdomains without false suffix matches."""
    host = hostname.lower().strip(".")
    for domain in allowed_domains:
        root = domain.lower().strip(".")
        if host == root or host.endswith("." + root):
            return True
    return False


def is_valid_url(url: str, allowed_domains: list[str], blocked_extensions: set[str]) -> tuple[bool, str]:
    parsed = urlparse(url)

    if parsed.scheme not in {"http", "https"}:
        return False, "unsupported_protocol"

    if not parsed.hostname:
        return False, "missing_domain"

    if parsed.username or parsed.password:
        return False, "embedded_credentials"

    if not domain_is_allowed(parsed.hostname, allowed_domains):
        return False, "outside_allowed_domain"

    suffix = PurePosixPath(parsed.path.lower()).suffix
    if suffix in blocked_extensions:
        return False, "blocked_file_type"

    if SKIP_RE.search(url):
        return False, "blocked_path_keyword"

    return True, "valid"


def is_detail_url(url: str) -> bool:
    """True when the URL looks like a movie detail page (see config.DETAIL_URL_REGEX)."""
    return bool(DETAIL_RE.search(url))


def parse_html(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "html.parser")


def extract_links(current_url: str, soup: BeautifulSoup) -> list[str]:
    """Extract HTTP(S) links and convert relative links to absolute URLs."""
    result: list[str] = []
    seen: set[str] = set()

    for tag in soup.find_all("a", href=True):
        href = tag.get("href", "").strip()
        if not href:
            continue

        lower = href.lower()
        if lower.startswith(("mailto:", "javascript:", "tel:", "data:")):
            continue

        absolute = urljoin(current_url, href)
        normalized = normalize_url(absolute)
        parsed = urlparse(normalized)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            continue

        if normalized not in seen:
            seen.add(normalized)
            result.append(normalized)

    return result


# ======================================================================
# Text cleaning helpers
# ======================================================================
def clean_text(value) -> str:
    if not isinstance(value, str):
        return ""
    value = htmllib.unescape(value)
    value = re.sub(r"<[^>]+>", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def to_names(value) -> list[str]:
    """str | dict | list -> list of names (people, genres, countries)."""
    out: list[str] = []
    if value is None:
        return out
    if isinstance(value, str):
        out += [clean_text(x) for x in re.split(r"[,;|]", value)]
    elif isinstance(value, dict):
        for key in ("name", "title", "full_name", "fullname", "value", "label"):
            if isinstance(value.get(key), str):
                out.append(clean_text(value[key]))
                break
    elif isinstance(value, (list, tuple)):
        for item in value:
            out += to_names(item)
    return [x for x in out if x]


def join_names(value) -> str:
    seen: set[str] = set()
    names: list[str] = []
    for name in to_names(value):
        if name.lower() not in seen:
            seen.add(name.lower())
            names.append(name)
    return ", ".join(names)


def to_year(value) -> int | None:
    if value is None:
        return None
    match = YEAR_RE.search(str(value))
    return int(match.group(1)) if match else None


def clean_title(title: str) -> str:
    title = clean_text(title)
    title = re.sub(r"\s*[|\-–—]\s*FPT\s*Play.*$", "", title, flags=re.I)
    title = re.sub(r"^(xem\s+phim|phim)\s+", "", title, flags=re.I)
    return title.strip()


def _normalize_record(raw: dict) -> dict:
    synopsis = raw.get("synopsis")
    return {
        "title": clean_title(raw.get("title", "")),
        "synopsis": clean_text(synopsis) if isinstance(synopsis, str) else "",
        "release_year": to_year(raw.get("release_year")),
        "genre": join_names(raw.get("genre")),
        "director": join_names(raw.get("director")),
        "cast": join_names(raw.get("cast")),
        "country": join_names(raw.get("country")),
    }


def _iter_dicts(obj):
    if isinstance(obj, dict):
        yield obj
        for value in obj.values():
            yield from _iter_dicts(value)
    elif isinstance(obj, list):
        for value in obj:
            yield from _iter_dicts(value)


# ======================================================================
# Movie extraction: three strategies, merged field by field
# ======================================================================
def from_jsonld(soup: BeautifulSoup) -> dict:
    """Strategy 1: schema.org JSON-LD blocks."""
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or script.get_text() or "")
        except ValueError:
            continue
        for d in _iter_dicts(data):
            types = d.get("@type")
            types = [types] if isinstance(types, str) else (types or [])
            if not any(str(t).lower() in LD_TYPES for t in types) or not d.get("name"):
                continue
            return _normalize_record({
                "title": d.get("name"),
                "synopsis": d.get("description"),
                "release_year": d.get("datePublished") or d.get("dateCreated") or d.get("copyrightYear"),
                "genre": d.get("genre"),
                "director": d.get("director"),
                "cast": d.get("actor"),
                "country": d.get("countryOfOrigin") or d.get("locationCreated"),
            })
    return {}


def from_next_data(soup: BeautifulSoup) -> dict:
    """Strategy 2: Next.js __NEXT_DATA__ JSON. Picks the nested object with the most movie-like keys."""
    script = soup.find("script", id="__NEXT_DATA__")
    if script is None:
        return {}
    try:
        data = json.loads(script.string or script.get_text() or "")
    except ValueError:
        return {}

    best: dict = {}
    best_score = 0
    for d in _iter_dicts(data):
        lowered = {str(k).lower(): v for k, v in d.items()}
        raw: dict = {}
        for field, aliases in KEY_ALIASES.items():
            for alias in aliases:
                if lowered.get(alias) not in (None, "", [], {}):
                    raw[field] = lowered[alias]
                    break
        # A candidate must have a string title AND a string synopsis.
        if not isinstance(raw.get("title"), str) or not isinstance(raw.get("synopsis"), str):
            continue
        if len(raw) > best_score:
            best, best_score = raw, len(raw)
    return _normalize_record(best) if best else {}


def from_meta(soup: BeautifulSoup) -> dict:
    """Strategy 3 (fallback): Open Graph / description meta tags."""
    def meta(*names: str) -> str:
        for name in names:
            tag = soup.find("meta", attrs={"property": name}) or soup.find("meta", attrs={"name": name})
            if tag and tag.get("content"):
                return tag["content"]
        return ""

    title = meta("og:title", "twitter:title")
    if not title and soup.title:
        title = soup.title.get_text(" ", strip=True)
    synopsis = meta("og:description", "description", "twitter:description")
    return _normalize_record({"title": title, "synopsis": synopsis})


STRATEGIES = [("json_ld", from_jsonld), ("next_data", from_next_data), ("meta", from_meta)]


def parse_all_strategies(soup: BeautifulSoup) -> dict:
    """Used by `main.py --probe`: show what each strategy extracts on its own."""
    return {name: fn(soup) for name, fn in STRATEGIES}


def extract_movie_information(soup: BeautifulSoup, url: str) -> tuple[dict | None, str]:
    """Merge the strategies (earlier ones win) and apply the quality rules.

    Returns (record, "ok") or (None, skip_reason).
    """
    merged: dict = {field: "" for field in MOVIE_FIELDS}
    merged["release_year"] = None

    for _, strategy in STRATEGIES:
        part = strategy(soup)
        for field in MOVIE_FIELDS:
            if not merged[field] and part.get(field):
                merged[field] = part[field]      # only fill fields still missing

    if config.REQUIRE_TITLE and not merged["title"]:
        return None, "missing_title"
    if config.REQUIRE_SYNOPSIS and len(merged["synopsis"]) < config.MIN_SYNOPSIS_LENGTH:
        return None, "missing_synopsis"

    merged["source"] = config.SOURCE_NAME
    merged["source_url"] = url
    return merged, "ok"
