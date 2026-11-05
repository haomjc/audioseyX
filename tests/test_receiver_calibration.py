import copy
from itertools import product
import unittest

import numpy as np

from audioseyx.auditory_balance import optimal_quantized_levels, balance_groups
from audioseyx.model import Measurement, Project
from audioseyx.receiver_constraints import receiver_profile, quantized_distance_proposal, distance_basis
from audioseyx.receiver_calibration import calibrate_receiver, _linked_groups, _cost_table, search_native_grid, evaluate_settings_table
from audioseyx.routing import crossover_responses
from audioseyx.spectral_grid import SpectralGrid


def fixture(channels=('FL','FR','SW1'), positions=('p0',), amplitudes=None):
    measurements=[];amplitudes=amplitudes or {}
    for position in positions:
        for channel in channels:
            ir=np.zeros(128);ir[60 if channel.startswith('SW') else 20]=amplitudes.get(channel,1.)
            measurements.append(Measurement(channel,position,channel,8000,ir,timing_reference='synthetic_common'))
    mains=[c for c in channels if not c.startswith('SW')]
    snapshot={'available':True,'configured_channels':list(channels),
              'speaker_types':{c:'small' for c in mains},'large_channels':[],
              'distances_m':{c:3. for c in channels},'levels_db':{c:0. for c in channels},
              'crossovers':{c:80. for c in mains},'bass_mode':'lfe','lfe_lowpass_hz':120.}
    project=Project('Native synthetic fixture',measurements,'synthetic','AVR-X3700H','XT32',
                    {'receiver_settings_snapshot':snapshot,'same_model_groups':[['FL','FR']] if 'FR' in channels else []})
    design={'tier':'XT32','channels':[{'channel':c,'sample_rate':8000,'taps':np.r_[1.,np.zeros(7)],
                                     'metrics':{'reference_level_db':0.}} for c in channels]}
    settings={'timing_confirmed':True,'relative_levels_confirmed':True,'routing_measurements_confirmed':True,
              'receiver_measurement_context':'native_uncorrected','receiver_delay_speed_m_s':340.,
              'receiver_max_delay_ms':6.,'receiver_crossover_candidates_hz':[60.,80.,100.],
              'receiver_analysis_high_hz':1000.,'receiver_auditory_balance':False,
              'bass_db':0.,'tilt_db_oct':0.,'validation_positions':[]}
    return project,design,snapshot,settings


class AuditoryBalanceTests(unittest.TestCase):
    def test_dynamic_program_is_the_exact_constrained_finite_grid_minimum(self):
        rng=np.random.default_rng(78)
        costs=[rng.random(5) for _ in range(4)];options=[np.arange(-2,3) for _ in range(4)]
        score,steps=optimal_quantized_levels(costs,options)
        reference=min(sum(costs[i][s+2] for i,s in enumerate(v)) for v in product(range(-2,3),repeat=4) if sum(v)==0)
        self.assertAlmostEqual(score,reference,places=12)
        self.assertEqual(sum(steps),0)

    def test_flat_four_db_mismatch_is_balanced_without_changing_group_mean(self):
        p,d,s,_=fixture(amplitudes={'FL':10**(.2),'FR':1})
        grid=SpectralGrid(p,d)
        report=balance_groups(grid,[['FL','FR']],s['levels_db'],{'p0':1},high=2500)
        self.assertEqual(report['trim_delta_db'],{'FL':-2.,'FR':2.})
        self.assertLess(report['groups'][0]['band_imbalance_rms_after_db'],1e-10)
        self.assertEqual(report['groups'][0]['zero_sum_delta_db'],0)
        self.assertFalse(report['loudness_standard_certification'])

    def test_existing_levels_are_not_double_counted_for_playback_measurements(self):
        p,d,s,_=fixture(amplitudes={'FL':10**(.2),'FR':1})
        s['levels_db'].update(FL=-2.,FR=2.)
        grid=SpectralGrid(p,d)
        raw=balance_groups(grid,[['FL','FR']],s['levels_db'],{'p0':1},high=2500)
        playback=balance_groups(grid,[['FL','FR']],s['levels_db'],{'p0':1},high=2500,
                                response_offsets_db={c:0. for c in s['levels_db']})
        self.assertEqual(raw['trim_delta_db'],{'FL':0.,'FR':0.})
        self.assertEqual(playback['trim_delta_db'],{'FL':-2.,'FR':2.})

    def test_repeat_count_does_not_change_spatial_priority(self):
        p,d,s,_=fixture(positions=('p0','p1'),amplitudes={'FL':10**(.2)})
        a=balance_groups(SpectralGrid(p,d),[['FL','FR']],s['levels_db'],{'p0':.6,'p1':.4},high=2500)
        p.measurements.extend(copy.deepcopy([m for m in p.measurements if m.position=='p0']))
        b=balance_groups(SpectralGrid(p,d),[['FL','FR']],s['levels_db'],{'p0':.6,'p1':.4},high=2500)
        self.assertEqual(a['trim_delta_db'],b['trim_delta_db'])
        self.assertAlmostEqual(a['groups'][0]['objective'],b['groups'][0]['objective'],places=12)


class NativeMenuSearchTests(unittest.TestCase):
    def test_power_polynomial_matches_the_direct_complex_forward_model(self):
        p,d,s,settings=fixture()
        grid=SpectralGrid(p,d);profile=receiver_profile(p.model)
        groups=_linked_groups(['FL','FR'],s,profile,settings)
        table=_cost_table(grid,s,settings,groups,s['levels_db'],{'p0':1})
        f=table['frequency'];w=1/f;w/=w.sum()
        for delay,invert in [(0.,False),(-.005,False),(.002,True)]:
            phase=np.exp(-2j*np.pi*f*delay)
            value=table['constant']+np.real(((-1 if invert else 1)*table['first'])@phase+table['second']@phase**2)
            for index,row in enumerate(table['rows']):
                hp,lp=crossover_responses(f,row['crossover_hz'],'lr4',8000)
                a=np.exp(-2j*np.pi*f*20/8000)*hp
                b=np.exp(-2j*np.pi*f*60/8000)*lp*phase*(-1 if invert else 1)
                direct=float(w@((np.abs(a+b)**2-1)**2))
                self.assertAlmostEqual(value[index],direct,places=11)

    def test_known_five_ms_offset_is_recovered_on_the_actual_distance_grid(self):
        p,d,s,settings=fixture()
        settings['receiver_delay_penalty']=0.
        original=[m.ir.copy() for m in p.measurements]
        result=calibrate_receiver(p,d,settings)
        self.assertAlmostEqual(result['search']['delay_s'],-.005,places=12)
        self.assertFalse(result['search']['inverted'])
        self.assertAlmostEqual(result['proposal']['distances_m']['SW1'],4.7,places=9)
        self.assertEqual(result['proposal']['crossovers_hz'],{'FL':80.,'FR':80.})
        self.assertLess(result['evaluation']['objective'],1e-10)
        self.assertTrue(result['search']['exact_minimum_over_declared_native_grid'])
        for m,before in zip(p.measurements,original):np.testing.assert_array_equal(m.ir,before)

    def test_sub_polarity_is_an_output_choice_not_a_wiring_diagnosis(self):
        p,d,s,settings=fixture(amplitudes={'SW1':-1})
        result=calibrate_receiver(p,d,settings)
        self.assertTrue(result['search']['inverted'])
        self.assertEqual(result['proposal']['inverted_outputs'],['SW1'])
        self.assertFalse(result['hardware_verified'])

    def test_conditional_group_selection_matches_full_crossover_enumeration(self):
        p,d,s,settings=fixture(channels=('FL','FR','C','SW1'))
        settings.update(receiver_max_delay_ms=.1)
        grid=SpectralGrid(p,d);profile=receiver_profile(p.model)
        groups=_linked_groups(['FL','FR','C'],s,profile,settings)
        table=_cost_table(grid,s,settings,groups,s['levels_db'],{'p0':1})
        search=search_native_grid(table,s,profile,{**settings,'_measured_subwoofer':'SW1'})
        best=float('inf');quantum=.01/340
        for k in range(-3,4):
            delay=k*quantum
            for invert in [False,True]:
                for pair,centre in product([60.,80.,100.],repeat=2):
                    value=evaluate_settings_table(table,delay,invert,settings,{'FL/FR':pair,'C':centre})['objective']
                    value+=.0001*(delay/.0001)**2+.00001*invert
                    best=min(best,value)
        self.assertAlmostEqual(search['objective'],best,places=11)
        self.assertEqual(search['linked_crossover_combinations'],9)

    def test_held_out_positions_do_not_select_any_menu_value(self):
        p,d,s,settings=fixture(positions=('p0','p1'))
        settings['validation_positions']=['p1']
        a=calibrate_receiver(p,d,settings)
        for m in p.measurements:
            if m.position=='p1':m.ir*=4
        b=calibrate_receiver(p,d,settings)
        self.assertEqual(a['proposal'],b['proposal'])
        self.assertEqual(a['search'],b['search'])
        self.assertFalse(b['independent_validation']['used_for_design'])
        self.assertGreater(b['independent_validation']['after']['objective'],a['independent_validation']['after']['objective']+1)

    def test_zero_subwoofer_stereo_uses_level_only_without_fabricating_timing(self):
        p,d,s,settings=fixture(channels=('FL','FR'),amplitudes={'FL':10**(.2)})
        settings.update(receiver_auditory_balance=True,auditory_high_hz=2500,timing_confirmed=False)
        result=calibrate_receiver(p,d,settings)
        self.assertIsNone(result['search'])
        self.assertEqual(result['proposal']['levels_db'],{'FL':-2.,'FR':2.})
        self.assertEqual(result['proposal']['crossovers_hz'],{})
        self.assertEqual(result['proposal']['distances_m'],s['distances_m'])

    def test_large_lfe_outputs_do_not_invent_a_crossover_or_timing_observation(self):
        p,d,s,settings=fixture()
        s['speaker_types']={'FL':'large','FR':'large'};s['crossovers']={};s['large_channels']=['FL','FR']
        settings['receiver_max_delay_ms']=.1
        result=calibrate_receiver(p,d,settings)
        self.assertEqual(result['proposal']['crossovers_hz'],{})
        self.assertFalse(result['search']['time_integration_observable'])
        self.assertEqual(result['proposal']['distances_m'],s['distances_m'])

    def test_routing_ensemble_retains_all_model_scenarios_and_matches_direct_costs(self):
        p,d,s,settings=fixture()
        settings['receiver_routing_models']=['lr4','bw2_lr4']
        grid=SpectralGrid(p,d);groups=_linked_groups(['FL','FR'],s,receiver_profile(p.model),settings)
        table=_cost_table(grid,s,settings,groups,s['levels_db'],{'p0':1})
        self.assertEqual(len(table['rows']),12)
        f=table['frequency'];w=1/f;w/=w.sum();delay=.001
        phase=np.exp(-2j*np.pi*f*delay)
        values=table['constant']+np.real(table['first']@phase+table['second']@phase**2)
        for i,row in enumerate(table['rows']):
            hp,lp=crossover_responses(f,row['crossover_hz'],row['routing_model'],8000)
            direct=w@((np.abs(np.exp(-2j*np.pi*f*.0025)*hp+np.exp(-2j*np.pi*f*.0075)*lp*phase)**2-1)**2)
            self.assertAlmostEqual(values[i],direct,places=11)

    def test_unconfirmed_context_or_missing_phase_is_not_assumed(self):
        p,d,s,settings=fixture()
        with self.assertRaisesRegex(ValueError,'context'):
            calibrate_receiver(p,d,{**settings,'receiver_measurement_context':'unknown'})
        with self.assertRaisesRegex(ValueError,'confirmations'):
            calibrate_receiver(p,d,{**settings,'relative_levels_confirmed':False})


class DistanceGridTests(unittest.TestCase):
    def test_common_offset_preserves_relative_delays_and_enforces_menu_limits(self):
        profile=receiver_profile('AVR-X3700H')
        proposal=quantized_distance_proposal({'FL':.2,'FR':1.2,'SW1':.1},'SW1',.002,340,profile)
        self.assertAlmostEqual(proposal['common_offset_m'],.58)
        self.assertAlmostEqual(proposal['distances_m']['SW1'],0)
        self.assertAlmostEqual(proposal['distances_m']['FR']-proposal['distances_m']['FL'],1.)
        self.assertIsNone(quantized_distance_proposal({'FL':0.,'FR':6.,'SW1':3.},'SW1',0,340,profile))

    def test_unknown_receivers_require_explicit_constraints(self):
        with self.assertRaises(ValueError):receiver_profile('UNKNOWN')
        profile=receiver_profile('AVR-X3700H');profile['distance_step_m']=.001
        with self.assertRaises(ValueError):receiver_profile('CUSTOM',profile)

    def test_legacy_reference_factor_is_separate_from_the_physical_speed(self):
        profile=receiver_profile('AVR-X3700H')
        self.assertEqual(profile['distance_delay_speed_m_s'],300.)
        current={'FL':3.,'FR':4.,'SW1':5.}
        unchanged,_=distance_basis(current,profile,{})
        self.assertEqual(unchanged,current)
        corrected,info=distance_basis(current,profile,{'receiver_distance_basis':'physical_rescale'})
        self.assertEqual(corrected['FL'],3.)
        self.assertAlmostEqual(corrected['FR'],3.87)
        self.assertFalse(info['hardware_verified'])


if __name__=='__main__':unittest.main()
