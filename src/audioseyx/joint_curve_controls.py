"""Shared native controls selected from multiple complete static EQ targets."""
from __future__ import annotations

import numpy as np

from .auditory_balance import balance_curve_groups
from .calibration_curves import CURVE_SPECS, PROFILE_KEYS, curve_designs
from .model import Project, unsafe_capture_indices
from .receiver_calibration import (CONTEXTS, _linked_groups,
                                    evaluate_settings_table, _bass_headroom_proxy, native_routed_charts)
from .native_power_model import build_native_delay_table, native_delay_grid, direct_power_evaluation, with_lfe_group
from .coupled_native_search import search_coupled_native_grid
from .headroom import curve_headroom
from .receiver_constraints import (receiver_profile, snapshot_fingerprint, distance_basis,
                                   quantized_distance_proposal, validate_receiver_proposal)
from .spectral_grid import SpectralGrid, position_weights
from .measurement_robustness import robustness_report, repeat_confidence


def checked_curve_weights(settings, names=('Flat','Reference')):
    supplied=settings.get('receiver_curve_weights',{name:1. for name in names})
    if not isinstance(supplied,dict) or set(supplied)!=set(names) or any(isinstance(v,bool) for v in supplied.values()):
        raise ValueError('Both calibration curves need explicit numeric priorities')
    values=np.asarray([supplied[name] for name in names],dtype=float)
    if not np.isfinite(values).all() or np.any(values<=0):raise ValueError('Curve priorities must be positive and finite')
    values/=values.max();values/=values.sum()
    if np.any(values<=0):raise ValueError('Curve priorities are too disproportionate to retain both numerically')
    return dict(zip(names,values.tolist()))


def _curve_grids(project, curves, budget, cancel):
    result={};used=0.
    for name,design in curves.items():
        remaining=budget-used
        if remaining<32:raise ValueError('The shared-curve memory budget cannot retain every complete grid')
        result[name]=SpectralGrid(project,design,memory_mib=remaining,cancel=cancel)
        used+=result[name].estimated_memory_mib
    return result


def _target_references(grid, snapshot, settings, mains, same_model):
    result={c:grid.items[c]['metrics']['reference_level_db'] +
            (snapshot['levels_db'][c] if settings['receiver_measurement_context']=='native_uncorrected' else 0.) for c in mains}
    if settings.get('receiver_auditory_balance',True):
        for group in same_model:
            if any(c not in result for c in group):raise ValueError('Level-target groups must name measured main outputs')
            mean=float(np.mean([result[c] for c in group]))
            for c in group:result[c]=mean
    return result


def combine_curve_tables(tables, weights, estimated_memory_mib=0.):
    """Retain each profile row; curve risk is evaluated inside each linked group."""
    if not tables or set(tables)!=set(weights):raise ValueError('Complete curve cost tables and priorities are required')
    first=next(iter(tables.values()))
    for table in tables.values():
        if table['groups']!=first['groups'] or table['band_hz']!=first['band_hz'] or not np.array_equal(table['frequency'],first['frequency']):
            raise ValueError('Shared curve tables require an identical physical grid, groups and declared band')
    result={**first,'curve_weights':dict(weights),'estimated_memory_mib':estimated_memory_mib}
    result.pop('_risk_layout',None)
    result['rows']=[{**row,'curve_profile':name} for name,table in tables.items() for row in table['rows']]
    for key in ['constant','first','second']:result[key]=np.concatenate([t[key] for t in tables.values()],axis=0)
    if 'gain_constant' in first:
        if any('gain_constant' not in t for t in tables.values()):raise ValueError('Every curve must retain its complete gain moments')
        for key in ['gain_constant','gain_first_linear','gain_first_cubic']:
            result[key]=np.concatenate([t[key] for t in tables.values()],axis=0)
    return result


def build_curve_table(grids, snapshot, settings_by_curve, groups, levels, positions, weights, *, selected=None, budget=512., cancel=None, delays=None, progress=None):
    if delays is None:
        first=next(iter(grids.values()));settings=next(iter(settings_by_curve.values()))
        profile=receiver_profile(first.project.model,settings.get('receiver_constraints'))
        delays=native_delay_grid(settings,profile)
    return build_native_delay_table(grids,snapshot,settings_by_curve,groups,levels,positions,weights,delays,
                                     selected=selected,budget=budget,cancel=cancel,progress=progress)


def calibrate_joint_curves(project, curves, settings, *, headroom_curves=None, progress=None, cancel=None, frozen_references=None):
    if unsafe_capture_indices(project):raise ValueError('Unverified capture addresses cannot enter coupled native planning')
    if set(curves) not in [{'Flat'},set(CURVE_SPECS)] or any(c.get('project_id')!=project.id for c in curves.values()):
        raise ValueError('Coupled controls require complete designs from this measurement project')
    reserve_curves=curves if headroom_curves is None else headroom_curves
    if set(reserve_curves)==set(CURVE_SPECS):
        curve_designs({**reserve_curves['Flat'],'curve_designs':{'Reference':reserve_curves['Reference']}})
    elif set(reserve_curves)!={'Flat'}:raise ValueError('The coefficient reserve must cover every output curve')
    for key in ['receiver_time_crossover_search','receiver_auditory_balance','receiver_sub_level_search']:
        if key in settings and not isinstance(settings[key],bool):raise ValueError('Native optimization switches must be explicit booleans')
    if settings.get('relative_levels_confirmed') is not True or settings.get('routing_measurements_confirmed') is not True:
        raise ValueError('Shared controls require confirmed relative levels and individual uncrossed outputs')
    context=settings.get('receiver_measurement_context')
    if context not in CONTEXTS:raise ValueError('Choose an explicit shared-curve measurement context')
    if context=='uncrossed_playback_eq_off' and project.origin=='native_avr':
        raise ValueError('Native calibration captures cannot be assumed to contain playback output controls')
    snapshot=settings.get('receiver_snapshot',project.metadata.get('receiver_settings_snapshot',{}))
    active=snapshot.get('configured_channels',[])
    if not snapshot.get('available') or not isinstance(active,list) or not active or len(active)!=len(set(active)) or any(c not in snapshot.get('distances_m',{}) or c not in snapshot.get('levels_db',{}) for c in active):
        raise ValueError('A complete saved receiver settings snapshot is required')
    source_hash=snapshot_fingerprint(snapshot)
    snapshot={**snapshot,'distances_m':{c:snapshot['distances_m'][c] for c in active},
              'levels_db':{c:snapshot['levels_db'][c] for c in active}}
    profile=receiver_profile(project.model,settings.get('receiver_constraints'))
    validation=set(curves['Flat'].get('validation_positions',[]))
    train=Project(project.name,[m for m in project.measurements if m.position not in validation],
                  project.origin,project.model,project.tier,dict(project.metadata))
    mains=[c for c in train.channels if not c.startswith('SW')];subs=[c for c in train.channels if c.startswith('SW')]
    if not mains or any(c not in active for c in train.channels) or any(snapshot.get('speaker_types',{}).get(c) not in ['small','large'] for c in mains):
        raise ValueError('The shared receiver snapshot lacks a measured main output, distance, level or speaker type')
    search_enabled=bool(subs) and settings.get('receiver_time_crossover_search',True)
    if search_enabled and (len(subs)!=1 or settings.get('timing_confirmed') is not True):
        raise ValueError('Shared time/crossover search requires common timing and one physical subwoofer')
    if search_enabled and any(m.ir is None and m.phase_deg is None for m in project.measurements):
        raise ValueError('Every retained training and validation response requires phase for shared timing evaluation')
    weights=checked_curve_weights(settings,tuple(curves))
    risk=float(settings.get('receiver_curve_risk',.35));temperature=float(settings.get('receiver_curve_risk_temperature',.15))
    if not np.isfinite([risk,temperature]).all() or not 0<=risk<=1 or not 0<temperature<=10:
        raise ValueError('Invalid shared-curve risk controls')
    selection={'mode':'joint_curves' if len(curves)>1 else 'single_curve_coupled','curves':list(curves),'curve_weights':weights,
               'curve_risk':risk,'curve_risk_temperature':temperature,
               'level_objective':'weighted_mean_per_curve_ERB_Huber_with_shared_zero_sum_native_trims',
               'menu_objective':'within_profile_position_source_model_risk_then_curve_risk_then_group_risk',
               'fixed_FIRs':True,'global_joint_FIR_level_timing_optimum_claimed':False,
               'reserve_curves':list(reserve_curves),'includes_lfe':bool(search_enabled)}
    controls=dict(snapshot['levels_db'])
    if any(not np.isfinite(controls[c]) or not profile['level_min_db']<=controls[c]<=profile['level_max_db'] for c in train.channels):
        raise ValueError('A baseline speaker level exceeds the declared receiver profile')
    reservation=curve_headroom(reserve_curves)
    output_upper={c:profile['level_max_db']-reservation['compensation_db'][c] for c in train.channels}
    budget=float(settings.get('receiver_memory_mib',512))
    grids=_curve_grids(train,curves,budget,cancel)
    positions=position_weights(train,settings)
    same_model=settings.get('same_model_groups',project.metadata.get('same_model_groups',[]))
    references={name:_target_references(grid,snapshot,settings,mains,same_model) for name,grid in grids.items()}
    if frozen_references is not None:
        if set(frozen_references)!=set(curves) or any(set(frozen_references[n])!=set(references[n]) or
                not np.isfinite(list(frozen_references[n].values())).all() for n in curves):
            raise ValueError('Refinement requires complete finite frozen training target references')
        references={n:dict(frozen_references[n]) for n in curves}
    balance=None
    if settings.get('receiver_auditory_balance',True) and same_model:
        offsets=controls if context=='native_uncorrected' else {c:0. for c in controls}
        balance=balance_curve_groups(grids,same_model,controls,positions,weights,
            low=float(settings.get('auditory_low_hz',200)),high=float(settings.get('auditory_high_hz',5000)),
            step_db=profile['level_step_db'],max_change_db=float(settings.get('auditory_max_change_db',3)),
            minimum_db=profile['level_min_db'],maximum_db=profile['level_max_db'],response_offsets_db=offsets,
            output_maximum_db=output_upper,record_weights={n:repeat_confidence(g,settings)[0] for n,g in grids.items()},cancel=cancel)
        for c,delta in balance['trim_delta_db'].items():controls[c]+=delta
    if any(controls[c]>output_upper[c]+1e-8 for c in mains):
        raise ValueError('A shared main level leaves insufficient coefficient compensation; reduce boost or revise baseline levels')
    base_distances,basis=distance_basis(snapshot['distances_m'],profile,settings)
    result={'schema':'audioseyx-receiver-calibration-1','model':project.model,'profile':profile,
            'baseline_snapshot_sha256':source_hash,
            'baseline':{'distances_m':snapshot['distances_m'],'levels_db':snapshot['levels_db'],'crossovers_hz':snapshot.get('crossovers',{})},
            'control_selection':selection,'auditory_balance':balance,'distance_basis':basis,
            'coefficient_gain_reservation':reservation,
            'measurement_robustness':robustness_report(grids,settings),
            'fixed_training_target_references_db':references['Flat'],'curve_target_references_db':references,
            'target_reference_rule':'Per-curve baseline references, common within confirmed same-model groups; frozen from training before gain/menu selection',
            'training_positions':sorted(positions),'validation_positions':sorted(validation),
            'measurement_records_used':len(train.measurements),'original_ir_modified':False,
            'hardware_verified':False,'receiver_settings_written':False,
            'assumptions':{'measurement_context':context,'output_level_and_distance_order_verified':False,
                           'distance_delay_speed_m_s':basis['receiver_speed_m_s'],'physical_speed_m_s':basis['physical_speed_m_s'],
                           'distance_delay_conversion_verified':False,'distance_basis_method':basis['method'],
                           'routing_models_considered':settings.get('receiver_routing_models') or [settings.get('crossover_model','lr4')],
                           'native_filter_reconstruction_verified':False}}
    result['measurement_robustness']['used_in_routed_objective']=bool(search_enabled)
    if not search_enabled:
        if basis['relative_distances_rescaled']:
            fitted=quantized_distance_proposal(base_distances,mains[0],0.,basis['receiver_speed_m_s'],profile)
            if fitted is None:raise ValueError('The rescaled shared distance basis does not fit receiver menu limits')
            base_distances=fitted['distances_m']
        result.update(proposal={'distances_m':base_distances,'levels_db':controls,'crossovers_hz':{},'inverted_outputs':[]},
                      search=None,independent_validation=None,native_level_holdout_evaluation_available=False,
                      notice='Shared auditory trim proposal across both complete EQ curves. No timing/crossover search or level-only holdout evaluation is claimed.')
        validate_receiver_proposal(result,snapshot,active)
        return result
    groups=with_lfe_group(_linked_groups(mains,snapshot,profile,settings),settings)
    internal={}
    for name,design in curves.items():
        eq={k:v for k,v in design['settings'].items() if k in PROFILE_KEYS}
        internal[name]={**settings,**eq,'_measured_subwoofer':subs[0],
                        'receiver_delay_speed_m_s':basis['receiver_speed_m_s'],
                        '_receiver_target_references_db':references[name],'_receiver_base_distances_m':base_distances}
    table=build_curve_table(grids,snapshot,internal,groups,controls,positions,weights,budget=budget,cancel=cancel,progress=progress)
    search=search_coupled_native_grid(table,snapshot,profile,internal['Flat'],controls,
                                      reservation['compensation_db'][subs[0]],progress=progress,cancel=cancel)
    evaluation=evaluate_settings_table(table,search['delay_s'],search['inverted'],internal['Flat'],search['crossovers_hz'],sub_gain=search['sub_gain'])
    original_controls=dict(controls)
    controls[subs[0]]=search['sub_level_db']
    proposal={'distances_m':search['distance_proposal']['distances_m'],'levels_db':dict(controls),
              'crossovers_hz':{c:search['crossovers_hz'][g['id']] for g in groups for c in g['channels'] if g['small']},
              'inverted_outputs':[subs[0]] if search['inverted'] else []}
    band=table['band_hz'];memory=table['estimated_memory_mib'];del table
    direct=direct_power_evaluation(grids,snapshot,internal,groups,proposal,positions,weights,search['crossovers_hz'],cancel)
    gap=abs(direct['objective']-evaluation['objective'])
    if gap>1e-8*max(1.,abs(direct['objective']),abs(evaluation['objective'])):
        raise ValueError('Direct complete-response evaluation disagrees with the coupled gain/delay power moments')
    direct['power_moment_absolute_gap']=float(gap)
    if search['baseline_admissible']:
        baseline_proposal={'distances_m':quantized_distance_proposal(base_distances,subs[0],0.,basis['receiver_speed_m_s'],profile)['distances_m'],
                           'levels_db':original_controls,'crossovers_hz':{c:g['current_hz'] for g in groups for c in g['channels'] if g['small']},'inverted_outputs':[]}
        baseline_direct=direct_power_evaluation(grids,snapshot,internal,groups,baseline_proposal,positions,weights,
                                                 {g['id']:g['current_hz'] for g in groups},cancel)
        if direct['objective']>baseline_direct['objective']+1e-8*max(1.,abs(baseline_direct['objective'])):
            raise ValueError('The selected complete-response objective is worse than its admissible baseline')
        direct['admissible_baseline_objective']=baseline_direct['objective']
    baseline_choices={g['id']:[g['current_hz']] for g in groups}
    original_settings={name:{**s,'_receiver_base_distances_m':snapshot['distances_m']} for name,s in internal.items()}
    table=build_curve_table(grids,snapshot,original_settings,groups,snapshot['levels_db'],positions,weights,
                            selected=baseline_choices,budget=budget,cancel=cancel,delays=[0.])
    original=evaluate_settings_table(table,0.,False,internal['Flat'],{g['id']:g['current_hz'] for g in groups});del table
    chart_sets={name:native_routed_charts(grid,snapshot,proposal,internal[name],positions,references[name]) for name,grid in grids.items()}
    charts=chart_sets.pop('Flat')
    cases={}
    for name,grid in grids.items():
        for model in result['assumptions']['routing_models_considered']:
            cases[name+' / '+model]=_bass_headroom_proxy(grid,snapshot,proposal,{**internal[name],'crossover_model':model})
    worst=max(cases,key=lambda k:cases[k]['maximum_bound_growth_db'])
    headroom={'cases':cases,'worst_curve_model':worst,'maximum_bound_growth_db':cases[worst]['maximum_bound_growth_db'],
              'notice':'All curve/model coherent-input routing bounds are retained. These are conditional proxies, not measured output, distortion, clipping or SPL limits.'}
    del grids,grid
    validation_evaluation=None
    if validation:
        held=Project(project.name,[m for m in project.measurements if m.position in validation],
                     project.origin,project.model,project.tier,dict(project.metadata))
        held_grids=_curve_grids(held,curves,budget,cancel)
        held_settings={name:{**s,'position_weights':{},'receiver_analysis_low_hz':band[0],
                             'receiver_analysis_high_hz':band[1]} for name,s in internal.items()}
        held_positions=position_weights(held,{})
        original_held={name:{**s,'_receiver_base_distances_m':snapshot['distances_m']} for name,s in held_settings.items()}
        table=build_curve_table(held_grids,snapshot,original_held,groups,snapshot['levels_db'],held_positions,weights,
                                selected=baseline_choices,budget=budget,cancel=cancel,delays=[0.])
        before=evaluate_settings_table(table,0.,False,internal['Flat'],{g['id']:g['current_hz'] for g in groups});del table
        selected={g['id']:[search['crossovers_hz'][g['id']]] for g in groups}
        held_after={name:{**s,'_receiver_base_distances_m':proposal['distances_m'],
                          '_fixed_inverted_outputs':proposal['inverted_outputs']} for name,s in held_settings.items()}
        table=build_curve_table(held_grids,snapshot,held_after,groups,controls,held_positions,weights,
                                selected=selected,budget=budget,cancel=cancel,delays=[0.])
        after=evaluate_settings_table(table,0.,False,internal['Flat'],search['crossovers_hz'])
        held_direct=direct_power_evaluation(held_grids,snapshot,held_settings,groups,proposal,held_positions,weights,search['crossovers_hz'],cancel)
        held_gap=abs(held_direct['objective']-after['objective'])
        if held_gap>1e-8*max(1.,abs(held_direct['objective']),abs(after['objective'])):
            raise ValueError('Held-out complete-response evaluation disagrees with the power moments')
        validation_evaluation={'positions':sorted(validation),'used_for_design':False,'references_from_training':True,
                               'renormalized_after_design':False,'before':before,'after':after,'direct_after':held_direct,
                               'power_moment_absolute_gap':float(held_gap)}
    search['baseline_scope']='shared_main_ERB_levels_and_selected_distance_basis; selected_fixed_FIRs; original_crossovers_and_zero_extra_sub_delay_or_sub_level_change'
    result.update(proposal=proposal,search=search,evaluation=evaluation,original_evaluation=original,direct_forward_check=direct,
                  independent_validation=validation_evaluation,analysis_band_hz=band,
                  estimated_objective_memory_mib=memory,bass_routing_headroom_proxy=headroom,routed_charts=charts,
                  curve_routed_charts=chart_sets,
                  notice='Complete selected static EQ targets and the declared LFE passband participate in coupled native controls. Every admissible sub-level, distance, polarity and linked crossover choice is covered conditionally on fixed FIRs and shared main ERB trims. Position/source/model risk is combined per curve, then across curves inside each group, then across groups. Actual encoder reserve is enforced when available; without it, a necessary FIR-only bound is reported. Direct complex-response checks retain every repeat. This is not a global FIR/trim/timing optimum or verified acoustic performance.')
    validate_receiver_proposal(result,snapshot,active)
    return result
