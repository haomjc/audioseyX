from __future__ import annotations

import itertools
import numpy as np
from scipy import signal
from scipy.fft import next_fast_len, rfft

from .dsp import min_phase_spectrum


def _db(values, power=False):
    return (10 if power else 20) * np.log10(np.maximum(values, 1e-30))


def _nullable(values, valid):
    return [float(v) if ok and np.isfinite(v) else None for v, ok in zip(values, valid)]


def _edc(samples):
    energy = np.cumsum(np.square(samples)[::-1])[::-1]
    return energy, _db(energy / max(float(energy[0]), 1e-30), True)


def _fit_decay(samples, fs, start_db, end_db):
    energy, curve = _edc(samples)
    result = {"available": False, "estimate_s": None, "fit_r2": None,
              "fit_start_s": None, "fit_end_s": None, "range_db": [start_db, end_db]}
    begin = np.flatnonzero(curve <= start_db)
    finish = np.flatnonzero(curve <= end_db)
    if not len(begin) or not len(finish):
        return {**result, "reason": 'The recording does not cover the required decay range'}
    a, b = int(begin[0]), int(finish[0])
    if b - a < max(20, int(fs * .01)) or b > len(samples) * .75:
        return {**result, "reason": 'The fitting interval is too short or too close to the end of the recording'}
    tail = samples[-max(16, len(samples) // 10):]
    noise_power = float(np.mean(tail ** 2))
    remaining_noise = noise_power * (len(samples) - b)
    snr = 10 * np.log10(max(float(energy[b]), 1e-30) / max(remaining_noise, 1e-30))
    # A tail estimate includes late reverberation too, so this deliberately errs
    # towards refusing a value when noise and truncated decay cannot be separated.
    if snr < 10:
        return {**result, "reason": 'The fit endpoint is less than 10 dB above the estimated remaining tail noise'}
    t = np.arange(a, b + 1) / fs
    slope, intercept = np.polyfit(t, curve[a:b + 1], 1)
    residual = curve[a:b + 1] - (slope * t + intercept)
    total = np.sum((curve[a:b + 1] - np.mean(curve[a:b + 1])) ** 2)
    r2 = 1 - np.sum(residual ** 2) / max(float(total), 1e-30)
    if slope >= 0 or r2 < .98:
        return {**result, "fit_r2": float(r2), "reason": 'The decay curve is not sufficiently linear'}
    estimate = -60 / slope
    if estimate <= 0 or not np.isfinite(estimate):
        return {**result, "reason": 'No valid decay slope was found'}
    return {**result, "available": True, "estimate_s": float(estimate), "fit_r2": float(r2),
            "fit_start_s": float(t[0]), "fit_end_s": float(t[-1]),
            "endpoint_snr_db": float(snr), "reason": 'Meets the current offline quality thresholds'}


def decay_analysis(samples, fs):
    energy, curve = _edc(samples)
    step = max(1, int(np.ceil(len(samples) / 1200)))
    indices = np.arange(0, len(samples), step)
    fits = {name: _fit_decay(samples, fs, a, b) for name, a, b in
            [("EDT", 0, -10), ("T20", -5, -25), ("T30", -5, -35)]}
    bands = []
    for centre in [125, 250, 500, 1000, 2000, 4000, 8000]:
        lo, hi = centre / np.sqrt(2), centre * np.sqrt(2)
        if hi >= fs * .46:
            continue
        if centre < 250:
            bands.append({"centre_hz": centre, "fits": {}, "notice": 'Low frequencies in small rooms are usually modal. Inspect the spectrogram; a diffuse-field RT60 is not reported.'})
            continue
        sos = signal.butter(3, [lo, hi], fs=fs, btype="bandpass", output="sos")
        band = signal.sosfilt(sos, samples)
        metrics = {name: _fit_decay(band, fs, a, b) for name, a, b in
                   [("EDT", 0, -10), ("T20", -5, -25), ("T30", -5, -35)]}
        for metric in metrics.values():
            if metric["available"] and metric["estimate_s"] < 18 / (hi - lo):
                metric.update(available=False, estimate_s=None, reason='The fit is too short and may be dominated by the analysis filter ringing')
        bands.append({"centre_hz": centre, "fits": metrics, "notice": 'Octave band, sixth-order forward bandpass; not an ISO-certified measurement.'})
    return {"time_ms": (indices / fs * 1000).tolist(), "edc_db": np.maximum(curve[indices], -100).tolist(),
            "fits": fits, "bands": bands, "noise_compensated": False,
            "notice": 'Schroeder reverse energy integration without noise subtraction. EDT describes early decay; T20/T30 extrapolate a fitted slope. Small-room bass decay must not be interpreted directly as diffuse-field reverberation time.'}


def _time_frequency(samples, fs, peak):
    # Restrict this diagnostic to the modal region, with one shared colour scale.
    a = max(0, peak - int(.128 * fs))
    b = min(len(samples), peak + int(2 * fs))
    raw = samples[a:b]
    target_fs = min(4000, int(fs))
    from fractions import Fraction
    ratio = Fraction(target_fs, int(fs)).limit_denominator(1024)
    raw = signal.resample_poly(raw, ratio.numerator, ratio.denominator)
    width = min(len(raw), max(32, round(target_fs * .128)))
    if width < 16:
        return None
    hop = max(1, round(width / 8))
    f, t, z = signal.stft(raw, fs=target_fs, window="hann", nperseg=width,
                          noverlap=width - hop, boundary="zeros", padded=False)
    power = np.abs(z) ** 2
    grid = np.geomspace(max(20, target_fs / width), min(500, target_fs * .4), 100)
    selected = np.asarray([np.interp(grid, f, column) for column in power.T])
    reference = max(float(selected.max()), 1e-30)
    return {"frequency_hz": grid.tolist(), "time_ms": ((t + a / fs - peak / fs) * 1000).tolist(),
            "level_db": np.maximum(_db(selected / reference, True), -70).tolist(),
            "window_ms": width * 1000 / target_fs, "resolution_hz": target_fs / width,
            "reference": 'One shared peak reference of 0 dB for the whole plot. Time is relative to the original impulse peak; individual frames are not normalized.'}


def analyze_measurement(measurement):
    m = measurement
    notices = []
    result = {"measurement": m.summary(), "level_unit": m.metadata.get("level_unit", "relative_db"),
              "time_domain_available": m.ir is not None, "notices": notices}
    phase_available = m.ir is not None or m.phase_deg is not None
    if m.ir is not None:
        nfft = next_fast_len(max(len(m.ir) * 2, 65536))
        f = np.fft.rfftfreq(nfft, 1 / m.sample_rate)
        response = rfft(m.ir, nfft)
        amplitude = np.abs(response)
        db = _db(amplitude)
        phase = np.unwrap(np.angle(response))
        gd = -np.gradient(phase, f) / (2 * np.pi) + m.start_time_s
        minimum = min_phase_spectrum(amplitude)
        minimum_gd = -np.gradient(np.unwrap(np.angle(minimum)), f) / (2 * np.pi)
        peak = int(np.argmax(np.abs(m.ir)))
        peak_time = m.start_time_s + peak / m.sample_rate
        excess = gd - minimum_gd - peak_time
        low, high = max(10, m.sample_rate / len(m.ir)), min(20000, m.sample_rate * .45)
        result["spectral_resolution_hz"] = m.sample_rate / len(m.ir)
        result["peak_time_ms"] = peak_time * 1000
        # Display decimation preserves narrow impulse/ETC peaks; raw data is untouched.
        a, b = max(0, peak - int(.02 * m.sample_rate)), min(len(m.ir), peak + int(2 * m.sample_rate))
        stride = max(1, int(np.ceil((b - a) / 1500)))
        indices = np.arange(a, b, stride)
        envelope = np.abs(signal.hilbert(m.ir))
        peak_amplitude = max(float(np.max(np.abs(m.ir))), 1e-30)
        envelope_peak = max(float(envelope.max()), 1e-30)
        ir_view, etc_view = [], []
        for k in indices:
            chunk = m.ir[k:min(k + stride, b)]
            ir_view.append(float(chunk[np.argmax(np.abs(chunk))] / peak_amplitude))
            etc_view.append(float(20 * np.log10(max(float(np.max(envelope[k:min(k + stride, b)])), 1e-30) / envelope_peak)))
        result["impulse"] = {"time_ms": (1000 * (m.start_time_s + indices / m.sample_rate)).tolist(),
                             "relative_amplitude": ir_view, "etc_db": np.maximum(etc_view, -100).tolist(),
                             "notice": 'Original measurement time is preserved. The display retains envelope peaks; the raw response is not shifted.'}
        onset = int(np.flatnonzero(np.abs(m.ir) >= np.max(np.abs(m.ir)) * .1)[0])
        post = m.ir[onset:]
        result["decay"] = decay_analysis(post, m.sample_rate)
        result["decay"]["onset_time_ms"] = (m.start_time_s + onset / m.sample_rate) * 1000
        result["spectrogram"] = _time_frequency(m.ir, m.sample_rate, peak)
        result["record_after_peak_ms"] = (len(m.ir) - peak) / m.sample_rate * 1000
        if result["record_after_peak_ms"] < 500:
            notices.append('Less than 500 ms was recorded after the peak. Slow decay may be truncated; unsupported fits are left unavailable.')
        notices.append('Excess group delay uses the original peak time as a display reference. This does not change the measurement or establish that the peak is the direct arrival.')
    else:
        f, db = m.frequency, m.magnitude_db
        amplitude = 10 ** (db / 20)
        low, high = max(10, float(f[0])), min(20000, float(f[-1]), m.sample_rate * .45)
        gd = -np.gradient(np.unwrap(np.radians(m.phase_deg)), f) / (2 * np.pi) if phase_available else None
        minimum_gd = excess = None
        notices.append('This measurement only contains frequency data. No impulse, ETC, decay or spectrogram is reconstructed from magnitude alone.')
        if phase_available:
            notices.append('Group delay uses the phase supplied by the file. Sparse frequency points and phase wrapping may limit reliability.')
    if high <= low:
        raise ValueError('Insufficient usable frequency range for a plot')
    grid = np.geomspace(low, high, 420)
    plot_db = np.interp(grid, f, db)
    ref_mask = (grid >= (35 if m.channel.startswith("SW") else 300)) & (grid <= (100 if m.channel.startswith("SW") else 1200))
    reference = float(np.median(plot_db[ref_mask])) if np.any(ref_mask) else float(np.median(plot_db))
    valid = np.interp(grid, f, db) > float(np.max(db)) - 60
    spectral = {"frequency_hz": grid.tolist(), "magnitude_db": plot_db.tolist(),
                "shape_db": (plot_db - reference).tolist(), "shape_reference_db": reference,
                "group_delay_ms": _nullable(np.interp(grid, f, gd) * 1000, valid) if gd is not None else None,
                "minimum_group_delay_ms": _nullable(np.interp(grid, f, minimum_gd) * 1000, valid) if minimum_gd is not None else None,
                "excess_group_delay_ms": _nullable(np.interp(grid, f, excess) * 1000, valid) if excess is not None else None}
    result["spectrum"] = spectral
    notices.append('Response and spectrogram levels are relative. They are not absolute dB SPL unless reliable calibration is explicitly provided.')
    return result


def room_modes(length, width, height, high_hz=200, speed=343):
    dimensions = np.asarray([length, width, height], dtype=float)
    high_hz, speed = float(high_hz), float(speed)
    if not np.isfinite(dimensions).all() or not np.all((dimensions >= 1) & (dimensions <= 50)):
        raise ValueError('Room length, width and height must be between 1 and 50 metres')
    if not np.isfinite(high_hz) or not 30 <= high_hz <= 300 or not np.isfinite(speed) or not 300 <= speed <= 380:
        raise ValueError('Invalid mode frequency limit or sound speed')
    limits = np.floor(2 * high_hz * dimensions / speed).astype(int)
    rows = []
    for order in itertools.product(*(range(n + 1) for n in limits)):
        axes = sum(n > 0 for n in order)
        if not axes:
            continue
        f = float(speed / 2 * np.sqrt(np.sum((np.asarray(order) / dimensions) ** 2)))
        if f <= high_hz:
            rows.append({"frequency_hz": f, "order": list(order), "kind": ["", 'Axial', 'Tangential', 'Oblique'][axes]})
    rows.sort(key=lambda row: row["frequency_hz"])
    return {"dimensions_m": dimensions.tolist(), "speed_m_s": speed, "modes": rows[:500],
            "total_modes": len(rows), "truncated": len(rows) > 500,
            "notice": 'Geometric prediction for a rectangular room with rigid boundaries. Openings, absorption, placement and EQ are not included; measurements are still required.'}
