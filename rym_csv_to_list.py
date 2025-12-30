import csv
from pathlib import Path


def csv_to_list(csv_path: Path, out_path: Path) -> None:
    lines = []
    with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f, delimiter=";")
        for r in reader:
            artist = (r.get("artist") or "").strip()
            album = (r.get("album") or "").strip()
            year = (r.get("year") or "").strip()
            if not artist or not album:
                continue
            y = f" ({year})" if year else ""
            lines.append(f"{artist} - {album}{y}")
    out_path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


if __name__ == "__main__":
    csv_to_list(Path("rym_favorites_1968_1975.csv"), Path("rym_favorites_list.txt"))
