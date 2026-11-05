"""Known fixed-filter native-control truth, separate from learned-EQ quality."""
from __future__ import annotations

import copy
import numpy as np

from .joint_curve_controls import calibrate_joint_curves


def run_coupled_fixture(project, learned_design, progress=None):
    fixture=copy.deepcopy(project)
    snapshot=fixture.metadata['receiver_settings_snapshot']
    snapshot['levels_db']={c:-6. for c in snapshot['configured_channels']}
    settings={**learned_design['settings'],'receiver_control_engine':'coupled','receiver_curve_control_mode':'joint',
              'receiver_curve_weights':{'Flat':.5,'Reference':.5},'receiver_sub_level_search':True,
              'receiver_analysis_low_hz':20.,'receiver_analysis_high_hz':200.,
              'receiver_sub_level_min_db':-12.,'receiver_sub_level_max_db':12.}
    settings.pop('curve_profiles',None)
    curves={}
    for name,gain in [('Flat',1.),('Reference',2.)]:
        eq_settings=copy.deepcopy(settings)
        eq_settings.update(optimizer='independent',phase_mode='minimum',bass_db=0.,tilt_db_oct=0.,target_curve=None)
        if name=='Reference':eq_settings['target_curve']={'frequency':[20.,200.,1000.],
            'level_db':[20*np.log10(2),20*np.log10(2),0.],'reference':'normalised_at_1khz'}
        channels=[]
        for item in learned_design['channels']:
            taps=np.zeros_like(item['taps']);taps[0]=gain
            channels.append({'channel':item['channel'],'sample_rate':item['sample_rate'],'taps':taps,
                             'metrics':{'reference_level_db':item['metrics']['reference_level_db']}})
        curves[name]={'tier':learned_design['tier'],'project_id':fixture.id,'origin':'synthetic',
                      'settings':eq_settings,'channels':channels,
                      'training_positions':learned_design['training_positions'],
                      'validation_positions':learned_design['validation_positions']}
    result=calibrate_joint_curves(fixture,curves,settings,progress=progress)
    result.pop('routed_charts',None);result.pop('curve_routed_charts',None)
    search=result['search'];proposal=result['proposal']
    checks=[
        {'name':'Both fixed targets and LFE participate in coupled native controls',
         'passed':result['control_selection']['mode']=='joint_curves' and result['control_selection']['includes_lfe']},
        {'name':'Coupled search recovers known shared five-millisecond timing',
         'passed':abs(search['delay_s']+.005)<1e-9},
        {'name':'Coupled search recovers known sub level with coefficient reserve',
         'passed':abs(search['sub_level_db']+6.)<1e-9},
        {'name':'Shared auditory trims retain the known relative level match',
         'passed':proposal['levels_db']['FL']==-6.5 and proposal['levels_db']['FR']==-5.5},
        {'name':'Native-delay transform retains every full frequency moment',
         'passed':search['native_delay_transform']['response_frequency_bins_omitted']==0 and search['native_delay_transform']['FIR_taps_removed']==0},
        {'name':'Independent complete complex sums confirm the selected state',
         'passed':result['direct_forward_check']['power_moment_absolute_gap']<1e-8 and not result['independent_validation']['used_for_design']},
    ]
    return {'origin':'synthetic_fixed_complete_XT32_FIRs','hardware_tested':False,
            'analysis_band_hz':[20.,200.],'all_passed':all(c['passed'] for c in checks),
            'checks':checks,'receiver_calibration':result,
            'notice':'Identity/+6.0206 dB complete FIRs with matching LF targets provide known gain/timing truth. This tests native control algebra, not learned-EQ quality or acoustic performance.'}
