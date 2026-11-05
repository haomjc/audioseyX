"""Positive repeat weights and explicit, finite measurement-uncertainty cases.

Consistency is not accuracy: two disagreeing records cannot identify which
one is wrong. No record, seat, response bin or phase reference is synthesized.
"""
from __future__ import annotations

from itertools import product
import numpy as np


def uncertainty_cases(settings):
    keys = ('receiver_main_uncertainty_db', 'receiver_sub_uncertainty_db',
            'receiver_timing_uncertainty_ms')
    limits = (3., 3., 5.)
    radii = []
    for key, maximum in zip(keys, limits):
        value = settings.get(key, 0.)
        if isinstance(value, bool):
            raise ValueError('Measurement uncertainty radii must be numeric')
        value = float(value)
        if not np.isfinite(value) or not 0 <= value <= maximum:
            raise ValueError('Invalid measurement uncertainty radius: ' + key)
        radii.append(value)
    axes = [(0.,) if radius == 0 else (-radius, 0., radius) for radius in radii]
    return [dict(main_gain_db=a, sub_gain_db=b, relative_sub_delay_ms=t,
                 mass=1./np.prod([len(axis) for axis in axes]))
            for a, b, t in product(*axes)]


def repeat_confidence(grid, settings):
    """Return per-record probabilities normalized separately at each output/seat."""
    mode = settings.get('receiver_repeat_weighting', 'uniform')
    if mode not in ('uniform', 'consistency'):
        raise ValueError('Choose uniform or consistency repeat weighting')
    floor = float(settings.get('receiver_repeat_weight_floor', .25))
    scale = float(settings.get('receiver_repeat_scale_db', 2.))
    if not np.isfinite([floor, scale]).all() or not 0 < floor <= 1 or not .1 <= scale <= 20:
        raise ValueError('Invalid repeat confidence controls')
    low = max(grid.low, float(settings.get('receiver_analysis_low_hz', 20)))
    high = min(grid.high, float(settings.get('receiver_analysis_high_hz', min(20000, grid.rate*.42))))
    key = (mode, floor, scale, low, high)
    cached = getattr(grid, '_repeat_confidence_cache', None)
    if cached is not None and cached[0] == key:
        return cached[1:]
    mask = (grid.frequency >= low) & (grid.frequency <= high)
    if not np.any(mask):
        raise ValueError('Repeat confidence needs common measured frequency coverage')
    frequency_mass = 1/np.maximum(grid.frequency[mask], low)
    frequency_mass /= frequency_mass.sum()
    probabilities = np.empty(len(grid.project.measurements))
    rows = []
    for (channel, position), indices in sorted(grid.groups.items()):
        db = np.array([20*np.log10(np.maximum(abs(grid.responses[i][mask]), 1e-15)) for i in indices])
        deviations = np.sqrt((db-np.median(db, axis=0))**2 @ frequency_mass)
        # At least three records are needed for a majority-based preference.
        raw = np.maximum(floor, 1/(1+(deviations/scale)**2)) if mode == 'consistency' and len(indices) >= 3 else np.ones(len(indices))
        weights = raw/raw.sum()
        probabilities[indices] = weights
        rows.append(dict(channel=channel, position=position, record_indices=indices,
                         weights=weights.tolist(), log_frequency_rms_deviation_db=deviations.tolist(),
                         preference_available=mode == 'consistency' and len(indices) >= 3,
                         noise_floor_estimated=False, phase_or_clock_corrected=False))
    report = dict(schema='audioseyx-repeat-confidence-1', mode=mode, groups=rows,
        minimum_relative_weight=floor, deviation_scale_db=scale, records_discarded=0,
        notice='Magnitude consistency weights are heuristic reliability preferences, not SNR, coherence or proof of accuracy. Fewer than three repeats receive equal weights. Seat priorities remain independent of repeat counts.')
    grid._repeat_confidence_cache = (key, probabilities, report)
    return probabilities, report


def robustness_report(grids, settings):
    # Confidence is computed from raw responses, so it is identical for all EQ curves.
    _, confidence = repeat_confidence(next(iter(grids.values())), settings)
    cases = uncertainty_cases(settings)
    return dict(schema='audioseyx-measurement-robustness-1', repeat_confidence=confidence,
        uncertainty_cases=cases, uncertainty_case_count=len(cases),
        uncertainty_source='explicit_user_bounds', uncertainty_bounds_measured=False,
        scenario_criterion='declared_nested_smooth_risk_over_finite_gain_and_relative_timing_cases',
        continuous_worst_case_guaranteed=False, actual_records_modified=False,
        notice='Uncertainty cases are model assumptions at the centre and endpoints of the declared bounds. They do not establish a continuous worst-case bound or calibrate unknown capture gain, timing, microphone response or native DSP reconstruction.')
