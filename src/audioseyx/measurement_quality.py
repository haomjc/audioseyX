"""Measurement-dependent analysis, without modifying or peak-aligning raw IRs."""
from __future__ import annotations

from collections import defaultdict
import numpy as np

from .dsp import spectrum


def frequency_dependent_response(measurement, frequencies, cycles=6.0, window_floor=1e-10, reference_sample=None):
    """Evaluate a peak-centred Gaussian time window separately at every bin.

    ``cycles / f`` is the window's amplitude FWHM. Contributions below the
    specified Gaussian amplitude floor are omitted, with an explicit absolute
    error bound. There is no octave-bank averaging or interpolation between
    differently windowed frequency responses.
    """
    if measurement.ir is None:
        raise ValueError("Frequency-dependent windowing requires the original impulse response")
    f = np.asarray(frequencies, dtype=float)
    if f.ndim != 1 or not np.isfinite(f).all() or np.any(f <= 0) or np.any(f > measurement.sample_rate / 2):
        raise ValueError("Invalid frequency-dependent window frequency grid")
    if not np.isfinite(cycles) or not .5 <= cycles <= 100 or not 1e-15 <= window_floor <= 1e-6:
        raise ValueError("Invalid frequency-dependent window settings")
    raw = measurement.ir
    rate = measurement.sample_rate
    peak = int(np.argmax(np.abs(raw))) if reference_sample is None else reference_sample
    if isinstance(peak,bool) or not isinstance(peak,(int,np.integer)) or not 0<=peak<len(raw):
        raise ValueError("Invalid frequency-dependent window reference sample")
    sigma_factor = cycles / np.sqrt(8 * np.log(2))
    radius_factor = np.sqrt(-2 * np.log(window_floor))
    response = np.empty(len(f), dtype=complex)
    for index, frequency in enumerate(f):
        sigma = sigma_factor / frequency
        radius = int(np.ceil(radius_factor * sigma * rate))
        begin, end = max(0, peak - radius), min(len(raw), peak + radius + 1)
        local_time = (np.arange(begin, end) - peak) / rate
        window = np.exp(-.5 * (local_time / sigma) ** 2)
        response[index] = np.dot(raw[begin:end] * window, np.exp(-2j * np.pi * frequency * local_time))
    response *= np.exp(-2j * np.pi * f * (measurement.start_time_s + peak / rate))
    return response, {"method": "per_frequency_gaussian_fwhm_cycles", "cycles": float(cycles),
                      "frequency_bins_evaluated": len(f), "window_amplitude_floor": window_floor,
                      "absolute_truncation_error_bound": float(window_floor * np.sum(np.abs(raw))),
                      "original_start_time_s": measurement.start_time_s, "peak_sample_reference": peak,
                      "reference_source":"largest_peak" if reference_sample is None else "explicit_analysis_reference",
                      "raw_ir_modified": False}


def repeat_diagnostics(measurements):
    groups = defaultdict(list)
    for measurement in measurements:
        groups[(measurement.channel, measurement.position)].append(measurement)
    rows = []
    for (channel, position), group in sorted(groups.items()):
        if len(group) < 2:
            continue
        low=max([30]+[float(m.frequency[0]) for m in group if m.ir is None])
        high=min([250]+[m.sample_rate/2 if m.ir is not None else min(m.sample_rate/2,float(m.frequency[-1])) for m in group])
        if low>=high:
            rows.append({"channel":channel,"position":position,"repeats":len(group),"available":False,
                         "reason":"No common measured coverage within the repeat-diagnostic band", "peak_time_spread_ms":None,
                         "median_level_spread_db":None,"complex_repeat_agreement_mean":None,
                         "complex_repeat_agreement_min":None,"measurements_discarded":0})
            continue
        grid=np.geomspace(low,high,96)
        responses = np.asarray([spectrum(m, grid) for m in group])
        power = np.mean(np.abs(responses) ** 2, axis=0)
        agreement = np.abs(np.mean(responses, axis=0)) ** 2 / np.maximum(power, 1e-30)
        arrivals = [m.start_time_s + np.argmax(np.abs(m.ir)) / m.sample_rate for m in group if m.ir is not None]
        levels = np.median(20 * np.log10(np.maximum(np.abs(responses), 1e-15)), axis=1)
        phase_known=all(m.ir is not None or m.phase_deg is not None for m in group)
        rows.append({"channel": channel, "position": position, "repeats": len(group),
                     "available":True,"common_diagnostic_band_hz":[low,high],"measured_phase_available":phase_known,
                     "peak_time_spread_ms": float(np.ptp(arrivals) * 1000) if len(arrivals) == len(group) else None,
                     "median_level_spread_db": float(np.ptp(levels)),
                     "complex_repeat_agreement_mean": float(np.mean(agreement)) if phase_known else None,
                     "complex_repeat_agreement_min": float(np.min(agreement)) if phase_known else None,
                     "measurements_discarded": 0})
    return {"groups": rows, "clock_drift_corrected": False,
            "notice": "Repeat agreement is a consistency diagnostic, not a cross-spectral coherence or clock-drift estimate. Clock calibration requires original recordings and independent timing markers."}
