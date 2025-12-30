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
