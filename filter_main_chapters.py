from __future__ import annotations

import argparse
import shutil
from pathlib import Path

from scrape_kongbugushi import clean_chapter_text, filter_main_chapter_run


def title_from_file(path: Path) -> str:
    with path.open("r", encoding="utf-8") as handle:
        return handle.readline().strip()


def classify_chapter_files(folder: Path) -> tuple[list[Path], list[Path]]:
    files = sorted(folder.glob("*.txt"))
    links = [(title_from_file(path), str(path)) for path in files]
    keep_paths = {Path(path) for _, path in filter_main_chapter_run(links)}
    keep = [path for path in files if path in keep_paths]
    remove = [path for path in files if path not in keep_paths]
    return keep, remove


def clean_existing_file(path: Path) -> None:
    raw = path.read_text(encoding="utf-8")
    lines = raw.splitlines()
    title = lines[0].strip() if lines else path.stem
    body = clean_chapter_text(title, raw)
    path.write_text(f"{title}\n\n{body}\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Move or delete non-main Kongbugushi scrape files from a folder."
    )
    parser.add_argument("--folder", type=Path, default=Path("chaojinjiyouxi4"))
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually move/delete files. Without this, only prints a dry run.",
    )
    parser.add_argument(
        "--delete",
        action="store_true",
        help="Delete non-main files instead of moving them to --move-dir.",
    )
    parser.add_argument("--move-dir", default="_supplementary")
    parser.add_argument(
        "--clean-text",
        action="store_true",
        help="Rewrite kept files to remove previous/next chapter navigation text.",
    )
    args = parser.parse_args()

    keep, remove = classify_chapter_files(args.folder)
    print(f"Main story files to keep: {len(keep)}")
    for path in keep:
        print(f"  KEEP   {path.name}")

    print(f"\nSupplementary files to remove: {len(remove)}")
    for path in remove:
        print(f"  REMOVE {path.name}")

    if not args.apply:
        print("\nDry run only. Re-run with --apply to move/remove these files.")
        if args.clean_text:
            print("Text cleaning is also dry-run only until --apply is provided.")
        return

    if args.clean_text:
        for path in keep:
            clean_existing_file(path)
        print(f"\nCleaned navigation text from {len(keep)} kept files.")

    if args.delete:
        for path in remove:
            path.unlink()
        print(f"\nDeleted {len(remove)} supplementary files.")
        return

    target_dir = args.folder / args.move_dir
    target_dir.mkdir(exist_ok=True)
    for path in remove:
        shutil.move(str(path), target_dir / path.name)
    print(f"\nMoved {len(remove)} supplementary files to {target_dir}.")


if __name__ == "__main__":
    main()
