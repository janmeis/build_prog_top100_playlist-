import os
import re
import sys
import argparse
import logging
import unicodedata
from pathlib import Path
from typing import List, Dict, Optional, Tuple
from bs4 import BeautifulSoup
from rapidfuzz import fuzz, process
from unidecode import unidecode

logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
DEFAULT_PRIMARY_LIB = r"\\\\synologyds920\\music"
DEFAULT_SECONDARY_LIB = r"\\\\synologyds920\\WdMyCloudEX2\\Rock & Jazz\\"
AUDIO_EXTS = {".flac", ".mp3", ".m4a", ".wav", ".ape", ".ogg"}


def normalize_text(s: str) -> str:
    s = s.strip().lower()
    # remove accents
    s = unidecode(s)
    # normalize common joiners/variants
    s = s.replace("&", " and ")
    s = s.replace("+", " ")
    # remove leading articles
    s = re.sub(r"^(the|a|an)\s+", "", s)
    # remove punctuation and collapse spaces
    s = re.sub(r"[\-_'\"(),.!/:;\[\]]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def normalize_album_for_match(s: str) -> str:
    """Album-specific normalization for matching.
    - Apply generic normalization
    - Remove leading 4-digit year tokens (e.g., "1973 obus" -> "obus")
    """
    s = normalize_text(s)
    s = re.sub(r"^\d{4}\s+", "", s)
    return s


def resolve_self_titled(album: str, artist: str) -> str:
    """Return the artist name if the album token indicates self-titled.
    Handles common shorthand like 's/t' or 's.t.' (case-insensitive).
    """
    token = album.strip().lower()
    if re.fullmatch(r"s\s*/\s*t|s\.?\s*t\.?", token):
        return artist.strip()
    return album


def extract_year(s: str) -> Optional[str]:
    """Extract a 4-digit year from a string, if present."""
    if not s:
        return None
    m = re.search(r"\b(\d{4})\b", str(s))
    return m.group(1) if m else None


"""Web fetching omitted: use --html-file or --list-file"""


def parse_album_list_from_text(text: str) -> List[Dict[str, Optional[str]]]:
    """Parse plain text lines with 'Artist - Album (Year)'."""
    pattern = re.compile(r"^\s*(?:\d+[).]\s*)?(?P<artist>[^\n\-]+?)\s*-\s*(?P<album>[^\n(]+?)(?:\s*\((?P<year>\d{4})\))?\s*$")
    entries: List[Dict[str, Optional[str]]] = []
    for line in text.splitlines():
        m = pattern.match(line)
        if m:
            artist = m.group("artist").strip()
            album = resolve_self_titled(m.group("album").strip(), artist)
            year = m.group("year")
            if artist and album:
                entries.append({"artist": artist, "album": album, "year": year})
    seen = set()
    unique_entries = []
    for e in entries:
        key = (normalize_text(e["artist"]), normalize_text(e["album"]))
        if key not in seen:
            seen.add(key)
            unique_entries.append(e)
    return unique_entries


def parse_album_list_from_html(html: str) -> List[Dict[str, Optional[str]]]:
    """Parse HTML saved from the forum page and extract entries."""
    soup = BeautifulSoup(html, "html.parser")
    text_chunks: List[str] = []
    for sel in [
        ("div", {"id": re.compile(r"^post_body_")}),
        ("td", {"class": re.compile(r"postdiv|msgBody")}),
        ("div", {"class": re.compile(r"post|msgBody")}),
    ]:
        for el in soup.find_all(sel[0], sel[1]):
            text_chunks.append(el.get_text("\n"))
    if not text_chunks:
        text_chunks = [soup.get_text("\n")]
    combined = "\n".join(text_chunks)
    
    # Stricter parsing for forum HTML: require (YYYY) and skip noisy lines
    strict_pattern = re.compile(
        r"^\s*(?:\d+[).]\s*)?(?P<artist>[^\n\-]+?)\s*-\s*(?P<album>[^\n(]+?)\s*\((?P<year>\d{4})\)\s*$"
    )

    def is_noise(line: str) -> bool:
        l = line.strip()
        if not l:
            return True
        noise_markers = [
            "Edited by",
            "http://",
            "https://",
        ]
        if any(m.lower() in l.lower() for m in noise_markers):
            return True
        if l.startswith("^"):
            return True
        # must contain delimiter ' - '
        if " - " not in l:
            return True
        return False

    entries: List[Dict[str, Optional[str]]] = []
    for line in combined.splitlines():
        if is_noise(line):
            continue
        m = strict_pattern.match(line)
        if m:
            artist = m.group("artist").strip()
            album = resolve_self_titled(m.group("album").strip(), artist)
            year = m.group("year").strip()
            if artist and album:
                entries.append({"artist": artist, "album": album, "year": year})

    # De-dupe by normalized artist/album
    seen = set()
    unique_entries: List[Dict[str, Optional[str]]] = []
    for e in entries:
        key = (normalize_text(e["artist"]), normalize_text(e["album"]))
        if key not in seen:
            seen.add(key)
            unique_entries.append(e)
    return unique_entries


def index_music_library(base_paths: List[str]) -> List[Dict[str, object]]:
    """Walk library paths and return candidate album directories and files."""
    candidates: List[Dict[str, object]] = []
    for base in base_paths:
        # Sanitize base path to handle stray quotes/whitespace from shell quoting
        if not base:
            continue
        sanitized = base.strip().strip('"').strip("'")
        base_p = Path(sanitized)
        if not base_p.exists():
            logging.warning("Library path not found: %s", sanitized)
            continue
        logging.info("Indexing library: %s", sanitized)
        for root, dirs, files in os.walk(sanitized, topdown=True):
            root_p = Path(root)
            # prune #recycle folders from traversal
            dirs[:] = [d for d in dirs if "#recycle" not in d.lower()]
            if any("#recycle" in part.lower() for part in root_p.parts):
                continue
            # derive artist/album from last two directories if possible
            parts = root_p.parts
            artist = None
            album = None
            if len(parts) >= 2:
                album = parts[-1]
                artist = parts[-2]
            # collect audio files and cues
            audio_files = [str(root_p / f) for f in files if Path(f).suffix.lower() in AUDIO_EXTS]
            cue_files = [str(root_p / f) for f in files if Path(f).suffix.lower() == ".cue"]
            if audio_files or cue_files:
                candidates.append({
                    "root": str(root_p),
                    "artist": artist or "",
                    "album": album or "",
                    "audio_files": audio_files,
                    "cue_files": cue_files,
                })
    logging.info("Indexed %d candidate album folders", len(candidates))
    return candidates


def simple_cue_tracks(cue_path: str) -> List[str]:
    """Return audio file(s) referenced in the CUE. Does not split single-file cues."""
    tracks: List[str] = []
    try:
        with open(cue_path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
        # CUE FILE "filename" WAVE/MP3/FLAC
        for m in re.finditer(r"FILE\s+\"([^\"]+)\"\s+\w+", content, re.IGNORECASE):
            referenced = m.group(1)
            # resolve relative to cue
            candidate = Path(cue_path).parent / referenced
            if candidate.exists():
                tracks.append(str(candidate))
    except Exception as e:
        logging.warning("Failed to parse cue %s: %s", cue_path, e)
    return tracks


def collect_album_tracks(candidate: Dict[str, object]) -> List[str]:
    audio_files: List[str] = candidate.get("audio_files", [])  # type: ignore
    cue_files: List[str] = candidate.get("cue_files", [])  # type: ignore

    # If one or more .cue files exist, include ONLY the .cue files in the playlist
    # and omit the underlying audio files (e.g., .flac, .ape). Many players will
    # use the .cue to reference the tracks.
    if cue_files:
        return sorted(unique_preserve_order(cue_files))
    # Otherwise, include audio files directly
    return sorted(audio_files)


def unique_preserve_order(seq: List[str]) -> List[str]:
    seen = set()
    out = []
    for x in seq:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


def dedupe_entries(entries: List[Dict[str, Optional[str]]], cutoff: int = 90) -> List[Dict[str, Optional[str]]]:
    """Merge near-duplicate albums per artist using fuzzy matching.
    - Group by normalized artist
    - Within group, keep first unique album; skip later ones if album similarity >= cutoff
    - If a duplicate has a year and the kept one doesn't, fill the year
    """
    grouped: Dict[str, List[Dict[str, Optional[str]]]] = {}
    for e in entries:
        akey = normalize_text(e.get("artist", ""))
        grouped.setdefault(akey, []).append(e)

    result: List[Dict[str, Optional[str]]] = []
    for akey, items in grouped.items():
        kept: List[Dict[str, Optional[str]]] = []
        for e in items:
            e_album_norm = normalize_text(e.get("album", ""))
            duplicate_idx: Optional[int] = None
            for idx, k in enumerate(kept):
                k_album_norm = normalize_text(k.get("album", ""))
                score = fuzz.token_set_ratio(e_album_norm, k_album_norm)
                if score >= cutoff:
                    duplicate_idx = idx
                    break
            if duplicate_idx is None:
                kept.append(e)
            else:
                # Merge year info if missing on kept entry
                k = kept[duplicate_idx]
                if not k.get("year") and e.get("year"):
                    k["year"] = e.get("year")
        result.extend(kept)
    # Preserve overall order approximately by sorting by artist/album normalized
    # but rely on caller to sort if desired.
    return result


def best_match(target_artist: str, target_album: str, candidates: List[Dict[str, object]], score_cutoff: int = 80, target_year: Optional[str] = None) -> Optional[Tuple[Dict[str, object], int]]:
    t_artist = normalize_text(target_artist)
    t_album = normalize_album_for_match(target_album)
    t_album_ns = t_album.replace(" ", "")
    orig_t_artist = target_artist
    has_multi_target = ("+" in orig_t_artist) or ("&" in orig_t_artist) or (" and " in orig_t_artist.lower())

    if not candidates:
        return None

    best: Optional[Dict[str, object]] = None
    best_score: int = -1

    for c in candidates:
        c_artist = normalize_text(str(c.get("artist", "")))  # type: ignore
        c_album = normalize_album_for_match(str(c.get("album", "")))  # type: ignore
        c_album_ns = c_album.replace(" ", "")

        # Component scores
        artist_set_score = fuzz.token_set_ratio(t_artist, c_artist)
        # Order-insensitive artist comparison to allow swapped names (e.g., "A & B" vs "B & A")
        artist_sort_score = fuzz.token_sort_ratio(t_artist, c_artist)
        label_score = fuzz.token_set_ratio(f"{t_artist} {t_album}", f"{c_artist} {c_album}")
        album_token_score = fuzz.token_set_ratio(t_album, c_album)
        album_ns_score = fuzz.ratio(t_album_ns, c_album_ns)

        # Require strong artist and album match to avoid false positives
        album_score = max(album_token_score, album_ns_score)

        # Fallback: accept order-swapped or band-variant artist names when album match is extremely strong
        # Example: "Barbara Thompson's Paraphernalia" -> "Barbara Thompson", "Julian Priester Pepo Mtoto" -> "Julian Priester",
        #           "Darryl Way's Wolf" -> "Darryl Way". Avoid false positive like "Egg" vs "Flied Egg".
        allowed_extras = {
            # Common add-ons or subprojects
            "paraphernalia", "pepo", "mtoto", "wolf",
            # Generic group descriptors
            "group", "band", "ensemble", "combination", "combo",
            "collective", "project", "orchestra", "quartet", "quintet",
            "sextet", "trio", "duo", "company",
            # Collaboration/linking terms
            "and", "with", "feat", "featuring",
            # Specific known phrase components
            "whole", "world",
        }
        t_tokens = set(t_artist.split())
        c_tokens = set(c_artist.split())
        orig_c_artist = str(c.get("artist", ""))  # type: ignore
        has_collab_marker = ("+" in orig_c_artist) or ("&" in orig_c_artist) or (" and " in orig_c_artist.lower())
        extras = {tok for tok in (t_tokens - c_tokens) if tok != "s"}
        artist_alias_ok = (
            bool(c_tokens)
            and c_tokens.issubset(t_tokens)
            and len(c_tokens) >= 2  # avoid one-word cases like "egg"
            and len(extras) > 0
            and all(tok in allowed_extras for tok in extras)
            and album_score >= max(score_cutoff, 95)
        )

        # Collaboration allowance: candidate artist is a superset of target (e.g., "Mirror + Lethe") with strong album match
        # Prevent false positives by requiring high artist_set_score and a small number of extra tokens or explicit collab marker
        collab_ok = (
            t_tokens.issubset(c_tokens)
            and album_score >= max(score_cutoff, 95)
            and artist_set_score >= 85
            and (has_collab_marker or len(c_tokens) <= len(t_tokens) + 2)
        )

        # Duo subset allowance: target lists multiple artists (e.g., "A & B"), candidate is a single-artist subset (e.g., "A"), album is very strong
        duo_subset_ok = (
            has_multi_target
            and c_tokens.issubset(t_tokens)
            and len(c_tokens) >= 2
            and album_score >= max(score_cutoff, 95)
            and artist_set_score >= 85
        )

        # Guard against subset artist matches while allowing swapped-orders, permitted band variants, collaborations, and duo subsets
        artist_ok = (artist_set_score >= 85 and artist_sort_score >= 90) or artist_alias_ok or collab_ok or duo_subset_ok
        if not artist_ok or album_score < score_cutoff:
            continue

        # Year bonus/penalty when a year is present in both
        cand_year = extract_year(c.get("album", "")) or extract_year(c.get("root", ""))  # type: ignore
        year_bonus = 0
        if target_year and cand_year:
            if str(cand_year) == str(target_year):
                year_bonus = 10
            else:
                year_bonus = -5

        score = max(label_score, album_score) + year_bonus
        score = max(0, min(100, score))

        if score > best_score:
            best_score = score
            best = c

    if best is not None and best_score >= score_cutoff:
        return best, best_score
    return None


def make_m3u8(tracks: List[str], out_path: str) -> None:
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for t in tracks:
            # Minimal playlist (no #EXTINF); many players accept this fine
            f.write(f"{t}\n")
    logging.info("Wrote playlist: %s (%d lines)", out_path, len(tracks))


def main():
    parser = argparse.ArgumentParser(description="Build M3U8 playlist for Top 100 Obscure British Prog Albums")
    # Web fetching removed; supply either --html-file or --list-file
    parser.add_argument("--list-file", default=None, help="Optional local text file containing 'Artist - Album (Year)' per line")
    parser.add_argument("--html-file", default=None, help="Optional local HTML file saved from the forum page to parse entries")
    parser.add_argument("--primary", required=True, default=None, help="Primary music library base path (UNC paths allowed, required)")
    parser.add_argument("--secondary", default=DEFAULT_SECONDARY_LIB, help="Secondary music library base path")
    parser.add_argument("--out", default=None, help="Output playlist path")
    parser.add_argument("--score-cutoff", type=int, default=85, help="Minimum fuzzy score to accept a match (0-100)")
    parser.add_argument("--dry-run", action="store_true", help="Do not write playlist; just report matches")
    parser.add_argument("--parse-only", action="store_true", help="Only parse the list and print entries")
    parser.add_argument("--sort-entries", action="store_true", help="Sort parsed entries by artist then album")
    parser.add_argument("--sort-tracks", action="store_true", help="Sort resulting track paths in the playlist")
    parser.add_argument("--write-list", default=None, help="Write parsed unique entries to a text file (one per line)")
    parser.add_argument("--not-found-log", default=None, help="Optional: path for not-found log; defaults to derived from --out")
    parser.add_argument("--log", default=None, help="Optional: path for main log (console output); defaults to derived from --out")
    args = parser.parse_args()

    # Validate primary path is specified and non-empty
    if not args.primary or not str(args.primary).strip():
        logging.error("Primary library path is required and cannot be empty. Use --primary <path>.")
        sys.exit(2)
    # Normalize quoting/whitespace from shell
    args.primary = str(args.primary).strip().strip('"').strip("'")

    # Ensure output path early so we can set up logging and cleanup
    if not args.out:
        args.out = "playlist.m3u8"
    out_base = Path(args.out)
    out_dir = out_base.parent if out_base.parent.as_posix() not in ("", ".") else Path(".")
    out_stem = out_base.stem
    exceptions_path = out_dir / f"{out_stem}-exceptions.txt"

    # Derive default log file paths from --out
    default_not_found_path = out_dir / f"{out_stem}-not-found-log.txt"
    default_log_path = out_dir / f"{out_stem}-log.txt"
    default_duplicates_path = out_dir / f"{out_stem}-duplicates.txt"

    # First thing: delete old not-found and log files; start a fresh log
    try:
        if default_not_found_path.exists():
            default_not_found_path.unlink()
        # Also remove a custom not-found path if provided
        if args.not_found_log:
            try:
                custom_nf = Path(str(args.not_found_log)).expanduser()
                if custom_nf.exists():
                    custom_nf.unlink()
            except Exception:
                pass
        if default_log_path.exists():
            default_log_path.unlink()
        # Also remove a custom main log path if provided
        if args.log:
            try:
                custom_log = Path(str(args.log)).expanduser()
                if custom_log.exists():
                    custom_log.unlink()
            except Exception:
                pass
        # Also remove legacy found-log filename if present
        legacy_found_log = out_dir / f"{out_stem}-found-log.txt"
        if legacy_found_log.exists():
            legacy_found_log.unlink()
    except Exception as _cleanup_err:
        # Non-fatal; continue
        logging.debug("Cleanup warning: %s", _cleanup_err)

    # Capture console logs to log file as the only log
    root_logger = logging.getLogger()
    log_path = Path(str(args.log)).expanduser() if args.log else default_log_path
    file_handler = logging.FileHandler(str(log_path), mode="w", encoding="utf-8")
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
    root_logger.addHandler(file_handler)

    entries: List[Dict[str, Optional[str]]] = []
    if args.html_file:
        p = Path(args.html_file)
        if not p.exists():
            logging.error("HTML file not found: %s", args.html_file)
            sys.exit(1)
        logging.info("Parsing entries from local HTML: %s", args.html_file)
        entries = parse_album_list_from_html(p.read_text(encoding="utf-8", errors="ignore"))
    elif args.list_file:
        p = Path(args.list_file)
        if not p.exists():
            logging.error("List file not found: %s", args.list_file)
            sys.exit(1)
        logging.info("Parsing entries from local file: %s", args.list_file)
        entries = parse_album_list_from_text(p.read_text(encoding="utf-8", errors="ignore"))
    else:
        logging.error("No input provided. Use --html-file or --list-file.")
        sys.exit(1)
    # Fuzzy de-duplicate similar entries per artist (e.g., Strangewings vs Strange Wings)
    entries = dedupe_entries(entries, cutoff=90)

    # Optional sort of entries
    if args.sort_entries:
        entries = sorted(
            entries,
            key=lambda e: (normalize_text(e["artist"]), normalize_text(e["album"]))
        )

    if args.write_list:
        outp = Path(args.write_list)
        lines = []
        for e in entries:
            year = f" ({e['year']})" if e.get('year') else ""
            lines.append(f"{e['artist']} - {e['album']}{year}")
        outp.write_text("\n".join(lines) + "\n", encoding="utf-8")
        logging.info("Wrote entries list: %s (%d lines)", outp, len(lines))

    if args.parse_only:
        for e in entries:
            print(f"{e['artist']} - {e['album']} ({e.get('year') or ''})")
        print(f"Total parsed entries: {len(entries)}")
        return

    # Derive base paths and filenames (used for exceptions) already set above

    all_tracks: List[str] = []
    matched_count = 0
    not_found: List[Dict[str, Optional[str]]] = []

    # Optional: load exceptions mapping to force-resolve entries to specific folders
    exceptions_map: Dict[Tuple[str, str], str] = {}
    if exceptions_path.exists():
        try:
            raw = exceptions_path.read_text(encoding="utf-8", errors="ignore")
            for line in raw.splitlines():
                line = line.strip()
                if not line:
                    continue
                # Expect format: 'Artist - Album (Year)\t<folder_path>'
                parts = line.split("\t")
                if len(parts) != 2:
                    continue
                left, folder = parts[0].strip(), parts[1].strip()
                m = re.match(r"^\s*(?P<artist>[^\n\-]+?)\s*-\s*(?P<album>[^\n(]+?)(?:\s*\((?P<year>\d{4})\))?\s*$", left)
                if not m:
                    continue
                artist = m.group("artist").strip()
                album = resolve_self_titled(m.group("album").strip(), artist)
                key = (normalize_text(artist), normalize_text(album))
                exceptions_map[key] = folder
            logging.info("Loaded exceptions: %d entries from %s", len(exceptions_map), exceptions_path)
        except Exception as e:
            logging.warning("Failed to read exceptions file %s: %s", exceptions_path, e)

    # 1) Index and match against PRIMARY first
    primary_candidates: List[Dict[str, object]] = []
    if args.primary:
        primary_candidates = index_music_library([args.primary])

    remaining: List[Dict[str, Optional[str]]] = []
    for e in entries:
        # First: honor exceptions mapping if present
        key = (normalize_text(e.get("artist", "")), normalize_text(e.get("album", "")))
        if key in exceptions_map:
            folder = exceptions_map[key]
            folder_p = Path(folder)
            if folder_p.exists():
                matched_count += 1
                # Collect tracks from the specified folder (prefer .cue files)
                audio_files: List[str] = []
                cue_files: List[str] = []
                for root, dirs, files in os.walk(folder):
                    root_p = Path(root)
                    # prune #recycle folders from traversal
                    dirs[:] = [d for d in dirs if "#recycle" not in d.lower()]
                    audio_files.extend([str(root_p / f) for f in files if Path(f).suffix.lower() in AUDIO_EXTS])
                    cue_files.extend([str(root_p / f) for f in files if Path(f).suffix.lower() == ".cue"])
                tracks = sorted(unique_preserve_order(cue_files)) if cue_files else sorted(unique_preserve_order(audio_files))
                all_tracks.extend(tracks)
                # Logging already captures match details; no separate found-lines tracking
                logging.info("Match (exceptions): %s - %s -> %s (%d files)", e["artist"], e["album"], folder, len(tracks))
                continue
            else:
                logging.warning("Exceptions path not found: %s (for %s - %s)", folder, e.get("artist"), e.get("album"))

        # In primary, ignore year differences: use name-based matching only
        bm = best_match(e["artist"], e["album"], primary_candidates, score_cutoff=args.score_cutoff, target_year=None)
        if bm:
            cand, score = bm
            matched_count += 1
            logging.info("Match (primary): %s - %s -> %s (score %d)", e["artist"], e["album"], cand["root"], score)
            tracks = collect_album_tracks(cand)
            all_tracks.extend(tracks)
            # Logging already captures match details; no separate found-lines tracking
        else:
            remaining.append(e)

    # 2) For any remaining, index and match against SECONDARY
    secondary_candidates: List[Dict[str, object]] = []
    if args.secondary and remaining:
        secondary_candidates = index_music_library([args.secondary])

    for e in remaining:
        bm = best_match(e["artist"], e["album"], secondary_candidates, score_cutoff=args.score_cutoff, target_year=e.get("year"))
        if bm:
            cand, score = bm
            matched_count += 1
            logging.info("Match (secondary): %s - %s -> %s (score %d)", e["artist"], e["album"], cand["root"], score)
            tracks = collect_album_tracks(cand)
            all_tracks.extend(tracks)
            # Logging already captures match details; no separate found-lines tracking
        else:
            logging.warning("No match: %s - %s", e["artist"], e["album"])
            not_found.append(e)

    # De-duplicate tracks while preserving order
    all_tracks = unique_preserve_order(all_tracks)

    # Optional sort of tracks
    if args.sort_tracks:
        all_tracks = sorted(all_tracks)

    # Write a dedicated not-found log if any entries were not matched
    not_found_path = Path(str(args.not_found_log)).expanduser() if args.not_found_log else default_not_found_path
    if not_found:
        try:
            lines = []
            for e in not_found:
                year = f" ({e['year']})" if e.get('year') else ""
                lines.append(f"{e['artist']} - {e['album']}{year}")
            not_found_path.parent.mkdir(parents=True, exist_ok=True)
            not_found_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            logging.info("Wrote not-found log: %s (%d lines)", not_found_path, len(lines))
        except Exception as e:
            logging.warning("Failed to write not-found log %s: %s", not_found_path, e)

    if args.dry_run:
        logging.info("Dry run: %d matched albums, %d tracks gathered", matched_count, len(all_tracks))
        for t in all_tracks[:20]:
            print(t)
        return

    make_m3u8(all_tracks, args.out)


if __name__ == "__main__":
    main()
