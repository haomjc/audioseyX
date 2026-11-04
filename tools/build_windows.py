"""Build a portable Windows executable from public source only."""
from pathlib import Path
import os
import subprocess
import sys
import shutil

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "src/audioseyx"
if sys.platform != "win32":
    raise SystemExit("The Windows build must run on Windows")
for name in ["native_tables.json", "channel_addresses.json"]:
    if (PACKAGE / name).exists():
        raise SystemExit("Remove excluded local compatibility resources before building")
BUILD = ROOT / "build/pyinstaller"
BUILD.mkdir(parents=True, exist_ok=True)
os.environ["PYINSTALLER_CONFIG_DIR"] = str(BUILD / "cache")
args = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--onefile", "--windowed",
        "--name", "audioseyX", "--distpath", str(ROOT / "dist/windows"),
        "--workpath", str(BUILD / "work"), "--specpath", str(BUILD), "--paths", str(ROOT / "src"),
        "--add-data", str(PACKAGE / "web") + ";audioseyx/web",
        "--add-data", str(PACKAGE / "synthetic_hashes.json") + ";audioseyx"]
args += ["--recursive-copy-metadata", "audioseyX"]
for name in ["matplotlib", "pandas", "IPython", "torch", "tensorflow", "jax", "jaxlib", "cupy", "pytest", "tkinter"]:
    args += ["--exclude-module", name]
args += [str(ROOT / "tools/windows_entry.py")]
subprocess.run(args, cwd=ROOT, check=True)

# Provide readable license copies beside the executable as well as embedded
# distribution metadata. Only known license/notice files are copied.
from PyInstaller.utils.hooks import copy_metadata
license_root = ROOT / "dist/windows/licenses"
metadata = copy_metadata("audioseyX", recursive=True) + copy_metadata("pyinstaller")
for source, destination in metadata:
    source = Path(source)
    for file in source.rglob("*"):
        if file.is_file() and any(word in file.name.lower() for word in ["license", "copying", "notice", "copyright"]):
            target = license_root / destination / file.relative_to(source)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(file, target)
python_license = Path(sys.base_prefix) / "LICENSE.txt"
if python_license.is_file():
    license_root.mkdir(parents=True, exist_ok=True)
    shutil.copy2(python_license, license_root / "Python-LICENSE.txt")
