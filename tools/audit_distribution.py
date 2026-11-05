"""Audit local release archives without extracting or executing their contents."""
from __future__ import annotations

import json
from pathlib import Path, PurePosixPath
import sys
import tarfile
import zipfile

FORBIDDEN_NAMES = {"native_tables.json", "channel_addresses.json", "native-codec-goldens.json", ".env", ".pypirc"}
FORBIDDEN_PARTS = {".git", ".private", ".native-profiles", "UserData", "用户数据", "__pycache__"}
FORBIDDEN_SUFFIXES = {".exe", ".ady", ".oca", ".awb", ".npz", ".npy", ".log", ".pyc"}
WHEEL_REQUIRED = {"audioseyx/__init__.py", "audioseyx/__main__.py", "audioseyx/synthetic_hashes.json",
                  "audioseyx/web/index.html", "audioseyx/web/app.js", "audioseyx/web/analysis.js",
                  "audioseyx/web/downloads.js", "audioseyx/web/joint.js", "audioseyx/web/layouts.js", "audioseyx/web/polarity.js", "audioseyx/web/transfer.js", "audioseyx/web/receiver-calibration.js", "audioseyx/web/clock.js", "audioseyx/web/verification.js", "audioseyx/web/curve-profiles.js", "audioseyx/web/style.css"}


def check_names(names):
    for name in names:
        path = PurePosixPath(name)
        if path.is_absolute() or ".." in path.parts or "\\" in name:
            raise ValueError("Unsafe archive path: " + name)
        if path.name in FORBIDDEN_NAMES or set(path.parts) & FORBIDDEN_PARTS or path.suffix.lower() in FORBIDDEN_SUFFIXES:
            raise ValueError("Excluded private or generated file in distribution: " + name)


def audit(path):
    path = Path(path)
    if path.suffix == ".whl":
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            check_names(names)
            if len(names) != len(set(names)) or archive.testzip() is not None:
                raise ValueError("Duplicate entries or corrupt wheel")
            missing = WHEEL_REQUIRED - set(names)
            if missing:
                raise ValueError("Wheel assets missing: " + ", ".join(sorted(missing)))
            if not any(name.endswith("/licenses/LICENSE") for name in names):
                raise ValueError("MIT license missing from wheel metadata")
            if not any(name.endswith("/third_party/eqx-LICENSE.txt") for name in names):
                raise ValueError("Upstream license missing from wheel metadata")
            if any(PurePosixPath(name).suffix in {".mqx", ".mdat"} for name in names):
                raise ValueError("Test fixtures do not belong in the wheel")
    elif path.name.endswith(".tar.gz"):
        with tarfile.open(path, "r:gz") as archive:
            members = archive.getmembers()
            names = [member.name for member in members]
            check_names(names)
            if len(names) != len(set(names)) or any(member.issym() or member.islnk() for member in members):
                raise ValueError("Duplicate entries or links in source archive")
            root = names[0].split("/", 1)[0]
            required = {root + "/pyproject.toml", root + "/LICENSE", root + "/docs/conf.py",
                        root + "/tools/audit_distribution.py", root + "/examples/manifest.json"}
            if not required <= set(names):
                raise ValueError("Source archive is incomplete")
    else:
        raise ValueError("Unsupported release archive: " + path.name)
    return {"file": path.name, "files": len(names), "bytes": path.stat().st_size, "passed": True}


if __name__ == "__main__":
    directory = Path(sys.argv[1] if len(sys.argv) > 1 else "dist")
    paths = sorted(directory.glob("*.whl")) + sorted(directory.glob("*.tar.gz"))
    if not paths or not any(p.suffix == ".whl" for p in paths) or not any(p.name.endswith(".tar.gz") for p in paths):
        raise SystemExit("A wheel and source archive are both required")
    print(json.dumps({"all_passed": True, "archives": [audit(path) for path in paths]}, indent=2))
