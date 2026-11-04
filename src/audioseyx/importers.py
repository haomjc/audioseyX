from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path

import numpy as np
from scipy.io import wavfile

from .capabilities import identify_tier
from .model import Measurement, Project, channel_and_position


def value(mapping, *names, default=None):
    if not isinstance(mapping, dict):
        return default
    lowered = {str(k).casefold(): v for k, v in mapping.items()}
    for name in names:
        if name.casefold() in lowered:
            return lowered[name.casefold()]
    return default


def _pairs(data):
    return data.items() if isinstance(data, dict) else enumerate(data) if isinstance(data, list) else []


def import_json(path: Path):
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict):
        raise ValueError('The project root must be an object')
    if value(data, "schema") == "audyssey-workbench-project-1":
        measurements = [Measurement(**m) for m in data["measurements"]]
        return Project(data.get("name", path.stem), measurements, data.get("origin", "unverified"),
                       data.get("model"), identify_tier(data.get("tier")), data.get("metadata", {}), data.get("warnings", []))
    warnings = []
    rows = []
    if value(data, "_measurements") is not None:
        channel_map = value(data, "_channelDataMap", default={})
        for index, item in enumerate(value(data, "_measurements", default=[])):
            if value(item, "Enabled", default=True) is False:
                continue
            channel_id = value(item, "ChannelGuid")
            info = value(channel_map.get(channel_id, {}), "Metadata", default={})
            designation = value(info, "AvrOriginatingDesignation", "DisplayName", default=str(channel_id))
            channel, _ = channel_and_position(designation)
            raw = base64.b64decode(value(item, "Data", default=""), validate=True)
            if len(raw) % 4:
                raise ValueError('The MQX measurement payload is not a float32 array')
            samples = np.frombuffer(raw, dtype="<f4").astype(float)
            rows.append(Measurement(channel, str(value(item, "PositionGuid", default=index)),
                                    f"{channel} · {index + 1}", 48000, samples,
                                    timing_reference="mqx_system_delay_unverified",
                                    metadata={"system_delay_samples": value(item, "SystemDelay"),
                                              "microphone": value(item, "MicCorrectionName")}))
        warnings.append('MQX is decoded using the observed 48 kHz float32 format. Generic microphone correction is not assumed; the timing reference needs confirmation.')
    else:
        channels = value(data, "detectedChannels")
        if channels is None:
            if value(data, "channels") is not None:
                raise ValueError('This OCA contains filters, not raw measurements. Import ADY, MQX, MDAT or an impulse response.')
            raise ValueError('No importable measurements were found')
        fs = float(value(data, "sampleRate", "sample_rate", default=48000))
        for index, item in _pairs(channels):
            channel, _ = channel_and_position(value(item, "commandId", "channelName", default=str(index)))
            raw_positions = value(item, "rawPositionIRs")
            responses = raw_positions if isinstance(raw_positions, list) and raw_positions else value(item, "responseData", default={})
            for position, samples in _pairs(responses):
                rows.append(Measurement(channel, "p" + str(position), f"{channel}{position}", fs,
                                        np.asarray(samples, dtype=float), timing_reference="ady_unverified"))
        warnings.append('ADY positions are retained without peak alignment or averaging. A missing sample rate is recorded as the commonly observed 48 kHz for this file family.')
    if not rows:
        raise ValueError('The file contains no enabled valid measurements')
    origin = "synthetic" if value(data, "synthetic", default=False) is True else "unverified"
    return Project(path.stem, rows, origin, value(data, "targetModelName", "model"),
                   identify_tier(value(data, "enMultEQType", "eqType", "MultEQType")),
                   {"source": path.name, "source_sha256": hashlib.sha256(path.read_bytes()).hexdigest()}, warnings)


def import_file(path, origin=None):
    path = Path(path)
    if path.stat().st_size > 128 * 1024 * 1024:
        raise ValueError('The maximum supported measurement file size is 128 MiB')
    suffix = path.suffix.lower()
    if suffix == ".awb":
        from .archive import load_archive
        project = load_archive(path)
    elif suffix == ".mdat":
        from .mdat_import import import_mdat
        project = import_mdat(path)
    elif suffix == ".wav":
        fs, samples = wavfile.read(path)
        if samples.ndim > 1:
            raise ValueError('Export one mono impulse-response WAV per channel; channel mapping is not guessed')
        if np.issubdtype(samples.dtype, np.integer):
            samples = samples.astype(float) / max(abs(np.iinfo(samples.dtype).min), np.iinfo(samples.dtype).max)
        channel, position = channel_and_position(path.stem)
        project = Project(path.stem, [Measurement(channel, position, path.stem, fs, samples)],
                          warnings=['WAV does not establish channel, position or timing-reference metadata. Check these in the workbench.'])
    else:
        project = import_json(path)
    manifest = Path(__file__).with_name("synthetic_hashes.json")
    if manifest.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() in json.loads(manifest.read_text(encoding="utf-8")):
        project.origin = "synthetic"
        project.warnings.append('This file matches a known public synthetic fixture and cannot be transferred as real calibration.')
    if origin and project.origin == "unverified":
        project.origin = origin
    return project


def save_project(project, path):
    rows = []
    for measurement in project.measurements:
        rows.append({key: array.tolist() if isinstance(array, np.ndarray) else array
                     for key, array in vars(measurement).items()})
    document = {"schema": "audyssey-workbench-project-1", "name": project.name,
                "origin": project.origin, "model": project.model, "tier": project.tier,
                "metadata": project.metadata, "warnings": project.warnings, "measurements": rows}
    Path(path).write_text(json.dumps(document, ensure_ascii=False, allow_nan=False), encoding="utf-8")
