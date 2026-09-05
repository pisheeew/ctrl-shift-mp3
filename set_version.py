"""
set_version.py
Stamp the release tag version into version_info.txt for PyInstaller.

The tag-triggered release workflow runs this right after checkout, before
build-release.ps1, so the built executable's Windows metadata identifies the
same version as the tag, the ZIP name, and the generated release notes:

    python set_version.py --tag v1.0.0

version_info.txt stays a normal checked-in file at its default 1.0.0 values;
stamping rewrites it in the working tree only.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from package_release import parse_tag

REPO_ROOT = Path(__file__).resolve().parent


def _replace_exactly_once(template: str, pattern: str, replacement: str, what: str) -> str:
    updated, count = re.subn(pattern, replacement, template)
    if count != 1:
        raise RuntimeError(
            f"Expected exactly one {what} in version_info.txt, found {count}; "
            "the template has drifted from the expected format."
        )
    return updated


def stamp_version_info(info_path: Path, version: tuple[int, int, int]) -> Path:
    """
    Rewrite the version fields in *info_path* to *version*.

    Updates filevers, prodvers, and the FileVersion/ProductVersion strings,
    leaving every other field untouched. Raises RuntimeError if the template
    no longer matches the expected format.
    """
    dotted = ".".join(str(part) for part in version)
    tuple_form = "(" + ", ".join(str(part) for part in version) + ", 0)"
    text = info_path.read_text(encoding="utf-8")

    fields = [
        ("filevers field", r"\bfilevers=\(\d+, \d+, \d+, \d+\)", f"filevers={tuple_form}"),
        ("prodvers field", r"\bprodvers=\(\d+, \d+, \d+, \d+\)", f"prodvers={tuple_form}"),
        ("FileVersion string", r"StringStruct\('FileVersion', '\d+\.\d+\.\d+'\)",
         f"StringStruct('FileVersion', '{dotted}')"),
        ("ProductVersion string", r"StringStruct\('ProductVersion', '\d+\.\d+\.\d+'\)",
         f"StringStruct('ProductVersion', '{dotted}')"),
    ]
    for what, pattern, replacement in fields:
        text = _replace_exactly_once(text, pattern, replacement, what)

    info_path.write_text(text, encoding="utf-8")
    return info_path


def stamp_version_from_tag(tag: str, info_path: Path) -> Path:
    """Parse *tag* (e.g. v1.0.0) and stamp its version into *info_path*."""
    parts = tuple(int(part) for part in parse_tag(tag).split("."))
    return stamp_version_info(info_path, parts)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Stamp the release tag version into version_info.txt."
    )
    parser.add_argument("--tag", required=True, help="release tag, e.g. v1.0.0")
    parser.add_argument("--file", default=str(REPO_ROOT / "version_info.txt"),
                        help="PyInstaller version file to stamp (default: version_info.txt)")
    args = parser.parse_args(argv)
    try:
        stamped = stamp_version_from_tag(args.tag, Path(args.file))
    except (ValueError, RuntimeError) as error:
        parser.exit(2, f"set_version.py: error: {error}\n")
    print(f"Stamped {args.tag} into {stamped}")


if __name__ == "__main__":
    main()
