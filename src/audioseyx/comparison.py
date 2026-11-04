from __future__ import annotations

from collections import defaultdict
import numpy as np

from .dsp import spectrum


def compare_projects(before, after):
    if after.origin == "native_avr" and after.metadata.get("normal_playback_verified") is not True:
        raise ValueError('These are receiver calibration-mode data, not confirmed corrected playback. Verify through the normal playback path; do not treat another calibration sweep as an after-correction measurement.')
    a_groups, b_groups = defaultdict(list), defaultdict(list)
    for project, groups in [(before, a_groups), (after, b_groups)]:
        for m in project.measurements:
            groups[(m.channel, m.position)].append(m)
    rows = []
    for key in sorted(a_groups.keys() & b_groups.keys()):
        a_rows, b_rows = a_groups[key], b_groups[key]
        all_rows = a_rows + b_rows
        low = max([20] + [float(m.frequency[0]) for m in all_rows if m.ir is None])
        high = min([200] + [m.sample_rate * .45 if m.ir is not None else float(m.frequency[-1]) for m in all_rows])
        if high / max(low, 1e-12) < 1.5:
            continue
        grid = np.geomspace(low, high, 144)
        a = np.median([20 * np.log10(np.maximum(np.abs(spectrum(m, grid)), 1e-12)) for m in a_rows], axis=0)
        b = np.median([20 * np.log10(np.maximum(np.abs(spectrum(m, grid)), 1e-12)) for m in b_rows], axis=0)
        units = {m.metadata.get("level_unit") for m in all_rows}
        chains = {m.metadata.get("measurement_chain_id") for m in all_rows}
        absolute = units == {"dB_SPL"} and all(m.metadata.get("absolute_level_calibrated") is True for m in all_rows)
        same_chain = len(units) == 1 and None not in units and len(chains) == 1 and None not in chains and "" not in chains
        comparable = absolute or same_chain
        a_shape, b_shape = a - np.median(a), b - np.median(b)
        rows.append({"channel": key[0], "position": key[1], "before_repeats": len(a_rows), "after_repeats": len(b_rows),
                     "level_comparable": comparable, "mean_level_delta_db": float(np.mean(b - a)) if comparable else None,
                     "level_reason": 'Comparable level units and calibration or measurement-chain metadata are recorded' if comparable else 'Comparable units and calibration or chain metadata are missing; only response shape is compared',
                     "ripple_before_db": float(np.std(a)), "ripple_after_db": float(np.std(b)),
                     "shape_delta_rms_db": float(np.sqrt(np.mean((b_shape - a_shape) ** 2))),
                     "frequency": grid.tolist(), "before": a_shape.tolist(), "after": b_shape.tolist()})
    if not rows:
        raise ValueError('No matching channel and position has a sufficient common frequency band; check the names')
    demo = before.origin == "synthetic" or after.origin == "synthetic"
    return {"matched": len(rows), "rows": rows, "after_origin": after.origin,
            "notice": ('This is a synthetic demonstration comparison, not an acoustic measurement.' if demo else 'This compares imported measurements.') +
            'Repeats are combined by median magnitude within each channel and position. Shape is median-level aligned over the common band. Level changes require comparable units and measurement chains; lower level alone does not prove shorter decay.'}
