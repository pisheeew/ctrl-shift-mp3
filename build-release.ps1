$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

$ffmpegVersion = "9.0.1"
$ffmpegTag = "autobuild-2026-09-05-13-10"
$ffmpegArchiveName = "ffmpeg-n9.0.1-26-g5c8e7e2433-win64-gpl-9.0.zip"
$ffmpegUrl = "https://github.com/BtbN/FFmpeg-Builds/releases/download/$ffmpegTag/$ffmpegArchiveName"
$ffmpegSha256 = "a8ebbaf7a99185f5abc3a2d3a657521c38d7966f06b70468d7ab29a67fe8654f"
$vendorRoot = Join-Path $root "vendor"
$archive = Join-Path $vendorRoot "ffmpeg-$ffmpegVersion.zip"
$extractRoot = Join-Path $vendorRoot "ffmpeg-extract"
$ffmpegDir = Join-Path $vendorRoot "ffmpeg"

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    throw "Python is required to build the release."
}

# requirements-release.txt is a hash-pinned lockfile (pip-compile
# --generate-hashes), so --require-hashes fails the build on any package
# whose downloaded artifact does not match the audited checksum.
python -m pip install --require-hashes --requirement requirements-release.txt
if ($LASTEXITCODE -ne 0) { throw "Could not install release dependencies." }

New-Item -ItemType Directory -Force -Path $vendorRoot | Out-Null
if (-not (Test-Path (Join-Path $ffmpegDir "ffmpeg.exe")) -or
    -not (Test-Path (Join-Path $ffmpegDir "ffprobe.exe"))) {
    Write-Host "Downloading FFmpeg $ffmpegVersion from the pinned BtbN Windows GPL build..."
    curl.exe -L --fail --output $archive $ffmpegUrl
    if ($LASTEXITCODE -ne 0) { throw "Could not download the pinned FFmpeg archive." }
    $actualHash = (Get-FileHash -Path $archive -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actualHash -ne $ffmpegSha256) {
        throw "FFmpeg archive checksum mismatch: expected $ffmpegSha256, got $actualHash."
    }
    if (Test-Path $extractRoot) { Remove-Item $extractRoot -Recurse -Force }
    Expand-Archive -Path $archive -DestinationPath $extractRoot -Force
    $bin = Get-ChildItem -Path $extractRoot -Filter ffmpeg.exe -Recurse | Select-Object -First 1
    if ($null -eq $bin) { throw "The FFmpeg archive did not contain ffmpeg.exe." }
    $binRoot = $bin.Directory.FullName
    if (-not (Test-Path (Join-Path $binRoot "ffprobe.exe"))) {
        throw "The FFmpeg archive did not contain a matching ffprobe.exe."
    }
    if (Test-Path $ffmpegDir) { Remove-Item $ffmpegDir -Recurse -Force }
    New-Item -ItemType Directory -Force -Path $ffmpegDir | Out-Null
    Copy-Item (Join-Path $binRoot "ffmpeg.exe") $ffmpegDir
    Copy-Item (Join-Path $binRoot "ffprobe.exe") $ffmpegDir
}

python -m PyInstaller --noconfirm --clean ctrl-shift-mp3.spec
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed." }

$zipPath = Join-Path $root "dist\ctrl-shift-mp3-$ffmpegVersion-win64.zip"
if (Test-Path $zipPath) { Remove-Item $zipPath -Force }
Compress-Archive -Path (Join-Path $root "dist\ctrl-shift-mp3\*") -DestinationPath $zipPath
Write-Host "Portable release created at $zipPath"
