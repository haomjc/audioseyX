"""Engineering development checklist, deliberately not a product performance score."""
from __future__ import annotations

from . import __version__


FEATURES = (
    ('room_targets', 'Room Correction', 'Magnitude EQ, target curves and correction bands', 'implemented_offline',
     'native_candidate_unverified', 'Full retained tier budget; microphone compensation and playback reconstruction remain unverified.'),
    ('spatial', 'Room Correction', 'Measured positions, seat priorities and independent validation', 'implemented_offline',
     'native_candidate_unverified', 'Missing seats cannot be filled by repeated scans of another position.'),
    ('alignment', 'Room Correction', 'Native distance and level alignment', 'implemented_offline',
     'native_candidate_unverified', 'Declared menu constraints and distance conversion; current X3700H firmware behavior still needs measurement.'),
    ('mixed_phase', 'Room Correction', 'Full complex frequency-dependent phase / impulse correction', 'reference_only',
     'export_only', 'Full-tap solver and FDW goals exist; writable mixed-phase synthesis and native playback are not validated.'),
    ('verification', 'Room Correction', 'Before/after frequency and temporal verification', 'implemented_offline',
     'measurement_workflow', 'Original IRs, comparable playback paths and sufficient recording length are required; fits can be unavailable.'),
    ('crossover', 'Bass Control', 'Main/sub crossover, shared delay and filter-polarity planning', 'implemented_offline',
     'native_candidate_unverified', 'One physical measured sub in the menu search, fixed FIR/trim state and explicit routing-model assumptions.'),
    ('multi_sub', 'Bass Control', 'Jointly optimized independently controlled subwoofers', 'reference_only',
     'export_only', 'Multi-sub complex FIR reference solver exists; independently writable output DSP and multi-sub native menu search are not established.'),
    ('bass_seats', 'Bass Control', 'Shared-bass multi-position risk and held-out evaluation', 'implemented_offline',
     'native_candidate_unverified', 'Model evaluation uses actual recorded positions and repeats, not a guaranteed result at unmeasured seats.'),
    ('support_routes', 'Active Room Treatment', 'Arbitrary main-to-main support and per-input cross filters', 'interface_blocked',
     'not_provided', 'The observed Audyssey coefficient interface does not expose the required arbitrary cross-channel convolution matrix.'),
    ('active_decay', 'Active Room Treatment', 'Cooperative active modal-decay suppression', 'interface_blocked',
     'not_provided', 'Energy/decay analysis is available; it is not active cancellation with ART support signals.'),
)


def capability_assessment(project=None):
    features = [dict(zip(('id', 'category', 'name', 'software_state', 'native_state', 'scope'), row)) for row in FEATURES]
    counts = {key: sum(f['software_state']==key for f in features)
              for key in ['implemented_offline', 'reference_only', 'interface_blocked']}
    context = None
    if project is not None:
        summary = project.summary()
        snapshot = project.metadata.get('receiver_settings_snapshot', {})
        configured = snapshot.get('configured_channels', []) if snapshot.get('available') else []
        context = {'model': project.model, 'tier': project.tier, 'layout': summary['layout']['signature'],
                   'measurement_count': len(project.measurements), 'original_ir_count': summary['ir_count'],
                   'measured_positions': summary['positions'],
                   'missing_seats': (summary.get('listening_area_coverage') or {}).get('missing_training_seats', []),
                   'configured_unmeasured_outputs': sorted(set(configured)-set(project.channels)),
                   'unsafe_capture_indices': summary['unsafe_capture_indices'],
                   'timing_reference_labels': sorted({m.timing_reference for m in project.measurements}),
                   'independent_sub_filter_control_verified': summary['layout']['independent_subwoofer_filters_verified'],
                   'normal_playback_verified_in_project': project.metadata.get('normal_playback_verified') is True}
    return {'schema': 'audioseyx-capability-assessment-1', 'version': __version__,
            'scope': 'Ten selected engineering capabilities, independently implemented; not Dirac code or a completeness specification.',
            'counts': counts, 'features': features, 'project_context': context,
            'native_hardware_validation_complete': False, 'performance_percentage': None,
            'sources': [
                'https://www.dirac.com/products/room-correction',
                'https://www.dirac.com/products/bass-control',
                'https://helpdesk.dirac.com/en/dirac-bass-control/Dirac-Live-Active-Room-Treatment-ART-FAQ-9be7'],
            'notice': 'Counts describe this project development checklist, not percentages of Dirac algorithms, acoustic quality or native execution. ART currently documents 20-150 Hz active operation; full-band room correction is a separate function. All native playback and acoustic claims still need model-specific verification.'}
