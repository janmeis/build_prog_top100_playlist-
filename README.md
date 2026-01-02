# Python Environment Setup

A dedicated virtual environment has been created at `.venv` in this workspace and configured for VS Code.

## Activate

PowerShell:
```powershell
.\.venv\Scripts\Activate.ps1
```

Command Prompt (CMD):
```bat
.\.venv\Scripts\activate.bat
```

## Run Python
```powershell
.\.venv\Scripts\python.exe your_script.py
```

## Install Packages
```powershell
.\.venv\Scripts\python.exe -m pip install <package>
```

Optional: save dependencies
```powershell
.\.venv\Scripts\python.exe -m pip freeze > requirements.txt
```

## VS Code
Interpreter is pinned via `.vscode/settings.json` to `.venv\\Scripts\\python.exe`. Opening or running Python files in this workspace will use the venv automatically.

## Playlist Builder

Generates an M3U8 playlist from the "My Top 100 Obscure British Prog Albums" list, matching albums in your libraries.

### What build_playlist.py does
- Parse input list: Reads entries from a saved forum HTML page (`--html-file`) or a plain text list (`--list-file`) with lines in the form `Artist - Album (Year)`. It de-duplicates near-duplicate entries per artist.
- Index your libraries: Walks the primary and optional secondary music library roots to find candidate album folders that contain audio or `.cue` files (skips `#recycle`). Subsequent runs use lightweight JSON caches for speed.
- Robust fuzzy matching: Normalizes accents, punctuation, leading articles, and year prefixes, then fuzzy-matches target `artist + album` to candidate folders. Year proximity (exact or ±1) is used as a tie-break when the list supplies a year. An exceptions file `<out-stem>-exceptions.txt` can override specific mappings.
- Collect tracks: If a matched folder contains `.cue` files, the playlist includes only the `.cue` files; otherwise it includes audio files (`.flac`, `.mp3`, `.m4a`, `.wav`, `.ape`, `.ogg`). Duplicates are removed; optional sorting is available.
- Write outputs: Produces the M3U8 playlist (`--out`). Also writes a not-found list next to the output by default and a single log file (configurable via `--log`). Supports `--dry-run` and `--parse-only` modes.

### Install deps
```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```
### Quick parse-only test
```powershell
.\.venv\Scripts\python.exe build_playlist.py --parse-only --list-file top100_list_sample.txt
```

### Parse saved HTML (preferred)
1. Open the forum page in your browser.
2. Save the page as HTML (e.g., `top100_forum.html`) into this workspace.
3. Parse it locally:
```powershell
.\.venv\Scripts\python.exe build_playlist.py --parse-only --html-file top100_forum.html --sort-entries
```

### Build playlist (dry run)
```powershell
.\.venv\Scripts\python.exe build_playlist.py `
	--list-file top100_list.txt `
	--primary \\synologyds920\music `
	--secondary "\\synologyds920\WdMyCloudEX2\Rock & Jazz\" `
	--sort-entries `
	--sort-tracks `
	--dry-run
```

Notes:
- In PowerShell, wrap paths containing `&` or spaces in quotes (e.g., "...Rock & Jazz...").
- Alternatively, escape only the ampersand with a backtick: `` `& `` (e.g., Rock `& Jazz). Quoting is simpler and recommended.

### Build playlist (write file)
```powershell
.\.venv\Scripts\python.exe build_playlist.py `
	--list-file top100_list.txt `
	--out top100_prog_obscure_british.m3u8
```

This will also create, by default, next to the playlist file:
- `top100_prog_obscure_british-not-found-log.txt` with any unmatched entries.
- `top100_prog_obscure_british-found-log.txt` with lines `Artist - Album (Year)\t<album_path>`.
- Optional: `top100_prog_obscure_british-exceptions.txt` to force-resolve tricky entries. Each line should be `Artist - Album (Year)\t<album_folder_path>`. If present, it is processed automatically before matching.
 - If duplicate album folders are matched by multiple entries, `top100_prog_obscure_british-duplicates.txt` is written with `<album_folder_path>\t<count>`.

### Options
- `--list-file`: Local text file with lines `Artist - Album (Year)`.
- `--html-file`: Parse entries from a locally saved HTML page.
- `--primary` / `--secondary`: UNC/base folders to search for albums.
- `--score-cutoff`: Fuzzy match threshold (default 85).
- `--sort-entries`: Sort entries (artist → album) before matching.
- `--sort-tracks`: Sort track paths in the final playlist.
- `--dry-run`: Report matches and sample track paths without writing.
- `--parse-only`: Only parse the list and print entries.
 - `--not-found-log`: Optional explicit path for unmatched entries. If omitted, a default is created next to `--out` named `<out-stem>-not-found-log.txt`.
 - `--found-log`: Optional explicit path for matched album paths. If omitted, a default is created next to `--out` named `<out-stem>-found-log.txt`. The file contains lines `Artist - Album (Year)\t<album_path>`.

### How matching works
- Normalizes accents (e.g., ö→o), removes leading articles (the/a/an), and punctuation.
- Uses token-based fuzzy matching on `artist - album` against folder names.
- Collects audio tracks (`.flac`, `.mp3`, `.m4a`, `.wav`, `.ape`, `.ogg`).
- If `.cue` files exist: playlist includes ONLY the `.cue` files and omits audio files.

## TXT → Library Path Mapping Rules

This project maps lines in a txt list (format: `Artist - Album (Year)`) to album folders under your libraries (`\\server\\share\\Artist\\[YYYY] Album`). These are the rules implemented by `build_playlist.py`:

### Input Parsing
- Numbering: Leading markers like `1)` or `12.` are stripped.
- Split point: Uses the last ` - ` to separate `artist` and `album`. Other hyphens `-` (without spaces around them) are part of names.
- Year: Optional trailing `(YYYY)` is extracted if present.
- Self‑titled shorthand: `s/t`, `s.t.` in the album field resolves to the artist name.
- De‑duplication: Entries are de‑duplicated by normalized `artist + album`.

### Text Normalization
- Accents: Removed using Unidecode.
- Joiners: `&` and `/` become `and`; `+` becomes a space.
- Articles: Leading `the|a|an|le|la|les|el|los|las|der|die|das` removed; French elision `l’` handled.
- Dashes: `-` are treated as spaces both in entries and on-disk paths.
- Dots: Excessive dots are removed (e.g., `T.R.A.M.` → `TRAM`).

### Album Normalization
- Target titles: Strip a single leading year token from album titles.
- Candidate folders: Strip one or more leading year tokens (handles `[1970 (2021)] Album`).

### Self‑Titled Rules
- If target is self‑titled (`album == artist` after normalization): candidate album may add only generic extras or numeric tokens.
- Allowed extras: `deluxe`, `remaster(ed)`, `edition`, `expanded`, `mono`, `stereo`, `complete`, `collection`, `anthology`.

### Candidate Discovery
- Sources: Primary library first, then secondary.
- Album folders: Must contain audio or `.cue` files; artist = parent folder, album = folder name.
- Skips: Paths containing `#recycle` are ignored.

### Artist Matching
- Strong gate: `token_set_ratio ≥ 85` and `token_sort_ratio ≥ 90`.
- Alias allowance: Candidate tokens ⊆ target tokens, size ≥ 2, extras only from a safe list (e.g., `group`, `band`, `ensemble`, `orchestra`, `paraphernalia`, `wolf`, `and`, `with`, `feat`), and album match ≥ 95.
- Collaboration superset: Target ⊆ candidate tokens, album ≥ 95, artist set ≥ 85, with explicit collab marker (`+`, `&`, `/`, `and`) or ≤ 2 extra tokens; target must have ≥ 2 tokens.
- Duo subset: Target lists multiple artists; candidate is a subset with ≥ 2 tokens; album ≥ 95; artist set ≥ 85.
- Single‑token guard: If target artist is one token and candidate is a superset, only accept when candidate album is strictly self‑titled and `no‑space album ratio ≥ 99`.

### Album Matching
- Strong gate requires both:
	- `token_set_ratio(target_album, candidate_album) ≥ score_cutoff` (default 85), and
	- `ratio(no‑space target_album, no‑space candidate_album) ≥ max(85, score_cutoff − 5)`.
 - Exact artist+album matches do not require year proximity.

### Year Handling
- Extraction: From candidate album name and its path. If a folder starts with stacked years like `[1973 (2004)]`, both are captured: `1973` = original release, `2004` = reissue/remaster.
- Tie‑breaks: If the input provides a year, bonuses apply to the closest candidate year among all captured years (original or reissue): exact (+10), ±1 (+5). No penalty otherwise.
- Preference: When both original and reissue match equally, the original year is naturally favored by folder naming but either can secure the bonus.
- Selection: Sort by year bucket (exact → ±1 → other) then by descending score.

### Compound Title Fallbacks
- Prefix case: Unique candidate whose album starts with target album tokens followed by `and`, with strong artist match (both scores ≥ 90).
- Segment case: Unique `X and Y` candidate where either segment equals or starts with the target album (ignoring numeric tokens); prefer raw strings containing the target year when provided.
 - Unique contains case: If artist strongly matches and a candidate album contains the target album string, and there’s no other candidate, accept it.

### Final Selection & Path Resolution
- Only candidates passing artist/album gates or fallbacks are considered.
- Pick best by year bucket then score; must meet `score_cutoff`.
- Use the candidate’s `root` directory for the mapped path (e.g., `\\synologyds920\\music\\Artist\\[YYYY] Album`).

### Exceptions & Secondary Library
- If an exceptions file `<out-stem>-exceptions.txt` is present, lines `Artist - Album (Year)\t<album_folder_path>` override matching.
- If no match in primary, the same rules are evaluated against the secondary library.

### Examples
- Arkus — `Arkus - 1914 (1981)` maps to `\\synologyds920\music\Arkus\[1981] 1914` via unique contains match.
- Schicke Führs Fröhling — `Schicke Führs Fröhling - Symphonic Pictures (1976)` maps to `\\synologyds920\music\Schicke, Führs & Fröhling\[1977] Symphonic Pictures 1-5 Sunburst 6-12 The Collected Works of SFF [Disc 1]` via unique contains match with strong artist alignment.
