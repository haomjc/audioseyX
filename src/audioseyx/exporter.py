from __future__ import annotations

import json
from pathlib import Path
import zipfile
import numpy as np
from scipy.io import wavfile

from .native import compile_channel
from .importers import save_project
from .compat import CompatibilityDataUnavailable


def export_bundle(project, design, directory, joint=None):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    save_project(project, directory / "measurements.awb.json")
    report = {key: value for key, value in design.items() if key != "channels"}
    report["joint_sub_alignment"] = joint
    report["channels"] = []
    native = {"schema": "audyssey-native-candidate-1", "tier": design["tier"],
              "origin": project.origin, "receiver_upload_ready": False, "hardware_verified": False,
              "encoding_available": True, "channels": []}
    for item in design["channels"]:
        try:
            if design.get('joint_optimization',{}).get('design_space')=='expanded_causal_fir_reference':
                raise CompatibilityDataUnavailable('Expanded joint FIR references are not native coefficient designs. Supply a native synthesis model; hardware validation remains required.')
            compiled = compile_channel(item, design["tier"])
        except CompatibilityDataUnavailable as error:
            native["encoding_available"] = False
            native["encoding_unavailable_reason"] = str(error)
            compiled = {"channel": item["channel"], "encoding_available": False, "reason": str(error)}
        channel = item["channel"]
        if not channel.replace("_", "").isalnum():
            raise ValueError('The channel name cannot be used as an export filename')
        wavfile.write(directory / (channel + "-filter.wav"), item["sample_rate"], item["taps"].astype(np.float32))
        native["channels"].append({key: value.tolist() if isinstance(value, np.ndarray) else value for key, value in compiled.items()})
        report["channels"].append({key: value for key, value in item.items() if key != "taps" and not key.startswith("_")})
    (directory / "design-report.json").write_text(json.dumps(report, ensure_ascii=False, allow_nan=False, indent=2), encoding="utf-8")
    (directory / "native-candidate.json").write_text(json.dumps(native, ensure_ascii=False, allow_nan=False), encoding="utf-8")
    lines = ['# Filter design report', "", f"Project: {project.name}", f"Target tier: {design['tier']}",
             f"Data origin: {project.origin}", "", 'Offline predictions without hardware validation. Candidate encoding is not firmware or a verified calibration for direct transfer.', "",
             '| Channel | Design sample rate | Design length | Target error: before to after | Phase mode |', "|---|---:|---:|---|---|"]
    if not native["encoding_available"]:
        lines[7:7] = ['Native encoding was not generated: ' + native["encoding_unavailable_reason"], ""]
    if design.get("joint_optimization"):
        optimization=design["joint_optimization"]
        lines[7:7]=["Joint design space: "+optimization["design_space"],
                     optimization["native_realization_notice"],
                     "Program-input sums, separate-position errors and holdout results are recorded in design-report.json.",""]
    for item in design["channels"]:
        m = item["metrics"]
        lines.append(f"| {item['channel']} | {item['sample_rate']} | {item['design_length']} | {m['rms_before_db']:.2f} → {m['rms_after_db']:.2f} dB | {m['phase_mode']} |")
    lines += ["", 'Error comparisons use the same input and target. They do not establish audible improvement or a room reverberation change.', "", '## Limitations']
    for item in design["channels"]:
        lines += [f"- {item['channel']}：{text}" for text in item["limitations"]]
    if joint:
        lines += ["", '## Shared-subwoofer alignment suggestion', "", "```json", json.dumps(joint, ensure_ascii=False, indent=2), "```"]
    (directory / 'design-report.md').write_text("\n".join(lines), encoding="utf-8")
    archive = directory.with_suffix(".zip")
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as output:
        for path in directory.iterdir():
            if path.is_file():
                output.write(path, path.name)
    return archive
