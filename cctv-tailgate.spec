# PyInstaller spec for the CCTV Tailgate Windows app.
# Build on Windows:  pyinstaller cctv-tailgate.spec --noconfirm
# Output:            dist\CCTV-Tailgate\CCTV-Tailgate.exe
from pathlib import Path

from PyInstaller.utils.hooks import collect_all

# Data files shipped inside the bundle.
#  - web_dashboard.html must sit next to src/web_server.py (it is loaded via
#    Path(__file__).with_name(...)), so it is placed under "src".
#  - windows_config.yaml is seeded to config.yaml on first run by launcher.py.
datas = [
    ("src/web_dashboard.html", "src"),
    ("packaging/windows_config.yaml", "."),
]
binaries = []
hiddenimports = []

# Ship the detection model if present so the app works offline; otherwise
# Ultralytics downloads it on first launch.
if Path("yolo11n.pt").exists():
    datas += [("yolo11n.pt", ".")]

# Pull in everything these heavy packages need (data files, binaries, submodules).
for package in ("ultralytics", "torch", "torchvision", "cv2", "lap"):
    try:
        pkg_datas, pkg_binaries, pkg_hidden = collect_all(package)
        datas += pkg_datas
        binaries += pkg_binaries
        hiddenimports += pkg_hidden
    except Exception:
        # torchvision / lap are optional depending on the environment.
        pass

a = Analysis(
    ["launcher.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="CCTV-Tailgate",
    console=True,
    icon=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    name="CCTV-Tailgate",
)
