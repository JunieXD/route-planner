#!/usr/bin/env python3
"""Build an installable Route Planner skill archive."""

from __future__ import annotations

import argparse
import stat
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

ROOT = Path(__file__).resolve().parents[2]
TOP_LEVEL_FILES = ("SKILL.md", "README.md", "LICENSE", "SECURITY.md")
RUNTIME_DIRECTORIES = ("agents", "references", "scripts")
EXCLUDED_NAMES = {"__pycache__", ".DS_Store"}


def included_files() -> list[Path]:
    files = [ROOT / name for name in TOP_LEVEL_FILES]
    for directory in RUNTIME_DIRECTORIES:
        files.extend(
            path
            for path in (ROOT / directory).rglob("*")
            if path.is_file()
            and not any(part in EXCLUDED_NAMES for part in path.parts)
            and path.suffix not in {".pyc", ".pyo"}
        )
    missing = [str(path.relative_to(ROOT)) for path in files if not path.exists()]
    if missing:
        raise FileNotFoundError(f"missing package files: {', '.join(missing)}")
    return sorted(set(files), key=lambda path: path.as_posix())


def build_archive(output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(output, "w", compression=ZIP_DEFLATED, compresslevel=9) as archive:
        for path in included_files():
            relative = path.relative_to(ROOT)
            info = ZipInfo(
                f"route-planner/{relative.as_posix()}",
                date_time=(2026, 1, 1, 0, 0, 0),
            )
            mode = path.stat().st_mode
            permissions = 0o755 if mode & stat.S_IXUSR else 0o644
            info.external_attr = (stat.S_IFREG | permissions) << 16
            info.compress_type = ZIP_DEFLATED
            archive.writestr(info, path.read_bytes())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    build_archive(args.output.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
