from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
import re
import tempfile
import zipfile

import numpy as np

from .model import Measurement, Project

ARRAY_FIELDS = ("ir", "frequency", "magnitude_db", "phase_deg")
MAX_TOTAL = 256 * 1024 * 1024


def save_archive(project, target):
    target = Path(target)
    if not 1 <= len(project.measurements) <= 1024:
        raise ValueError('A project must contain 1-1024 measurements')
    document = {"schema": "audyssey-workbench-archive-1", "name": project.name,
                "origin": project.origin, "model": project.model, "tier": project.tier,
                "metadata": project.metadata, "warnings": project.warnings, "measurements": []}
    with tempfile.NamedTemporaryFile(dir=target.parent, suffix=".awb.tmp", delete=False) as temporary:
        temporary_path = Path(temporary.name)
    try:
        total = 0
        with zipfile.ZipFile(temporary_path, "w", zipfile.ZIP_DEFLATED) as archive:
            for index, measurement in enumerate(project.measurements):
                row = vars(measurement).copy()
                for field in ARRAY_FIELDS:
                    if row[field] is None:
                        continue
                    buffer = io.BytesIO()
                    np.save(buffer, np.asarray(row[field], dtype="<f8"), allow_pickle=False)
                    raw = buffer.getvalue()
                    total += len(raw)
                    if total > MAX_TOTAL:
                        raise ValueError('Project arrays exceed 256 MiB; save smaller batches')
                    name = f"arrays/{index:04d}-{field}.npy"
                    archive.writestr(name, raw)
                    row[field] = {"array": name, "sha256": hashlib.sha256(raw).hexdigest()}
                document["measurements"].append(row)
            manifest = json.dumps(document, ensure_ascii=False, allow_nan=False).encode("utf-8")
            if len(manifest) > 4 * 1024 * 1024:
                raise ValueError('Project metadata is too large')
            archive.writestr("manifest.json", manifest)
        if temporary_path.stat().st_size > 128 * 1024 * 1024:
            raise ValueError('The compressed project exceeds 128 MiB; save smaller batches')
        temporary_path.replace(target)
    finally:
        temporary_path.unlink(missing_ok=True)
    return target


def load_archive(path):
    try:
        with zipfile.ZipFile(path) as archive:
            entries = archive.infolist()
            names = [entry.filename for entry in entries]
            if len(names) != len(set(names)) or not 1 <= len(names) <= 4097:
                raise ValueError('The project contains duplicate entries or too many entries')
            if sum(entry.file_size for entry in entries) > MAX_TOTAL + 4 * 1024 * 1024:
                raise ValueError('The uncompressed project exceeds the size limit')
            if "manifest.json" not in names or archive.getinfo("manifest.json").file_size > 4 * 1024 * 1024:
                raise ValueError('The project manifest is missing or too large')
            if any(name != "manifest.json" and not re.fullmatch(r"arrays/\d{4}-(ir|frequency|magnitude_db|phase_deg)\.npy", name) for name in names):
                raise ValueError('The project contains an unexpected path and will not be extracted')
            data = json.loads(archive.read("manifest.json"))
            if data.get("schema") != "audyssey-workbench-archive-1":
                raise ValueError('Unsupported project archive version')
            rows = data["measurements"]
            if not isinstance(rows, list) or not 1 <= len(rows) <= 1024:
                raise ValueError('Invalid measurement count in project')
            used, measurements = {"manifest.json"}, []
            for original in rows:
                row = original.copy()
                for field in ARRAY_FIELDS:
                    ref = row.get(field)
                    if ref is None:
                        continue
                    if not isinstance(ref, dict) or set(ref) != {"array", "sha256"}:
                        raise ValueError('Invalid project array reference')
                    name = ref["array"]
                    if name not in names or name in used or not name.endswith("-" + field + ".npy"):
                        raise ValueError('A project array is missing, reused or has the wrong type')
                    if archive.getinfo(name).file_size > 34 * 1024 * 1024:
                        raise ValueError('An individual project array is too large')
                    raw = archive.read(name)
                    if hashlib.sha256(raw).hexdigest() != ref["sha256"]:
                        raise ValueError('Project array checksum mismatch')
                    # Check the header before allocation: a tiny malformed NPY must
                    # not be allowed to declare a multi-terabyte shape.
                    stream = io.BytesIO(raw)
                    version = np.lib.format.read_magic(stream)
                    if version not in [(1, 0), (2, 0)]:
                        raise ValueError('Unsupported project array format version')
                    reader = np.lib.format.read_array_header_1_0 if version == (1, 0) else np.lib.format.read_array_header_2_0
                    shape, fortran, dtype = reader(stream, max_header_size=1024)
                    if len(shape) != 1 or not 2 <= shape[0] <= 4_194_304 or dtype.kind != "f" or dtype.itemsize not in [4, 8]:
                        raise ValueError('Invalid project array type or shape')
                    if len(raw) - stream.tell() != shape[0] * dtype.itemsize:
                        raise ValueError('The declared array length does not match its payload')
                    row[field] = np.frombuffer(raw, dtype=dtype, count=shape[0], offset=stream.tell()).astype(float)
                    used.add(name)
                measurements.append(Measurement(**row))
            if used != set(names):
                raise ValueError('The project contains unreferenced data')
            return Project(str(data["name"]), measurements, data.get("origin", "unverified"),
                           data.get("model"), data.get("tier"), data.get("metadata", {}), data.get("warnings", []))
    except (zipfile.BadZipFile, KeyError, TypeError, OverflowError) as error:
        raise ValueError('Could not read the workbench project archive: ' + str(error)) from error
