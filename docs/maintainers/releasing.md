# Maintainer guide: releasing ctrl+shift+mp3

How a Windows portable release is built, validated, and published. End-user
install guidance lives in the README
([Download for Windows](../../README.md#download-for-windows)); this document
is for maintainers cutting and reviewing releases.

## What a release is

A release is a draft GitHub Release, created by one tagged workflow run, with
these assets:

| Asset                        | Produced by           | Notes                                    |
| ---------------------------- | --------------------- | ---------------------------------------- |
| `ctrl-shift-mp3-windows-v<version>.zip` | `package_release.py` | The versioned portable bundle            |
| `ctrl-shift-mp3-windows-v<version>.zip.sha256` | `package_release.py` | `sha256sum`-compatible checksum |
| `NOTICES.txt`                | `package_release.py`  | Third-party notices (Python packages, FFmpeg, build tooling) |
| Release body                 | `package_release.py`  | Draft notes generated as `dist/release-notes.md` |

The ZIP contains a PyInstaller one-folder bundle: `ctrl-shift-mp3.exe`, the
frontend assets, `README.md`, `LICENSE`, `NOTICES.txt` (the third-party
notices travel inside the ZIP, beside the binaries they cover), and bundled
`ffmpeg.exe` / `ffprobe.exe`. The executable runs with a visible console;
`Ctrl+C` stops it, and `--debug` adds Flask diagnostic output.

Publishing is always a manual step. The workflow creates the release as a
**draft** and never publishes it.

## Cut a release

1. Make sure `main` is in the state you want to ship (tests green locally:
   `python -m unittest tests.py`).
2. Tag and push the tag:

   ```bash
   git tag v1.2.3
   git push origin v1.2.3
   ```

   Tags must match `v<major>.<minor>.<patch>` (for example `v1.0.0`);
   `package_release.py` rejects anything else before any asset is uploaded.

3. The **Release** workflow
   ([`.github/workflows/release.yml`](../../.github/workflows/release.yml))
   runs on `windows-latest` and, in order:
   - checks out the tag and sets up the pinned Python;
   - installs the pinned release dependencies;
   - runs the unit test suite (`tests.py`);
   - runs `build-release.ps1`, which installs the pinned requirements,
     downloads the pinned FFmpeg archive, verifies its SHA-256, runs
     PyInstaller, and zips the bundle;
   - verifies the bundle exists and runs the packaged smoke test
     (`smoke_test.py`), launching `ctrl-shift-mp3.exe` from outside the
     checkout;
   - runs `package_release.py --tag <tag>`, which generates `NOTICES.txt`,
     copies it into the bundle, rebuilds the versioned ZIP from the bundle
     directory, and writes the checksum, the draft release notes, and
     `dist/release-assets.txt`;
   - creates the **draft** release via `gh release create --draft`, uploading
     exactly the assets listed in `dist/release-assets.txt`.
4. Review the draft (checklist below), then publish it from the GitHub UI.

## Review checklist before publishing a draft

- The workflow run is fully green — in particular the smoke-test step passed
  on the built bundle, not on source.
- The draft's ZIP name matches `ctrl-shift-mp3-windows-v<version>.zip` and
  the tag.
- The digest in the `.sha256` asset matches the digest in the release notes
  body (both are generated from the same ZIP, but check).
- `NOTICES.txt` was reviewed: the package list and the FFmpeg version / URL /
  archive checksum match what you expect for this release.
- Spot-check by downloading the ZIP, verifying the checksum, extracting, and
  running `ctrl-shift-mp3.exe` once.
- The draft notes' version, checksum, and guidance are accurate.

## Pinned inputs (reproducibility)

Every release input is pinned so the same tag rebuilds the same bundle:

| Input            | Where it lives                                                    |
| ---------------- | ----------------------------------------------------------------- |
| Python version   | `.github/workflows/release.yml` (`python-version: "3.14.3"`)      |
| Python packages  | `requirements-release.txt` (exact pins, includes PyInstaller)     |
| FFmpeg build     | `build-release.ps1` (`$ffmpegVersion`, `$ffmpegTag`, `$ffmpegArchiveName`, `$ffmpegSha256`) |
| GPL license text | `docs/licenses/GPL-2.0.txt` (retained verbatim inside `NOTICES.txt`) |

`package_release.py` reads the Python and FFmpeg pins from those same files
when generating notices, so the notices always describe the artifacts that
were actually built. There is deliberately no second copy of the pins.

Current FFmpeg pin: version 7.1.1, BtbN win64 GPL build
[`autobuild-2025-08-31-13-00`](https://github.com/BtbN/FFmpeg-Builds/releases/tag/autobuild-2025-08-31-13-00).
The archive's SHA-256 is verified at download time by `build-release.ps1`;
a mismatch fails the build before packaging.

## Reproduce a build locally

From a Windows checkout:

```powershell
powershell -ExecutionPolicy Bypass -File build-release.ps1   # pinned deps + FFmpeg + PyInstaller
python smoke_test.py                                          # packaged smoke test
python package_release.py --tag v1.2.3                        # versioned ZIP + checksum + notices + notes
```

`build-release.ps1` produces the raw bundle ZIP under its FFmpeg-derived name
(`dist\ctrl-shift-mp3-7.1.1-win64.zip`); `package_release.py` copies
`NOTICES.txt` into the bundle directory and rebuilds it as the versioned
release ZIP, then generates the remaining assets. The local result should be
equivalent to the CI artifact apart from build timestamps.

## Updating pinned dependencies deliberately

**Python packages.** Edit `requirements-release.txt`. The pin assertions in
`tests.py` (`TestPackageRelease`) will fail until updated — update them in the
same change. Remember the runtime pins and the PyInstaller pin serve different
purposes: only runtime packages are recorded in `NOTICES.txt`.

**FFmpeg.** Choose the new BtbN win64 GPL build, then update the four pins in
`build-release.ps1` together (`$ffmpegVersion`, `$ffmpegTag`,
`$ffmpegArchiveName`, `$ffmpegSha256` — compute the archive digest with
`Get-FileHash -Algorithm SHA256`). The pin assertions in `tests.py` and the
pinned URL referenced from the README troubleshooting section must be updated
in the same change. Do not bump FFmpeg casually: the release's redistributed
binaries, their checksum, and the retained GPL obligations are what change.

**Python (runner).** Bump `python-version` in the workflow deliberately and
validate locally with the same interpreter, since PyInstaller support for new
Python versions lands per release.

## Draft-only publishing

`gh release create --draft` is intentional: generated notices, checksums, and
notes are reviewed by a human before anything becomes publicly visible. Never
change the workflow to publish automatically; change the review process
instead if it feels slow.
