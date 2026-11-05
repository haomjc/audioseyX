"""Documented menu grids and explicit distance-to-delay modeling assumptions."""
from __future__ import annotations
import hashlib
import json
import numpy as np

from .routing import X3700H_CROSSOVERS


def receiver_profile(model, supplied=None):
    if supplied is None:
        if "3700H" not in str(model).upper():
            raise ValueError("Supply an explicit receiver-constraint profile for this model")
        supplied = {"distance_min_m": 0., "distance_max_m": 18., "distance_spread_lt_m": 6.,
                    "distance_step_m": .01, "level_min_db": -12., "level_max_db": 12.,
                    "level_step_db": .5, "crossovers_hz": list(X3700H_CROSSOVERS),
                    'distance_delay_speed_m_s': 300.,
                    'distance_delay_evidence': 'observed_user_provided_reference_package_model; current_firmware_not_verified'}
    result = dict(supplied)
    keys = ["distance_min_m", "distance_max_m", "distance_spread_lt_m", "distance_step_m",
            "level_min_db", "level_max_db", "level_step_db"]
    if any(k not in result for k in keys) or not np.isfinite([result[k] for k in keys]).all():
        raise ValueError("A complete finite receiver-constraint profile is required")
    if not 0 <= result['distance_min_m'] < result['distance_max_m'] <= 100 or not .01 <= result['distance_step_m'] <= 1 or not 0 < result['distance_spread_lt_m'] <= 100:
        raise ValueError("Invalid receiver distance constraints")
    if not -30 <= result['level_min_db'] < result['level_max_db'] <= 30 or not .1 <= result['level_step_db'] <= 1:
        raise ValueError("Invalid receiver level constraints")
    if abs(result['distance_step_m'] * 100 - round(result['distance_step_m'] * 100)) > 1e-8 or abs(result['level_step_db'] * 10 - round(result['level_step_db'] * 10)) > 1e-8:
        raise ValueError("Receiver constraints must be representable in observed native centimetre/tenth-dB units")
    choices = result.get('crossovers_hz', [])
    if not choices or any(not np.isfinite(v) or not 20 <= v <= 1000 for v in choices):
        raise ValueError("Explicit native crossover choices are required")
    result['crossovers_hz'] = sorted(set(float(v) for v in choices))
    return result


def distance_basis(current, profile, settings):
    method = settings.get('receiver_distance_basis', 'preserve')
    speed = float(settings.get('receiver_delay_speed_m_s', profile.get('distance_delay_speed_m_s', 343)))
    physical = float(settings.get('receiver_physical_speed_m_s', 343))
    if not np.isfinite([speed, physical]).all() or not 250 <= speed <= 400 or not 250 <= physical <= 400:
        raise ValueError('Explicit finite physical and receiver distance/delay conversion models are required')
    if method not in ['preserve', 'physical_rescale']:
        raise ValueError('Choose whether to preserve or explicitly rescale the current main distance basis')
    if method == 'preserve':
        return dict(current), {'method': method, 'receiver_speed_m_s': speed, 'physical_speed_m_s': physical,
                               'relative_distances_rescaled': False, 'hardware_verified': False}
    reference = next((c for c in current if not c.startswith('SW')), next(iter(current)))
    anchor = current[reference]; step = profile['distance_step_m']
    values = {c: round((anchor + (v - anchor) * speed / physical) / step) * step for c, v in current.items()}
    return values, {'method': method, 'reference_output': reference, 'receiver_speed_m_s': speed,
                    'physical_speed_m_s': physical, 'relative_distance_ratio': speed / physical,
                    'relative_distances_rescaled': True, 'hardware_verified': False,
                    'notice': 'Explicitly assumes the saved relative distances represent physical-distance differences. Do not use on already corrected distances without verification.'}


def snapshot_fingerprint(snapshot):
    keys = ['configured_channels', 'speaker_types', 'large_channels', 'distances_m', 'levels_db',
            'crossovers', 'bass_mode', 'lfe_lowpass_hz']
    data = {k: snapshot.get(k) for k in keys}
    return hashlib.sha256(json.dumps(data, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def quantized_distance_proposal(current, sub, relative_delay_s, speed_m_s, profile):
    """Increasing a menu distance advances that output in the assumed model."""
    if sub not in current or not np.isfinite([relative_delay_s, speed_m_s]).all() or not 250 <= speed_m_s <= 400:
        raise ValueError("Explicit finite receiver distance/delay modeling is required")
    step = profile['distance_step_m']
    values = dict(current)
    if any(not np.isfinite(v) or abs(v / step - round(v / step)) > 1e-6 for v in values.values()):
        raise ValueError("The baseline distances do not lie on the selected native grid")
    delta = -relative_delay_s * speed_m_s
    if abs(delta / step - round(delta / step)) > 1e-6:
        raise ValueError("Requested delay does not correspond to a native distance step")
    values[sub] = round((values[sub] + delta) / step) * step
    low, high = min(values.values()), max(values.values())
    if high - low >= profile['distance_spread_lt_m'] - 1e-10:
        return None
    begin = int(np.ceil((profile['distance_min_m'] - low) / step - 1e-9))
    end = int(np.floor((profile['distance_max_m'] - high) / step + 1e-9))
    if begin > end:
        return None
    shift_steps = int(np.clip(0, begin, end))
    adjusted = {c: float(round((v + shift_steps * step) / step) * step) for c, v in values.items()}
    return {'distances_m': adjusted, 'common_offset_m': shift_steps * step,
            'sub_distance_delta_m': delta, 'sub_relative_delay_ms': relative_delay_s * 1000,
            'distance_delay_speed_m_s': speed_m_s, 'distance_delay_model_verified': False}


def validate_receiver_proposal(calibration, snapshot, channels):
    """Validate the saved model and all menu units before native preparation."""
    if not isinstance(calibration, dict) or calibration.get('schema') != 'audioseyx-receiver-calibration-1':
        raise ValueError('A complete current receiver calibration proposal is required')
    if calibration.get('baseline_snapshot_sha256') != snapshot_fingerprint(snapshot):
        raise ValueError('Receiver speaker settings changed since modeling; recalculate and review the proposal')
    profile = receiver_profile(calibration.get('model'), calibration.get('profile'))
    proposal = calibration.get('proposal', {})
    active = set(snapshot.get('configured_channels', []))
    if active != set(channels) or set(proposal.get('distances_m', {})) != active or set(proposal.get('levels_db', {})) != active:
        raise ValueError('The native receiver proposal must cover every configured and measured output exactly')
    distance = proposal['distances_m']; levels = proposal['levels_db']
    if any(not np.isfinite(v) or not profile['distance_min_m'] <= v <= profile['distance_max_m']
           or abs(v / profile['distance_step_m'] - round(v / profile['distance_step_m'])) > 1e-6 for v in distance.values()):
        raise ValueError('A proposed distance lies outside the native menu grid')
    if max(distance.values()) - min(distance.values()) >= profile['distance_spread_lt_m'] - 1e-10:
        raise ValueError('The proposed distance spread exceeds the documented menu constraints')
    if any(not np.isfinite(v) or not profile['level_min_db'] <= v <= profile['level_max_db']
           or abs(v / profile['level_step_db'] - round(v / profile['level_step_db'])) > 1e-6 for v in levels.values()):
        raise ValueError('A proposed level lies outside the native menu grid')
    crossovers = proposal.get('crossovers_hz', {})
    if any(c not in active or snapshot.get('speaker_types', {}).get(c) != 'small' or value not in profile['crossovers_hz']
           or value != round(value) for c, value in crossovers.items()):
        raise ValueError('A proposed crossover is not valid for the selected Small output')
    inversions = proposal.get('inverted_outputs', [])
    if len(inversions) != len(set(inversions)) or any(c not in active or not c.startswith('SW') for c in inversions):
        raise ValueError('Native calibration polarity changes must name measured subwoofer filters')
    return proposal
