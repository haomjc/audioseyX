"""Load an explicitly supplied native-coefficient synthesis model as data only.

No legacy decoder is guessed. A supplied model is evidence supplied by its
author, not hardware validation performed by audioseyX.
"""
from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
import zipfile

import numpy as np
from scipy import sparse

from .capabilities import capability


def _array(archive, name, limit):
    info = archive.getinfo(name + ".npy")
    if info.file_size > limit:
        raise ValueError("Native synthesis array exceeds its memory budget")
    raw = archive.read(info)
    stream = io.BytesIO(raw)
    version = np.lib.format.read_magic(stream)
    if version not in [(1, 0), (2, 0)]:
        raise ValueError("Unsupported native synthesis NPY version")
    reader = np.lib.format.read_array_header_1_0 if version == (1, 0) else np.lib.format.read_array_header_2_0
    shape, _, dtype = reader(stream, max_header_size=1024)
    if len(shape) != 1 or dtype.kind not in "fiu" or dtype.itemsize > 8 or shape[0] < 1:
        raise ValueError("Invalid native synthesis array type or shape")
    expected = shape[0] * dtype.itemsize
    if expected > limit or len(raw) - stream.tell() != expected:
        raise ValueError("Native synthesis array header does not match its payload")
    values = np.frombuffer(raw, dtype=dtype, offset=stream.tell()).copy()
    if not np.isfinite(values).all():
        raise ValueError("Nonfinite native synthesis data")
    return values


def load_realization(path, tier, model=None, memory_mib=512):
    path = Path(path).expanduser().resolve()
    if not path.is_file() or path.stat().st_size > 1024 * 1024:
        raise ValueError("Native synthesis manifest is missing or too large")
    raw_manifest = path.read_bytes()
    document = json.loads(raw_manifest)
    if not isinstance(document,dict) or not isinstance(document.get("profiles"),dict):
        raise ValueError("Native synthesis manifest and profiles must be JSON objects")
    if document.get("schema") != "audioseyx-native-synthesis-1" or document.get("tier") != capability(tier).tier:
        raise ValueError("Native synthesis schema or Audyssey tier mismatch")
    if not document.get("model") or (model and document["model"] != model):
        raise ValueError("Native synthesis model must match the measurement project exactly")
    if not np.isfinite(memory_mib) or not 16 <= memory_mib <= 32768:
        raise ValueError("Invalid native synthesis memory budget")
    limit = int(memory_mib * 1024 ** 2)
    loaded = {}
    used_bytes = 0
    for role, entry in document.get("profiles", {}).items():
        if role not in ["main", "sub"] or not isinstance(entry, dict):
            raise ValueError("Invalid native synthesis output role")
        name = entry.get("file", "")
        if not isinstance(name, str) or Path(name).name != name or "\\" in name or not name.endswith(".npz"):
            raise ValueError("Native synthesis files must be named local NPZ files")
        target = (path.parent / name).resolve()
        if target.parent != path.parent or not target.is_file() or target.stat().st_size > limit:
            raise ValueError("Native synthesis file is missing, outside its directory or too large")
        with target.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        if digest != entry.get("sha256"):
            raise ValueError("Native synthesis checksum mismatch")
        with zipfile.ZipFile(target) as archive:
            names = archive.namelist()
            if set(names) != {n + ".npy" for n in ["data", "indices", "indptr", "shape", "identity"]} or len(names) != 5:
                raise ValueError("Unexpected or duplicate native synthesis archive entries")
            total = sum(info.file_size for info in archive.infolist())
            # Account for decompressed arrays and float64/int64 CSR conversion
            # at their peak, rather than just the compressed file size.
            if 3*total + used_bytes > limit:
                raise ValueError("Native synthesis models exceed the explicit memory budget")
            arrays = {name: _array(archive, name, limit) for name in ["data", "indices", "indptr", "shape", "identity"]}
        shape = arrays["shape"]
        length, rate, packed = capability(tier).shape("SW1" if role == "sub" else "FL")
        if shape.dtype.kind not in "iu" or tuple(shape) != (length, packed):
            raise ValueError("Native synthesis dimensions do not match the writable coefficient budget")
        data, indices, indptr = arrays["data"], arrays["indices"], arrays["indptr"]
        if indices.dtype.kind not in "iu" or indptr.dtype.kind not in "iu" or len(data) != len(indices) or len(indptr) != length + 1:
            raise ValueError("Invalid native synthesis CSR arrays")
        if indptr[0] != 0 or indptr[-1] != len(data) or np.any(indptr[1:] < indptr[:-1]) or np.any(indptr > len(data)):
            raise ValueError("Invalid native synthesis row pointers")
        if np.any(indices >= packed) or np.any(indices < 0) or arrays["identity"].shape != (packed,):
            raise ValueError("Invalid native synthesis coefficient indices or identity")
        matrix = sparse.csr_matrix((data.astype(float), indices.astype(np.int64), indptr.astype(np.int64)), shape=(length, packed))
        matrix.sum_duplicates()
        matrix.eliminate_zeros()
        if not np.isfinite(matrix.data).all():
            raise ValueError("Native synthesis matrix overflows while merging duplicate entries")
        identity_parameters = arrays["identity"].astype(float)
        reserved=matrix.getnnz(axis=0)==0
        if np.any(identity_parameters[reserved]!=0):
            raise ValueError("Unused native synthesis columns must have zero identity parameters")
        identity = matrix @ identity_parameters
        if not np.isfinite(identity).all() or not np.any(identity):
            raise ValueError("Native synthesis identity response is invalid")
        loaded[role] = {"matrix": matrix, "identity_parameters":identity_parameters, "identity":identity,
                        "sample_rate":rate, "coefficients":packed, "reserved_zero_columns":int(np.sum(reserved)),"sha256":digest}
        used_bytes += matrix.data.nbytes+matrix.indices.nbytes+matrix.indptr.nbytes+identity.nbytes+identity_parameters.nbytes
    if not loaded:
        raise ValueError("Native synthesis manifest contains no output models")
    return {"profiles":loaded, "manifest_sha256":hashlib.sha256(raw_manifest).hexdigest(),
            "model":document["model"], "firmware":document.get("firmware"),
            "evidence":document.get("evidence", {}), "loaded_bytes":used_bytes,
            "hardware_verified_by_audioseyx":False,
            "notice":"The synthesis model and any validation evidence were supplied by the user. AudioseyX has not independently verified this model on the receiver."}
