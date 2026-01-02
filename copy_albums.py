import argparse
import logging
import os
import re
import shutil
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

# Windows/UNC friendly name sanitizer (folder names)
INVALID_CHARS = set('<>:"/\\|?*')
# Destination root safety: never touch these system folders
PROTECTED_DIRS = {"system volume information", "$recycle.bin"}

def sanitize_component(name: str) -> str:
    # Replace invalid characters with space, collapse whitespace
    cleaned = ''.join(' ' if ch in INVALID_CHARS else ch for ch in name)
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    # Strip trailing dots/spaces (Windows forbids)
    cleaned = cleaned.rstrip(' .')
    # Avoid reserved names
    if cleaned.upper() in {'CON','PRN','AUX','NUL','COM1','COM2','COM3','COM4','COM5','COM6','COM7','COM8','COM9','LPT1','LPT2','LPT3','LPT4','LPT5','LPT6','LPT7','LPT8','LPT9'}:
        cleaned = f"_{cleaned}"
    return cleaned

def parse_year_from_album_folder(name: str) -> Optional[str]:
    # Prefer 4-digit year(s) from bracketed prefix(es), e.g. "[1976 [2016]] Oh Yeah", "[1971, 1974] ..."
    # Find bracketed segments anywhere, collect years
    years: List[int] = []
    for m in re.finditer(r"\[(.*?)\]", name):
        seg = m.group(1)
        years += [int(y) for y in re.findall(r"(?<!\d)(19\d{2}|20\d{2})(?!\d)", seg)]
    if years:
        return str(min(years))
    # Fallback: first 4-digit year anywhere in name
    m = re.search(r"(?<!\d)(19\d{2}|20\d{2})(?!\d)", name)
    return m.group(1) if m else None

def strip_leading_brackets(name: str) -> str:
    # Remove leading bracketed segments like "[1976] ", "[1976 [2016]] "
    s = name
    while s.startswith('['):
        end = s.find(']')
        if end == -1:
            break
        s = s[end+1:].lstrip()
    return s

def extract_album_info(album_folder: Path) -> Tuple[str, Optional[str], str]:
    artist = album_folder.parent.name
    folder_name = album_folder.name
    year = parse_year_from_album_folder(folder_name)
    album = strip_leading_brackets(folder_name)
    album = album.strip(' -')
    return artist, year, album

def dest_album_dirname(artist: str, year: Optional[str], album: str) -> str:
    y = year or 'Unknown'
    return sanitize_component(f"{artist} - {y} - {album}")

def parse_m3u8_tracks(playlist_path: Path) -> List[Path]:
    tracks: List[Path] = []
    with playlist_path.open('r', encoding='utf-8', errors='ignore') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            # Treat raw path lines as Windows/UNC paths
            tracks.append(Path(line))
    return tracks

def unique_album_folders_from_tracks(tracks: List[Path]) -> List[Path]:
    seen: Set[str] = set()
    albums: List[Path] = []
    for t in tracks:
        album = t.parent
        key = str(album).lower()
        if key not in seen:
            seen.add(key)
            albums.append(album)
    return albums

def list_destination_dirs(dest_root: Path) -> Set[str]:
    if not dest_root.exists():
        return set()
    # Omit '.temp' and protected system folders from destination scope
    names: Set[str] = set()
    for p in dest_root.iterdir():
        if not p.is_dir():
            continue
        name_lower = p.name.lower()
        if name_lower == '.temp':
            continue
        if name_lower in PROTECTED_DIRS:
            logging.info(f"Skip protected folder in destination: {p.name}")
            continue
        names.add(p.name)
    return names

def iter_source_files(src_album: Path) -> List[Path]:
    # Recursively collect files under album folder, omitting any path inside a '.temp' directory
    files: List[Path] = []
    for p in src_album.rglob('*'):
        if p.is_dir() and p.name.lower() == '.temp':
            # Skip directory marker; guard again at file level
            continue
        if p.is_file():
            parents_lower = {pp.name.lower() for pp in p.parents}
            if '.temp' in parents_lower:
                continue
            files.append(p)
    return files

def ensure_dir(path: Path):
    path.mkdir(parents=True, exist_ok=True)


def setup_logger(log_path: Path):
    ensure_dir(log_path.parent)
    logging.basicConfig(
        level=logging.INFO,
        format='[%(levelname)s] %(message)s',
        handlers=[
            logging.FileHandler(log_path, encoding='utf-8'),
            logging.StreamHandler()
        ]
    )


def sync_albums(playlist_path: Path, dest_root: Path, apply: bool, log_path: Optional[Path] = None):
    # Default log next to playlist
    if log_path is None:
        log_path = playlist_path.with_name(playlist_path.stem + '-copy-log.txt')
    setup_logger(log_path)

    logging.info(f"Playlist: {playlist_path}")
    logging.info(f"Destination: {dest_root}")
    logging.info(f"Mode: {'APPLY' if apply else 'DRY-RUN'}")
    logging.info("Omitting '.temp' folders from sync scope")

    tracks = parse_m3u8_tracks(playlist_path)
    logging.info(f"Parsed tracks: {len(tracks)}")

    album_dirs = unique_album_folders_from_tracks(tracks)
    logging.info(f"Unique album folders: {len(album_dirs)}")

    desired_map: Dict[str, Path] = {}
    for album in album_dirs:
        artist, year, title = extract_album_info(album)
        dest_name = dest_album_dirname(artist, year, title)
        if dest_name in desired_map:
            # Prefer first occurrence; skip duplicates
            continue
        desired_map[dest_name] = album
        logging.info(f"List: {album} -> {dest_name}")

    existing = list_destination_dirs(dest_root)
    to_copy = sorted(set(desired_map.keys()) - existing)
    to_delete = sorted(existing - set(desired_map.keys()))

    logging.info(f"Planned: copy {len(to_copy)} new albums, delete {len(to_delete)} obsolete albums")

    # Copy phase (flatten files into dest album folder)
    copied_count = 0
    for name in to_copy:
        src = desired_map[name]
        dest_dir = dest_root / name
        if apply:
            ensure_dir(dest_dir)
        # Collect files
        files = iter_source_files(src)
        logging.info(f"Copy {src} -> {dest_dir} ({len(files)} files){' [APPLY]' if apply else ' [DRY-RUN]'}")
        for sf in files:
            df = dest_dir / sanitize_component(sf.name)
            if apply:
                # Handle collision: skip if exists with same size; else overwrite
                if df.exists():
                    try:
                        if sf.stat().st_size == df.stat().st_size:
                            logging.info(f"Skip (same size): {df.name}")
                            continue
                    except Exception:
                        pass
                try:
                    shutil.copy2(sf, df)
                except Exception as e:
                    logging.warning(f"Copy failed: {sf} -> {df} ({e})")
            else:
                # Dry-run log only
                pass
        if apply:
            copied_count += 1

    # Delete phase
    deleted_count = 0
    for name in to_delete:
        obsolete_dir = dest_root / name
        # Double-protect: skip deletion of system folders even if surfaced
        if name.lower() in PROTECTED_DIRS:
            logging.info(f"Skip protected folder (no delete): {obsolete_dir}")
            continue
        logging.info(f"Delete {obsolete_dir}{' [APPLY]' if apply else ' [DRY-RUN]'}")
        if apply and obsolete_dir.exists():
            try:
                shutil.rmtree(obsolete_dir)
                deleted_count += 1
            except Exception as e:
                logging.warning(f"Delete failed: {obsolete_dir} ({e})")
        else:
            pass

    logging.info(f"Summary: copied={copied_count}, deleted={deleted_count}, listed={len(desired_map)}")
    logging.info(f"Log written: {log_path}")


def main():
    parser = argparse.ArgumentParser(description='Sync album folders from an M3U8 playlist to a flat destination (Artist - Year - Album).')
    parser.add_argument('--playlist', required=True, help='Path to .m3u8 playlist.')
    parser.add_argument('--dest', required=True, help='Destination directory for synced albums (e.g., E:\\).')
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--apply', action='store_true', help='Apply changes (copy/delete).')
    group.add_argument('--dry-run', action='store_true', help='Dry-run only (default).')
    parser.add_argument('--log', help='Optional log file path; defaults next to playlist.')

    args = parser.parse_args()
    # Default to dry-run if neither flag given
    if not args.apply and not args.dry_run:
        args.dry_run = True

    playlist = Path(args.playlist)
    dest = Path(args.dest)
    log = Path(args.log) if args.log else None

    sync_albums(playlist, dest, apply=(not args.dry_run), log_path=log)


if __name__ == '__main__':
    main()
