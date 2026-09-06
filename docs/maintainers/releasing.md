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
   `set_version.py` rejects anything else within the first minute of the
   run, and `package_release.py` enforces the same format again before any
   asset is uploaded.

3. The **Release** workflow
   ([`.github/workflows/release.yml`](../../.github/workflows/release.yml))
   runs on `windows-latest` and, in order:
   - checks out the tag and stamps the tag version into `version_info.txt`
     (`set_version.py`), so the exe metadata, ZIP name, and release notes all
     identify the same version; a tag that is not `v<major>.<minor>.<patch>`
     fails in this first step;
   - sets up the pinned Python;
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

### Re-run a failed release run

If a tagged run fails after the tag is pushed (flaky smoke test, transient
FFmpeg fetch), retry it manually instead of moving or re-pushing the tag: run
the **Release** workflow from the GitHub UI (or
`gh workflow run release.yml --ref main -f tag=v1.2.3`), passing the same tag
as the `tag` input. The run checks out and releases that tag exactly as a tag
push would; the input is pre-filled with the triggering ref if left empty, so
an accidental empty dispatch on a branch fails fast at the version check
rather than releasing the wrong ref.

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
| Python packages  | `requirements-release.txt` (hash-pinned lockfile, includes PyInstaller; top-level pins in `requirements-release.in`) |
| FFmpeg build     | `build-release.ps1` (`$ffmpegVersion`, `$ffmpegTag`, `$ffmpegArchiveName`, `$ffmpegSha256`) |
| GPL license text | `docs/licenses/GPL-2.0.txt` (retained verbatim inside `NOTICES.txt`) |

`package_release.py` reads the Python and FFmpeg pins from those same files
when generating notices, so the notices always describe the artifacts that
were actually built. There is deliberately no second copy of the pins.

Current FFmpeg pin: version 9.0.1, BtbN win64 GPL build
[`autobuild-2026-09-05-13-10`](https://github.com/BtbN/FFmpeg-Builds/releases/tag/autobuild-2026-09-05-13-10).
The archive's SHA-256 is verified at download time by `build-release.ps1`;
a mismatch fails the build before packaging.

## Reproduce a build locally

From a Windows checkout:

```powershell
python set_version.py --tag v1.2.3                          # stamp exe metadata (optional, defaults to 1.0.0)
powershell -ExecutionPolicy Bypass -File build-release.ps1   # pinned deps + FFmpeg + PyInstaller
python smoke_test.py                                          # packaged smoke test
python package_release.py --tag v1.2.3                        # versioned ZIP + checksum + notices + notes
```

`build-release.ps1` produces the raw bundle ZIP under its FFmpeg-derived name
(`dist\ctrl-shift-mp3-9.0.1-win64.zip`); `package_release.py` copies
`NOTICES.txt` into the bundle directory and rebuilds it as the versioned
release ZIP, then generates the remaining assets. A local rebuild should be
functionally equivalent to the CI artifact, though PyInstaller output is not
byte-reproducible.

## Updating pinned dependencies deliberately

**Python packages.** `requirements-release.txt` is a hash-pinned lockfile:
every package in the transitive closure carries exact `--hash=sha256:...`
values, and both the Release workflow and `build-release.ps1` install it with
`pip install --require-hashes`, so a compromised or typo-squatted PyPI upload
cannot flow into the bundle (see ADR-0002). To bump a dependency, regenerate
the lockfile — never hand-edit its pins or hashes:

1. Edit the top-level pin in `requirements-release.in` (the only file with
   hand-written versions; everything else in the lockfile is generated).
2. From the pinned Python (3.14.3) run:

   ```bash
   python -m pip install --upgrade pip-tools
   python -m piptools compile --generate-hashes --strip-extras --allow-unsafe \
       --output-file requirements-release.txt requirements-release.in
   ```

3. Commit both files in the same change. The pin assertions in `tests.py`
   (`TestPackageRelease`) fail if the lockfile and `.in` disagree on a
   version, or if any lockfile entry is missing its sha256 hash.

`--allow-unsafe` matters: PyInstaller's `setuptools` dependency would
otherwise be left unpinned, and `pip install --require-hashes` rejects a
requirements file containing an unpinned package. The hashes pip-compile
records cover every file on each PyPI release (all platforms plus the
sdist), so the same lockfile serves the Windows release build and the Linux
CI install. Remember the runtime pins and the PyInstaller pin serve
different purposes: only runtime packages are recorded in `NOTICES.txt`.

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
