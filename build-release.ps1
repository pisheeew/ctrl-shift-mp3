$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

$ffmpegVersion = "7.1.1"
$ffmpegUrl = "https://github.com/BtbN/FFmpeg-Builds/releases/download/autobuild-2025-03-31-12-56/ffmpeg-n${ffmpegVersion}-win64-gpl-${ffmpegVersion}.zip"
$vendorRoot = Join-Path $root "vendor"
$archive = Join-Path $vendorRoot "ffmpeg-$ffmpegVersion.zip"
$extractRoot = Join-Path $vendorRoot "ffmpeg-extract"
$ffmpegDir = Join-Path $vendorRoot "ffmpeg"

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    throw "Python is required to build the release."
}

python -m pip install --requirement requirements-release.txt
if ($LASTEXITCODE -ne 0) { throw "Could not install release dependencies." }

New-Item -ItemType Directory -Force -Path $vendorRoot | Out-Null
if (-not (Test-Path (Join-Path $ffmpegDir "ffmpeg.exe")) -or
    -not (Test-Path (Join-Path $ffmpegDir "ffprobe.exe"))) {
    Write-Host "Downloading FFmpeg $ffmpegVersion from the pinned BtbN Windows GPL build..."
    Invoke-WebRequest -Uri $ffmpegUrl -OutFile $archive
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
