"""Full declared native sub-level/delay/polarity grid with linked crossover risk."""
from __future__ import annotations

from math import prod
import numpy as np

from .receiver_calibration import _group_costs, evaluate_settings_table
from .receiver_constraints import quantized_distance_proposal


def sub_level_candidates(current, profile, settings, compensation_db=0.):
    enabled=settings.get('receiver_sub_level_search',True)
    if not isinstance(enabled,bool):raise ValueError('Subwoofer level search must be explicitly enabled or disabled')
    step=profile['level_step_db']
    if not np.isfinite(current) or abs(current/step-round(current/step))>1e-6:
        raise ValueError('The baseline subwoofer level does not lie on the declared native grid')
    low=float(settings.get('receiver_sub_level_min_db',profile['level_min_db']))
    high=float(settings.get('receiver_sub_level_max_db',profile['level_max_db']))
    if not np.isfinite([low,high,compensation_db]).all() or low>high or compensation_db<0:
        raise ValueError('Invalid subwoofer level range or coefficient reserve')
    low=max(low,profile['level_min_db']);high=min(high,profile['level_max_db']-compensation_db)
    if not enabled:
        if not low-1e-9<=current<=high+1e-9:raise ValueError('The fixed subwoofer level has no room for coefficient compensation')
        return np.asarray([current])
    values=np.arange(int(np.ceil(low/step-1e-9)),int(np.floor(high/step+1e-9))+1)*step
    if not len(values):raise ValueError('No declared subwoofer level fits the receiver and coefficient-reserve limits')
    return values


def search_coupled_native_grid(table, snapshot, profile, settings, levels, compensation_db=0., *, progress=None, cancel=None):
    if 'gain_constant' not in table:raise ValueError('Coupled native search requires complete gain power moments')
    speed=float(settings.get('receiver_delay_speed_m_s',profile.get('distance_delay_speed_m_s',343)))
    maximum=float(settings.get('receiver_max_delay_ms',15))/1000
    if not np.isfinite([speed,maximum]).all() or not 250<=speed<=400 or not 0<=maximum<=.1:
        raise ValueError('Invalid native delay range or conversion model')
    sub=settings['_measured_subwoofer'];current=float(levels[sub])
    level_values=sub_level_candidates(current,profile,settings,compensation_db)
    gains=10**((level_values-current)/20)
    quantum=profile['distance_step_m']/speed
    count=int(np.floor(maximum/quantum+1e-9))
    distances=settings.get('_receiver_base_distances_m',snapshot['distances_m'])
    delays=[];proposals=[]
    for k in range(-count,count+1):
        proposal=quantized_distance_proposal(distances,sub,k*quantum,speed,profile)
        if proposal is not None:delays.append(k*quantum);proposals.append(proposal)
    if not delays:raise ValueError('No delay fits the declared receiver distance grid and spread')
    penalties=np.asarray([float(settings.get(k,d)) for k,d in [
        ('receiver_delay_penalty',.0001),('receiver_polarity_penalty',.00001),('receiver_sub_level_penalty',.0001)]])
    if not np.isfinite(penalties).all() or np.any(penalties<0) or np.any(penalties>10):
        raise ValueError('Invalid coupled native setting-change penalties')
    current_crossovers={g['id']:g['current_hz'] for g in table['groups']}
    baseline=evaluate_settings_table(table,0.,False,settings,current_crossovers,sub_gain=1.)
    baseline_distance=quantized_distance_proposal(distances,sub,0.,speed,profile)
    admissible=baseline_distance is not None and np.any(np.abs(level_values-current)<1e-9)
    best={**baseline,'delay_s':0.,'inverted':False,'sub_level_db':current,'sub_gain':1.,'distance_proposal':baseline_distance}
    rank=(baseline['objective'],0.,0.,0) if admissible else (float('inf'),float('inf'),float('inf'),1)
    evaluated=0;normalizer=max(profile['level_max_db']-profile['level_min_db'],profile['level_step_db'])
    c=table['gain_constant'];batch=min(32,int(table.get('state_batch_size',256)))
    gain_batch=max(1,int(table.get('state_batch_size',256))//batch)
    for start in range(0,len(delays),batch):
        if cancel is not None and cancel.is_set():raise InterruptedError('Coupled native grid search cancelled')
        values=np.asarray(delays[start:start+batch])
        if 'delay_grid_s' in table:
            stored=table['delay_grid_s']
            indices=np.rint((values-stored[0])/(stored[-1]-stored[0])*(len(stored)-1)).astype(int) if len(stored)>1 else np.zeros(len(values),dtype=int)
            if np.any(indices<0) or np.any(indices>=len(stored)) or not np.allclose(stored[indices],values,rtol=0,atol=1e-12):
                raise ValueError('The transformed objective does not contain every admissible native delay')
            linear=table['gain_first_linear_at_delays'][:,indices]
            cubic=table['gain_first_cubic_at_delays'][:,indices];second=table['second_at_delays'][:,indices]
        else:
            phase=np.exp(-2j*np.pi*table['frequency'][:,None]*values[None,:])
            linear=table['gain_first_linear']@phase;cubic=table['gain_first_cubic']@phase;second=table['second']@(phase**2)
        for gain_start in range(0,len(gains),gain_batch):
            if cancel is not None and cancel.is_set():raise InterruptedError('Coupled native gain search cancelled')
            gain_values=gains[gain_start:gain_start+gain_batch]
            db_values=level_values[gain_start:gain_start+gain_batch]
            q=gain_values[None,None,:]
            constant=c[:,None,0:1]+q*q*c[:,None,1:2]+q**4*c[:,None,2:3]
            first=q*linear[:,:,None]+q**3*cubic[:,:,None]
            for inverted in [False,True]:
                losses=np.maximum(0,constant+np.real((-first if inverted else first)+q*q*second[:,:,None]))
                losses=losses.reshape(len(table['rows']),-1)
                objective,choices,group_errors=_group_costs(table,losses,settings)
                objective=objective.reshape(len(values),len(gain_values))
                objective+=(penalties[0]*(values[:,None]/maximum)**2 if maximum else 0)+penalties[1]*inverted+penalties[2]*((db_values[None,:]-current)/normalizer)**2
                if not np.isfinite(objective).all():raise ValueError('The coupled native objective is not finite')
                minimum=objective.min();ties=np.argwhere(objective==minimum)
                delay_index,gain_index=min(ties,key=lambda pair:(abs(db_values[pair[1]]-current),abs(values[pair[0]])))
                delay_index=int(delay_index);gain_index=int(gain_index)
                index=delay_index*len(gain_values)+gain_index
                candidate=(float(minimum),float(abs(db_values[gain_index]-current)),float(abs(values[delay_index])),int(inverted))
                evaluated+=objective.size
                if candidate<rank:
                    rank=candidate
                    best={'objective':candidate[0],'delay_s':float(values[delay_index]),'inverted':inverted,
                          'sub_level_db':float(db_values[gain_index]),'sub_gain':float(gain_values[gain_index]),'distance_proposal':proposals[start+delay_index],
                          'crossovers_hz':{g['id']:None if choice[index] is None else float(choice[index]) for g,choice in zip(table['groups'],choices)},
                          'group_power_error':{g['id']:float(value[index]) for g,value in zip(table['groups'],group_errors)}}
        if progress:progress(85+round(10*min(start+batch,len(delays))/len(delays)),
                             f'Coupled native grid: {min(start+batch,len(delays))}/{len(delays)} delays; {len(level_values)} shared sub levels')
    if not np.isfinite(rank[0]):raise ValueError('No feasible coupled native setting state was found')
    checked=evaluate_settings_table(table,best['delay_s'],best['inverted'],settings,best['crossovers_hz'],sub_gain=best['sub_gain'])
    if 'delay_grid_s' in table:
        observable=any(np.any(np.ptp(table[key].real,axis=1)>1e-20) for key in ['gain_first_linear_at_delays','gain_first_cubic_at_delays','second_at_delays'])
    else:observable=bool(np.any(np.abs(table['gain_first_linear'])>1e-20) or np.any(np.abs(table['gain_first_cubic'])>1e-20) or np.any(np.abs(table['second'])>1e-20))
    best.update(baseline=baseline,baseline_admissible=bool(admissible),
                objective_without_state_penalties=checked['objective'],per_curve=checked.get('per_curve',{}),
                evaluated_delay_polarity_states=2*len(delays),evaluated_delay_polarity_gain_states=evaluated,
                sub_level_candidates_db=level_values.tolist(),coefficient_compensation_reserved_db=float(compensation_db),
                time_integration_observable=observable,
                linked_crossover_combinations=prod(len(g['choices_hz']) for g in table['groups']),
                exact_minimum_over_declared_native_grid=True,
                native_delay_transform=table.get('native_delay_transform'),
                objective_scope='fixed_complete_FIRs_and_shared_main_ERB_trims; exhaustive_native_sub_gain_delay_polarity_grid_with_conditionally_exact_linked_crossovers_and_LFE')
    return best
