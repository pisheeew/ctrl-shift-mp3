from pathlib import Path

from PyInstaller.building.build_main import Analysis, PYZ, EXE, COLLECT
from PyInstaller.utils.hooks import collect_all, collect_submodules


ROOT = Path(SPECPATH)
frontend = ROOT / "frontend"
ffmpeg_dir = ROOT / "vendor" / "ffmpeg"

datas = [
    (str(frontend), "frontend"),
    (str(ROOT / "README.md"), "."),
    (str(ROOT / "LICENSE"), "."),
]
binaries = []
hiddenimports = ["tkinter", *collect_submodules("tkinter")]

for package in ("flask", "yt_dlp", "rapidfuzz", "spotipy"):
    package_datas, package_binaries, package_hiddenimports = collect_all(package)
    datas.extend(package_datas)
    binaries.extend(package_binaries)
    hiddenimports.extend(package_hiddenimports)

if ffmpeg_dir.is_dir():
    binaries.extend(
        [
            (str(ffmpeg_dir / "ffmpeg.exe"), "ffmpeg"),
            (str(ffmpeg_dir / "ffprobe.exe"), "ffmpeg"),
        ]
    )

analysis = Analysis(
    [str(ROOT / "main.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(analysis.pure)
exe = EXE(
    pyz,
    analysis.scripts,
    analysis.binaries,
    analysis.datas,
    [],
    name="ctrl-shift-mp3",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    icon=str(ROOT / "assets" / "ctrl-shift-mp3.ico"),
    version=str(ROOT / "version_info.txt"),
)
coll = COLLECT(
    exe,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    name="ctrl-shift-mp3",
)
