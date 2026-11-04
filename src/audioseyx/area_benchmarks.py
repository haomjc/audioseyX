"""Synthetic three-seat validation, kept separate from personal measurements."""
from time import monotonic
import numpy as np
from . import __version__
from .dsp import demo_project,design_project
from .listening_area import three_seat_row_plan


def run_listening_area_benchmark(iterations=60,progress=None):
    started=monotonic();project=demo_project(positions=4)
    project.name='Abstract three-seat synthetic fixture'
    plan=three_seat_row_plan()
    settings={'optimizer':'native_joint','timing_confirmed':True,'relative_levels_confirmed':True,
              'routing_measurements_confirmed':True,'listening_area':plan,'validation_positions':['p3'],
              'high_hz':500,'bass_db':3,'tilt_db_oct':-.5,'joint_delay_ms':5,'fdw_cycles':6,
              'joint_penalty_metric':'root_data_diagonal','joint_cg_iterations':150,
              'joint_iterations':iterations,'position_risk':.35}
    design=design_project(project,'XT32',settings,progress)
    evaluation=design['joint_evaluation'];optimizer=design['joint_optimization']
    resolution=design['settings']['listening_area_resolution']
    checks=[{'name':'Three distinct measured training seats','passed':resolution['required_seats_measured']},
            {'name':'Central/left/right priorities','passed':all(abs(resolution['position_weights'][p]-weight)<1e-12 for p,weight in [('p0',.5),('p1',.25),('p2',.25)])},
            {'name':'Every XT32 reference tap retained','passed':optimizer['optimization_variables']==195586},
            {'name':'Actual returned-filter gain feasibility','passed':optimizer['gain_constraints_satisfied']},
            {'name':'Independent spatial holdout excluded from fitting','passed':not evaluation['holdout']['used_for_design']},
            {'name':'Exact smooth worst-position risk is used','passed':optimizer['spatial_risk_solver']=='exact_logsumexp_newton_cg'},
            {'name':'Finite training and holdout errors','passed':bool(np.isfinite(evaluation['joint']['weighted_magnitude_rms_db']) and np.isfinite(evaluation['holdout']['joint']['weighted_magnitude_rms_db']))}]
    return {'version':__version__,'origin':'synthetic','hardware_tested':False,'all_passed':all(c['passed'] for c in checks),
            'checks':checks,'settings':settings,'listening_area':resolution,'joint_optimization':optimizer,
            'joint_evaluation':evaluation,'runtime_seconds':monotonic()-started,
            'notice':'An abstract deterministic fixture, not measurements of the user\'s row. Feasibility, exact-risk computation and held-out evaluation are separate from numerical convergence and hardware validation.'}
