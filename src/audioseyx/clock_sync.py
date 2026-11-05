"""Clock estimation from repeated known pilots in ORIGINAL audio recordings.

Already-deconvolved impulses do not contain the required timing evidence.
No sound is played and no recording or original file is modified here.
"""
from __future__ import annotations

import hashlib
import numpy as np
from scipy import signal
from scipy.optimize import minimize_scalar


def pilot_signal(sample_rate=48000, duration=.04):
    if not 4000 <= sample_rate <= 192000 or not .01 <= duration <= .2:
        raise ValueError('Invalid pilot signal rate or duration')
    count = round(sample_rate * duration)
    t = np.arange(count) / sample_rate
    return signal.chirp(t, sample_rate * .06, duration, sample_rate * .3,
                        method='logarithmic') * np.hanning(count)


def _vector(data, name):
    value = np.asarray(data, dtype=float)
    if value.ndim != 1 or not 32 <= len(value) <= 32_000_000 or not np.isfinite(value).all() or not np.any(value):
        raise ValueError('A complete finite, non-silent mono ' + name + ' is required')
    return value


def _interpolated_peak(correlation, index):
    begin, end = max(0, index - 40), min(len(correlation), index + 41)
    samples = np.arange(begin, end)
    values = correlation[begin:end]
    def amplitude(x):
        distance = x - samples
        z = np.maximum(0, 1 - (distance / 41)**2)
        window = np.i0(14 * np.sqrt(z)) / np.i0(14)
        return float(np.dot(values, np.sinc(distance) * window))
    result = minimize_scalar(lambda x: -abs(amplitude(x)), bounds=(index - .6, index + .6),
                             method='bounded', options={'xatol': 1e-7})
    return float(result.x)


def estimate_clock(recording, pilot, sample_rate, marker_times_s, *, latency_guess_s=0.,
                   search_radius_s=.2, minimum_correlation=.35, maximum_ppm=3000.,
                   maximum_fit_residual_samples=1., cancel=None):
    recording, pilot = _vector(recording, 'original recording'), _vector(pilot, 'pilot waveform')
    recording_hash = hashlib.sha256(recording.astype('<f8').tobytes()).hexdigest()
    # Scale only the matching calculation, avoiding overflow/underflow in
    # energy normalization. The original amplitude and audio remain intact.
    analysis_recording = recording / np.max(np.abs(recording))
    pilot = pilot / np.max(np.abs(pilot))
    times = np.asarray(marker_times_s, dtype=float)
    if times.ndim != 1 or not 3 <= len(times) <= 100 or not np.isfinite(times).all() or np.any(times < 0) or np.any(np.diff(times) <= 0):
        raise ValueError('At least three distinct known pilot times from a continuous reference signal are required')
    if not np.isfinite([sample_rate, latency_guess_s, search_radius_s, minimum_correlation, maximum_ppm, maximum_fit_residual_samples]).all() or not 4000 <= sample_rate <= 768000 or not 0 < search_radius_s <= 2 or not 0 < minimum_correlation <= 1 or not 0 < maximum_ppm <= 20000 or not 0 < maximum_fit_residual_samples <= 10:
        raise ValueError('Invalid raw-recording clock analysis controls')
    if times[-1] - times[0] < .5 or np.any(np.diff(times) < 2 * search_radius_s + len(pilot) / sample_rate):
        raise ValueError('Pilot search windows must be distinct and span at least half a second')
    pilot = pilot - pilot.mean()
    energy = float(pilot @ pilot)
    if energy <= 1e-24: raise ValueError('The pilot contains insufficient time-varying energy')
    detected, matches = [], []
    for expected in times:
        if cancel is not None and cancel.is_set(): raise InterruptedError('Raw clock analysis cancelled')
        first = max(0, int(np.floor((expected + latency_guess_s - search_radius_s) * sample_rate)))
        last = min(len(recording), int(np.ceil((expected + latency_guess_s + search_radius_s) * sample_rate)) + len(pilot))
        segment = analysis_recording[first:last]
        if len(segment) < len(pilot) + 4:
            raise ValueError('A known pilot search window falls outside the original recording')
        correlation = signal.correlate(segment, pilot, mode='valid', method='fft')
        cumulative = np.r_[0., np.cumsum(segment**2)]
        local_energy = np.maximum(cumulative[len(pilot):] - cumulative[:-len(pilot)], 0)
        energy_floor = max(float(local_energy.max()) * 1e-12, np.finfo(float).tiny)
        # FFT/cumulative-sum roundoff in digital silence must not become a
        # near-unit normalized match by division through an artificial floor.
        agreement = np.divide(np.abs(correlation), np.sqrt(energy * local_energy),
                              out=np.zeros_like(local_energy), where=local_energy > energy_floor)
        agreement = np.minimum(agreement, 1.)
        index = int(np.argmax(agreement)); confidence = float(agreement[index])
        if index < 2 or index >= len(agreement) - 2 or confidence < minimum_correlation:
            raise ValueError('A known pilot has no reliable interior correlation peak')
        guard = max(2, round(.002 * sample_rate))
        alternatives = agreement.copy(); alternatives[max(0, index - guard):index + guard + 1] = 0
        second = float(alternatives.max())
        if second > confidence * .95:
            raise ValueError('Competing pilot/echo peaks make clock estimation ambiguous')
        observed = (first + _interpolated_peak(correlation, index)) / sample_rate
        detected.append(observed)
        matches.append({'expected_time_s': float(expected), 'observed_time_s': observed,
                        'normalized_correlation': confidence, 'competing_peak_correlation': second})
    observed = np.asarray(detected)
    if np.any(np.diff(observed) <= 0): raise ValueError('Detected pilots are not chronologically distinct')
    centred = times - times.mean()
    slope = float(centred @ (observed - observed.mean()) / (centred @ centred))
    offset = float(observed.mean() - slope * times.mean())
    residual = observed - (offset + slope * times)
    ppm = (slope - 1) * 1e6
    if not np.isfinite(slope) or slope <= 0 or abs(ppm) > maximum_ppm:
        raise ValueError('The estimated clock ratio exceeds the declared drift range')
    if np.max(np.abs(residual)) * sample_rate > maximum_fit_residual_samples:
        raise ValueError('Pilot timing is inconsistent with one stable affine clock; no correction was accepted')
    uncertainty = float(np.sqrt((residual @ residual) / (len(times) - 2) / (centred @ centred)) * 1e6)
    for match, value in zip(matches, residual): match['fit_residual_samples'] = float(value * sample_rate)
    return {'method': 'known_repeated_pilot_full_recording_affine_clock', 'sample_rate_hz': sample_rate,
            'recording_sha256': recording_hash,
            'recording_samples': len(recording),
            'recorded_seconds_per_reference_second': slope, 'estimated_drift_ppm': ppm,
            'fit_standard_error_ppm': uncertainty, 'fitted_offset_s': offset,
            'maximum_fit_residual_samples': float(np.max(np.abs(residual)) * sample_rate),
            'anchor_recording_time_s': float(observed[0]), 'pilots': matches,
            'pilot_sha256': hashlib.sha256(pilot.astype('<f8').tobytes()).hexdigest(),
            'raw_recording_modified': False, 'direct_arrival_identified': False,
            'notice': 'Clock scale fitted from at least three known repeated pilots. The offset can include latency and a consistent room reflection; it is not a direct-arrival measurement. Stationary signal/path and a stable clock are assumptions. Existing deconvolved IRs cannot establish this fit.'}


def correct_recording(recording, analysis, *, taps=128, beta=14., start_time_s=0.,
                      memory_mib=512, cancel=None):
    recording = _vector(recording, 'original recording')
    if analysis.get('method') != 'known_repeated_pilot_full_recording_affine_clock':
        raise ValueError('Validated original-recording pilot analysis is required before clock correction')
    if analysis.get('recording_samples') != len(recording) or analysis.get('recording_sha256') != hashlib.sha256(recording.astype('<f8').tobytes()).hexdigest():
        raise ValueError('The recording differs from the pilot-verified clock analysis')
    slope = float(analysis['recorded_seconds_per_reference_second'])
    rate = float(analysis['sample_rate_hz'])
    if not isinstance(taps, int) or not 32 <= taps <= 1024 or taps % 2 or not np.isfinite([beta, start_time_s, memory_mib, slope]).all() or not 4 <= beta <= 20 or not 32 <= memory_mib <= 32768 or not .98 <= slope <= 1.02:
        raise ValueError('Invalid clock resampling precision, memory or ratio')
    ratio = 1 / slope
    count = int(np.floor((len(recording) - 1) * ratio)) + 1
    estimate = (len(recording) + count) * 8 + taps * 4096 * 8 * 6
    if estimate > memory_mib * 1024**2:
        raise ValueError('The complete clock-corrected recording exceeds the explicit memory budget')
    output = np.empty(count)
    half = taps // 2; offsets = np.arange(-half + 1, half + 1)
    cutoff = min(1., ratio); scale = float(np.i0(beta))
    for begin in range(0, count, 4096):
        if cancel is not None and cancel.is_set(): raise InterruptedError('Raw recording clock correction cancelled')
        end = min(count, begin + 4096)
        position = np.arange(begin, end) / ratio
        neighbours = np.floor(position).astype(np.int64)[:, None] + offsets
        distance = position[:, None] - neighbours
        window = np.i0(beta * np.sqrt(np.maximum(0, 1 - (distance / half)**2))) / scale
        kernel = cutoff * np.sinc(cutoff * distance) * window
        kernel /= kernel.sum(axis=1)[:, None]
        valid = (neighbours >= 0) & (neighbours < len(recording))
        samples = recording[np.clip(neighbours, 0, len(recording) - 1)] * valid
        output[begin:end] = np.sum(samples * kernel, axis=1)
    anchor = float(analysis['anchor_recording_time_s'])
    if not np.isfinite(output).all(): raise ValueError('Clock interpolation produced non-finite audio; no corrected file was accepted')
    corrected_start = start_time_s + (1 - ratio) * anchor
    return output, {'method': 'continuous_phase_Kaiser_windowed_sinc', 'filter_taps': taps, 'kaiser_beta': beta,
                    'output_samples': len(output), 'input_samples': len(recording), 'output_sample_rate_hz': rate,
                    'output_start_time_s': corrected_start, 'original_start_time_s': start_time_s,
                    'anchor_recording_time_preserved': True, 'resampling_ratio': ratio,
                    'estimated_working_memory_mib': estimate / 1024**2,
                    'peak_absolute_output': float(np.max(np.abs(output))),
                    'zero_padding_at_recording_edges': True, 'deconvolution_performed': False,
                    'notice': 'A separate complete raw recording is resampled at continuous fractional positions. Original audio is retained. Finite sinc precision and edge padding are reported; this is not a lossless transform or an impulse-response deconvolution.'}
