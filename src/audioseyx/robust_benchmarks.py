"""Full 7.1.4, two-curve finite-uncertainty EQ/control benchmark."""
from __future__ import annotations
import copy
import time

from .native_refinement import refine_native_curves


def run_robust_fixture(project, initial, progress=None):
    started=time.monotonic()
    project=copy.deepcopy(project)
    for measurement in project.measurements:
        if not measurement.channel.startswith('SW'):
            measurement.ir[920]+=.3
    curves={'Flat':copy.deepcopy(initial),'Reference':copy.deepcopy(initial['curve_designs']['Reference'])}
    curves['Flat'].pop('curve_designs',None)
    for curve in curves.values():
        for key in ('receiver_calibration','shared_receiver_evaluation','temporal_prediction','routed_charts'):
            curve.pop(key,None)
    settings={**initial['settings'],'receiver_control_engine':'coupled','receiver_eq_refinement':True,
        'receiver_refinement_rounds':1,'receiver_refinement_step_db':.25,
        'receiver_routing_models':['lr4','bw2_lr4'],'receiver_crossover_candidates_hz':[80.],
        'receiver_sub_level_min_db':-.5,'receiver_sub_level_max_db':.5,'receiver_max_delay_ms':.1,
        'receiver_analysis_high_hz':1000.,'receiver_repeat_weighting':'consistency',
        'receiver_main_uncertainty_db':.2,'receiver_sub_uncertainty_db':.2,
        'receiver_timing_uncertainty_ms':.1,'temporal_diagnostics':False}
    result=refine_native_curves(project,curves,settings,progress=progress)
    report=result['eq_refinement'];quality=result['measurement_robustness']
    scores=[row['objective'] for row in report['trace'] if row['accepted']]
    checks=[
        {'name':'Robust two-curve full 7.1.4 FIR budgets retained','passed':all(sum(len(i['taps']) for i in curve['channels'])==195586 for curve in curves.values())},
        {'name':'Complete 27 gain and relative-timing uncertainty cases','passed':quality['uncertainty_case_count']==27 and quality['used_in_routed_objective']},
        {'name':'Robust direct and moment objectives agree in training and holdout','passed':result['direct_forward_check']['power_moment_absolute_gap']<1e-8 and result['independent_validation']['power_moment_absolute_gap']<1e-8},
        {'name':'Accepted EQ blocks improve the same frozen training objective','passed':all(b<a for a,b in zip(scores,scores[1:])) and report['final_objective']<=report['initial_objective']},
        {'name':'Robust refinement never fits the held-out seat','passed':not report['held_out_used_for_selection'] and not result['independent_validation']['used_for_design']},
        {'name':'No records removed or measured uncertainty guarantee invented','passed':quality['repeat_confidence']['records_discarded']==0 and not quality['uncertainty_bounds_measured'] and not quality['continuous_worst_case_guaranteed']},
    ]
    return dict(checks=checks,receiver_calibration=result,runtime_seconds=time.monotonic()-started,
                origin='synthetic',hardware_tested=False,sound_played=False)
