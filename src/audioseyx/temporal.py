"""Measured-window temporal diagnostics; no inferred direct arrival or room RT."""
from __future__ import annotations

import numpy as np
from scipy import signal

from .analysis import _fit_decay


DEFAULT_BANDS = (
    ('20-150 Hz', 20., 150.),
    *tuple((f'{centre:g} Hz octave', centre / np.sqrt(2), centre * np.sqrt(2))
           for centre in [31.5, 63., 125., 250., 500., 1000., 2000., 4000., 8000., 16000.]),
)
CUTOFFS_MS = (50, 80, 150)


def time_reference(measurement):
    if measurement.ir is None:
        raise ValueError('Original impulse responses are required for temporal analysis')
    explicit = measurement.metadata.get('temporal_anchor_time_s')
    first = float(measurement.start_time_s)
    end = first + len(measurement.ir) / measurement.sample_rate
    if explicit is not None:
        value = float(explicit)
        if not np.isfinite(value) or not first <= value < end:
            raise ValueError('The explicit temporal anchor must lie inside the original recording')
        return value, 'explicit temporal anchor; direct-arrival identity is not inferred'
    return first + int(np.argmax(np.abs(measurement.ir))) / measurement.sample_rate, 'original broadband peak; not a verified direct arrival'


def common_window(measurements, anchors):
    start = max(m.start_time_s - a for m, a in zip(measurements, anchors))
    end = min(m.start_time_s + len(m.ir) / m.sample_rate - a for m, a in zip(measurements, anchors))
    if not start <= 0 < end:
        raise ValueError('No common observed window around the temporal references')
    return float(start), float(end)


def _index(time_s, fs, start, length):
    # Exact sample times are included; tolerate arithmetic round-off at a boundary.
    return int(np.clip(np.ceil((time_s - start) * fs - 1e-8), 0, length))


def _energy_metrics(samples, fs, start, anchor, window):
    a = _index(anchor + window[0], fs, start, len(samples))
    zero = _index(anchor, fs, start, len(samples))
    b = _index(anchor + window[1], fs, start, len(samples))
    energy = np.square(samples[a:b])
    total = float(np.sum(energy))
    if total <= np.finfo(float).tiny:
        return {'available': False, 'reason': 'No usable energy in the common observed window'}
    post = samples[zero:b]
    post_total = float(np.dot(post, post))
    metrics = {'available': True, 'samples_used': b - a,
               'pre_reference_energy_fraction': float(np.sum(energy[:zero-a]) / total),
               'late_energy_fraction': {}, 'early_late_ratio_db': {}}
    for cutoff in CUTOFFS_MS:
        # The recording end is exclusive. At least one late sample is necessary.
        k = _index(anchor + cutoff / 1000, fs, start, len(samples))
        key = str(cutoff)
        if cutoff / 1000 >= window[1] or k >= b or post_total <= np.finfo(float).tiny:
            metrics['late_energy_fraction'][key] = None
            metrics['early_late_ratio_db'][key] = None
            continue
        late = float(np.dot(samples[k:b], samples[k:b]))
        early = max(0., post_total - late)
        metrics['late_energy_fraction'][key] = float(late / post_total)
        metrics['early_late_ratio_db'][key] = (float(10*np.log10(early/late))
                                               if early > 0 and late > 0 else None)
    metrics['decay_fits'] = {name: _fit_decay(post, fs, begin, end)
                            for name, begin, end in [('EDT', 0, -10), ('T20', -5, -25), ('T30', -5, -35)]}
    return metrics


def temporal_profile(measurement, *, anchor_time_s=None, relative_window_s=None,
                     bands=DEFAULT_BANDS, memory_mib=128):
    anchor, source = time_reference(measurement)
    if anchor_time_s is not None:
        anchor = float(anchor_time_s)
        source = 'fixed original-response temporal anchor; not refitted after filtering'
    fs = float(measurement.sample_rate)
    start = float(measurement.start_time_s)
    n = len(measurement.ir)
    support = (start-anchor, start+n/fs-anchor)
    window = tuple(map(float, relative_window_s if relative_window_s is not None else support))
    if (len(window) != 2 or not np.isfinite([anchor, *window]).all() or
        not window[0] <= 0 < window[1] or window[0] < support[0]-1e-10 or window[1] > support[1]+1e-10):
        raise ValueError('Temporal windows must lie within observed response support')
    memory_mib = float(memory_mib)
    if not np.isfinite(memory_mib) or memory_mib <= 0 or 128*n > memory_mib*1024**2:
        raise ValueError('The declared temporal-analysis memory budget is insufficient; no response is shortened')
    bands = tuple(bands)
    if len(bands) > 64:
        raise ValueError('At most 64 declared temporal bands are supported')
    raw = np.asarray(measurement.ir, dtype=float)
    # Only a working copy is scaled, once per complete IR, for numerical stability.
    x = raw / np.max(np.abs(raw))
    broadband = _energy_metrics(x, fs, start, anchor, window)
    rows = []
    for label, low, high in bands:
        low, high = float(low), float(high)
        if not np.isfinite([low, high]).all() or not 0 < low < high:
            raise ValueError('Temporal bands must have finite increasing positive edges')
        row = {'name': str(label), 'band_hz': [low, high]}
        if high >= fs * .46:
            rows.append({**row, 'available': False, 'reason': 'Band exceeds the recorded Nyquist margin'})
            continue
        sos = signal.butter(3, [low, high], fs=fs, btype='bandpass', output='sos')
        radius = float(max(abs(pole) for section in sos for pole in np.roots(section[3:])))
        if not 0 < radius < 1:
            raise ValueError('The analysis filter does not have a stable measurable decay reference')
        filter_pole_t60 = float(-np.log(1000)/(fs*np.log(radius)))
        y = signal.sosfilt(sos, x)
        # Use the identical observed window and anchor for every analysis band.
        values = _energy_metrics(y, fs, start, anchor, window)
        reference = np.zeros(n)
        reference[_index(anchor, fs, start, n)] = 1.
        reference = signal.sosfilt(sos, reference)
        ring = _energy_metrics(reference, fs, start, anchor, window)
        values['analysis_filter_reference'] = ring
        values['analysis_filter_slowest_pole_t60_s'] = filter_pole_t60
        for name, fit in values.get('decay_fits', {}).items():
            # A fitted slope dominated by the diagnostic filter is not room evidence.
            # A fast analysis filter need not have a long enough interval for
            # an empirical T20 fit: its known pole decay still supplies a guard.
            if fit['available'] and fit['estimate_s'] <= 1.5 * filter_pole_t60:
                fit.update(available=False, estimate_s=None,
                           reason='Measured decay cannot be separated from analysis-filter ringing in this window')
        rows.append({**row, **values})
    return {'available': True, 'measurement': measurement.summary(), 'anchor_time_s': anchor,
            'anchor_source': source, 'relative_window_s': list(window),
            'original_sample_count': n, 'broadband': broadband, 'bands': rows,
            'original_ir_modified': False, 'noise_subtraction_applied': False,
            'notice': 'Ratios use one observed window and one broadband reference. The original samples are unchanged. Peak-referenced energy ratios are not ISO clarity or a direct-arrival measurement. Fitted slopes are descriptive, not certified room RT60; low-frequency modes and analysis-filter ringing require separate interpretation.'}


def _distribution(values):
    values = [float(v) for v in values if v is not None and np.isfinite(v)]
    return {'count': len(values), 'median': float(np.median(values)) if values else None,
            'minimum': min(values) if values else None, 'maximum': max(values) if values else None}


def _summary(profiles, band_name=None):
    selected = [p['broadband'] if band_name is None else next(b for b in p['bands'] if b['name']==band_name)
                for p in profiles]
    return {'repeat_count': len(profiles),
            'pre_reference_energy_fraction': _distribution(v.get('pre_reference_energy_fraction') for v in selected),
            'late_energy_fraction': {str(k): _distribution(v.get('late_energy_fraction', {}).get(str(k)) for v in selected)
                                     for k in CUTOFFS_MS},
            'decay_fit_s': {name: _distribution(v.get('decay_fits', {}).get(name, {}).get('estimate_s') for v in selected)
                            for name in ['EDT', 'T20', 'T30']}}


def compare_temporal_records(before, after, *, memory_mib=128):
    records = [*before, *after]
    if not before or not after or any(m.ir is None for m in records):
        return {'available': False, 'reason': 'Every retained before/after repeat needs an original impulse response; magnitude-only data are not reconstructed'}
    anchors = [time_reference(m)[0] for m in records]
    window = common_window(records, anchors)
    profiles = [temporal_profile(m, relative_window_s=window, memory_mib=memory_mib) for m in records]
    a, b = profiles[:len(before)], profiles[len(before):]
    timebases = {m.metadata.get('timebase_id') for m in records}
    verified = (len(timebases) == 1 and None not in timebases and '' not in timebases and
                all(m.metadata.get('timing_reference_verified') is True for m in records))
    return {'available': True, 'before': a, 'after': b, 'relative_window_s': list(window),
            'broadband': {'before': _summary(a), 'after': _summary(b)},
            'bands': [{'name': band[0], 'band_hz': list(band[1:]),
                       'before': _summary(a, band[0]), 'after': _summary(b, band[0])} for band in DEFAULT_BANDS],
            'absolute_peak_time_delta_ms': (float((np.median([m.start_time_s+np.argmax(np.abs(m.ir))/m.sample_rate for m in after])-
                                                    np.median([m.start_time_s+np.argmax(np.abs(m.ir))/m.sample_rate for m in before]))*1000)
                                            if verified else None),
            'shared_timebase_verified_in_metadata': verified, 'raw_alignment_applied': False,
            'notice': 'All repeats are retained individually and summarized within this output/position. Independent peak references describe response shape only; a measured delay change requires an explicitly verified shared timebase. A common observed window prevents a shorter after-recording from being interpreted as faster decay.'}


def predict_filter_temporal(project, design, *, memory_mib=128, cancel=None):
    """Evaluate complete linear FIR convolution; intentionally no bass-route claim."""
    from .model import Measurement
    lookup = {item['channel']: item for item in design['channels']}
    records = []
    for index, m in enumerate(project.measurements):
        if cancel is not None and cancel.is_set():
            raise InterruptedError('Temporal prediction cancelled')
        if m.ir is None:
            records.append({'measurement_index': index, 'available': False,
                            'reason': 'Original IR is missing; temporal response is not synthesized from magnitude'})
            continue
        item = lookup[m.channel]
        if item['sample_rate'] != m.sample_rate:
            records.append({'measurement_index': index, 'available': False,
                            'reason': 'Filter and measurement sample rates differ; a temporal multirate reconstruction model is not supplied'})
            continue
        size = len(m.ir) + len(item['taps']) - 1
        if not np.isfinite(float(memory_mib)) or 192*size > float(memory_mib)*1024**2:
            raise ValueError('The declared temporal-prediction memory budget is insufficient; full convolution is retained')
        taps = np.asarray(item['taps'], dtype=float)
        full = signal.fftconvolve(m.ir, taps, mode='full')
        after = Measurement(m.channel, m.position, m.name+' / predicted EQ-stage only', m.sample_rate,
                            full, start_time_s=m.start_time_s, timing_reference=m.timing_reference)
        anchor = time_reference(m)[0]
        window = (m.start_time_s-anchor, m.start_time_s+len(m.ir)/m.sample_rate-anchor)
        records.append({'measurement_index': index, 'available': True, 'channel': m.channel, 'position': m.position,
                        'used_for_filter_selection': m.position not in design.get('validation_positions', []),
                        'full_linear_convolution_samples': len(full), 'filter_samples_at_measurement_rate': len(taps),
                        'before': temporal_profile(m, anchor_time_s=anchor, relative_window_s=window, memory_mib=memory_mib),
                        'after': temporal_profile(after, anchor_time_s=anchor, relative_window_s=window, memory_mib=memory_mib)})
    return {'schema': 'audioseyx-temporal-prediction-1', 'stage': 'individual_output_EQ_FIR_only',
            'records': records, 'all_original_records_retained': True, 'hardware_verified': False,
            'uses_requested_delay_to_move_reference': False,
            'notice': 'Complete linear convolution with the returned FIR; the original temporal anchor is fixed before filtering. Metrics stop at the original measured support, so the FIR tail does not invent a longer room recording. Distance, trim, crossover, mixed-output sums, native reconstruction and acoustic feedback are not included; this is an EQ-stage diagnostic, not ART decay control or after-correction measurement.'}
