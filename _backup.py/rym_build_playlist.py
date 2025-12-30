import argparse
import csv
import logging
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import build_playlist as bp

logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")


def read_rym_csv(csv_path: Path) -> List[Dict[str, Optional[str]]]:
    rows: List[Dict[str, Optional[str]]] = []
    with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f, delimiter=";")
        for r in reader:
            artist = (r.get("artist") or "").strip()
            album = (r.get("album") or "").strip()
            year = (r.get("year") or "").strip() or None
            order = (r.get("order") or "").strip()
            rows.append({
                "order": order,
                "artist": artist,
                "album": album,
                "year": year,
            })
    # Keep only entries with artist and album
    rows = [r for r in rows if r["artist"] and r["album"]]
    return rows


def artist_variants(artist: str) -> List[str]:
    a = bp.normalize_text(artist)
    variants = {a}
    # Drop bracketed and parenthetical suffixes
    for sep in ["[", "("]:
        if sep in a:
            variants.add(a.split(sep, 1)[0].strip())
    # Split on &/and and keep first primary name
    for conj in [" & ", " and "]:
        if conj in a:
            variants.add(a.split(conj, 1)[0].strip())
    # Remove common suffixes
    for suf in [" band", " group", " orchestra", " open music", " quartet", " trio"]:
        if a.endswith(suf):
            variants.add(a[: -len(suf)].strip())
    # Shorter heuristic: first two words
    parts = a.split()
    if len(parts) > 2:
        variants.add(" ".join(parts[:2]))
    return [v for v in variants if v]


def best_match_with_variants(artist: str, album: str, candidates: List[Dict[str, object]], score_cutoff: int = 80) -> Optional[Tuple[Dict[str, object], int]]:
    # Try full artist first
    primary = bp.best_match(artist, album, candidates, score_cutoff=score_cutoff)
    if primary:
        return primary
    # Try artist variants
    best: Optional[Tuple[Dict[str, object], int]] = None
    best_score = -1
    for va in artist_variants(artist):
        res = bp.best_match(va, album, candidates, score_cutoff=0)
        if res and res[1] > best_score:
            best, best_score = res
    if best and best_score >= score_cutoff:
        return best
    return None


def main():
    parser = argparse.ArgumentParser(description="Build M3U8 playlist from RYM CSV using fuzzy matching")
    parser.add_argument("--csv", default=str(Path("rym_favorites_1968_1975.csv")), help="Input CSV path (order;artist;album;year)")
    parser.add_argument("--out", default=str(Path("rym_favorites_1968_1975.m3u8")), help="Output M3U8 playlist path")
    # Use the same defaults as build_playlist for consistency
    parser.add_argument("--primary", default=bp.DEFAULT_PRIMARY_LIB, help="Primary music library base path")
    parser.add_argument("--secondary", default=bp.DEFAULT_SECONDARY_LIB, help="Secondary music library base path")
    parser.add_argument("--score-cutoff", type=int, default=80, help="Minimum fuzzy score to accept a match (0-100)")
    parser.add_argument("--sort-tracks", action="store_true", help="Sort resulting track paths in the playlist")
    parser.add_argument("--not-found-log", default=str(Path("rym_not_found.txt")), help="Path to write unmatched entries")
    parser.add_argument("--dry-run", action="store_true", help="Do not write playlist; just report matches")
    args = parser.parse_args()

    csv_path = Path(args.csv)
    if not csv_path.exists():
        logging.error("CSV not found: %s", csv_path)
        return

    entries = read_rym_csv(csv_path)
    logging.info("Loaded %d CSV rows", len(entries))

    # Index libraries
    candidates = bp.index_music_library([args.primary, args.secondary])

    all_tracks: List[str] = []
    matched_count = 0
    not_found: List[Dict[str, Optional[str]]] = []

    for e in entries:
        artist = e.get("artist") or ""
        album = e.get("album") or ""
        year = e.get("year")
        bm = best_match_with_variants(artist, album, candidates, score_cutoff=args.score_cutoff)
        if bm:
            cand, score = bm
            matched_count += 1
            logging.info("Match: %s - %s -> %s (score %d)", artist, album, cand["root"], score)
            tracks = bp.collect_album_tracks(cand)
            all_tracks.extend(tracks)
        else:
            logging.warning("No match: %s - %s", artist, album)
            not_found.append({"artist": artist, "album": album, "year": year})

    # De-duplicate tracks while preserving order
    all_tracks = bp.unique_preserve_order(all_tracks)

    if args.sort_tracks:
        all_tracks = sorted(all_tracks)

    # Write not-found log
    if args.not_found_log:
        log_path = Path(args.not_found_log)
        lines = []
        for e in not_found:
            y = f" ({e['year']})" if e.get('year') else ""
            lines.append(f"{e['artist']} - {e['album']}{y}")
        log_path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
        logging.info("Wrote not-found log: %s (%d lines)", log_path, len(lines))

    if args.dry_run:
        logging.info("Dry run: %d matched albums, %d tracks gathered", matched_count, len(all_tracks))
        for t in all_tracks[:20]:
            print(t)
        return

    bp.make_m3u8(all_tracks, args.out)


if __name__ == "__main__":
    main()
