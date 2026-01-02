import os
import json
import re
import sys
import argparse
import logging
from pathlib import Path
from typing import List, Dict, Optional, Tuple
from bs4 import BeautifulSoup
from rapidfuzz import fuzz
from unidecode import unidecode

logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
DEFAULT_SECONDARY_LIB = r"\\\\synologyds920\\WdMyCloudEX2\\Rock & Jazz\\"
AUDIO_EXTS = {".flac", ".mp3", ".m4a", ".wav", ".ape", ".ogg"}


def normalize_text(s: str) -> str:
    s = s.strip().lower()
    # remove accents
    s = unidecode(s)
    # normalize common joiners/variants
    s = s.replace("&", " and ")
    s = s.replace("+", " ")
    s = s.replace("/", " and ")
    # special case: remove dots without spacing (e.g., T.R.A.M. -> tram)
    s = s.replace(".", "")
    # remove leading articles (EN, FR, ES, DE) and French elision l'
    s = re.sub(r"^(?:the|a|an|le|la|les|el|los|las|der|die|das)\s+", "", s)
    s = re.sub(r"^l\s*'", "", s)
    # remove punctuation and collapse spaces
    s = re.sub(r"[\-_'\"(),!/:;\[\]]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def normalize_album_for_match(s: str, *, for_candidate: bool = False) -> str:
    """Album-specific normalization for matching.
    - Apply generic normalization
    - For candidate folders, remove one or more leading year tokens (e.g., "[1970 (2021)] Title" -> "title").
      After normalization, brackets and parentheses are removed, so consecutive years appear as tokens.
    - For target titles, remove a single leading year token only (conservative).
    """
    s_norm = normalize_text(s)
    if for_candidate:
        # Strip one or more leading 4-digit year tokens that often prefix folder names
        s_norm = re.sub(r"^(?:\d{4}\s*){1,3}", "", s_norm)
    else:
        s_norm = re.sub(r"^\d{4}\s+", "", s_norm)
    # After stripping year tokens, ensure leading articles are harmonized for album comparison
    # This avoids mismatches like target "Les Porches" -> "porches" while candidate stays "les porches".
    s_norm = re.sub(r"^(?:the|a|an|le|la|les|el|los|las|der|die|das)\s+", "", s_norm)
    s_norm = re.sub(r"^l\s*'", "", s_norm)
    return s_norm.strip()


def normalize_artist(s: str) -> str:
    """Artist-specific normalization.
    - Remove trailing parenthetical or bracket qualifiers like "(US)", "(UK)", "(IRL)" or "[JP]".
      This treats folder names such as "Asia (US)" the same as "Asia".
    - Apply generic text normalization after stripping qualifiers.
    """
    if not s:
        return ""
    # Strip a single trailing (...) or [...] segment
    base = re.sub(r"\s*[\(\[][^^)\]]+[\)\]]\s*$", "", str(s)).strip()
    return normalize_text(base)


def resolve_self_titled(album: str, artist: str) -> str:
    """Return the artist name if the album token indicates self-titled.
    Handles common shorthand like 's/t' or 's.t.' (case-insensitive).
    """
    token = album.strip().lower()
    if re.fullmatch(r"s\s*/\s*t|s\.?\s*t\.?", token):
        return artist.strip()
    return album


def extract_years(s: str) -> List[int]:
    """Extract all 4-digit years from a string, preferring bracketed segments.
    Example: "[1973 (2004)] Title" -> [1973, 2004].
    Returns unique years in the order first seen.
    """
    out: List[int] = []
    if not s:
        return out
    text = str(s)
    # 1) Years inside any [...] segments (collect in order)
    for m in re.finditer(r"\[(.*?)\]", text):
        seg = m.group(1)
        for y in re.findall(r"(?<!\d)(19\d{2}|20\d{2})(?!\d)", seg):
            val = int(y)
            if val not in out:
                out.append(val)
    # 2) Fallback: any other standalone years in the string
    for y in re.findall(r"(?<!\d)(19\d{2}|20\d{2})(?!\d)", text):
        val = int(y)
        if val not in out:
            out.append(val)
    return out


"""Web fetching omitted: use --html-file or --list-file"""


def parse_album_list_from_text(text: str) -> List[Dict[str, Optional[str]]]:
    """Parse plain text lines with 'Artist - Album (Year)'.
    Uses the last ' - ' as the artist/album separator to allow hyphens inside artist names
    (e.g., 'Jean Cohen - Solal - Captain Tarthopom (1973)')."""
    entries: List[Dict[str, Optional[str]]] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        # Support full-line comments starting with '#'
        if line.startswith("#"):
            continue
        # Drop leading numbering like '1) ' or '12. '
        line = re.sub(r"^(?:\d+[).]\s*)", "", line)
        # Extract optional trailing year '(YYYY)'
        year_match = re.search(r"\((\d{4})\)\s*$", line)
        year = year_match.group(1) if year_match else None
        if year_match:
            line = line[:year_match.start()].rstrip()
        # Split on the LAST occurrence of ' - '
        if " - " not in line:
            continue
        artist_part, album_part = line.rsplit(" - ", 1)
        artist = artist_part.strip()
        album = resolve_self_titled(album_part.strip(), artist)
        if artist and album:
            entries.append({"artist": artist, "album": album, "year": year})
    seen = set()
    unique_entries = []
    for e in entries:
        key = (normalize_artist(e["artist"]), normalize_text(e["album"]))
        if key not in seen:
            seen.add(key)
            unique_entries.append(e)
    return unique_entries


def parse_album_list_from_html(html: str) -> List[Dict[str, Optional[str]]]:
    """Parse HTML saved from the forum page and extract entries.
    Uses the last ' - ' as the artist/album separator and requires a trailing '(YYYY)'."""
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

    def is_noise(line: str) -> bool:
        l = line.strip()
        if not l:
            return True
        if len(l) < 6:
            return True
        # Skip quote headers and reply metadata
        if re.match(r"^\s*(On\s+\w+\s+\d{1,2},\s+\d{4}|Quote|Originally\s+posted|http|https|www\.)", l, re.IGNORECASE):
            return True
        return False

    entries: List[Dict[str, Optional[str]]] = []
    for raw in combined.splitlines():
        if is_noise(raw):
            continue
        line = raw.strip()
        # Require trailing year '(YYYY)'
        year_match = re.search(r"\((\d{4})\)\s*$", line)
        if not year_match:
            continue
        year = year_match.group(1)
        line = line[:year_match.start()].rstrip()
        if " - " not in line:
            continue
        artist_part, album_part = line.rsplit(" - ", 1)
        artist = artist_part.strip()
        album = resolve_self_titled(album_part.strip(), artist)
        entries.append({"artist": artist, "album": album, "year": year})
    seen = set()
    unique_entries = []
    for e in entries:
        key = (normalize_artist(e["artist"]), normalize_text(e["album"]))
        if key not in seen:
            seen.add(key)
            unique_entries.append(e)
    return unique_entries


def index_music_library(base_paths: List[str]) -> List[Dict[str, object]]:
    """Walk library paths and return candidate album directories and files."""
    candidates: List[Dict[str, object]] = []
    disc_folder_re = re.compile(
        r"^(?:cd|disc|disk|lp|side|bonus|extra|extras|vinyl|cassette|tape)(?:\s*[\-_]?[a-z0-9]+)?$",
        re.IGNORECASE,
    )
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
            # derive artist/album; if leaf is a disc subfolder (e.g., CD1, Disc 1, LP), step up one level
            parts = root_p.parts
            artist = None
            album = None
            if len(parts) >= 2:
                candidate_album = parts[-1]
                candidate_artist = parts[-2]
                if disc_folder_re.match(candidate_album.strip()):
                    if len(parts) >= 3:
                        album = parts[-2]
                        artist = parts[-3]
                    else:
                        album = candidate_album
                        artist = candidate_artist
                else:
                    album = candidate_album
                    artist = candidate_artist
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


def load_cached_candidates(tmp_path: Path) -> Optional[List[Dict[str, object]]]:
    """Load cached candidates from a JSON file if it exists and is valid."""
    try:
        if tmp_path.exists():
            data = json.loads(tmp_path.read_text(encoding="utf-8", errors="ignore"))
            if isinstance(data, list):
                return data  # each item expected to be a dict with string fields
    except Exception as e:
        logging.warning("Failed to load cache %s: %s", tmp_path, e)
    return None


def save_candidates_cache(candidates: List[Dict[str, object]], tmp_path: Path) -> None:
    """Persist candidates to a JSON cache file."""
    try:
        payload = []
        for c in candidates:
            payload.append({
                "root": str(c.get("root", "")),
                "artist": str(c.get("artist", "")),
                "album": str(c.get("album", "")),
                "audio_files": list(c.get("audio_files", [])),
                "cue_files": list(c.get("cue_files", [])),
            })
        tmp_path.write_text(json.dumps(payload), encoding="utf-8")
    except Exception as e:
        logging.warning("Failed to write cache %s: %s", tmp_path, e)


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
        akey = normalize_artist(e.get("artist", ""))
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
    t_artist = normalize_artist(target_artist)
    t_album = normalize_album_for_match(target_album)
    t_album_ns = t_album.replace(" ", "")
    # Detect self-titled target (album name equals artist name after normalization)
    t_is_self_titled = (t_album == t_artist)
    orig_t_artist = target_artist
    has_multi_target = ("+" in orig_t_artist) or ("&" in orig_t_artist) or ("/" in orig_t_artist) or (" and " in orig_t_artist.lower())

    if not candidates:
        return None

    # Collect viable hits, then select using artist-first and year closeness rules
    hits: List[Tuple[Dict[str, object], int, Optional[int]]] = []  # (candidate, score, year_diff)

    for c in candidates:
        c_artist = normalize_artist(str(c.get("artist", "")))  # type: ignore
        # Use more aggressive cleanup for candidate folders to drop stacked leading years
        c_album = normalize_album_for_match(str(c.get("album", "")), for_candidate=True)  # type: ignore
        c_album_ns = c_album.replace(" ", "")

        # Component scores
        artist_set_score = fuzz.token_set_ratio(t_artist, c_artist)
        # Order-insensitive artist comparison to allow swapped names (e.g., "A & B" vs "B & A")
        artist_sort_score = fuzz.token_sort_ratio(t_artist, c_artist)
        label_score = fuzz.token_set_ratio(f"{t_artist} {t_album}", f"{c_artist} {c_album}")
        album_token_score = fuzz.token_set_ratio(t_album, c_album)
        album_ns_score = fuzz.ratio(t_album_ns, c_album_ns)

        # Stricter album gating: avoid subset-based false positives (e.g., "Color" vs "Color Humano", "Congreso" vs "El Congreso")
        # Require BOTH token_set and no-space similarity to be strong
        strong_album_ok = (album_token_score >= score_cutoff and album_ns_score >= max(85, score_cutoff - 5))
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
        has_collab_marker = ("+" in orig_c_artist) or ("&" in orig_c_artist) or ("/" in orig_c_artist) or (" and " in orig_c_artist.lower())
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
            and len(t_tokens) >= 2  # do not allow single-word targets to match supersets (e.g., "alice" -> "alice cooper")
        )

        # Duo subset allowance: target lists multiple artists (e.g., "A & B"), candidate is a single-artist subset (e.g., "A"), album is very strong
        duo_subset_ok = (
            has_multi_target
            and c_tokens.issubset(t_tokens)
            and len(c_tokens) >= 2
            and album_score >= max(score_cutoff, 95)
            and artist_set_score >= 85
        )

        # Additional guard: if target artist is a single token and candidate artist is a superset,
        # only allow when the candidate album is strictly self-titled as well (album == artist).
        single_token_superset = (len(t_tokens) == 1 and not c_tokens == t_tokens and t_tokens.issubset(c_tokens))

        # Additional album guard for self-titled targets: candidate album must not introduce non-generic extra tokens
        # Example: target "Color" should not match candidate album "Color Humano".
        allowed_album_extras = {"deluxe", "remaster", "remastered", "edition", "expanded", "mono", "stereo", "complete", "collection", "anthology"}
        t_album_tokens = set(t_album.split())
        c_album_tokens = set(c_album.split())
        album_extras = {tok for tok in (c_album_tokens - t_album_tokens)}

        # Guard against subset artist matches while allowing swapped-orders, permitted band variants, collaborations, and duo subsets
        artist_ok = (artist_set_score >= 85 and artist_sort_score >= 90) or artist_alias_ok or collab_ok or duo_subset_ok
        if artist_ok and single_token_superset:
            # Require strict self-titled on candidate to accept single-word superset matches
            if not (t_is_self_titled and c_album == c_artist and album_ns_score >= 99):
                artist_ok = False
        # Album must pass strong gating; for self-titled, reject non-generic extras
        def _extras_ok(tokens):
            for tok in tokens:
                # Allow numeric tokens (e.g., reissue years like 2021)
                if isinstance(tok, str) and tok.isdigit():
                    continue
                if tok in allowed_album_extras:
                    continue
                return False
            return True
        if t_is_self_titled and album_extras and not _extras_ok(album_extras):
            strong_album_ok = False

        if not artist_ok or not strong_album_ok:
            continue

        # Year handling: prefer exact match, then ±1 if multiple candidates exist.
        # Do not penalize other years to keep reissues acceptable when unique.
        # Consider original and reissue years if present (e.g., "[1973 (2004)] Title")
        cand_years: List[int] = []
        try:
            cand_years.extend(extract_years(c.get("album", "")))  # type: ignore
        except Exception:
            pass
        try:
            # Include years from the full path as well
            cand_years.extend([y for y in extract_years(c.get("root", "")) if y not in cand_years])  # type: ignore
        except Exception:
            pass
        year_diff: Optional[int] = None
        if target_year and cand_years:
            try:
                ty = int(str(target_year))
                year_diff = min(abs(y - ty) for y in cand_years)
            except Exception:
                year_diff = None

        # Base score from strongest text similarity
        score = max(label_score, album_score)
        # Positive bonuses only for close year matches
        if year_diff is not None:
            if year_diff == 0:
                score = min(100, score + 10)
            elif year_diff == 1:
                score = min(100, score + 5)

        score = max(0, min(100, score))
        hits.append((c, score, year_diff))

    if not hits:
        # Fallback: unique prefix compound album case
        # If a candidate album begins with the exact target album tokens and is followed by 'and',
        # and artist matches strongly, accept it only when it's unique across candidates.
        prefix_candidates: List[Tuple[Dict[str, object], int]] = []
        t_album_tokens_list = t_album.split()
        for c in candidates:
            c_artist = normalize_text(str(c.get("artist", "")))  # type: ignore
            c_album = normalize_album_for_match(str(c.get("album", "")), for_candidate=True)  # type: ignore
            c_tokens = c_album.split()
            # Require c_album to start with the full target album tokens and next token be 'and'
            if len(c_tokens) > len(t_album_tokens_list) and c_tokens[:len(t_album_tokens_list)] == t_album_tokens_list and c_tokens[len(t_album_tokens_list)] == "and":
                artist_set_score = fuzz.token_set_ratio(t_artist, c_artist)
                artist_sort_score = fuzz.token_sort_ratio(t_artist, c_artist)
                if artist_set_score >= 90 and artist_sort_score >= 90:
                    prefix_candidates.append((c, max(90, score_cutoff)))
        if len(prefix_candidates) == 1:
            return prefix_candidates[0][0], prefix_candidates[0][1]
        # Fallback 2: unique compound segment match (X and Y) where one segment equals the target album
        segment_candidates: List[Tuple[Dict[str, object], int]] = []
        for c in candidates:
            c_artist = normalize_text(str(c.get("artist", "")))  # type: ignore
            c_album_raw = str(c.get("album", ""))  # type: ignore
            c_album = normalize_album_for_match(c_album_raw, for_candidate=True)
            c_tokens = c_album.split()
            # Find an 'and' token to split compound titles
            if "and" not in c_tokens:
                continue
            and_idx = c_tokens.index("and")
            left = c_tokens[:and_idx]
            right = c_tokens[and_idx + 1:]
            # Allow trailing or leading year tokens around the segment, and accept prefix match
            def strip_years(tokens: List[str]) -> List[str]:
                return [tok for tok in tokens if not tok.isdigit()]
            def starts_with_target(tokens: List[str]) -> bool:
                return (len(tokens) >= len(t_album_tokens_list) and tokens[:len(t_album_tokens_list)] == t_album_tokens_list)
            matches_left = (strip_years(left) == t_album_tokens_list) or starts_with_target(left)
            matches_right = (strip_years(right) == t_album_tokens_list) or starts_with_target(right)
            if not (matches_left or matches_right):
                continue
            artist_set_score = fuzz.token_set_ratio(t_artist, c_artist)
            artist_sort_score = fuzz.token_sort_ratio(t_artist, c_artist)
            if artist_set_score >= 90 and artist_sort_score >= 90:
                # Optional: if target_year provided, prefer candidates whose raw album string contains that year
                if target_year and isinstance(target_year, str):
                    if target_year in c_album_raw:
                        segment_candidates.append((c, max(92, score_cutoff)))
                    else:
                        segment_candidates.append((c, max(90, score_cutoff)))
                else:
                    segment_candidates.append((c, max(90, score_cutoff)))
        if len(segment_candidates) == 1:
            return segment_candidates[0][0], segment_candidates[0][1]
        # Fallback 3: unique contains match — candidate album contains target album string
        contains_candidates: List[Tuple[Dict[str, object], int]] = []
        for c in candidates:
            c_artist = normalize_text(str(c.get("artist", "")))  # type: ignore
            c_album = normalize_album_for_match(str(c.get("album", "")), for_candidate=True)  # type: ignore
            # substring containment on normalized strings
            if c_album and t_album and (t_album in c_album):
                artist_set_score = fuzz.token_set_ratio(t_artist, c_artist)
                artist_sort_score = fuzz.token_sort_ratio(t_artist, c_artist)
                if artist_set_score >= 90 and artist_sort_score >= 90:
                    contains_candidates.append((c, max(90, score_cutoff)))
        if len(contains_candidates) == 1:
            return contains_candidates[0][0], contains_candidates[0][1]
        # else fall through to no result
        return None

    # Artist-first selection: we already gated artist/album above. Now pick by year closeness, then score.
    def year_bucket(d: Optional[int]) -> int:
        if d is None:
            return 2
        if d == 0:
            return 0
        if d == 1:
            return 1
        return 2

    # Sort: best year bucket (0 exact, 1 ±1, 2 others) then by descending score
    hits.sort(key=lambda t: (year_bucket(t[2]), -t[1]))
    top_cand, top_score, _ = hits[0]
    if top_score >= score_cutoff:
        return top_cand, top_score
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
    # If --out is not provided, derive it from the list file so logs
    # and not-found files are created alongside the list.
    if not args.out:
        if args.list_file:
            lf = Path(str(args.list_file)).expanduser()
            args.out = str(lf.with_suffix(".m3u8"))
        else:
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
    # Skip filesystem cleanup when running in dry-run mode
    if not args.dry_run:
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
    # In dry-run, only show warnings/errors in console; no file writes
    if args.dry_run:
        root_logger.setLevel(logging.WARNING)
    else:
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
        logging.debug("Parsing entries from local HTML: %s", args.html_file)
        entries = parse_album_list_from_html(p.read_text(encoding="utf-8", errors="ignore"))
    elif args.list_file:
        p = Path(args.list_file)
        if not p.exists():
            logging.error("List file not found: %s", args.list_file)
            sys.exit(1)
        logging.debug("Parsing entries from local file: %s", args.list_file)
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
            key=lambda e: (normalize_artist(e["artist"]), normalize_text(e["album"]))
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

    # Log the ordered entry listing with explicit year information
    for idx, e in enumerate(entries, start=1):
        yr_paren = f" ({e['year']})" if e.get('year') else ""
        logging.debug("%d. %s - %s%s", idx, e["artist"], e["album"], yr_paren)

    # Optional: load exceptions mapping to force-resolve entries to specific folders
    exceptions_map: Dict[Tuple[str, str], str] = {}
    if exceptions_path.exists():
        try:
            raw = exceptions_path.read_text(encoding="utf-8", errors="ignore")
            for line in raw.splitlines():
                line = line.strip()
                if not line:
                    continue
                # Support full-line comments starting with '#'
                if line.startswith("#"):
                    continue
                # Accept either a tab or two-or-more spaces as the separator between left and path
                sep = re.search(r"\t|\s{2,}", line)
                if not sep:
                    continue
                left = line[:sep.start()].strip()
                folder = line[sep.end():].strip()
                # First extract artist and the title (which may contain parentheses);
                # the optional trailing year is handled separately to avoid truncating
                # legitimate parentheses in album titles like 'Ποα (Poa)'.
                m = re.match(r"^\s*(?P<artist>.+?)\s*-\s*(?P<title>.+?)\s*$", left)
                if not m:
                    continue
                artist = m.group("artist").strip()
                title = m.group("title").strip()
                # If the title ends with '(YYYY)', peel it off as year, keeping inner parentheses intact
                ym = re.search(r"\((\d{4})\)\s*$", title)
                if ym:
                    album_raw = title[:ym.start()].rstrip()
                else:
                    album_raw = title
                album = resolve_self_titled(album_raw, artist)
                key = (normalize_artist(artist), normalize_text(album))
                exceptions_map[key] = folder
            logging.info("Loaded exceptions: %d entries from %s", len(exceptions_map), exceptions_path)
        except Exception as e:
            logging.warning("Failed to read exceptions file %s: %s", exceptions_path, e)

    # 1) Index and match against PRIMARY first (use cache if available)
    project_root = Path(__file__).resolve().parent
    primary_cache = project_root / "primary.tmp"
    primary_candidates: List[Dict[str, object]] = []
    if args.primary:
        cached = load_cached_candidates(primary_cache)
        if cached is not None:
            logging.info("Loaded primary index from cache: %s (%d folders)", primary_cache, len(cached))
            primary_candidates = cached
        else:
            primary_candidates = index_music_library([args.primary])
            # Save cache even in dry-run so future runs are faster
            save_candidates_cache(primary_candidates, primary_cache)

    remaining: List[Tuple[int, Dict[str, Optional[str]]]] = []
    for idx, e in enumerate(entries, start=1):
        # First: honor exceptions mapping if present
        key = (normalize_artist(e.get("artist", "")), normalize_text(e.get("album", "")))
        if key in exceptions_map:
            folder = exceptions_map[key]
            folder_p = Path(folder)

            def _exists_or_try_variants(p: Path) -> Optional[Path]:
                # Try straightforward existence first
                if p.exists():
                    return p
                # Normalize common dash/space variants in last segment: " - " vs "-" and Unicode dashes
                name = p.name
                parent = p.parent
                dash_variants = set()
                dash_variants.add(name.replace(" – ", " - "))
                dash_variants.add(name.replace("—", "-").replace(" – ", " - "))
                dash_variants.add(re.sub(r"\s*-\s*", "-", name))  # collapse spaces around hyphen
                dash_variants.add(re.sub(r"-", " - ", name))        # expand to spaced hyphen
                for nv in dash_variants:
                    alt = parent / nv
                    if alt.exists():
                        return alt
                # As a last resort, look for a sibling dir under parent matching normalized tokens
                try:
                    target_tok = normalize_text(name)
                    for cand in parent.iterdir():
                        if cand.is_dir():
                            if normalize_text(cand.name) == target_tok:
                                return cand
                except Exception:
                    pass
                return None

            resolved = _exists_or_try_variants(folder_p)
            if resolved is not None:
                matched_count += 1
                # Collect tracks from the specified folder (prefer .cue files)
                audio_files: List[str] = []
                cue_files: List[str] = []
                for root, dirs, files in os.walk(str(resolved)):
                    root_p = Path(root)
                    # prune #recycle folders from traversal
                    dirs[:] = [d for d in dirs if "#recycle" not in d.lower()]
                    audio_files.extend([str(root_p / f) for f in files if Path(f).suffix.lower() in AUDIO_EXTS])
                    cue_files.extend([str(root_p / f) for f in files if Path(f).suffix.lower() == ".cue"])
                tracks = sorted(unique_preserve_order(cue_files)) if cue_files else sorted(unique_preserve_order(audio_files))
                all_tracks.extend(tracks)
                # Logging already captures match details; no separate found-lines tracking
                yr = e.get('year') or "?"
                logging.info("Match (exceptions): %d. %s - %s (%s) -> %s (%d files)", idx, e["artist"], e["album"], yr, str(resolved), len(tracks))
                continue
            else:
                yr = e.get('year') or "?"
                logging.warning("Exceptions path not found: %s (for %s - %s (%s))", folder, e.get("artist"), e.get("album"), yr)

        # In primary, use target year for tie-breaking (exact/±1 favored),
        # but best_match only applies positive bonuses so reissues still match.
        bm = best_match(e["artist"], e["album"], primary_candidates, score_cutoff=args.score_cutoff, target_year=e.get("year"))
        if bm:
            cand, score = bm
            matched_count += 1
            yr = e.get('year') or "?"
            logging.info("Match (primary): %d. %s - %s (%s) -> %s (score %d)", idx, e["artist"], e["album"], yr, cand["root"], score)
            tracks = collect_album_tracks(cand)
            all_tracks.extend(tracks)
            # Logging already captures match details; no separate found-lines tracking
        else:
            remaining.append((idx, e))

    # 2) For any remaining, index and match against SECONDARY (use cache if available)
    secondary_candidates: List[Dict[str, object]] = []
    if args.secondary and remaining:
        secondary_cache = project_root / "secondary.tmp"
        cached = load_cached_candidates(secondary_cache)
        if cached is not None:
            logging.info("Loaded secondary index from cache: %s (%d folders)", secondary_cache, len(cached))
            secondary_candidates = cached
        else:
            secondary_candidates = index_music_library([args.secondary])
            # Save cache even in dry-run so future runs are faster
            save_candidates_cache(secondary_candidates, secondary_cache)

    for idx, e in remaining:
        bm = best_match(e["artist"], e["album"], secondary_candidates, score_cutoff=args.score_cutoff, target_year=e.get("year"))
        if bm:
            cand, score = bm
            matched_count += 1
            yr = e.get('year') or "?"
            logging.info("Match (secondary): %d. %s - %s (%s) -> %s (score %d)", idx, e["artist"], e["album"], yr, cand["root"], score)
            tracks = collect_album_tracks(cand)
            all_tracks.extend(tracks)
            # Logging already captures match details; no separate found-lines tracking
        else:
            logging.warning("No match: %d. %s - %s (%s)", idx, e["artist"], e["album"], e.get('year') or "?")
            not_found.append(e)

    # De-duplicate tracks while preserving order
    all_tracks = unique_preserve_order(all_tracks)

    # Optional sort of tracks
    if args.sort_tracks:
        all_tracks = sorted(all_tracks)

    # Write a dedicated not-found log only in non-dry-run mode when there are entries
    not_found_path = Path(str(args.not_found_log)).expanduser() if args.not_found_log else default_not_found_path
    if not args.dry_run and not_found:
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
        return

    make_m3u8(all_tracks, args.out)


if __name__ == "__main__":
    main()
