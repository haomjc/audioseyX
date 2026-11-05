import copy
import json
import unittest

import numpy as np

from audioseyx.model import Measurement, Project
from audioseyx.temporal import temporal_profile, compare_temporal_records, predict_filter_temporal
from audioseyx.comparison import compare_projects
from audioseyx.dsp import design_project
from audioseyx.feature_coverage import capability_assessment


class TemporalTests(unittest.TestCase):
    def response(self, tau=.08, fs=8000, duration=1., delay=.01, gain=1., metadata=None):
        x=np.zeros(round(fs*duration)); onset=round(fs*delay)
        t=np.arange(len(x)-onset)/fs
        x[onset:]=np.exp(-t/tau)
        return Measurement('FL','p0','damped response',fs,gain*x,metadata=metadata or {})

    def test_attenuation_does_not_establish_faster_decay(self):
        a=self.response(); b=self.response(gain=.0001)
        result=compare_temporal_records([a],[b])
        self.assertAlmostEqual(result['broadband']['before']['late_energy_fraction']['80']['median'],
                               result['broadband']['after']['late_energy_fraction']['80']['median'],places=13)
        self.assertAlmostEqual(result['broadband']['before']['decay_fit_s']['T20']['median'],
                               result['broadband']['after']['decay_fit_s']['T20']['median'],places=13)
        self.assertAlmostEqual(result['broadband']['before']['decay_fit_s']['T20']['median'],.08*np.log(1000),places=5)

    def test_known_faster_decay_is_detected_separately_from_gain(self):
        result=compare_temporal_records([self.response(.1)],[self.response(.025,gain=10)])
        a=result['broadband']['before']; b=result['broadband']['after']
        self.assertLess(b['late_energy_fraction']['80']['median'],a['late_energy_fraction']['80']['median']/10)
        self.assertAlmostEqual(a['decay_fit_s']['T20']['median']/b['decay_fit_s']['T20']['median'],4.,places=4)

    def test_unknown_timebase_never_reports_absolute_latency_change(self):
        result=compare_temporal_records([self.response(delay=.01)],[self.response(delay=.03)])
        self.assertIsNone(result['absolute_peak_time_delta_ms'])
        self.assertFalse(result['raw_alignment_applied'])
        self.assertAlmostEqual(result['broadband']['before']['late_energy_fraction']['80']['median'],
                               result['broadband']['after']['late_energy_fraction']['80']['median'],places=9)

    def test_explicit_shared_timebase_allows_measured_peak_shift(self):
        meta={'timebase_id':'synthetic common clock','timing_reference_verified':True}
        result=compare_temporal_records([self.response(delay=.01,metadata=meta)],
                                        [self.response(delay=.03,metadata=meta)])
        self.assertAlmostEqual(result['absolute_peak_time_delta_ms'],20.)
        changed=self.response(delay=.03,metadata={**meta,'timebase_id':'different'})
        self.assertIsNone(compare_temporal_records([self.response(metadata=meta)],[changed])['absolute_peak_time_delta_ms'])

    def test_shorter_recording_cannot_appear_to_have_faster_decay(self):
        a=self.response(.4,duration=2.)
        b=copy.deepcopy(a); b.ir=b.ir[:2000]
        result=compare_temporal_records([a],[b])
        self.assertEqual(result['before'][0]['broadband'],result['after'][0]['broadband'])
        self.assertLess(result['relative_window_s'][1],.25)
        self.assertFalse(result['after'][0]['broadband']['decay_fits']['T30']['available'])

    def test_all_repeats_are_retained_and_summarized_without_arbitrary_pairing(self):
        result=compare_temporal_records([self.response(.08),self.response(.16)],[self.response(.06)])
        self.assertEqual(len(result['before']),2)
        self.assertEqual(len(result['after']),1)
        self.assertEqual(result['broadband']['before']['late_energy_fraction']['80']['count'],2)
        self.assertEqual(result['bands'][0]['after']['repeat_count'],1)

    def test_frequency_only_repeat_disables_temporal_inference(self):
        m=Measurement('FL','p0','magnitude only',frequency=[20,200],magnitude_db=[0,0])
        result=compare_temporal_records([self.response(),m],[self.response()])
        self.assertFalse(result['available'])
        self.assertIn('Every retained',result['reason'])

    def test_analysis_filter_own_decay_is_not_reported_as_room_decay(self):
        x=np.zeros(16000); x[80]=1
        profile=temporal_profile(Measurement('FL','p0','impulse',8000,x))
        for band in profile['bands']:
            for fit in band.get('decay_fits',{}).values():
                self.assertFalse(fit['available'])
        self.assertFalse(profile['bands'][-1]['available'])
        json.dumps(profile,allow_nan=False)

    def test_memory_budget_and_anchor_are_enforced_without_truncation(self):
        m=self.response(); saved=m.ir.copy()
        with self.assertRaisesRegex(ValueError,'budget'):
            temporal_profile(m,memory_mib=.001)
        m.metadata['temporal_anchor_time_s']=10.
        with self.assertRaisesRegex(ValueError,'anchor'):
            temporal_profile(m)
        np.testing.assert_array_equal(saved,m.ir)

    def test_real_slow_band_decay_is_retained_when_the_analysis_filter_is_fast(self):
        fs=8000;t=np.arange(fs*2)/fs
        x=np.random.default_rng(45).normal(size=len(t))*np.exp(-np.log(1000)*t/.45)
        m=Measurement('FL','p0','known noisy exponential',fs,x,metadata={'temporal_anchor_time_s':0.})
        profile=temporal_profile(m,bands=[('1000 Hz octave',1000/np.sqrt(2),1000*np.sqrt(2))])
        band=profile['bands'][0]
        self.assertTrue(band['decay_fits']['T20']['available'])
        self.assertAlmostEqual(band['decay_fits']['T20']['estimate_s'],.45,delta=.04)
        self.assertLess(band['analysis_filter_slowest_pole_t60_s'],.02)

    def test_prediction_uses_full_linear_convolution_and_original_fixed_anchor(self):
        m=self.response(); project=Project('synthetic',[m],origin='synthetic')
        taps=np.zeros(1001); taps[0]=.1; taps[900]=2.
        design={'channels':[{'channel':'FL','sample_rate':8000,'taps':taps}],'validation_positions':['p0']}
        saved=m.ir.copy(); result=predict_filter_temporal(project,design)
        row=result['records'][0]
        self.assertEqual(row['full_linear_convolution_samples'],len(m.ir)+len(taps)-1)
        self.assertEqual(row['before']['anchor_time_s'],row['after']['anchor_time_s'])
        self.assertEqual(row['before']['relative_window_s'],row['after']['relative_window_s'])
        self.assertFalse(row['used_for_filter_selection'])
        self.assertEqual(result['stage'],'individual_output_EQ_FIR_only')
        np.testing.assert_array_equal(m.ir,saved)

    def test_prediction_refuses_to_guess_multirate_reconstruction(self):
        project=Project('synthetic',[self.response()],origin='synthetic')
        result=predict_filter_temporal(project,{'channels':[{'channel':'FL','sample_rate':6000,'taps':[1,0]}]})
        self.assertFalse(result['records'][0]['available'])
        self.assertIn('multirate',result['records'][0]['reason'])

    def test_design_opt_in_adds_diagnostics_without_changing_any_filter_taps(self):
        x=np.zeros(2048); x[50]=1
        project=Project('synthetic',[Measurement('FL','p0','pure delay',48000,x)],origin='synthetic')
        a=design_project(project,'MultEQ',{'bass_db':0,'tilt_db_oct':0})
        b=design_project(project,'MultEQ',{'bass_db':0,'tilt_db_oct':0,'temporal_diagnostics':True})
        np.testing.assert_array_equal(a['channels'][0]['taps'],b['channels'][0]['taps'])
        self.assertTrue(b['temporal_prediction']['records'][0]['available'])

    def test_matching_normal_playback_context_is_separate_from_shape_diagnostics(self):
        a=Project('before',[self.response()],origin='user_file')
        b=Project('after',[self.response(.03)],origin='user_file')
        self.assertFalse(compare_projects(a,b)['normal_playback_paths_comparable_in_metadata'])
        for p in [a,b]:p.metadata.update(normal_playback_verified=True,measurement_path_id='same uncrossed playback')
        self.assertTrue(compare_projects(a,b)['normal_playback_paths_comparable_in_metadata'])
        self.assertFalse(compare_projects(a,b)['acoustic_improvement_certified'])


class CoverageTests(unittest.TestCase):
    def test_checklist_counts_are_not_performance_percentages(self):
        report=capability_assessment()
        self.assertEqual(report['counts'],{'implemented_offline':6,'reference_only':2,'interface_blocked':2})
        self.assertEqual(sum(report['counts'].values()),len(report['features']))
        self.assertIsNone(report['performance_percentage'])
        self.assertFalse(report['native_hardware_validation_complete'])

    def test_current_coverage_does_not_fabricate_seats_or_configured_outputs(self):
        from audioseyx.listening_area import three_seat_row_plan
        x=np.zeros(128);x[10]=1
        p=Project('one point',[Measurement('FL','p0','one',ir=x),Measurement('FL','p0','two',ir=x)],
                  model='Denon AVR-X3700H',tier='XT32',metadata={
                      'listening_area':three_seat_row_plan(),
                      'receiver_settings_snapshot':{'available':True,'configured_channels':['FL','SW2']}})
        context=capability_assessment(p)['project_context']
        self.assertEqual(context['measurement_count'],2)
        self.assertEqual(context['measured_positions'],['p0'])
        self.assertEqual(context['missing_seats'],['left','right'])
        self.assertEqual(context['configured_unmeasured_outputs'],['SW2'])


if __name__=='__main__':unittest.main()
