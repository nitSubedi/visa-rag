# PyInstaller spec for the desktop app. Built by packaging/build_macos.sh, which first
# puts the runtime (llama.cpp's server + the two model files) in build/runtime.
# ruff: noqa
from pathlib import Path

ROOT = Path(SPECPATH).resolve().parent
RUNTIME = ROOT / "build" / "runtime"

datas = [
    (str(ROOT / "src" / "visa" / "ui"), "visa/ui"),
    (str(ROOT / "sources"), "sources"),
    (str(ROOT / "register"), "register"),
    (str(ROOT / "bulletin"), "bulletin"),
    (str(RUNTIME / "models"), "runtime/models"),
    (str(ROOT / "build" / "seed"), "seed"),
]
# llama.cpp's server and the libraries released with it, kept together and executable.
binaries = [(str(p), "runtime/bin") for p in sorted((RUNTIME / "bin").iterdir()) if p.is_file()]

a = Analysis(
    [str(ROOT / "packaging" / "visa_app.py")],
    pathex=[str(ROOT / "src")],
    binaries=binaries,
    datas=datas,
    hiddenimports=["webview.platforms.cocoa"],
    excludes=["tkinter", "matplotlib", "IPython", "pytest"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="Visa Research",
          console=False, codesign_identity=None, target_arch="arm64")
coll = COLLECT(exe, a.binaries, a.datas, name="Visa Research")
app = BUNDLE(
    coll,
    name="Visa Research.app",
    bundle_identifier="io.github.nitsubedi.visa-research",
    info_plist={
        "CFBundleShortVersionString": "0.1.0",
        "CFBundleDisplayName": "Visa Research",
        "LSMinimumSystemVersion": "12.0",
        "NSHighResolutionCapable": True,
    },
)
