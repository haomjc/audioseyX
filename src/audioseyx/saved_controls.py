"""Data-linked interface defaults, never restored measurement/transfer consent."""
from __future__ import annotations

import copy
import hashlib
import json
import numpy as np

from . import __version__
from .receiver_constraints import snapshot_fingerprint


FIELDS=frozenset('''tier optimizer phase_mode low_hz high_hz sub_high_hz max_boost_db max_cut_db
    bass_db tilt_db_oct smoothing_octave phase_high_hz delay_ms crossover_hz
    joint_phase_target joint_delay_ms position_risk joint_iterations joint_bound_tolerance_db joint_memory_mib
    fdw_cycles fdw_reference fdw_start_hz large_channels crossover_model bass_mode lfe_lowpass_hz
    receiver_control_engine receiver_curve_control_mode receiver_curve_weights receiver_curve_risk
    receiver_curve_risk_temperature receiver_lfe_priority receiver_sub_level_search receiver_sub_level_min_db
    receiver_sub_level_max_db receiver_native_state_batch receiver_max_delay_ms receiver_delay_speed_m_s
    receiver_memory_mib receiver_auditory_balance receiver_time_crossover_search receiver_crossover_candidates_hz
    receiver_measurement_context receiver_analysis_low_hz receiver_analysis_high_hz receiver_position_risk
    validation_positions temporal_diagnostics temporal_memory_mib'''.split())
PROFILE_FIELDS=frozenset('low_hz high_hz sub_high_hz max_boost_db max_cut_db bass_db tilt_db_oct smoothing_octave'.split())


def project_signature(project):
    digest=hashlib.sha256()
    header={'model':project.model,'tier':project.tier,'groups':project.metadata.get('same_model_groups'),
            'listening_area':project.metadata.get('listening_area'),
            'snapshot':snapshot_fingerprint(project.metadata.get('receiver_settings_snapshot',{}))}
    digest.update(json.dumps(header,sort_keys=True,allow_nan=False).encode())
    for m in project.measurements:
        digest.update(json.dumps([m.channel,m.position,m.sample_rate,m.start_time_s,m.timing_reference],allow_nan=False).encode())
        for value in [m.ir,m.frequency,m.magnitude_db,m.phase_deg]:
            if value is None:digest.update(b'none')
            else:
                array=np.asarray(value,dtype='<f8')
                digest.update(str(array.shape).encode());digest.update(array.tobytes())
    return digest.hexdigest()


def safe_controls(settings):
    if not isinstance(settings,dict):raise ValueError('Saved processing controls must be an object')
    result={k:copy.deepcopy(v) for k,v in settings.items() if k in FIELDS}
    if isinstance(settings.get('curve_profiles'),dict):
        result['curve_profiles']={name:{k:copy.deepcopy(v) for k,v in profile.items() if k in PROFILE_FIELDS}
                                  for name,profile in settings['curve_profiles'].items()
                                  if name in ['Flat','Reference'] and isinstance(profile,dict)}
    # These flags describe a new user's decision for the current workflow.
    # A prior successful computation is not consent to reuse them on startup.
    result.update(timing_confirmed=False,relative_levels_confirmed=False,routing_measurements_confirmed=False,
                  uncorrected_measurements_confirmed=False,apply_receiver_adjustments=False,
                  allow_partial_listening_area=False,receiver_distance_basis='preserve')
    encoded=json.dumps(result,allow_nan=False)
    if len(encoded.encode())>2*1024**2:raise ValueError('Saved processing controls exceed the size limit')
    return result


def make_saved_controls(project, settings):
    return {'schema':'audioseyx-saved-controls-1','software_version':__version__,
            'project_signature':project_signature(project),'settings':safe_controls(settings)}


def load_saved_controls(path, project):
    if project is None or not path.is_file():return None
    if path.stat().st_size>2*1024**2:raise ValueError('Saved processing controls exceed the size limit')
    def reject_constant(value):raise ValueError('Non-finite saved controls')
    document=json.loads(path.read_text(encoding='utf-8'),parse_constant=reject_constant)
    if not isinstance(document,dict) or document.get('schema')!='audioseyx-saved-controls-1':
        raise ValueError('Unknown saved processing controls schema')
    if document.get('project_signature')!=project_signature(project):return None
    return safe_controls(document.get('settings'))
