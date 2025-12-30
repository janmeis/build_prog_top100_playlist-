import json
import time
import csv
import html
import re
import argparse
from datetime import datetime, timezone
from urllib.request import Request, urlopen
from urllib.parse import urlencode
from pathlib import Path

BASE_URL = "https://www.reddit.com/r/progalbums/new.json"
HEADERS = {"User-Agent": "prog-albums-crawler/1.0"}
MAX_POSTS = 1000
SLEEP_SEC = 1

# Regex to match last "(Country, Year or range)" at the end
ALBUM_REGEX = re.compile(r"^(.*)\s*\(([^,]+),\s*(.+?)\)\s*$")

def utc_to_excel_datetime(ts):
    """Convert UTC timestamp to Excel-friendly YYYY-MM-DD HH:MM"""
    dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    return dt.strftime("%Y-%m-%d %H:%M")

def normalize_dashes(title):
    """Replace all dash variants and surrounding spaces with a single standard separator"""
    dash_pattern = r"\s*[\u2010\u2011\u2012\u2013\u2014\u2015-]\s*"
    return re.sub(dash_pattern, " - ", title)

def clean_text(s):
    """Strip normal and invisible Unicode spaces from both ends"""
    if not s:
        return ""
    # Replace non-breaking and zero-width spaces with normal space
    s = re.sub(r"[\u00A0\u202F\u200B\u200C\u200D]", " ", s)
    # Collapse multiple spaces to single space
    s = re.sub(r"\s+", " ", s)
    return s.strip()

def split_title(title):
    """Split artist and album robustly, clean invisible spaces"""
    title = html.unescape(title)
    title = normalize_dashes(title)

    # Split only on first dash
    parts = title.split(" - ", 1)
    if len(parts) == 2:
        artist = clean_text(parts[0])
        album_full = clean_text(parts[1])
    else:
        artist = clean_text(title)
        album_full = ""

    # Extract album name, country, year (allow ranges and extra parentheses)
    match = ALBUM_REGEX.search(album_full)
    if match:
        album_name = clean_text(match.group(1))
        country = clean_text(match.group(2))
        year = clean_text(match.group(3))
    else:
        album_name = album_full
        country = ""
        year = ""

    return artist, album_name, country, year

def crawl_new_posts():
    posts_out = []
    after = None

    while len(posts_out) < MAX_POSTS:
        params = {"limit": 100}
        if after:
            params["after"] = after

        url = BASE_URL + "?" + urlencode(params)
        req = Request(url, headers=HEADERS)

        with urlopen(req) as response:
            data = json.loads(response.read().decode("utf-8"))

        children = data["data"]["children"]
        if not children:
            break

        for c in children:
            p = c["data"]
            artist, album, country, year = split_title(p["title"])
            posts_out.append({
                "artist": artist,
                "album": album,
                "country": country,
                "year": year,
                "score": p.get("score", 0),
                "date": utc_to_excel_datetime(p["created_utc"]),
                "created_utc": p["created_utc"],  # for sorting
                "permalink": "https://reddit.com" + p["permalink"]
            })

        after = data["data"]["after"]
        if not after:
            break

        time.sleep(SLEEP_SEC)

    return posts_out[:MAX_POSTS]

def write_csv(posts, filename):
    fieldnames = ["artist", "album", "country", "year", "score", "date", "permalink"]

    with open(filename, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, delimiter=";")
        writer.writeheader()
        for p in posts:
            writer.writerow({
                "artist": p["artist"],
                "album": p["album"],
                "country": p["country"],
                "year": p["year"],
                "score": p["score"],
                "date": p["date"],
                "permalink": p["permalink"]
            })

def first_year_token(year_text: str) -> str:
    """Return first 4-digit year from text, else empty string."""
    if not year_text:
        return ""
    m = re.search(r"\b(\d{4})\b", str(year_text))
    return m.group(1) if m else ""

def write_list(posts, filename):
    """Write a list file that build_playlist.py understands: 'Artist - Album (Year)'.
    Year is optional; include only first 4-digit year if present.
    """
    out_path = Path(filename)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    for p in posts:
        artist = (p.get("artist") or "").strip()
        album = (p.get("album") or "").strip()
        year = first_year_token(p.get("year") or "")
        if not artist or not album:
            continue
        yr = f" ({year})" if year else ""
        lines.append(f"{artist} - {album}{yr}")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + ("\n" if lines else ""))
    print(f"List written: {out_path} ({len(lines)} lines)")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Crawl r/progalbums and write playlist-friendly list")
    parser.add_argument("--list-folder", default="lesser_known_prog_albums", help="Output folder for the list file (default: lesser_known_prog_albums)")
    args = parser.parse_args()

    print("Crawling /new ...")
    posts = crawl_new_posts()
    print(f"Collected {len(posts)} posts")

    # Sort newest → oldest
    posts_sorted = sorted(posts, key=lambda p: p["created_utc"], reverse=True)

    write_csv(posts_sorted, "progalbums_new_sorted.csv")
    print("CSV written: progalbums_new_sorted.csv")

    # Also write a build_playlist-friendly list file in the requested folder
    list_dir = Path(args.list_folder)
    stem = list_dir.name
    list_file = list_dir / f"{stem}.txt"
    write_list(posts_sorted, str(list_file))
