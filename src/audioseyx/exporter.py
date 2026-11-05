from __future__ import annotations

import json
from pathlib import Path
import zipfile
import numpy as np
from scipy.io import wavfile

from .native import compile_channel
from .importers import save_project
from .compat import CompatibilityDataUnavailable
from .coefficient_transport import quantization_report
from .calibration_curves import CURVE_SPECS, curve_designs, design_report


def _write_design(project, design, directory, joint=None, *, curve_name='Flat', include_measurements=True):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    files = []
    if include_measurements:
        save_project(project, directory / "measurements.awb.json")
        files.append(directory / 'measurements.awb.json')
    report = design_report(design)
    report['calibration_curve'] = curve_name
    report["joint_sub_alignment"] = joint
    report["channels"] = []
    native = {"schema": "audyssey-native-candidate-1", "tier": design["tier"],
              'calibration_curve':curve_name, 'curve_id':CURVE_SPECS[curve_name]['curve_id'],
              "origin": project.origin, "receiver_upload_ready": False, "hardware_verified": False,
              "encoding_available": True, "float32_serialization_available": True, "channels": []}
    for item in design["channels"]:
        try:
            if design.get('joint_optimization',{}).get('design_space')=='expanded_causal_fir_reference':
                raise CompatibilityDataUnavailable('Expanded joint FIR references are not native coefficient designs. Supply a native synthesis model; hardware validation remains required.')
            compiled = compile_channel(item, design["tier"])
            try:
                compiled["float32_precision"] = {"available": True,
                    **quantization_report(compiled["coefficients"])}
            except ValueError as error:
                # General reference WAVs remain useful even when a supplied
                # native parameterization cannot serialize to Float32.
                native["float32_serialization_available"] = False
                compiled["float32_precision"] = {"available": False, "reason": str(error)}
        except CompatibilityDataUnavailable as error:
            native["encoding_available"] = False
            native["float32_serialization_available"] = False
            native["encoding_unavailable_reason"] = str(error)
            compiled = {"channel": item["channel"], "encoding_available": False, "reason": str(error)}
        channel = item["channel"]
        if not channel.replace("_", "").isalnum():
            raise ValueError('The channel name cannot be used as an export filename')
        wavfile.write(directory / (channel + "-filter.wav"), item["sample_rate"], item["taps"].astype(np.float32))
        files.append(directory / (channel + '-filter.wav'))
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
    files.extend(directory/name for name in ['design-report.json','design-report.md','native-candidate.json'])
    return files


def export_bundle(project, design, directory, joint=None):
    directory = Path(directory)
    curves = curve_designs(design) if design.get('curve_designs') is not None else {'Flat':design}
    files = _write_design(project, design, directory, joint)
    if 'Reference' in curves:
        reference_directory = directory/'curves'/'Reference'
        files.extend(_write_design(project, curves['Reference'], reference_directory,
                                   curve_name='Reference', include_measurements=False))
        path = directory/'native-candidate.json'
        candidate = json.loads(path.read_text(encoding='utf-8'))
        candidate.update(schema='audyssey-native-candidate-2',
                         curve_configuration=design['curve_configuration'],
                         additional_curve_files={'Reference':'curves/Reference/native-candidate.json'})
        path.write_text(json.dumps(candidate, ensure_ascii=False, allow_nan=False),encoding='utf-8')
    archive = directory.with_suffix(".zip")
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as output:
        for path in files:
            output.write(path, path.relative_to(directory).as_posix())
    return archive
