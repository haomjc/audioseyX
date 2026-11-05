"""Public, synthetic known-solution checks for the integrated native workflow."""
from __future__ import annotations
import hashlib
import platform
import time
import numpy as np
from scipy import signal
import scipy

from . import __version__
from .clock_sync import pilot_signal, estimate_clock, correct_recording
from .dsp import design_project
from .model import Measurement, Project
from .listening_area import three_seat_row_plan


def run_native_workflow_benchmark(progress=None):
    started=time.monotonic();rate=48000
    channels=['FL','FR','C','SLA','SRA','SBL','SBR','TFL','TFR','TRL','TRR','SW1']
    rows=[]
    for position in ['p0','p1','p2','p3']:
        for channel in channels:
            amplitude=10**(.5/20) if channel=='FL' else 10**(-.5/20) if channel=='FR' else 1.
            for repeat in range(2):
                value=np.zeros(2048);value[720 if channel=='SW1' else 480]=amplitude
                rows.append(Measurement(channel,position,f'{channel}/{position}/{repeat}',rate,value,
                                        timing_reference='synthetic_common'))
    mains=channels[:-1]
    snapshot={'available':True,'configured_channels':channels,'speaker_types':{c:'small' for c in mains},
              'large_channels':[],'distances_m':{c:3. for c in channels},'levels_db':{c:0. for c in channels},
              'crossovers':{c:80. for c in mains},'bass_mode':'lfe','lfe_lowpass_hz':120.}
    project=Project('Abstract full native-menu benchmark',rows,'synthetic','AVR-X3700H','XT32',
        {'receiver_settings_snapshot':snapshot,'same_model_groups':[['FL','FR'],['SLA','SRA'],['SBL','SBR'],['TFL','TFR','TRL','TRR']],
         'listening_area':three_seat_row_plan()})
    settings={'optimizer':'native_calibration','phase_mode':'minimum','validation_positions':['p3'],
              'timing_confirmed':True,'relative_levels_confirmed':True,'routing_measurements_confirmed':True,
              'receiver_measurement_context':'native_uncorrected','receiver_routing_models':['lr4'],
              'receiver_delay_speed_m_s':300.,'receiver_max_delay_ms':6.,'receiver_delay_penalty':0.,
              'receiver_crossover_candidates_hz':[60.,80.,100.],'receiver_memory_mib':1024,
              'low_hz':20,'high_hz':500,'sub_high_hz':200,'max_boost_db':3,'max_cut_db':12,
              'bass_db':0.,'tilt_db_oct':0.,'temporal_diagnostics':True,
              'curve_profiles':{'Reference':{'bass_db':3.,'target_curve':None}}}
    result=design_project(project,'XT32',settings,progress)
    native=result['receiver_calibration'];proposal=native['proposal'];search=native['search']
    checks=[{'name':'Complete XT32 fixed-FIR space retained','passed':sum(len(i['taps']) for i in result['channels'])==195586},
            {'name':'Known five-millisecond shared bass timing','passed':abs(search['delay_s']+.005)<1e-9},
            {'name':'Native distance centimetres','passed':abs(proposal['distances_m']['SW1']-4.5)<1e-9},
            {'name':'Discrete same-model level match','passed':proposal['levels_db']['FL']==-.5 and proposal['levels_db']['FR']==.5},
            {'name':'Exact linked crossover selection retains neutral choice','passed':all(v==80 for v in proposal['crossovers_hz'].values())},
            {'name':'Full native finite-grid search','passed':search['exact_minimum_over_declared_native_grid']},
            {'name':'Independent position excluded from all settings selection','passed':not native['independent_validation']['used_for_design']},
            {'name':'Three distinct measured training seats','passed':result['settings']['listening_area_resolution']['required_seats_measured']}]
    temporal=result['temporal_prediction']
    complete_temporal=(len(temporal['records'])==len(rows) and
                      all(r['available'] and r['measurement_index']==i for i,r in enumerate(temporal['records'])))
    fixed_references=all(r['before']['anchor_time_s']==r['after']['anchor_time_s'] and
                         r['before']['relative_window_s']==r['after']['relative_window_s']
                         for r in temporal['records'])
    checks.extend([
        {'name':'All original repeats retained in complete EQ-stage convolution','passed':complete_temporal},
        {'name':'Temporal references and measured support remain fixed','passed':fixed_references}])
    reference_curve = result['curve_designs']['Reference']
    checks.extend([
        {'name':'Reference retains its complete independent XT32 FIR space',
         'passed':sum(len(i['taps']) for i in reference_curve['channels'])==195586 and
                  any(not np.array_equal(a['taps'],b['taps']) for a,b in zip(result['channels'],reference_curve['channels']))},
        {'name':'Both curves share the primary-selected native controls',
         'passed':reference_curve['shared_receiver_evaluation']['proposal']==proposal and
                  not reference_curve['shared_receiver_evaluation']['controls_selected_for_this_curve']}])
    clock_rate=8000;duration=.04;times=np.array([.4,1.3,2.2,3.1]);ppm=350.
    recording_time=np.arange(32000)/clock_rate;reference=(recording_time-.035)/(1+ppm*1e-6)
    recording=np.zeros(len(recording_time))
    for marker in times:
        local=reference-marker;selected=(local>=0)&(local<=(round(clock_rate*duration)-1)/clock_rate)
        window=.5-.5*np.cos(2*np.pi*local[selected]*clock_rate/(round(clock_rate*duration)-1))
        recording[selected]+=signal.chirp(local[selected],clock_rate*.06,duration,clock_rate*.3,method='logarithmic')*window
    pilot=pilot_signal(clock_rate,duration)
    before=estimate_clock(recording,pilot,clock_rate,times,latency_guess_s=.035,search_radius_s=.08)
    corrected,mapping=correct_recording(recording,before)
    after=estimate_clock(corrected,pilot,clock_rate,times,latency_guess_s=.035,search_radius_s=.08)
    checks.extend([{'name':'Known original-recording clock drift','passed':abs(before['estimated_drift_ppm']-ppm)<1.5},
                   {'name':'Corrected clock scale','passed':abs(after['estimated_drift_ppm'])<1.5},
                   {'name':'No fabricated IR or direct-arrival interpretation','passed':not mapping['deconvolution_performed'] and not before['direct_arrival_identified']}])
    from .coupled_benchmarks import run_coupled_fixture
    coupled=run_coupled_fixture(project,result,progress)
    checks.extend(coupled['checks'])
    from .robust_benchmarks import run_robust_fixture
    robust=run_robust_fixture(project,result,progress)
    checks.extend(robust['checks'])
    fingerprint=hashlib.sha256()
    for m in rows:fingerprint.update(m.ir.astype('<f8').tobytes())
    return {'version':__version__,'origin':'synthetic','hardware_tested':False,'sound_played':False,
            'all_passed':all(c['passed'] for c in checks),'checks':checks,'settings':settings,
            'measurement_sha256':fingerprint.hexdigest(),'receiver_calibration':native,
            'coupled_native_fixture':coupled,
            'robust_refinement_fixture':robust,
            'temporal_prediction':{'stage':temporal['stage'],'records':len(temporal['records']),
                                   'all_original_records_retained':complete_temporal,'fixed_references_and_support':fixed_references,
                                   'hardware_verified':False},
            'reference_curve':{'expanded_fir_taps':sum(len(i['taps']) for i in reference_curve['channels']),
                               'shared_controls_evaluation':reference_curve['shared_receiver_evaluation']},
            'clock':{'known_drift_ppm':ppm,'before':before,'after':after,'mapping':mapping},
            'runtime_seconds':time.monotonic()-started,
            'environment':{'python':platform.python_version(),'numpy':np.__version__,'scipy':scipy.__version__},
            'notice':'Abstract known-solution native-menu and raw-recording fixtures. Native grid exactness is conditional on fixed FIRs, the proposed trim state and the declared model; it is not global acoustic optimality, OCA/GSonic equality or hardware validation.'}
