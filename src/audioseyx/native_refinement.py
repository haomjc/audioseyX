"""Monotone block refinement of complete native minimum-phase EQ and controls.

Magnitude sensitivities propose a projected minimum-phase step. Its acceptance
uses complete returned FIRs, refitted native controls and frozen physical targets.
The proposal direction is not an exact minimum-phase or global optimizer.
"""
from __future__ import annotations

import copy
import numpy as np
from scipy.fft import rfft, irfft, next_fast_len

from .calibration_curves import PROFILE_KEYS, fir_fingerprint
from .dsp import min_phase_spectrum, band_weight, smooth_log, evaluate_channel_filter
from .joint_curve_controls import calibrate_joint_curves, _curve_grids
from .model import Project
from .native_power_model import power_scenarios, with_lfe_group
from .receiver_calibration import _linked_groups
from .spectral_grid import position_weights
from .risk_derivatives import fixed_choice_row_gradient


def _cancel(cancel):
    if cancel is not None and cancel.is_set():
        raise InterruptedError('Native EQ/control refinement cancelled; no candidate activated')


def _context(project, curves, calibration, settings, cancel):
    validation = set(curves['Flat'].get('validation_positions', []))
    train = Project(project.name, [m for m in project.measurements if m.position not in validation],
                    project.origin, project.model, project.tier, dict(project.metadata), id=project.id)
    grids = _curve_grids(train, curves, float(settings.get('receiver_memory_mib', 512)), cancel)
    snapshot = settings.get('receiver_snapshot', project.metadata.get('receiver_settings_snapshot', {}))
    mains = [c for c in train.channels if not c.startswith('SW')]
    groups = with_lfe_group(_linked_groups(mains, snapshot, calibration['profile'], settings), settings)
    proposal = calibration['proposal']
    internal = {name: {**settings, **{k:v for k,v in design['settings'].items() if k in PROFILE_KEYS},
        'receiver_delay_speed_m_s': calibration['distance_basis']['receiver_speed_m_s'],
        '_receiver_base_distances_m': proposal['distances_m'],
        '_fixed_inverted_outputs': proposal['inverted_outputs'],
        '_receiver_target_references_db': calibration['curve_target_references_db'][name]}
        for name, design in curves.items()}
    return train, grids, snapshot, groups, internal, position_weights(train, settings)


def magnitude_sensitivities(grids, snapshot, groups, internal, proposal, positions, weights, crossovers, cancel=None):
    """Exact derivatives for independent real log-amplitudes at the current state.

    Phase changes caused by minimum-phase projection are evaluated at acceptance,
    rather than omitted from the final objective.
    """
    selected = {g['id']: [crossovers[g['id']]] for g in groups}
    rows, losses = [], []
    for name, grid in grids.items():
        for row, _, w, pairs in power_scenarios(grid, snapshot, internal[name], groups, proposal['levels_db'], positions, selected):
            _cancel(cancel)
            losses.append(sum(p*float(w@((abs(m+s)**2-1)**2)) for m,s,p in pairs.weighted()))
            rows.append({**row, 'curve_profile':name})
    table = dict(rows=rows, groups=groups, curve_weights=weights)
    objective, derivatives = fixed_choice_row_gradient(table, losses, next(iter(internal.values())), crossovers)
    gradients = {}
    index = 0
    frequency = None
    for name, grid in grids.items():
        sub = next(c for c in grid.items if c.startswith('SW'))
        gradients[name] = {}
        for row, f, w, pairs in power_scenarios(grid, snapshot, internal[name], groups, proposal['levels_db'], positions, selected):
            _cancel(cancel)
            frequency = f
            source = row['source']
            bass_gradient = gradients[name].setdefault(sub, np.zeros(len(f)))
            main_gradient = None if source == 'LFE' else gradients[name].setdefault(source, np.zeros(len(f)))
            for main,bass,probability in pairs.weighted():
                total = main+bass
                factor = 4*derivatives[index]*probability*w*(abs(total)**2-1)
                bass_gradient += factor*np.real(np.conj(total)*bass)
                if main_gradient is not None:
                    main_gradient += factor*np.real(np.conj(total)*main)
            index += 1
    return objective, frequency, gradients


def _project_item(item, seed, grid, settings, frequencies, gradient, step_db, smoothing):
    fs = item['sample_rate']
    nfft = next_fast_len(max(65536, len(item['taps'])*4))
    f = np.fft.rfftfreq(nfft, 1/fs)
    current = 20*np.log10(np.maximum(abs(rfft(item['taps'], nfft)), 1e-15))
    original = 20*np.log10(np.maximum(abs(rfft(seed['taps'], nfft)), 1e-15))
    density = gradient*np.maximum(frequencies, 1)
    density = smooth_log(density, frequencies, smoothing)
    size = float(np.max(abs(density)))
    if size < 1e-15:
        return copy.deepcopy(item), True
    delta = np.interp(f, frequencies, -step_db*density/size, left=0, right=0)
    low, high = item['metrics']['effective_correction_band_hz']
    gate = band_weight(f, low, high)
    source = [i for i,m in enumerate(grid.project.measurements) if m.channel == item['channel']]
    raw = np.array([np.interp(f, grid.frequency, 20*np.log10(np.maximum(abs(grid.responses[i]), 1e-15))) for i in source])
    centre = np.median(raw, axis=0)
    spread = 1.4826*np.median(abs(raw-centre), axis=0)
    trust = np.clip(1-spread/12, 0, 1)*np.clip((centre-item['metrics']['reference_level_db']+18)/12, 0, 1)
    upper = np.maximum(original, float(settings.get('max_boost_db', 3))*trust*gate)
    lower = np.minimum(original, -float(settings.get('max_cut_db', 12))*gate)
    wanted = np.clip(current+delta*gate, lower, upper)
    full = irfft(min_phase_spectrum(10**(wanted/20)), nfft)
    taps = full[:len(item['taps'])].copy()
    fade = min(64, max(4, len(taps)//16), len(taps))
    taps[-fade:] *= np.linspace(1, 0, fade)**2
    actual = 20*np.log10(np.maximum(abs(rfft(taps, nfft)), 1e-15))
    feasible = bool(np.isfinite(taps).all() and np.all(actual <= upper+.1) and np.all(actual >= lower-.1))
    result = copy.deepcopy(item)
    result['taps'] = taps
    result['metrics'].update(filter_peak=float(abs(taps).max()), coefficient_l1_gain_bound=float(abs(taps).sum()),
        truncated_energy_fraction=float(np.sum(full[len(taps):]**2)/max(np.sum(full**2), 1e-30)))
    result['refinement_projection'] = dict(method='full_length_cepstral_minimum_phase',
        gain_envelope_tolerance_db=.1, protected_deep_null_and_spatial_boost=True,
        projection_frequency_bins=len(f), actual_returned_FIR_checked=True)
    return result, feasible


def _refresh_reports(project, curves, settings, cancel=None):
    for design in curves.values():
        held = set(design.get('validation_positions', []))
        for item in design['channels']:
            _cancel(cancel)
            old = item['chart']
            from scipy import signal
            gain = 20*np.log10(np.maximum(abs(signal.freqz(item['taps'], worN=np.asarray(old['frequency']), fs=item['sample_rate'])[1]), 1e-15))
            old['after'] = (np.asarray(old['before'])+gain).tolist()
            training = [m for m in project.measurements if m.channel == item['channel'] and m.position not in held]
            evaluation = evaluate_channel_filter(training, item, design['settings'])
            item['metrics']['rms_after_db'] = evaluation['rms_after_db']
            item['metrics']['rms_before_db'] = evaluation['rms_before_db']
            item['metrics']['rms_weighting'] = 'equal_seats_then_equal_repeats_EQ_stage_only'
        if held:
            design['independent_validation'] = dict(positions=sorted(held), used_for_design=False,
                stage='refined_EQ_FIR_before_receiver_controls', channels=[evaluate_channel_filter(
                    [m for m in project.measurements if m.channel == item['channel'] and m.position in held],
                    item, design['settings']) for item in design['channels']])
        if settings.get('temporal_diagnostics', False):
            from .temporal import predict_filter_temporal
            design['temporal_prediction'] = predict_filter_temporal(project, design,
                memory_mib=float(settings.get('temporal_memory_mib', 128)),cancel=cancel)


def refine_native_curves(project, curves, settings, *, headroom_curves=None, progress=None, cancel=None):
    enabled = settings.get('receiver_eq_refinement', False)
    if not isinstance(enabled, bool):
        raise ValueError('EQ refinement must be an explicit boolean')
    calibration = calibrate_joint_curves(project, curves, settings, headroom_curves=headroom_curves,
                                         progress=progress, cancel=cancel)
    if not enabled:
        return calibration
    if not calibration['search']:
        raise ValueError('EQ/control refinement requires complete one-sub timing and routing references')
    rounds = settings.get('receiver_refinement_rounds', 3)
    step = float(settings.get('receiver_refinement_step_db', .5))
    smoothing = float(settings.get('receiver_refinement_smoothing_octave', 1/3))
    if isinstance(rounds, bool) or not isinstance(rounds, int) or not 1 <= rounds <= 12 or not np.isfinite([step,smoothing]).all() or not .05 <= step <= 3 or not 1/24 <= smoothing <= 2:
        raise ValueError('Invalid native EQ refinement budget or step controls')
    frozen = copy.deepcopy(calibration['curve_target_references_db'])
    accepted = copy.deepcopy(curves)
    score = calibration['direct_forward_check']['objective']
    trace = [dict(round=0, accepted=True, objective=score, block='initial_native_controls')]
    stop = 'round_budget'
    for iteration in range(1, rounds+1):
        _cancel(cancel)
        train,grids,snapshot,groups,internal,positions = _context(project, accepted, calibration, settings, cancel)
        objective,f,gradients = magnitude_sensitivities(grids,snapshot,groups,internal,calibration['proposal'],positions,
            calibration['control_selection']['curve_weights'],calibration['search']['crossovers_hz'],cancel)
        if abs(objective-score) > 1e-8*max(1.,abs(score)):
            raise ValueError('Refinement objective changed before proposing a step')
        candidates = []
        for fraction in (1., .5, .25, .125):
            candidate = copy.deepcopy(accepted)
            feasible = True
            for name,design in candidate.items():
                eq = {**settings, **{k:v for k,v in design['settings'].items() if k in PROFILE_KEYS}}
                for index,item in enumerate(design['channels']):
                    _cancel(cancel)
                    proposed,ok = _project_item(item,curves[name]['channels'][index],grids[name],eq,f,gradients[name][item['channel']],step*fraction,smoothing)
                    design['channels'][index] = proposed
                    feasible &= ok
            candidates.append((fraction,candidate,feasible))
        # Release full raw-response grids before any candidate's full-grid search.
        del grids
        improved = False
        for fraction,candidate,feasible in candidates:
            _cancel(cancel)
            entry = dict(round=iteration, step_db=step*fraction, accepted=False)
            if not feasible:
                trace.append({**entry,'reason':'returned_FIR_gain_envelope'})
                continue
            if progress:
                progress(86, f'EQ/control refinement {iteration}/{rounds}; step {step*fraction:g} dB')
            reserve = {**(headroom_curves or accepted), **candidate}
            try:
                proposed = calibrate_joint_curves(project,candidate,settings,headroom_curves=reserve,
                    frozen_references=frozen,cancel=cancel)
            except ValueError as error:
                trace.append({**entry,'reason':'candidate_infeasible','detail':str(error)})
                continue
            value = proposed['direct_forward_check']['objective']
            if value < score-1e-10*max(1.,abs(score)):
                trace.append({**entry,'accepted':True,'objective':value,'previous_objective':score})
                accepted,calibration,score = candidate,proposed,value
                improved = True
                break
            trace.append({**entry,'objective':value,'reason':'no_strict_training_improvement'})
        if not improved:
            stop = 'no_accepted_projected_step'
            break
    _cancel(cancel)
    _refresh_reports(project,accepted,settings,cancel)
    _cancel(cancel)
    for name in curves:
        curves[name]['channels'] = accepted[name]['channels']
        for key in ('independent_validation','temporal_prediction'):
            if key in accepted[name]:curves[name][key] = accepted[name][key]
    report = dict(schema='audioseyx-native-eq-refinement-1', trace=trace, stop_reason=stop,
        rounds_requested=rounds, accepted_EQ_steps=sum(t.get('accepted',False) for t in trace[1:]),
        initial_objective=trace[0]['objective'], final_objective=score,
        frozen_training_target_references_db=frozen, held_out_used_for_selection=False,
        complete_FIRs_and_frequency_bins_retained=True, receiver_controls_refitted_at_each_feasible_trial=True,
        global_minimum_claimed=False, hardware_verified=False,
        output_FIR_sha256={n:{i['channel']:fir_fingerprint(i['taps']) for i in d['channels']} for n,d in curves.items()},
        notice='Projected magnitude-sensitivity proposals include full minimum-phase reconstruction and native control refitting. Only strictly improving feasible full-response training objectives are accepted. A stopped local search does not establish stationarity, a global optimum or measured acoustic improvement.')
    calibration['eq_refinement'] = report
    calibration['control_selection']['fixed_FIRs'] = False
    calibration['control_selection']['FIRs_fixed_within_each_native_grid_search'] = True
    return calibration
