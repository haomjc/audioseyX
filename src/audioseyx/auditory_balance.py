"""Auditory-band response matching using an explicit, discrete trim objective.

ERB-spaced energy analysis is not an ISO loudness calculation. This independently
implemented method does not incorporate GSonic's proprietary balancing engine.
"""
from __future__ import annotations

import numpy as np


def erb_rate(frequency):
    return 21.4 * np.log10(1 + .00437 * np.asarray(frequency))


def erb_bank(frequency, low=200., high=5000., spacing=1.):
    if not np.isfinite([low, high, spacing]).all() or not 20 <= low < high <= 20000 or not .25 <= spacing <= 4:
        raise ValueError("Invalid auditory-band analysis range")
    centres = np.arange(erb_rate(low), erb_rate(high) + 1e-12, spacing)
    if len(centres) < 2:
        raise ValueError("At least two auditory bands are required")
    distance = np.abs(erb_rate(frequency)[None, :] - centres[:, None]) / spacing
    bank = np.maximum(0, 1 - distance)
    bank[:, (frequency < low) | (frequency > high)] = 0
    if np.any(bank.sum(axis=1) <= 0):
        raise ValueError("The measured frequency grid does not cover every requested auditory band")
    bank /= bank.sum(axis=1)[:, None]
    return bank, (10 ** (centres / 21.4) - 1) / .00437


def optimal_quantized_levels(costs, options):
    """Exact finite-grid separable minimum with a zero-sum integer constraint."""
    if len(costs) != len(options) or not costs:
        raise ValueError("Complete channel costs and discrete trim options are required")
    states = {0: (0., ())}
    for values, candidates in zip(costs, options):
        if len(values) != len(candidates) or not len(values) or not np.isfinite(values).all():
            raise ValueError("Invalid discrete auditory-balance costs")
        updated = {}
        for total, (score, path) in states.items():
            for value, step in zip(values, candidates):
                if not isinstance(step, (int, np.integer)):
                    raise ValueError("Trim-grid indices must be integers")
                key, trial = total + int(step), score + float(value)
                if key not in updated or trial < updated[key][0]:
                    updated[key] = (trial, path + (int(step),))
        states = updated
    if 0 not in states:
        raise ValueError("The trim limits have no zero-sum native-grid solution")
    return states[0]


def _huber(value, width):
    magnitude = np.abs(value)
    return np.where(magnitude <= width, .5 * value**2, width * (magnitude - .5 * width))


def _group_observations(grid, group, positions, bank, low, high, offsets, record_weights=None):
    covered=[m for m in grid.project.measurements if m.channel in group]
    if any(low < (float(m.frequency[0]) if m.ir is None else 0) or
           high > (float(m.frequency[-1]) if m.ir is None else m.sample_rate/2) for m in covered) or any(high>grid.items[c]['sample_rate']/2 for c in group):
        raise ValueError('Auditory balancing requires complete measured/filter coverage for its speaker group')
    observations=[];masses=[]
    for position,mass in positions.items():
        per_channel=[]
        for channel in group:
            indices=grid.groups.get((channel,position),[])
            if not indices:raise ValueError('Auditory balance is missing an output/position response')
            powers=np.asarray([np.abs(grid.corrected(i))**2 for i in indices])
            weights=np.full(len(indices),1/len(indices)) if record_weights is None else np.asarray(record_weights)[indices]
            weights=weights/weights.sum()
            per_channel.append(10*np.log10(np.maximum(weights@(powers@bank.T),1e-30))+float(offsets.get(channel,0)))
        observations.append(per_channel);masses.append(mass)
    observed=np.asarray(observations).transpose(1,0,2)
    weight=np.asarray(masses,dtype=float);weight/=weight.sum()
    return observed,observed.mean(axis=0),weight


def balance_groups(grid, groups, levels_db, positions, *, low=200., high=5000., step_db=.5,
                   max_change_db=3., minimum_db=-12., maximum_db=12., huber_db=2.,
                   response_offsets_db=None, cancel=None):
    if not np.isfinite([step_db, max_change_db, minimum_db, maximum_db, huber_db]).all() or not .05 <= step_db <= 1 or not 0 <= max_change_db <= 12 or minimum_db >= maximum_db or not 0 < huber_db <= 12:
        raise ValueError("Invalid discrete speaker-level limits")
    bank, centres = erb_bank(grid.frequency, low, high)
    offsets = levels_db if response_offsets_db is None else response_offsets_db
    corrections, reports, used = {}, [], set()
    for group in groups:
        if cancel is not None and cancel.is_set():
            raise InterruptedError("Auditory balance analysis cancelled")
        if not isinstance(group, (list, tuple)) or len(group) < 2 or len(set(group)) != len(group):
            raise ValueError("Auditory matching requires distinct confirmed same-model groups")
        if used.intersection(group) or any(c not in grid.items or c.startswith("SW") for c in group):
            raise ValueError("Auditory groups must name distinct measured main outputs")
        used.update(group)
        observed,reference,weight=_group_observations(grid,group,positions,bank,low,high,offsets)
        options, costs = [], []
        for index, channel in enumerate(group):
            current = float(levels_db.get(channel, 0))
            lower = max(-max_change_db, minimum_db - current)
            upper = min(max_change_db, maximum_db - current)
            steps = np.arange(int(np.ceil(lower / step_db - 1e-10)), int(np.floor(upper / step_db + 1e-10)) + 1)
            delta = observed[index] - reference
            values = np.array([float(weight @ _huber(delta + k * step_db, huber_db).mean(axis=1))
                               + 1e-8 * (k * step_db)**2 for k in steps])
            options.append(steps); costs.append(values)
        score, selected = optimal_quantized_levels(costs, options)
        gains = np.asarray(selected) * step_db
        before = float(np.sqrt(np.mean(weight @ ((observed - reference)**2).mean(axis=2).T)))
        after = float(np.sqrt(np.mean(weight @ ((observed + gains[:, None, None] - reference)**2).mean(axis=2).T)))
        corrections.update(dict(zip(group, gains.tolist())))
        reports.append({"channels": list(group), "trim_delta_db": dict(zip(group, gains.tolist())),
                        "band_imbalance_rms_before_db": before, "band_imbalance_rms_after_db": after,
                        "objective": score, "zero_sum_delta_db": float(gains.sum()),
                        "global_discrete_minimum": True, "native_step_db": step_db})
    return {"method": "ERB_spaced_response_energy_Huber_native_grid_dynamic_programming",
            "trim_delta_db": corrections, "groups": reports, "band_centres_hz": centres.tolist(),
            "analysis_band_hz": [low, high], "all_positions_and_repeats_retained": True,
            "hardware_verified": False, "loudness_standard_certification": False,
            "notice": "Relative matching of confirmed same-model speakers. This response-energy method is not an ISO loudness calculation or a verified reproduction of GSonic. The full measured FFT grid contributes to the auditory bands; raw impulses are unchanged."}


def balance_curve_groups(grids, groups, levels_db, positions, curve_weights, *, low=200., high=5000.,
                         step_db=.5, max_change_db=3., minimum_db=-12., maximum_db=12., huber_db=2.,
                         response_offsets_db=None, output_maximum_db=None, record_weights=None, cancel=None):
    """One exact zero-sum trim state for a weighted sum of complete curve costs."""
    names=list(grids)
    if not names or set(names)!=set(curve_weights):raise ValueError('Every auditory curve requires an explicit weight')
    profile_mass=np.asarray([curve_weights[c] for c in names],dtype=float)
    if not np.isfinite(profile_mass).all() or np.any(profile_mass<=0):raise ValueError('Auditory curve weights must be positive and finite')
    profile_mass/=profile_mass.sum()
    if not np.isfinite([step_db,max_change_db,minimum_db,maximum_db,huber_db]).all() or not .05<=step_db<=1 or not 0<=max_change_db<=12 or minimum_db>=maximum_db or not 0<huber_db<=12:
        raise ValueError('Invalid shared discrete speaker-level limits')
    if any(set(g.items)!=set(grids[names[0]].items) for g in grids.values()):raise ValueError('Auditory curves must cover the same outputs')
    banks={c:erb_bank(g.frequency,low,high) for c,g in grids.items()}
    offsets=levels_db if response_offsets_db is None else response_offsets_db
    corrections={};reports=[];used=set()
    for group in groups:
        if cancel is not None and cancel.is_set():raise InterruptedError('Shared-curve auditory balance cancelled')
        if not isinstance(group,(list,tuple)) or len(group)<2 or len(set(group))!=len(group) or used.intersection(group) or any(c not in grids[names[0]].items or c.startswith('SW') for c in group):
            raise ValueError('Shared auditory groups require distinct confirmed measured main outputs')
        used.update(group)
        observations={c:_group_observations(grids[c],group,positions,banks[c][0],low,high,offsets,
                                          None if record_weights is None else record_weights[c]) for c in names}
        options=[];costs=[]
        for index,channel in enumerate(group):
            current=float(levels_db.get(channel,0))
            limit=maximum_db if output_maximum_db is None else min(maximum_db,float(output_maximum_db[channel]))
            lower=max(-max_change_db,minimum_db-current);upper=min(max_change_db,limit-current)
            steps=np.arange(int(np.ceil(lower/step_db-1e-10)),int(np.floor(upper/step_db+1e-10))+1)
            if not len(steps):raise ValueError('No shared main-level state fits the declared change and coefficient-reserve limits')
            values=np.zeros(len(steps))
            for name,mass in zip(names,profile_mass):
                observed,reference,weight=observations[name]
                delta=observed[index]-reference
                values+=mass*np.asarray([float(weight@_huber(delta+k*step_db,huber_db).mean(axis=1)) for k in steps])
            values+=1e-8*(steps*step_db)**2
            options.append(steps);costs.append(values)
        score,selected=optimal_quantized_levels(costs,options)
        gains=np.asarray(selected)*step_db
        per_curve={}
        for name in names:
            observed,reference,weight=observations[name]
            before=float(np.sqrt(np.mean(weight@((observed-reference)**2).mean(axis=2).T)))
            after=float(np.sqrt(np.mean(weight@((observed+gains[:,None,None]-reference)**2).mean(axis=2).T)))
            per_curve[name]={'band_imbalance_rms_before_db':before,'band_imbalance_rms_after_db':after}
        # Weighted RMS summaries retain the individual curve evidence below.
        before=float(np.sqrt(sum(m*per_curve[c]['band_imbalance_rms_before_db']**2 for c,m in zip(names,profile_mass))))
        after=float(np.sqrt(sum(m*per_curve[c]['band_imbalance_rms_after_db']**2 for c,m in zip(names,profile_mass))))
        corrections.update(dict(zip(group,gains.tolist())))
        reports.append({'channels':list(group),'trim_delta_db':dict(zip(group,gains.tolist())),
                        'band_imbalance_rms_before_db':before,'band_imbalance_rms_after_db':after,
                        'per_curve':per_curve,'objective':score,'zero_sum_delta_db':float(gains.sum()),
                        'global_discrete_minimum':True,'native_step_db':step_db})
    return {'method':'shared_curve_weighted_ERB_Huber_native_grid_dynamic_programming',
            'curve_weights':dict(zip(names,profile_mass.tolist())), 'trim_delta_db':corrections,'groups':reports,
            'analysis_band_hz':[low,high],'band_centres_hz':banks[names[0]][1].tolist(),
            'all_positions_and_repeats_retained':True,'hardware_verified':False,'loudness_standard_certification':False,
            'notice':'One shared native trim state minimizes the weighted mean of per-curve ERB Huber costs with zero-sum group changes. This is a conditional level-matching objective, not a joint optimum of FIRs, gains and bass timing, or an ISO loudness model.'}
