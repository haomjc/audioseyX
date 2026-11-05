"""Joint native-menu planning with exact finite-grid conditional separability.

All native distance grid points, both sub polarities and every supplied linked
crossover choice are considered. Given a sub delay/polarity, linked crossover
groups are independent under a monotone nested risk objective. Taking each
group's best choice is therefore equivalent to enumerating their Cartesian
product. No FIR taps or measured frequency bins are removed.
"""
from __future__ import annotations

from itertools import product
import copy
from math import prod
import numpy as np
from scipy import signal
from scipy.special import logsumexp

from .auditory_balance import balance_groups
from .curves import target_offset
from .model import Project
from .receiver_constraints import receiver_profile, snapshot_fingerprint, quantized_distance_proposal, distance_basis, validate_receiver_proposal
from .receiver_web import CROSSOVER_GROUPS
from .routing import crossover_responses
from .spectral_grid import SpectralGrid, position_weights

CONTEXTS = ('native_uncorrected', 'uncrossed_playback_eq_off')


def smooth_risk(values, masses, weight, temperature):
    values=np.asarray(values,dtype=float);masses=np.asarray(masses,dtype=float)
    if values.ndim not in [1,2] or masses.shape!=(values.shape[0],) or not np.isfinite(values).all() or not np.isfinite(masses).all() or np.any(masses<0) or not np.any(masses>0):
        raise ValueError('Risk values and masses must be complete and finite')
    if not np.isfinite([weight,temperature]).all() or not 0<=weight<=1 or temperature<=0:
        raise ValueError('Invalid smooth-risk controls')
    positive=masses>0;values=values[positive];masses=masses[positive]
    masses/=masses.max();masses/=masses.sum()
    mean=masses@values
    if weight==0:return mean
    reference=values.max(axis=0)
    with np.errstate(over='ignore'):
        shifted=(values-reference)/temperature
    worst=reference+temperature*logsumexp(shifted,b=masses[:,None] if values.ndim==2 else masses,axis=0)
    return worst if weight==1 else (1-weight)*mean+weight*worst


def _linked_groups(mains, snapshot, profile, settings):
    remaining = set(mains); groups = []
    links = settings.get('receiver_crossover_groups') or list(CROSSOVER_GROUPS.values())
    if settings.get('receiver_crossover_groups'):
        flattened = [c for group in links for c in group]
        if len(flattened) != len(set(flattened)) or set(flattened) != set(mains):
            raise ValueError('Explicit linked crossover groups must partition every measured main output exactly once')
    requested = settings.get('receiver_crossover_candidates_hz', profile['crossovers_hz'])
    if isinstance(requested, str): requested = [float(v.strip()) for v in requested.split(',') if v.strip()]
    if not isinstance(requested, (list, tuple)) or not 1 <= len(requested) <= 32:
        raise ValueError('Supply 1-32 explicit native crossover candidates')
    if any(v not in profile['crossovers_hz'] for v in requested):
        raise ValueError('A crossover candidate is not on the selected receiver menu grid')
    for linked in list(links) + [[c] for c in mains]:
        channels = [c for c in linked if c in remaining]
        if not channels: continue
        remaining.difference_update(channels)
        current = [snapshot.get('crossovers', {}).get(c) for c in channels]
        small = [snapshot.get('speaker_types', {}).get(c) == 'small' for c in channels]
        if not all(small) and any(small):
            raise ValueError('Linked crossover groups must have consistent speaker types')
        if not any(small) and snapshot.get('bass_mode') == 'lfe':
            groups.append({'id': '/'.join(channels), 'channels': channels, 'current_hz': None,
                           'choices_hz': [None], 'small': False})
            continue
        if any(v is None or v not in profile['crossovers_hz'] for v in current) or len(set(current)) != 1:
            raise ValueError('A complete, consistent baseline crossover is required for each linked group')
        # Large outputs keep their current extraction model; new Large/bass
        # modes are not guessed or introduced by this workflow.
        choices = sorted(set(list(requested) + current)) if all(small) else [current[0]]
        groups.append({'id': '/'.join(channels), 'channels': channels, 'current_hz': current[0],
                       'choices_hz': choices, 'small': all(small)})
    return groups


def _cost_table(grid, snapshot, settings, groups, levels, positions, selected=None):
    models = settings.get('receiver_routing_models', [])
    if models:
        if not isinstance(models, (list, tuple)) or not 1 <= len(models) <= 2 or len(set(models)) != len(models) or any(m not in ['lr4', 'bw2_lr4'] for m in models):
            raise ValueError('Choose one or both explicit bass-management slope models')
        if len(models) == 1:
            return _cost_table(grid, snapshot, {**settings, 'receiver_routing_models': [], 'crossover_model': models[0]}, groups, levels, positions, selected)
        low = float(settings.get('receiver_analysis_low_hz', 20))
        high = float(settings.get('receiver_analysis_high_hz', min(20000, grid.high, grid.rate * .42)))
        bins = int(np.count_nonzero((grid.frequency >= low) & (grid.frequency <= high)))
        entries = sum(len(g['choices_hz'] if selected is None else selected[g['id']]) * len(g['channels']) * len(positions) for g in groups)
        estimate = grid.estimated_memory_mib + entries * bins * 32 * len(models) * 2 / 1024**2 + 64
        if estimate > float(settings.get('receiver_memory_mib', 512)):
            raise ValueError('The full routing-model ensemble exceeds the explicit memory budget; no models or bins were omitted')
        tables = [_cost_table(grid, snapshot, {**settings, 'receiver_routing_models': [], 'crossover_model': m}, groups, levels, positions, selected) for m in models]
        combined = dict(tables[0])
        combined['rows'] = [{**r, 'routing_model': model, 'mass': r['mass'] / len(models)} for model, table in zip(models, tables) for r in table['rows']]
        for key in ['constant', 'first', 'second']: combined[key] = np.concatenate([t[key] for t in tables], axis=0)
        combined['estimated_memory_mib'] = estimate
        return combined
    context = settings['receiver_measurement_context']
    sub = [c for c in grid.items if c.startswith('SW')]
    if len(sub) != 1:
        raise ValueError('Native-menu timing search currently requires one measured physical subwoofer; multi-sub reference FIR design remains separate')
    sub = sub[0]
    low = float(settings.get('receiver_analysis_low_hz', 20))
    high = float(settings.get('receiver_analysis_high_hz', min(20000, grid.high, grid.rate * .42)))
    if not grid.low <= low < high <= min(grid.high, grid.rate * .49):
        raise ValueError('Native-menu analysis requires a complete common measured frequency band')
    mask = (grid.frequency >= low) & (grid.frequency <= high)
    f = grid.frequency[mask]
    if len(f) < 4: raise ValueError('Insufficient measured frequency bins for native-menu search')
    weight = 1 / np.maximum(f, low); weight /= weight.sum()
    speed = float(settings.get('receiver_delay_speed_m_s', 343))
    current_level = snapshot['levels_db']; distances = settings.get('_receiver_base_distances_m', snapshot['distances_m'])
    if context not in CONTEXTS:
        raise ValueError('Choose an explicit uncrossed measurement context; active-EQ playback and unknown gain/delay paths are not assumed')
    if not 250 <= speed <= 400: raise ValueError('Invalid assumed receiver distance/delay conversion')
    responses = []
    for index, m in enumerate(grid.project.measurements):
        gain = levels[m.channel] if context == 'native_uncorrected' else levels[m.channel] - current_level[m.channel]
        phase_distance = distances[m.channel] if context == 'native_uncorrected' else distances[m.channel] - snapshot['distances_m'][m.channel]
        responses.append(grid.corrected(index)[mask] * 10 ** (gain / 20) * np.exp(2j * np.pi * f * phase_distance / speed))
    rows, constants, first, second = [], [], [], []
    entries = sum(len(g['choices_hz'] if selected is None else selected[g['id']]) * len(g['channels']) * len(positions) for g in groups)
    estimated = grid.estimated_memory_mib + entries * len(f) * 32 / 1024**2 + 64
    if estimated > float(settings.get('receiver_memory_mib', 512)):
        raise ValueError('The full native-menu cost table exceeds the explicit memory budget; no bins, positions or choices were dropped')
    offsets = target_offset(settings['target_curve'], f) if settings.get('target_curve') else (
        float(settings.get('bass_db', 3)) / (1 + (f / 100)**4)
        + float(settings.get('tilt_db_oct', -.5)) * np.log2(np.maximum(f, 1000) / 1000))
    for group_index, group in enumerate(groups):
        choices = group['choices_hz'] if selected is None else selected[group['id']]
        for hz in choices:
            hp, lp = (np.ones(len(f), complex), np.zeros(len(f), complex)) if hz is None else crossover_responses(f, hz, settings.get('crossover_model', 'lr4'), grid.rate)
            for source in group['channels']:
                large = snapshot['speaker_types'][source] == 'large'
                main_route = np.ones_like(hp) if large else hp
                sub_route = lp if not large or snapshot.get('bass_mode') == 'lfe_plus_main' else np.zeros_like(lp)
                ref = grid.items[source]['metrics']['reference_level_db']
                if context == 'native_uncorrected': ref += current_level[source]
                ref = settings.get('_receiver_target_references_db', {}).get(source, ref)
                target = 10 ** ((ref + offsets) / 20)
                for position, mass in positions.items():
                    main_indices = grid.groups.get((source, position), [])
                    sub_indices = grid.groups.get((sub, position), [])
                    if not main_indices or not sub_indices:
                        raise ValueError('Native timing search requires every main/sub output at every retained position')
                    c0 = 0.; c1 = np.zeros(len(f), complex); c2 = c1.copy()
                    count = len(main_indices) * len(sub_indices)
                    for a, b in product(main_indices, sub_indices):
                        main = responses[a] * main_route / target
                        bass = responses[b] * sub_route / target
                        p = np.abs(main)**2 + np.abs(bass)**2 - 1
                        cross = np.conj(main) * bass
                        c0 += float(weight @ (p**2 + 2 * np.abs(cross)**2)) / count
                        c1 += weight * 4 * p * cross / count
                        c2 += weight * 2 * cross**2 / count
                    if not np.isfinite(c0) or not np.isfinite(c1).all() or not np.isfinite(c2).all():
                        raise ValueError('The measured native-menu power model is not numerically finite')
                    rows.append({'group': group_index, 'crossover_hz': hz, 'source': source,
                                 'position': position, 'mass': mass / len(group['channels']), 'repeat_scenarios': count})
                    constants.append(c0); first.append(c1); second.append(c2)
    return {'rows': rows, 'constant': np.asarray(constants), 'first': np.asarray(first), 'second': np.asarray(second),
            'frequency': f, 'groups': groups, 'band_hz': [low, high], 'estimated_memory_mib': estimated}


def _risk_layout(table):
    if '_risk_layout' in table:return table['_risk_layout']
    profiles=list(table.get('curve_weights',{})) or [None]
    if profiles!=[None]:
        weights=np.asarray(list(table['curve_weights'].values()),dtype=float)
        if not np.isfinite(weights).all() or np.any(weights<=0):raise ValueError('Every curve needs a positive finite risk weight')
    layout=[]
    for index,group in enumerate(table['groups']):
        possible=sorted({r['crossover_hz'] for r in table['rows'] if r['group']==index},key=lambda v:-1 if v is None else v)
        if group['current_hz'] in possible:possible=[group['current_hz']]+[v for v in possible if v!=group['current_hz']]
        variants={}
        for hz in possible:
            entries={}
            for profile in profiles:
                indices=np.asarray([i for i,row in enumerate(table['rows']) if row['group']==index and row['crossover_hz']==hz
                                    and (profile is None or row.get('curve_profile')==profile)],dtype=int)
                if not len(indices):raise ValueError('Every declared curve must contribute to every crossover choice')
                entries[profile]=(indices,np.asarray([table['rows'][i]['mass'] for i in indices]))
            variants[hz]=entries
        layout.append(variants)
    table['_risk_layout']=layout
    return layout


def _group_costs(table, losses, settings, select_best=True, chosen=None, profile_only=None):
    risk = float(settings.get('receiver_position_risk', .35)); temperature = float(settings.get('receiver_risk_temperature', .15))
    if not 0 <= risk <= 1 or not 0 < temperature <= 10:
        raise ValueError('Invalid native calibration risk controls')
    values, choices, masses = [], [], []
    penalty = float(settings.get('receiver_crossover_change_penalty', .002))
    if not 0 <= penalty <= 10: raise ValueError('Invalid crossover-change penalty')
    layout=_risk_layout(table)
    curve_weights=table.get('curve_weights') if profile_only is None else None
    curve_risk=float(settings.get('receiver_curve_risk',.35));curve_temperature=float(settings.get('receiver_curve_risk_temperature',.15))
    if curve_weights and (not np.isfinite([curve_risk,curve_temperature]).all() or not 0<=curve_risk<=1 or not 0<curve_temperature<=10):
        raise ValueError('Invalid shared-curve risk controls')
    for g_index, group in enumerate(table['groups']):
        variants = []; frequencies = []
        possible = list(layout[g_index])
        if not select_best: possible = [chosen[group['id']]]
        for hz in possible:
            entries=layout[g_index].get(hz)
            if entries is None:raise ValueError('A selected native crossover is absent from the complete objective')
            if curve_weights:
                values_by_curve=[]
                for name in curve_weights:
                    selected,masses_by_curve=entries[name]
                    values_by_curve.append(smooth_risk(losses[selected],masses_by_curve,risk,temperature))
                value=smooth_risk(np.asarray(values_by_curve),np.asarray(list(curve_weights.values())),curve_risk,curve_temperature)
            else:
                indices,weight=entries[profile_only]
                value=smooth_risk(losses[indices],weight,risk,temperature)
            variants.append(value + (penalty * np.log2(hz / group['current_hz'])**2 if hz is not None else 0))
            frequencies.append(hz)
        variants = np.asarray(variants)
        best = np.argmin(variants, axis=0)
        values.append(np.take_along_axis(variants, best[None, :], axis=0)[0])
        choices.append(np.asarray(frequencies)[best])
        masses.append(group.get('mass',len(group['channels'])))
    values = np.asarray(values)
    objective = smooth_risk(values, np.asarray(masses), risk, temperature)
    return objective, choices, values


def evaluate_settings_table(table, delay, inverted, settings, crossovers=None, *, sub_gain=1.):
    if not np.isfinite(sub_gain) or sub_gain<=0:raise ValueError('Subwoofer gain must be positive and finite')
    if 'delay_grid_s' in table:
        grid=table['delay_grid_s'];index=int(np.argmin(np.abs(grid-delay)))
        if abs(grid[index]-delay)>1e-12:raise ValueError('The requested evaluation delay is not on the complete native grid')
        g=sub_gain;c=table['gain_constant']
        first=g*table['gain_first_linear_at_delays'][:,index]+g**3*table['gain_first_cubic_at_delays'][:,index]
        losses=c[:,0]+g*g*c[:,1]+g**4*c[:,2]+np.real((-first if inverted else first)+g*g*table['second_at_delays'][:,index])
    elif 'gain_constant' in table:
        phase = np.exp(-2j * np.pi * table['frequency'] * delay)
        g=sub_gain;c=table['gain_constant']
        first=g*(table['gain_first_linear']@phase)+g**3*(table['gain_first_cubic']@phase)
        losses=c[:,0]+g*g*c[:,1]+g**4*c[:,2]+np.real((-first if inverted else first)+g*g*(table['second']@(phase**2)))
    else:
        phase = np.exp(-2j * np.pi * table['frequency'] * delay)
        if sub_gain!=1:raise ValueError('This objective has no declared subwoofer gain moments')
        losses = table['constant'] + np.real(((-1 if inverted else 1) * table['first']) @ phase + table['second'] @ (phase**2))
    losses = np.maximum(losses, 0)[:, None]
    objective, choices, values = _group_costs(table, losses, settings, crossovers is None, crossovers)
    result = {'objective': float(objective[0]),
            'crossovers_hz': {g['id']: None if c[0] is None else float(c[0]) for g, c in zip(table['groups'], choices)},
            'group_power_error': {g['id']: float(v[0]) for g, v in zip(table['groups'], values)}}
    if table.get('curve_weights'):
        result['per_curve']={}
        for name in table['curve_weights']:
            score,_,groups=_group_costs(table,losses,settings,False,result['crossovers_hz'],profile_only=name)
            result['per_curve'][name]={'objective':float(score[0]),
                                      'group_power_error':{g['id']:float(v[0]) for g,v in zip(table['groups'],groups)},
                                      'controls_reselected':False}
    return result


def search_native_grid(table, snapshot, profile, settings, *, progress=None, cancel=None):
    speed = float(settings.get('receiver_delay_speed_m_s', profile.get('distance_delay_speed_m_s', 343))); maximum = float(settings.get('receiver_max_delay_ms', 15)) / 1000
    if not 0 <= maximum <= .1 or not 250 <= speed <= 400:
        raise ValueError('Invalid native delay search range or assumed distance/delay conversion')
    quantum = profile['distance_step_m'] / speed
    count = int(np.floor(maximum / quantum + 1e-9))
    # Program rows describe mains; the selected measured sub is explicit.
    sub = settings['_measured_subwoofer']
    distances = settings.get('_receiver_base_distances_m', snapshot['distances_m'])
    candidates, proposals = [], []
    for k in range(-count, count + 1):
        value = k * quantum
        proposal = quantized_distance_proposal(distances, sub, value, speed, profile)
        if proposal is not None:
            candidates.append(value); proposals.append(proposal)
    if not candidates: raise ValueError('No delay lies within the receiver distance grid and spread constraints')
    baseline_choices = {g['id']: g['current_hz'] for g in table['groups']}
    baseline = evaluate_settings_table(table, 0., False, settings, baseline_choices)
    baseline_proposal = quantized_distance_proposal(distances, sub, 0., speed, profile)
    best = {**baseline, 'delay_s': 0., 'inverted': False, 'distance_proposal': baseline_proposal}
    best_score = baseline['objective'] if baseline_proposal is not None else float('inf'); evaluations = 0
    delay_penalty = float(settings.get('receiver_delay_penalty', .0001))
    polarity_penalty = float(settings.get('receiver_polarity_penalty', .00001))
    if not np.isfinite([delay_penalty, polarity_penalty]).all() or not 0 <= delay_penalty <= 10 or not 0 <= polarity_penalty <= 10:
        raise ValueError('Invalid native setting-change penalties')
    batch = 32
    for start in range(0, len(candidates), batch):
        if cancel is not None and cancel.is_set(): raise InterruptedError('Native calibration search cancelled')
        delays = np.asarray(candidates[start:start + batch])
        phase = np.exp(-2j * np.pi * table['frequency'][:, None] * delays[None, :])
        first = table['first'] @ phase; second = table['second'] @ phase**2
        for inverted in [False, True]:
            losses = np.maximum(0, table['constant'][:, None] + np.real((-first if inverted else first) + second))
            objective, choices, values = _group_costs(table, losses, settings)
            objective += (delay_penalty * (delays / maximum)**2 if maximum else 0) + polarity_penalty * inverted
            index = int(np.argmin(objective)); evaluations += len(delays)
            if objective[index] < best_score:
                best_score = float(objective[index])
                best = {'objective': best_score, 'delay_s': float(delays[index]), 'inverted': inverted,
                        'crossovers_hz': {g['id']: None if c[index] is None else float(c[index]) for g, c in zip(table['groups'], choices)},
                        'group_power_error': {g['id']: float(v[index]) for g, v in zip(table['groups'], values)},
                        'distance_proposal': proposals[start + index]}
        if progress: progress(90 + round(6 * min(start + batch, len(candidates)) / len(candidates)),
                              f'Native distance grid {min(start + batch, len(candidates))}/{len(candidates)}; every linked crossover choice retained')
    best.update(baseline=baseline, evaluated_delay_polarity_states=evaluations,
                time_integration_observable=bool(np.any(np.abs(table['first']) > 1e-20) or np.any(np.abs(table['second']) > 1e-20)),
                linked_crossover_combinations=prod(len(g['choices_hz']) for g in table['groups']),
                exact_minimum_over_declared_native_grid=True,
                objective_scope='fixed_FIRs_and_explicit_power_response_model; global_acoustic_optimality_not_claimed')
    if table.get('curve_weights'):
        checked=evaluate_settings_table(table,best['delay_s'],best['inverted'],settings,best['crossovers_hz'])
        best['per_curve']=checked['per_curve']
        best['objective_without_state_penalties']=checked['objective']
        best['curve_weights']=dict(table['curve_weights'])
        best['objective_scope']='fixed_FIRs_and_shared_ERB_trim_state; nested_position_curve_group_risk; conditional_native_grid_minimum'
    return best


def _bass_headroom_proxy(grid, snapshot, proposal, settings):
    sub = settings['_measured_subwoofer']
    f = grid.frequency
    selected = (f >= max(1., grid.low)) & (f <= min(grid.high, grid.rate * .42, 20000))
    frequencies = f[selected]
    lfe = signal.sosfreqz(signal.butter(4, snapshot.get('lfe_lowpass_hz', 120), fs=grid.rate, output='sos'),
                         worN=frequencies, fs=grid.rate)[1]
    rows=[]
    for name, crossovers, levels in [('baseline', snapshot['crossovers'], snapshot['levels_db']),
                                    ('proposed', {**snapshot['crossovers'], **proposal['crossovers_hz']}, proposal['levels_db'])]:
        magnitude=np.abs(lfe)
        for c in grid.items:
            if c.startswith('SW'): continue
            if snapshot['speaker_types'].get(c)=='large' and snapshot.get('bass_mode')!='lfe_plus_main':continue
            _,low=crossover_responses(frequencies,crossovers[c],settings.get('crossover_model','lr4'),grid.rate)
            magnitude+=np.abs(low)
        bound=magnitude*np.abs(grid.filters[sub][selected])*10**(levels[sub]/20)
        peak=int(np.argmax(bound))
        rows.append({'name':name,'maximum_induced_amplitude_bound':float(bound[peak]),
                     'frequency_hz':float(frequencies[peak])})
    growth=20*np.log10(max(rows[1]['maximum_induced_amplitude_bound'],1e-30)/max(rows[0]['maximum_induced_amplitude_bound'],1e-30))
    return {'rows':rows,'maximum_bound_growth_db':float(growth),'lfe_input_gain_assumed_db':0.,
            'notice':'Conservative coherent-input routing/FIR/output-level proxy. This excludes unknown AVR gain order, internal attenuation, driver distortion and available acoustic output; it is not a clipping or SPL certificate.'}


def native_routed_charts(grid, snapshot, proposal, settings, positions, references):
    subs=[c for c in grid.items if c.startswith('SW')]
    if len(subs)!=1 or any(m.ir is None and m.phase_deg is None for m in grid.project.measurements):return []
    sub=subs[0];context=settings['receiver_measurement_context'];speed=settings['receiver_delay_speed_m_s']
    f=grid.frequency;selected=(f>=max(10.,grid.low))&(f<=min(20000,grid.high,grid.rate*.42));frequency=f[selected]
    display=np.geomspace(max(10.,float(frequency[0])),float(frequency[-1]),360)
    sources=[c for c in grid.items if not c.startswith('SW')]+['LFE']
    models=settings.get('receiver_routing_models') or [settings.get('crossover_model','lr4')]
    offset=target_offset(settings['target_curve'],frequency) if settings.get('target_curve') else (
        float(settings.get('bass_db',3))/(1+(frequency/100)**4)+float(settings.get('tilt_db_oct',-.5))*np.log2(np.maximum(frequency,1000)/1000))
    weights=1/np.maximum(frequency,20);weights/=weights.sum();output=[]
    lfe=signal.sosfreqz(signal.butter(4,snapshot.get('lfe_lowpass_hz',120),fs=grid.rate,output='sos'),worN=frequency,fs=grid.rate)[1]
    for model in models:
        for source in sources:
            curves={k:np.zeros(len(display)) for k in ['before','after','target']};squares=[0.,0.];mass_total=0.
            ref=references[source] if source!='LFE' else grid.items[sub]['metrics']['reference_level_db']+(
                snapshot['levels_db'][sub] if context=='native_uncorrected' else 0.)
            target=10**((ref+offset)/20)*(np.abs(lfe) if source=='LFE' else 1)
            for position,mass in positions.items():
                channels=[sub] if source=='LFE' else [source,sub]
                repeated=[grid.groups[(c,position)] for c in channels]
                scenario_count=prod(len(indices) for indices in repeated)
                for combination in product(*repeated):
                    predictions=[]
                    for after in [False,True]:
                        predicted=np.zeros(len(frequency),complex)
                        for channel,index in zip(channels,combination):
                            value=grid.responses[index][selected].copy()
                            if after:value*=grid.filters[channel][selected]
                            levels=proposal['levels_db'] if after else snapshot['levels_db']
                            distance=proposal['distances_m'] if after else snapshot['distances_m']
                            gain=levels[channel] if context=='native_uncorrected' else levels[channel]-snapshot['levels_db'][channel]
                            delay_distance=distance[channel] if context=='native_uncorrected' else distance[channel]-snapshot['distances_m'][channel]
                            value*=10**(gain/20)*np.exp(2j*np.pi*frequency*delay_distance/speed)
                            if after and channel in proposal['inverted_outputs']:value*=-1
                            if source=='LFE':route=lfe
                            else:
                                large=snapshot['speaker_types'][source]=='large'
                                hz=proposal['crossovers_hz'].get(source,snapshot['crossovers'].get(source)) if after else snapshot['crossovers'].get(source)
                                hp,lp=(np.ones(len(frequency),complex),np.zeros(len(frequency),complex)) if hz is None else crossover_responses(frequency,hz,model,grid.rate)
                                route=np.ones_like(hp) if channel==source and large else hp if channel==source else lp if not large or snapshot.get('bass_mode')=='lfe_plus_main' else np.zeros_like(lp)
                            predicted+=value*route
                        predictions.append(predicted)
                    scalar=mass/scenario_count;mass_total+=scalar
                    for n,key in enumerate(['before','after']):
                        db=20*np.log10(np.maximum(np.abs(predictions[n]),1e-15));curves[key]+=scalar*np.interp(display,frequency,db)
                        error=20*np.log10(np.maximum(np.abs(predictions[n]),1e-15)/np.maximum(target,1e-15))
                        squares[n]+=scalar*float(weights@(error**2))
                    curves['target']+=scalar*np.interp(display,frequency,20*np.log10(np.maximum(target,1e-15)))
            output.append({'channel':source if len(models)==1 else source+' / '+model,
                'chart':{'frequency':display.tolist(),**{k:(v/mass_total).tolist() for k,v in curves.items()}},
                'metrics':{'rms_before_db':float(np.sqrt(squares[0]/mass_total)),'rms_after_db':float(np.sqrt(squares[1]/mass_total)),
                           'phase_mode':'native_receiver_model','effective_delay_ms':0.},
                'limitations':['Unprocessed-output response with baseline controls versus the proposed minimum-phase EQ and native controls, under the stated routing model. This is not measured stock-Audyssey or corrected playback. Plot averaging does not replace per-record fitting; raw timing and repeats are retained.']})
    return output


def evaluate_shared_curve_controls(project, design, primary, *, cancel=None):
    """Forward-evaluate an alternate EQ; never select a second hardware state."""
    settings = dict(design['settings'])
    snapshot = settings.get('receiver_snapshot', project.metadata.get('receiver_settings_snapshot', {}))
    proposal = copy.deepcopy(validate_receiver_proposal(primary, snapshot, snapshot.get('configured_channels', [])))
    joint=primary.get('control_selection',{}).get('mode')=='joint_curves'
    result = {'schema': 'audioseyx-shared-curve-controls-1', 'shared_settings_from': 'joint_curves' if joint else 'Flat',
              'proposal': proposal, 'controls_selected_for_this_curve': False,
              'participated_in_shared_control_selection':joint,
              'hardware_verified': False, 'original_ir_modified': False}
    if not primary.get('search'):
        return {**result, 'available': False,
                'reason': 'The primary proposal is level-only; routed time/crossover evaluation requires its complete timing model. Reference charts show the EQ stage only.'}
    validation = set(design.get('validation_positions', []))
    train = Project(project.name, [m for m in project.measurements if m.position not in validation],
                    project.origin, project.model, project.tier, dict(project.metadata))
    grid = SpectralGrid(train, design, memory_mib=float(settings.get('receiver_memory_mib', 512)), cancel=cancel)
    weights = position_weights(train, settings)
    mains = [c for c in train.channels if not c.startswith('SW')]
    subs = [c for c in train.channels if c.startswith('SW')]
    if len(subs)!=1:
        raise ValueError('Fixed shared-curve timing evaluation requires one physical subwoofer')
    profile = primary['profile']
    groups = _linked_groups(mains, snapshot, profile, settings)
    if primary.get('control_selection',{}).get('includes_lfe'):
        from .native_power_model import with_lfe_group
        groups=with_lfe_group(groups,settings)
    references = {c:grid.items[c]['metrics']['reference_level_db'] +
                  (snapshot['levels_db'][c] if settings['receiver_measurement_context']=='native_uncorrected' else 0.) for c in mains}
    if settings.get('receiver_auditory_balance', True):
        for group in settings.get('same_model_groups', project.metadata.get('same_model_groups', [])):
            value = float(np.mean([references[c] for c in group]))
            for c in group:references[c] = value
    choices = {g['id']: proposal['crossovers_hz'].get(g['channels'][0], g['current_hz']) for g in groups}
    if any(any(proposal['crossovers_hz'].get(c,g['current_hz'])!=choices[g['id']] for c in g['channels']) for g in groups):
        raise ValueError('Common curve controls disagree within a linked crossover group')
    internal = {**settings, 'receiver_delay_speed_m_s':primary['assumptions']['distance_delay_speed_m_s'],
                '_receiver_target_references_db': references, '_receiver_base_distances_m': proposal['distances_m']}
    selected = {g['id']:[choices[g['id']]] for g in groups}
    if primary.get('control_selection',{}).get('includes_lfe'):
        from .native_power_model import direct_power_evaluation
        evaluation=direct_power_evaluation({'Reference':grid},snapshot,{'Reference':internal},groups,
                                            proposal,weights,{'Reference':1.},choices,cancel)
        charts=native_routed_charts(grid,snapshot,proposal,internal,weights,references)
        band=[float(internal.get('receiver_analysis_low_hz',20)),float(internal.get('receiver_analysis_high_hz',min(20000,grid.high,grid.rate*.42)))]
        del grid
        held_evaluation=None
        if validation:
            held=Project(project.name,[m for m in project.measurements if m.position in validation],
                         project.origin,project.model,project.tier,dict(project.metadata))
            held_grid=SpectralGrid(held,design,memory_mib=float(settings.get('receiver_memory_mib',512)),cancel=cancel)
            held_settings={**internal,'position_weights':settings.get('validation_position_weights',{}),'receiver_analysis_low_hz':band[0],'receiver_analysis_high_hz':band[1]}
            value=direct_power_evaluation({'Reference':held_grid},snapshot,{'Reference':held_settings},groups,
                                           proposal,position_weights(held,held_settings),{'Reference':1.},choices,cancel)
            held_evaluation={'positions':sorted(validation),'used_for_design':False,'references_from_training':True,
                             'renormalized_after_design':False,'evaluation':value}
        return {**result,'available':True,'training_positions':sorted(weights),'validation_positions':sorted(validation),
                'fixed_training_target_references_db':references,'evaluation':evaluation,'independent_validation':held_evaluation,
                'routed_charts':charts,'direct_complete_response_evaluation':True,
                'notice':'The alternate EQ is evaluated by direct complete complex sums at the common selected controls, including LFE. No second menu search, target fit or holdout normalization occurs.'}
    builder=_cost_table
    table = builder(grid, snapshot, internal, groups, proposal['levels_db'], weights, selected)
    evaluation = evaluate_settings_table(table, 0., subs[0] in proposal['inverted_outputs'], internal, choices)
    band = table['band_hz']; del table
    charts = native_routed_charts(grid, snapshot, proposal, internal, weights, references)
    held_evaluation = None
    if validation:
        held = Project(project.name, [m for m in project.measurements if m.position in validation],
                       project.origin, project.model, project.tier, dict(project.metadata))
        held_grid = SpectralGrid(held, design, memory_mib=float(settings.get('receiver_memory_mib', 512)), cancel=cancel)
        held_settings = {**internal, 'position_weights':settings.get('validation_position_weights',{}), 'receiver_analysis_low_hz':band[0], 'receiver_analysis_high_hz':band[1]}
        held_weights = position_weights(held, held_settings)
        table = builder(held_grid, snapshot, held_settings, groups, proposal['levels_db'], held_weights, selected)
        held_evaluation = {'positions':sorted(validation), 'used_for_design':False,
                           'references_from_training':True, 'renormalized_after_design':False,
                           'evaluation':evaluate_settings_table(table, 0., subs[0] in proposal['inverted_outputs'], held_settings, choices)}
    return {**result, 'available':True, 'training_positions':sorted(weights), 'validation_positions':sorted(validation),
            'fixed_training_target_references_db':references, 'evaluation':evaluation,
            'independent_validation':held_evaluation, 'routed_charts':charts,
            'notice':'Reference EQ forward-evaluated with the selected common distance/level/crossover/polarity proposal held fixed. Selection source and participation are recorded separately. Every repeat and routing-model alternative is retained; holdouts never refit controls or references.'}


def calibrate_receiver(project, design, settings, *, progress=None, cancel=None):
    if settings.get('relative_levels_confirmed') is not True or settings.get('routing_measurements_confirmed') is not True:
        raise ValueError('Native calibration needs explicit common timing, relative levels and uncrossed-output measurement confirmations')
    if settings.get('receiver_measurement_context') not in CONTEXTS:
        raise ValueError('Select the native-calibration or uncrossed/EQ-off playback measurement context')
    if settings['receiver_measurement_context'] == 'uncrossed_playback_eq_off' and project.origin == 'native_avr':
        raise ValueError('Native calibration captures cannot be assumed to include normal-playback output controls')
    snapshot = settings.get('receiver_snapshot', project.metadata.get('receiver_settings_snapshot', {}))
    if not snapshot.get('available'):
        raise ValueError('A saved or live read-only receiver speaker-settings snapshot is required')
    source_snapshot_hash = snapshot_fingerprint(snapshot)
    active = snapshot.get('configured_channels', [])
    if not active or any(c not in snapshot.get('distances_m', {}) or c not in snapshot.get('levels_db', {}) for c in active):
        raise ValueError('The receiver snapshot must describe every configured output distance and level')
    snapshot = {**snapshot, 'distances_m': {c: snapshot['distances_m'][c] for c in active},
                'levels_db': {c: snapshot['levels_db'][c] for c in active}}
    validation = set(settings.get('validation_positions', []))
    train = Project(project.name, [m for m in project.measurements if m.position not in validation],
                    project.origin, project.model, project.tier, dict(project.metadata))
    profile = receiver_profile(project.model, settings.get('receiver_constraints'))
    mains = [c for c in train.channels if not c.startswith('SW')]; subs = [c for c in train.channels if c.startswith('SW')]
    search_enabled = bool(subs) and settings.get('receiver_time_crossover_search', True)
    if not mains: raise ValueError('Measured main outputs are required for native receiver calibration')
    if search_enabled and (len(subs) != 1 or settings.get('timing_confirmed') is not True):
        raise ValueError('Native time/crossover search requires common timing confirmations and one physical subwoofer; level-only analysis remains available')
    if search_enabled and any(m.ir is None and m.phase_deg is None for m in train.measurements):
        raise ValueError('Native time/crossover calibration requires measured phase for every response')
    if any(c not in snapshot.get('distances_m', {}) or c not in snapshot.get('levels_db', {}) for c in train.channels) or any(c not in snapshot.get('speaker_types', {}) for c in mains):
        raise ValueError('The receiver snapshot lacks a measured output distance, level or speaker type')
    controls = dict(snapshot['levels_db'])
    for c in train.channels:
        if not profile['level_min_db'] <= controls[c] <= profile['level_max_db']:
            raise ValueError('A baseline speaker level exceeds the declared receiver profile')
    weights = position_weights(train, settings)
    grid = SpectralGrid(train, design, memory_mib=float(settings.get('receiver_memory_mib', 512)), cancel=cancel)
    same_model = settings.get('same_model_groups', project.metadata.get('same_model_groups', []))
    # Freeze a common reference within confirmed same-model groups before
    # choosing any gain or crossover; independent per-output normalization
    # would otherwise penalize the very level match being requested.
    references = {c: grid.items[c]['metrics']['reference_level_db'] + (
        controls[c] if settings['receiver_measurement_context'] == 'native_uncorrected' else 0.) for c in mains}
    if settings.get('receiver_auditory_balance', True):
        for group in same_model:
            if any(c not in references for c in group): raise ValueError('Level-target groups must name measured main outputs')
            mean = float(np.mean([references[c] for c in group]))
            for c in group: references[c] = mean
    balance = None
    if settings.get('receiver_auditory_balance', True) and same_model:
        offsets = controls if settings['receiver_measurement_context'] == 'native_uncorrected' else {c: 0. for c in controls}
        balance = balance_groups(grid, same_model, controls, weights,
            low=float(settings.get('auditory_low_hz', 200)), high=float(settings.get('auditory_high_hz', 5000)),
            step_db=profile['level_step_db'], max_change_db=float(settings.get('auditory_max_change_db', 3)),
            minimum_db=profile['level_min_db'], maximum_db=profile['level_max_db'], response_offsets_db=offsets, cancel=cancel)
        for c, delta in balance['trim_delta_db'].items(): controls[c] += delta
    if not search_enabled:
        base_distances, basis = distance_basis(snapshot['distances_m'], profile, settings)
        if basis['relative_distances_rescaled']:
            fitted = quantized_distance_proposal(base_distances, mains[0], 0., basis['receiver_speed_m_s'], profile)
            if fitted is None: raise ValueError('The explicitly rescaled distance basis does not fit the receiver menu limits')
            base_distances = fitted['distances_m']
        return {'schema': 'audioseyx-receiver-calibration-1', 'model': project.model,
                'baseline_snapshot_sha256': source_snapshot_hash,
                'baseline': {'distances_m': snapshot['distances_m'], 'levels_db': snapshot['levels_db'],
                             'crossovers_hz': snapshot.get('crossovers', {})},
                'proposal': {'distances_m': base_distances, 'levels_db': controls,
                             'crossovers_hz': {}, 'inverted_outputs': []},
                'distance_basis': basis,
                'profile': profile, 'search': None, 'auditory_balance': balance,
                'independent_validation': None,
                'native_level_holdout_evaluation_available': False,
                'training_positions': sorted(weights), 'validation_positions': sorted(validation),
                'assumptions': {'measurement_context': settings['receiver_measurement_context'],
                                'output_level_and_distance_order_verified': False,
                                'distance_basis_method': basis['method'], 'distance_delay_speed_m_s': basis['receiver_speed_m_s']},
                'measurement_records_used': len(train.measurements), 'original_ir_modified': False,
                'hardware_verified': False, 'receiver_settings_written': False,
                'notice': 'Auditory-band native level proposal; no bass timing or crossover search. The chosen distance basis is explicit and crossovers are retained. This does not measure absolute or standardized perceptual loudness.'}
    groups = _linked_groups(mains, snapshot, profile, settings)
    base_distances, basis = distance_basis(snapshot['distances_m'], profile, settings)
    speed = float(settings.get('receiver_delay_speed_m_s', profile.get('distance_delay_speed_m_s', 343)))
    internal = {**settings, 'receiver_delay_speed_m_s': speed, '_measured_subwoofer': subs[0],
                '_receiver_target_references_db': references, '_receiver_base_distances_m': base_distances}
    table = _cost_table(grid, snapshot, internal, groups, controls, weights)
    result = search_native_grid(table, snapshot, profile, internal, progress=progress, cancel=cancel)
    chosen = {c: result['crossovers_hz'][g['id']] for g in groups for c in g['channels'] if g['small']}
    delta = result['distance_proposal']
    proposal = {'distances_m': delta['distances_m'], 'levels_db': controls, 'crossovers_hz': chosen,
                'inverted_outputs': [subs[0]] if result['inverted'] else []}
    headroom = _bass_headroom_proxy(grid, snapshot, proposal, internal)
    charts = native_routed_charts(grid, snapshot, proposal, internal, weights, references)
    evaluation = evaluate_settings_table(table, result['delay_s'], result['inverted'], internal, result['crossovers_hz'])
    result['baseline_scope'] = 'proposed_levels_and_selected_main_distance_basis_with_original_crossovers_and_zero_extra_sub_delay'
    band = table['band_hz']
    del table
    baseline_choices = {g['id']: [g['current_hz']] for g in groups}
    original_settings = {**internal, '_receiver_base_distances_m': snapshot['distances_m']}
    original_table = _cost_table(grid, snapshot, original_settings, groups, snapshot['levels_db'], weights, baseline_choices)
    original_evaluation = evaluate_settings_table(original_table, 0., False, original_settings,
                                                   {g['id']: g['current_hz'] for g in groups})
    del original_table
    validation_evaluation = None
    if validation:
        held = Project(project.name, [m for m in project.measurements if m.position in validation],
                       project.origin, project.model, project.tier, dict(project.metadata))
        held_grid = SpectralGrid(held, design, memory_mib=float(settings.get('receiver_memory_mib', 512)), cancel=cancel)
        held_settings = {**internal, 'position_weights': settings.get('validation_position_weights',{}), 'receiver_analysis_low_hz': band[0], 'receiver_analysis_high_hz': band[1]}
        held_weights = position_weights(held, held_settings)
        after_choices = {g['id']: [result['crossovers_hz'][g['id']]] for g in groups}
        original_held_settings = {**held_settings, '_receiver_base_distances_m': snapshot['distances_m']}
        t = _cost_table(held_grid, snapshot, original_held_settings, groups, snapshot['levels_db'], held_weights, baseline_choices)
        before = evaluate_settings_table(t, 0., False, original_held_settings, {g['id']: g['current_hz'] for g in groups})
        del t
        t = _cost_table(held_grid, snapshot, held_settings, groups, controls, held_weights, after_choices)
        after = evaluate_settings_table(t, result['delay_s'], result['inverted'], held_settings, result['crossovers_hz'])
        validation_evaluation = {'positions': sorted(validation), 'used_for_design': False,
                                 'references_from_training': True, 'renormalized_after_design': False,
                                 'before': before, 'after': after}
    return {'schema': 'audioseyx-receiver-calibration-1', 'model': project.model,
            'baseline_snapshot_sha256': source_snapshot_hash, 'baseline': {
                'distances_m': snapshot['distances_m'], 'levels_db': snapshot['levels_db'], 'crossovers_hz': snapshot['crossovers']},
            'proposal': proposal, 'profile': profile, 'search': result, 'evaluation': evaluation,
            'distance_basis': basis,
            'fixed_training_target_references_db': references,
            'target_reference_rule': ('common baseline reference within confirmed same-model groups' if balance else 'per-output baseline reference') + '; frozen before gain/menu selection',
            'original_evaluation': original_evaluation, 'independent_validation': validation_evaluation,
            'bass_routing_headroom_proxy': headroom,
            'routed_charts': charts,
            'auditory_balance': balance, 'analysis_band_hz': band,
            'training_positions': sorted(weights), 'validation_positions': sorted(validation),
            'measurement_records_used': len(train.measurements), 'original_ir_modified': False,
            'assumptions': {'measurement_context': settings['receiver_measurement_context'],
                'output_level_and_distance_order_verified': False, 'distance_delay_speed_m_s': speed,
                'distance_delay_conversion_verified': False, 'crossover_model': settings.get('crossover_model', 'lr4'),
                'distance_basis_method': basis['method'], 'physical_speed_m_s': basis['physical_speed_m_s'],
                'routing_models_considered': settings.get('receiver_routing_models') or [settings.get('crossover_model', 'lr4')],
                'native_filter_reconstruction_verified': False},
            'hardware_verified': False, 'receiver_settings_written': False,
            'notice': 'Modeled native-menu proposal using every measured FFT bin in the declared analysis band and every retained repeat. The finite-grid search is exact for its stated conditional model and fixed FIRs. Receiver routing, capture gain/delay path and native reconstruction remain unverified. Polarity is a filter choice, not a diagnosis of reversed wiring.'}
