"""
package_release.py
Assemble the draft-release assets for a tagged ctrl+shift+mp3 release.

Run this after build-release.ps1 has produced the portable bundle ZIP in
dist/ (the tag-triggered GitHub workflow does exactly that):

    python package_release.py --tag v1.0.0

It turns the single built ZIP into the reviewable draft-release set:

  ctrl-shift-mp3-windows-v1.0.0.zip          the versioned portable ZIP
  ctrl-shift-mp3-windows-v1.0.0.zip.sha256   SHA-256 checksum (sha256sum format)
  NOTICES.txt                                third-party notices (Python
                                             packages, bundled FFmpeg, and
                                             the PyInstaller build tool)
  release-notes.md                           draft release body

The pins are read from the same files the build itself uses, so the notices
and notes always describe the artifacts that were actually built:

  - requirements-release.txt  for the pinned Python packages
  - build-release.ps1         for the pinned FFmpeg version, archive, and
                              archive checksum
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent

_BUILT_ZIP_GLOB = "ctrl-shift-mp3-*-win64.zip"
_TAG_RE = re.compile(r"^v(?P<version>\d+(?:\.\d+)+)$")
_REQUIREMENT_RE = re.compile(r"^\s*(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)==(?P<version>[^\s#]+)")
_BUILD_ONLY_PACKAGES = {"pyinstaller"}
_LICENSE_FILE_RE = re.compile(r"^(licen[cs]e|copying|notice)", re.IGNORECASE)
_LICENSE_FILE_LIMIT = 200_000


def parse_tag(tag: str) -> str:
    """Return the bare version for a tag such as v1.0.0, or raise ValueError."""
    match = _TAG_RE.match(tag.strip())
    if not match:
        raise ValueError(
            f"Tag {tag!r} is not a version tag like v1.0.0 "
            "(expected 'v' followed by dotted numbers)."
        )
    return match.group("version")


def load_release_pins() -> tuple[list[tuple[str, str]], dict[str, str]]:
    """
    Read the release pins from the files the build itself uses.

    Returns (python_pins, ffmpeg_pins) where python_pins is a list of
    (name, version) pairs for the runtime packages that ship in the bundle,
    and ffmpeg_pins describes the pinned BtbN Windows GPL build.
    """
    python_pins = []
    for line in (REPO_ROOT / "requirements-release.txt").read_text(encoding="utf-8").splitlines():
        match = _REQUIREMENT_RE.match(line)
        if match and match.group("name").lower() not in _BUILD_ONLY_PACKAGES:
            python_pins.append((_canonical(match.group("name")), match.group("version")))

    script = (REPO_ROOT / "build-release.ps1").read_text(encoding="utf-8")

    def _pin(variable: str) -> str:
        match = re.search(rf'^\${variable} = "([^"]+)"', script, re.MULTILINE)
        if not match:
            raise RuntimeError(f"Could not read the pinned ${variable} from build-release.ps1.")
        return match.group(1)

    ffmpeg_pins = {
        "version": _pin("ffmpegVersion"),
        "tag": _pin("ffmpegTag"),
        "archive": _pin("ffmpegArchiveName"),
        "sha256": _pin("ffmpegSha256"),
    }
    # Reconstruct the download URL the same way build-release.ps1 builds it,
    # so there is a single source of truth for the pinned source.
    match = re.search(r'^\$ffmpegUrl = "(?P<prefix>[^"]*)\$ffmpegTag/\$ffmpegArchiveName(?P<suffix>[^"]*)"',
                      script, re.MULTILINE)
    if not match:
        raise RuntimeError("Could not read the pinned $ffmpegUrl template from build-release.ps1.")
    ffmpeg_pins["url"] = (
        f"{match.group('prefix')}{ffmpeg_pins['tag']}/{ffmpeg_pins['archive']}{match.group('suffix')}"
    )
    return python_pins, ffmpeg_pins


def _canonical(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def collect_python_distributions(python_pins: list[tuple[str, str]]) -> list[importlib.metadata.Distribution]:
    """
    Return the installed distributions of the runtime dependency tree.

    The closure starts from the pinned top-level packages and follows
    Requires-Dist metadata. Environment markers are ignored on purpose: the
    result is the superset of declared dependencies that are actually
    installed in the pinned environment, which errs toward inclusion for
    license compliance.
    """
    installed = {_canonical(dist.metadata["Name"] or dist.name): dist
                 for dist in importlib.metadata.distributions()}
    pinned_versions = {_canonical(name): version for name, version in python_pins}
    closure: dict[str, importlib.metadata.Distribution] = {}
    queue = list(pinned_versions)
    while queue:
        canonical = queue.pop()
        if canonical in closure:
            continue
        dist = installed.get(canonical)
        if dist is None:
            # Declared dependencies whose environment markers exclude this
            # interpreter are normally absent; only a missing seed pin means
            # the release environment was not prepared.
            if canonical in pinned_versions:
                print(f"warning: pinned package {canonical!r} is not installed; "
                      "run pip install -r requirements-release.txt first", file=sys.stderr)
            continue
        if canonical in pinned_versions and dist.version != pinned_versions[canonical]:
            print(f"warning: installed {canonical} {dist.version} does not match the "
                  f"pinned {pinned_versions[canonical]}; the notices describe the "
                  "installed environment, not requirements-release.txt", file=sys.stderr)
        closure[canonical] = dist
        for requirement in dist.requires or []:
            declared = re.match(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)", requirement)
            if declared:
                queue.append(_canonical(declared.group(1)))
    return [closure[key] for key in sorted(closure)]


def _license_summary(dist: importlib.metadata.Distribution) -> str:
    meta = dist.metadata
    parts = []
    expression = meta.get("License-Expression")
    if expression:
        parts.append(expression)
    license_field = meta.get("License")
    if license_field:
        parts.append(" ".join(license_field.split()))
    classifiers = [c for c in meta.get_all("Classifier") or [] if c.startswith("License ::")]
    if classifiers:
        parts.extend(c.split(" :: ", 2)[-1] for c in classifiers)
    return "; ".join(parts) if parts else "not declared in package metadata"


def _license_files(dist: importlib.metadata.Distribution) -> str:
    """Return the license texts shipped in the package's dist-info, if any."""
    sections = []
    for file in dist.files or []:
        if not _LICENSE_FILE_RE.match(file.name):
            continue
        try:
            text = dist.locate_file(file).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if len(text) > _LICENSE_FILE_LIMIT:
            text = text[:_LICENSE_FILE_LIMIT] + "\n[... truncated ...]"
        sections.append(f"--- {file.name} ---\n{text.strip()}")
    return "\n\n".join(sections)


def write_notices(path: Path, ffmpeg_pins: dict[str, str]) -> Path:
    """Write NOTICES.txt covering the bundled Python packages and FFmpeg."""
    python_pins, _ = load_release_pins()
    dists = collect_python_distributions(python_pins)

    lines = [
        "ctrl+shift+mp3 third-party notices",
        "===================================",
        "",
        "This file was generated by package_release.py from the pinned release",
        "inputs (requirements-release.txt and build-release.ps1). It lists the",
        "third-party components redistributed with the portable Windows release",
        "and the license information they declare. Review it against the",
        "built bundle before publishing the release.",
        "",
        "1. Bundled Python packages",
        "--------------------------",
        "The portable bundle embeds the following Python packages and their",
        "transitive dependencies, as installed from the pinned release",
        "requirements:",
        "",
    ]
    for dist in dists:
        meta = dist.metadata
        lines.append(f"* {meta['Name']} {meta['Version']}")
    for dist in dists:
        meta = dist.metadata
        lines.extend([
            "",
            f"### {meta['Name']} {meta['Version']}",
        ])
        if meta.get("Summary"):
            lines.append(f"Summary: {meta['Summary']}")
        home = meta.get("Home-page")
        project_urls = meta.get_all("Project-URL") or []
        if not home:
            for url in project_urls:
                if url.lower().startswith(("home", "source", "repository")):
                    home = url.split(", ", 1)[-1]
                    break
        if home:
            lines.append(f"Home page: {home}")
        lines.append(f"License: {_license_summary(dist)}")
        license_files = _license_files(dist)
        if license_files:
            lines.extend(["", license_files])

    lines.extend([
        "",
        "2. Bundled FFmpeg",
        "-----------------",
        "The portable bundle ships ffmpeg.exe and ffprobe.exe from the pinned",
        "BtbN Windows GPL build:",
        f"  FFmpeg version: {ffmpeg_pins['version']}",
        f"  Source build:   {ffmpeg_pins['tag']}",
        f"  Download URL:   {ffmpeg_pins['url']}",
        f"  Archive:        {ffmpeg_pins['archive']}",
        f"  Archive SHA-256: {ffmpeg_pins['sha256']}",
        "  Upstream:       https://ffmpeg.org",
        "",
        "This build is configured with --enable-gpl and is therefore distributed",
        "under the GNU General Public License. The applicable license texts and",
        "FFmpeg's own licensing documentation are available at",
        "https://www.gnu.org/licenses/ and https://ffmpeg.org/legal.html.",
        "FFmpeg is free software; redistribution must follow the GPL terms.",
        "The pinned download URL above identifies the corresponding source for",
        "the redistributed binaries.",
        "",
        "3. Build tooling",
        "----------------",
        "The executable was produced with PyInstaller. PyInstaller is licensed",
        "under the GNU GPL v2 or later, with a special bootloader exception that",
        "explicitly allows distributing applications built with it (including",
        "this bundle) under any license. PyInstaller itself is not redistributed",
        "as a Python package.",
        "",
    ])

    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def write_release_notes(path: Path, version: str, zip_name: str, checksum: str,
                        ffmpeg_version: str) -> Path:
    """Write the draft release body (markdown)."""
    notes = f"""# ctrl+shift+mp3 {version} — portable Windows release

A portable, self-contained Windows build of ctrl+shift+mp3. Extract the ZIP
anywhere and run `ctrl-shift-mp3.exe`; no Python or FFmpeg installation is
required.

## Extract and run

1. Download `{zip_name}` (and its `.sha256` checksum file) below.
2. Extract the ZIP to a folder of your choice.
3. Run `ctrl-shift-mp3.exe`. Your browser opens the local interface at
   http://127.0.0.1:8743/ (if that port is busy, the app picks another free
   localhost port and shows the URL it is actually using).
4. Close the browser tab, or press Ctrl+C in the console window, to stop.

## First launch: Windows SmartScreen

This release is not code-signed, so Windows may show
"Windows protected your PC". Choose **More info** → **Run anyway**. Only run
releases downloaded from this project's GitHub Releases page.

## Verify the checksum

In PowerShell:

    Get-FileHash .\\{zip_name} -Algorithm SHA256

The expected SHA-256 digest is:

    {checksum}

It is also recorded in `{zip_name}.sha256`.

## Bundled FFmpeg

`ffmpeg.exe` and `ffprobe.exe` (FFmpeg {ffmpeg_version}, pinned BtbN win64
GPL build) are bundled and used automatically; no separate FFmpeg install is
needed. If the bundled binaries are unavailable, the app falls back to
`ffmpeg` on `PATH`. See `NOTICES.txt` for version, source, and license
information.

## Privacy and data locations

The app contacts Spotify and YouTube from your machine and stores
credentials, cache, and logs in your user profile — never inside the
extracted folder. The local server binds to 127.0.0.1 only.

## Troubleshooting

- Browser did not open: type the printed `http://127.0.0.1:<port>/` URL into
  your browser manually.
- For console diagnostics, run `ctrl-shift-mp3.exe --debug`.
- FFmpeg problems: the app logs which binaries it selected.

## Responsible use

Only download audio you have the right to use, such as music you own.

## License and notices

This release redistributes third-party components. `NOTICES.txt` (attached
to this release and included in the bundle) lists the bundled Python
packages, the FFmpeg build, and their licenses. The project license is
included in the bundle as `LICENSE`.
"""
    path.write_text(notes, encoding="utf-8")
    return path


def assemble_release(tag: str, dist_dir: Path) -> dict[str, Path]:
    """
    Turn the built bundle ZIP in *dist_dir* into the draft-release assets.

    Returns the paths of the versioned ZIP, its checksum file, the third-party
    notices, and the draft release notes.
    """
    version = parse_tag(tag)
    built = sorted(dist_dir.glob(_BUILT_ZIP_GLOB))
    if len(built) != 1:
        raise RuntimeError(
            f"Expected exactly one built {_BUILT_ZIP_GLOB} in {dist_dir}, "
            f"found {len(built)}: {[path.name for path in built]}"
        )

    zip_path = dist_dir / f"ctrl-shift-mp3-windows-v{version}.zip"
    built[0].replace(zip_path)

    digest = hashlib.sha256()
    with zip_path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    checksum = digest.hexdigest()
    checksum_path = zip_path.with_name(zip_path.name + ".sha256")
    checksum_path.write_text(f"{checksum}  {zip_path.name}\n", encoding="utf-8")

    _, ffmpeg_pins = load_release_pins()
    notices_path = write_notices(dist_dir / "NOTICES.txt", ffmpeg_pins)
    notes_path = write_release_notes(
        dist_dir / "release-notes.md",
        version=version,
        zip_name=zip_path.name,
        checksum=checksum,
        ffmpeg_version=ffmpeg_pins["version"],
    )
    return {"zip": zip_path, "checksum": checksum_path, "notices": notices_path, "notes": notes_path}


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Assemble draft-release assets from the built bundle ZIP.")
    parser.add_argument("--tag", required=True, help="release tag, e.g. v1.0.0")
    parser.add_argument("--dist-dir", default=str(REPO_ROOT / "dist"),
                        help="directory containing the built ZIP (default: dist)")
    args = parser.parse_args(argv)
    try:
        assets = assemble_release(args.tag, dist_dir=Path(args.dist_dir))
    except (ValueError, RuntimeError) as error:
        parser.exit(2, f"package_release.py: error: {error}\n")
    for path in assets.values():
        print(f"Created {path}")


if __name__ == "__main__":
    main()
