"""Independent static targets in the two observed native calibration slots."""
from __future__ import annotations

import copy
import hashlib
import numpy as np

from .curves import parse_target_curve


# Observed wire-slot / OCA-field association, not a hardware certification.
CURVE_SPECS = {
    'Flat': {'curve_id': 0, 'calibration_field': 'filterLV'},
    'Reference': {'curve_id': 1, 'calibration_field': 'filter'},
}
PROFILE_KEYS = frozenset({'low_hz', 'high_hz', 'sub_high_hz', 'max_boost_db', 'max_cut_db',
                          'bass_db', 'tilt_db_oct', 'smoothing_octave', 'target_curve'})


def fir_fingerprint(taps):
    values = np.asarray(taps, dtype='<f8')
    if values.ndim!=1 or not len(values) or not np.isfinite(values).all():
        raise ValueError('Calibration FIRs must be finite complete vectors')
    return hashlib.sha256(values.tobytes()).hexdigest()


def checked_profile(profile):
    if not isinstance(profile, dict) or set(profile)-PROFILE_KEYS:
        raise ValueError('Each curve may override EQ bands, gain limits, smoothing and target only; receiver controls and spatial references are shared')
    result = copy.deepcopy(profile)
    curve = result.get('target_curve')
    if curve is not None:
        if not isinstance(curve, dict):
            raise ValueError('A curve target must contain frequency and level_db arrays')
        frequency, level = curve.get('frequency', []), curve.get('level_db', [])
        if not isinstance(frequency, (list, tuple)) or not isinstance(level, (list, tuple)) or len(frequency)!=len(level):
            raise ValueError('Curve target frequencies and gains must match')
        checked = parse_target_curve('\n'.join(f'{f} {g}' for f,g in zip(frequency, level)))
        if 'name' in curve:
            if not isinstance(curve['name'], str) or len(curve['name'])>180:
                raise ValueError('Keep the target name within 180 characters')
            checked['name'] = curve['name']
        result['target_curve'] = checked
    return result


def curve_designs(design):
    """Return both complete curves, mirroring only the explicit legacy mode."""
    other = design.get('curve_designs')
    if other is None:
        return {'Flat': design, 'Reference': design}
    if not isinstance(other, dict) or set(other)!={'Reference'}:
        raise ValueError('An independent native curve bundle must contain exactly one Reference design')
    reference = other['Reference']
    if not isinstance(reference, dict) or reference.get('curve_designs') is not None:
        raise ValueError('Nested or malformed calibration curve bundles are not supported')
    for item in [design, reference]:
        if item.get('settings', {}).get('phase_mode', 'minimum') != 'minimum' or item.get('joint_model'):
            raise ValueError('Independent native curves require minimum-phase EQ; mixed-phase references remain separate')
    if reference.get('tier') != design.get('tier') or reference.get('project_id') != design.get('project_id'):
        raise ValueError('Both calibration curves must describe the same project and Audyssey tier')
    if reference.get('training_positions') != design.get('training_positions') or reference.get('validation_positions') != design.get('validation_positions'):
        raise ValueError('Both calibration curves must retain identical training and validation positions')
    for key in ['listening_area', 'position_weights', 'validation_positions', 'receiver_measurement_context',
                'timing_confirmed', 'relative_levels_confirmed', 'routing_measurements_confirmed']:
        if reference.get('settings', {}).get(key) != design.get('settings', {}).get(key):
            raise ValueError('Calibration curves cannot use different spatial or timing references')
    if reference.get('receiver_calibration') is not None:
        raise ValueError('Only the primary curve may select common receiver controls')
    shared = reference.get('shared_receiver_evaluation')
    if shared and shared.get('proposal') != design.get('receiver_calibration', {}).get('proposal'):
        raise ValueError('The Reference evaluation differs from the common receiver proposal')
    return {'Flat': design, 'Reference': reference}


def design_report(design):
    """Public/report form for both curves; never expose FIR/native arrays."""
    if not design:
        return None
    result = {key:value for key,value in design.items() if key not in ['channels', 'curve_designs']}
    result['channels'] = [{k:v for k,v in item.items() if k!='taps' and not k.startswith('_')}
                           for item in design['channels']]
    if design.get('curve_designs') is not None:
        curve_designs(design)
        result['curve_designs'] = {'Reference': design_report(design['curve_designs']['Reference'])}
    return result


def design_calibration_curves(project, tier, settings, progress=None, *, cancel=None):
    from .dsp import design_project
    profiles = settings.get('curve_profiles')
    if not isinstance(profiles, dict) or 'Reference' not in profiles or set(profiles)-set(CURVE_SPECS):
        raise ValueError('Supply a Reference EQ profile and optionally a Flat profile')
    if settings.get('phase_mode', 'minimum')!='minimum' or settings.get('optimizer')=='native_joint':
        raise ValueError('Two native curves require minimum-phase EQ; full complex/mixed-phase design remains separate')
    profiles = {name:checked_profile(profile) for name,profile in profiles.items()}
    common = {k:copy.deepcopy(v) for k,v in settings.items() if k!='curve_profiles'}
    flat_settings = {**common, **profiles.get('Flat', {})}
    def flat_progress(number, message):
        if progress:progress(round(number*.45),'Flat / '+message)
    flat = design_project(project, tier, flat_settings, flat_progress, cancel=cancel)
    if cancel is not None and cancel.is_set():
        raise InterruptedError('Two-curve design cancelled')
    reference_settings = {**common, **profiles['Reference'], 'optimizer': 'independent'}
    def reference_progress(number, message):
        if progress:progress(45+round(number*.4),'Reference / '+message)
    reference = design_project(project, tier, reference_settings, reference_progress, cancel=cancel)
    if flat.get('receiver_calibration'):
        from .receiver_calibration import evaluate_shared_curve_controls
        evaluation = evaluate_shared_curve_controls(project, reference, flat['receiver_calibration'], cancel=cancel)
        reference['shared_receiver_evaluation'] = evaluation
        charts = evaluation.pop('routed_charts', [])
        if charts:reference['routed_charts'] = charts
        else:
            for item in reference['channels']:item['limitations'].append(evaluation['reason'])
    flat['curve_designs'] = {'Reference': reference}
    flat['settings']['curve_profiles'] = profiles
    flat['curve_configuration'] = {
        'schema': 'audioseyx-calibration-curves-1', 'independent_eq_targets': True,
        'primary_curve': 'Flat', 'shared_receiver_controls_from': 'Flat',
        'curve_slots': copy.deepcopy(CURVE_SPECS),
        'receiver_mapping_hardware_verified': False,
        'notice': 'Two independent static minimum-phase EQ designs. Speaker distances, trims, crossovers and sub-filter polarity are common to both slots. Any native-menu proposal is selected using Flat only; Reference is evaluated with those controls fixed, not independently reoptimized. Static target selection is not Dynamic EQ or automatic loudness compensation.'}
    curve_designs(flat)
    return flat
