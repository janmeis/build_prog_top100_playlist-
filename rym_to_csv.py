import argparse
import logging
import re
from pathlib import Path
from glob import glob
from typing import List, Optional, Tuple

from bs4 import BeautifulSoup
from unidecode import unidecode

logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")

logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")


def normalize_text(s: str) -> str:
    s = s.strip()
    s = unidecode(s)
    s = re.sub(r"\s+", " ", s)
    return s


def parse_rym_page(html: str) -> List[Tuple[Optional[int], str, str, Optional[int]]]:
    """Parse a RYM favorites list page saved as HTML.

    Expected structure:
    - Table with id="user_list"
    - Within each row (tr):
      - Order number: element with class "list_mobile_number" (e.g., "1")
      - Artist: element with class "list_artist" (text)
      - Album: element with class "list_album" (text)
      - Release date: element with class "rel_date" (not used for CSV)

    Returns list of tuples (order, artist, album).
    """
    soup = BeautifulSoup(html, "html.parser")
    results: List[Tuple[Optional[int], str, str, Optional[int]]] = []

    table = soup.select_one("table#user_list")
    if not table:
        logging.warning("user_list table not found; 0 entries parsed")
        return results

    for tr in table.select("tr"):
        # Skip header rows
        if tr.find(["th"]):
            continue
        # Extract order number
        # Prefer the dedicated number cell, then fallback to mobile number span
        order_el = tr.select_one("td.number") or tr.select_one(".list_mobile_number, [class*=list_mobile_number]")
        artist_el = tr.select_one(".list_artist, [class*=list_artist]")
        album_el = tr.select_one(".list_album, [class*=list_album]")
        date_el = tr.select_one(".rel_date, [class*=rel_date]")

        if not artist_el or not album_el:
            continue

        artist = normalize_text(artist_el.get_text(" "))
        album = normalize_text(album_el.get_text(" "))
        # Remove trailing year in parentheses from album if present
        album = re.sub(r"\s*\(\s*\d{4}\s*\)\s*$", "", album)

        order_num: Optional[int] = None
        if order_el:
            # Extract digits, handle values like "#12" or "12."
            m = re.search(r"(\d+)", order_el.get_text())
            if m:
                try:
                    order_num = int(m.group(1))
                except Exception:
                    order_num = None

        # Extract year from rel_date cell if available
        year_val: Optional[int] = None
        if date_el:
            m = re.search(r"(\d{4})", date_el.get_text())
            if m:
                try:
                    year_val = int(m.group(1))
                except Exception:
                    year_val = None

        if artist and album:
            results.append((order_num, artist, album, year_val))

    # Do not deduplicate within a page; keep all rows and sort by order
    results.sort(key=lambda x: (0 if x[0] is not None else 1, x[0] if x[0] is not None else 0))
    logging.info("Parsed %d entries from page", len(results))
    return results


def write_csv_semicolon(rows: List[Tuple[int, str, str, Optional[int]]], out_path: Path) -> None:
    # UTF-8 with BOM to help Excel on Windows
    header = "order;artist;album;year\n"
    with open(out_path, "w", encoding="utf-8-sig", newline="") as f:
        f.write(header)
        for order, artist, album, year in rows:
            # Escape semicolons minimally by replacing with commas in data
            a = artist.replace(";", ",")
            b = album.replace(";", ",")
            y = str(year) if year else ""
            f.write(f"{order};{a};{b};{y}\n")
    logging.info("Wrote CSV: %s (%d rows)", out_path, len(rows))


def main():
    parser = argparse.ArgumentParser(description="Parse saved RYM list HTML into CSV (order;artist;album)")
    parser.add_argument("--out", default=str(Path("rym_favorites_1968_1975.csv")), help="Output CSV path")
    parser.add_argument(
        "--local-glob",
        default="rym_favorites_1968_1975/rym_favorites_1968_1975_*.html",
        help="Parse local HTML files matching this glob (default: rym_favorites_1968_1975/rym_favorites_1968_1975_*.html)",
    )
    parser.add_argument("--local-files", nargs="+", default=None, help="Parse these local HTML files in order")
    args = parser.parse_args()

    # Collect local files
    file_list: List[Path] = []
    if args.local_glob:
        for p in sorted(glob(args.local_glob)):
            file_list.append(Path(p))
    if args.local_files:
        for p in args.local_files:
            file_list.append(Path(p))

    # De-duplicate and filter existing
    seen_paths = set()
    files: List[Path] = []
    for p in file_list:
        if p.exists() and p.resolve() not in seen_paths:
            seen_paths.add(p.resolve())
            files.append(p)
    if not files:
        logging.error("No local HTML files found to parse.")
        return

    aggregated: List[Tuple[Optional[int], str, str, Optional[int]]] = []
    for fp in files:
        logging.info("Parsing local file: %s", fp)
        html = fp.read_text(encoding="utf-8", errors="ignore")
        page_entries = parse_rym_page(html)
        aggregated.extend(page_entries)

    # Build map by order (primary) to ensure full coverage and correct sequencing
    by_order: dict[int, Tuple[int, str, str, Optional[int]]] = {}
    extras: List[Tuple[int, str, str, Optional[int]]] = []
    for order, artist, album, year in aggregated:
        if order is not None:
            if order not in by_order:
                by_order[order] = (order, artist, album, year)
        else:
            extras.append((0, artist, album, year))

    rows = [by_order[k] for k in sorted(by_order.keys())]
    rows.extend(extras)
    write_csv_semicolon(rows, Path(args.out))


if __name__ == "__main__":
    main()
