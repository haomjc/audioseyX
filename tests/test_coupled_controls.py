import copy
from itertools import product
import unittest
from unittest.mock import patch
import numpy as np

from audioseyx.auditory_balance import balance_curve_groups
from audioseyx.coupled_native_search import search_coupled_native_grid, sub_level_candidates
from audioseyx.dsp import design_project
from audioseyx.joint_curve_controls import checked_curve_weights, combine_curve_tables
from audioseyx.joint_curve_controls import calibrate_joint_curves
from audioseyx.native_power_model import (with_lfe_group,power_scenarios,build_power_table,
                                          build_native_delay_table,native_delay_grid)
from audioseyx.receiver_calibration import _linked_groups,evaluate_settings_table,smooth_risk
from audioseyx.receiver_constraints import receiver_profile
from audioseyx.spectral_grid import SpectralGrid
from test_receiver_calibration import fixture


def tables_fixture(two_models=False):
    p,d,s,settings=fixture()
    settings.update(receiver_crossover_groups=[['FL'],['FR']],receiver_crossover_candidates_hz=[60.,80.],
                    receiver_max_delay_ms=.1,receiver_sub_level_min_db=-1.,receiver_sub_level_max_db=1.,
                    receiver_delay_penalty=0.,receiver_polarity_penalty=0.,receiver_sub_level_penalty=0.,
                    receiver_crossover_change_penalty=0.,receiver_curve_risk=.4,receiver_position_risk=.25,
                    receiver_lfe_priority=1.5)
    if two_models:settings['receiver_routing_models']=['lr4','bw2_lr4']
    repeated=copy.deepcopy(p.measurements)
    for index,m in enumerate(repeated):m.ir=np.roll(m.ir*(.8+index*.15),index+1)
    p.measurements+=repeated
    alternate=copy.deepcopy(d)
    alternate['channels'][0]['taps'][1]=.25
    alternate['channels'][-1]['taps']*=.7
    curves={'Flat':d,'Reference':alternate}
    grids={c:SpectralGrid(p,design) for c,design in curves.items()}
    profile=receiver_profile(p.model)
    groups=with_lfe_group(_linked_groups(['FL','FR'],s,profile,settings),settings)
    weights={'Flat':.7,'Reference':.3}
    per_settings={c:{**settings,'bass_db':0. if c=='Flat' else 2.,'_measured_subwoofer':'SW1'} for c in curves}
    tables={c:build_power_table(g,s,per_settings[c],groups,s['levels_db'],{'p0':1.}) for c,g in grids.items()}
    return p,s,profile,grids,groups,per_settings,weights,tables


def independent_risk(values,masses,risk,temperature):
    values=np.asarray(values);mass=np.asarray(masses,dtype=float);mass/=mass.sum()
    maximum=values.max()
    return (1-risk)*float(mass@values)+risk*(maximum+temperature*np.log(float(mass@np.exp((values-maximum)/temperature))))


class PowerModelTests(unittest.TestCase):
    def test_repeat_moments_equal_every_explicit_cartesian_physical_sum(self):
        _,s,_,grids,groups,options,_,tables=tables_fixture(True)
        for name,grid in grids.items():
            scenarios=list(power_scenarios(grid,s,options[name],groups,s['levels_db'],{'p0':1.}))
            table=tables[name]
            for row_index,(row,f,w,pairs) in enumerate(scenarios):
                self.assertEqual(row['repeat_scenarios'],4 if row['source']!='LFE' else 2)
                for gain,delay,inverted in [(1.,0.,False),(.7,.0002,True),(1.4,-.001,False)]:
                    phase=np.exp(-2j*np.pi*f*delay);sign=-1 if inverted else 1
                    direct=sum(float(w@((np.abs(main+sign*gain*bass*phase)**2-1)**2)) for main,bass in pairs())/row['repeat_scenarios']
                    c=table['gain_constant'][row_index]
                    actual=c[0]+gain**2*c[1]+gain**4*c[2]+np.real(sign*(gain*(table['gain_first_linear'][row_index]@phase)+gain**3*(table['gain_first_cubic'][row_index]@phase))+gain**2*(table['second'][row_index]@(phase**2)))
                    self.assertAlmostEqual(direct,actual,places=11)

    def test_lfe_retains_its_passband_and_has_no_delay_or_polarity_dependence(self):
        _,_,_,_,_,options,weights,tables=tables_fixture()
        combined=combine_curve_tables(tables,weights)
        lfe=[i for i,r in enumerate(combined['rows']) if r['source']=='LFE']
        self.assertTrue(lfe)
        self.assertTrue(np.all(combined['gain_first_linear'][lfe]==0))
        self.assertTrue(np.all(combined['gain_first_cubic'][lfe]==0))
        self.assertTrue(np.all(combined['second'][lfe]==0))
        choices={g['id']:g['current_hz'] for g in combined['groups']}
        a=evaluate_settings_table(combined,0.,False,options['Flat'],choices,sub_gain=.8)
        b=evaluate_settings_table(combined,.001,True,options['Flat'],choices,sub_gain=.8)
        self.assertAlmostEqual(a['group_power_error']['LFE'],b['group_power_error']['LFE'],places=13)
        self.assertLessEqual(tables['Flat']['lfe_band_hz'][1],120.)

    def test_complete_frequency_transform_matches_full_moments_at_every_native_point(self):
        _,s,profile,grids,groups,options,weights,tables=tables_fixture(True)
        delays=native_delay_grid(options['Flat'],profile)
        native=build_native_delay_table(grids,s,options,groups,s['levels_db'],{'p0':1.},weights,delays,budget=512)
        complete=combine_curve_tables(tables,weights)
        choices={g['id']:g['current_hz'] for g in groups}
        for delay,gain,inverted in product(delays,[.7,1.,1.4],[False,True]):
            a=evaluate_settings_table(native,float(delay),inverted,options['Flat'],choices,sub_gain=gain)
            b=evaluate_settings_table(complete,float(delay),inverted,options['Flat'],choices,sub_gain=gain)
            self.assertAlmostEqual(a['objective'],b['objective'],places=9)
        self.assertEqual(native['native_delay_transform']['frequency_bins_used'],len(complete['frequency']))
        self.assertEqual(native['native_delay_transform']['response_frequency_bins_omitted'],0)
        self.assertNotIn('gain_first_linear',native)
        with self.assertRaisesRegex(ValueError,'native grid'):
            evaluate_settings_table(native,float(delays[1]+delays[2])/2,False,options['Flat'],choices)

    def test_zero_delay_transform_and_memory_refusal_do_not_discard_data(self):
        _,s,_,grids,groups,options,weights,tables=tables_fixture()
        with patch('audioseyx.native_power_model._row_moments') as construct:
            with self.assertRaisesRegex(ValueError,'no curves'):
                build_native_delay_table(grids,s,options,groups,s['levels_db'],{'p0':1.},weights,[0.],budget=32)
            construct.assert_not_called()
        native=build_native_delay_table(grids,s,options,groups,s['levels_db'],{'p0':1.},weights,[0.],budget=512)
        complete=combine_curve_tables(tables,weights)
        self.assertAlmostEqual(evaluate_settings_table(native,0.,False,options['Flat'])['objective'],
                               evaluate_settings_table(complete,0.,False,options['Flat'])['objective'],places=10)

    def test_native_gain_delay_polarity_and_crossover_search_matches_independent_brute_force(self):
        _,s,profile,grids,groups,options,weights,_=tables_fixture(True)
        delays=native_delay_grid(options['Flat'],profile)
        table=build_native_delay_table(grids,s,options,groups,s['levels_db'],{'p0':1.},weights,delays,budget=512)
        settings=options['Flat']
        selected=search_coupled_native_grid(table,s,profile,settings,s['levels_db'])
        scenarios={c:list(power_scenarios(g,s,options[c],groups,s['levels_db'],{'p0':1.})) for c,g in grids.items()}
        best=float('inf');count=0
        for cross_values in product(*(g['choices_hz'] for g in groups)):
            for delay,inverted,level in product(delays,[False,True],np.arange(-1.,1.01,.5)):
                gain=10**(level/20);group_values=[]
                for group_index,group in enumerate(groups):
                    per_curve=[]
                    for name in weights:
                        losses=[];masses=[]
                        for row,f,w,pairs in scenarios[name]:
                            if row['group']!=group_index or row['crossover_hz']!=cross_values[group_index]:continue
                            phase=np.exp(-2j*np.pi*f*delay)*(-1 if inverted else 1)
                            losses.append(sum(float(w@((np.abs(a+gain*b*phase)**2-1)**2)) for a,b in pairs())/row['repeat_scenarios'])
                            masses.append(row['mass'])
                        per_curve.append(independent_risk(losses,masses,settings['receiver_position_risk'],settings.get('receiver_risk_temperature',.15)))
                    group_values.append(independent_risk(per_curve,list(weights.values()),settings['receiver_curve_risk'],settings.get('receiver_curve_risk_temperature',.15)))
                value=independent_risk(group_values,[g.get('mass',len(g['channels'])) for g in groups],settings['receiver_position_risk'],settings.get('receiver_risk_temperature',.15))
                best=min(best,value);count+=1
        self.assertAlmostEqual(selected['objective'],best,places=8)
        self.assertEqual(selected['evaluated_delay_polarity_gain_states'],len(delays)*2*5)
        self.assertEqual(count,selected['evaluated_delay_polarity_gain_states']*selected['linked_crossover_combinations'])
        self.assertTrue(selected['exact_minimum_over_declared_native_grid'])

    def test_gain_candidates_reserve_codec_compensation_and_never_silently_round_baseline(self):
        profile=receiver_profile('AVR-X3700H')
        values=sub_level_candidates(0.,profile,{},6.1)
        self.assertEqual(values[0],-12.)
        self.assertEqual(values[-1],5.5)
        with self.assertRaisesRegex(ValueError,'baseline'):
            sub_level_candidates(.1,profile,{})
        with self.assertRaisesRegex(ValueError,'No declared'):
            sub_level_candidates(0.,profile,{'receiver_sub_level_min_db':6.},6.1)


class SharedObjectiveTests(unittest.TestCase):
    def test_conflicting_erb_curves_choose_one_shared_trim_state(self):
        p,d,s,_=fixture(channels=('FL','FR'),amplitudes={'FL':10**(.2)})
        other=copy.deepcopy(d);other['channels'][0]['taps']*=10**(-.4)
        grids={'Flat':SpectralGrid(p,d),'Reference':SpectralGrid(p,other)}
        equal=balance_curve_groups(grids,[['FL','FR']],s['levels_db'],{'p0':1.},{'Flat':.5,'Reference':.5},high=2500)
        weighted=balance_curve_groups(grids,[['FL','FR']],s['levels_db'],{'p0':1.},{'Flat':.75,'Reference':.25},high=2500)
        self.assertEqual(equal['trim_delta_db'],{'FL':0.,'FR':0.})
        self.assertEqual(weighted['trim_delta_db'],{'FL':-1.5,'FR':1.5})
        self.assertEqual(weighted['groups'][0]['zero_sum_delta_db'],0.)
        self.assertEqual(set(weighted['groups'][0]['per_curve']),{'Flat','Reference'})

    def test_weight_and_temperature_extremes_remain_finite_without_ignoring_a_curve(self):
        self.assertEqual(checked_curve_weights({'receiver_curve_weights':{'Flat':1e308,'Reference':1e308}}),{'Flat':.5,'Reference':.5})
        for value in [0.,float('nan'),-1.]:
            with self.assertRaises(ValueError):checked_curve_weights({'receiver_curve_weights':{'Flat':value,'Reference':1.}})
        result=smooth_risk(np.array([1e100,2e100]),np.array([1.,1.]),1.,1e-200)
        self.assertTrue(np.isfinite(result));self.assertEqual(result,2e100)
        self.assertEqual(smooth_risk(np.array([1.,100.]),np.array([1.,0.]),1.,1e-200),1.)

    def test_joint_controls_keep_holdouts_out_of_every_filter_gain_and_target_reference(self):
        p,_,_,settings=fixture(positions=('p0','p1'))
        settings.update(optimizer='native_calibration',receiver_curve_control_mode='joint',
                        curve_profiles={'Reference':{'bass_db':3}},validation_positions=['p1'],
                        receiver_sub_level_min_db=-.5,receiver_sub_level_max_db=.5,receiver_max_delay_ms=.1)
        a=design_project(p,'XT32',settings)
        for m in p.measurements:
            if m.position=='p1':m.ir=m.ir*4
        b=design_project(p,'XT32',settings)
        self.assertEqual(a['receiver_calibration']['proposal'],b['receiver_calibration']['proposal'])
        for name in ['Flat','Reference']:
            x=a if name=='Flat' else a['curve_designs'][name]
            y=b if name=='Flat' else b['curve_designs'][name]
            for first,second in zip(x['channels'],y['channels']):np.testing.assert_array_equal(first['taps'],second['taps'])
        self.assertEqual(a['receiver_calibration']['curve_target_references_db'],b['receiver_calibration']['curve_target_references_db'])
        self.assertFalse(b['receiver_calibration']['independent_validation']['used_for_design'])
        self.assertGreater(b['receiver_calibration']['independent_validation']['after']['objective'],a['receiver_calibration']['independent_validation']['after']['objective'])
        self.assertTrue(b['curve_designs']['Reference']['shared_receiver_evaluation']['participated_in_shared_control_selection'])
        self.assertLess(b['receiver_calibration']['direct_forward_check']['power_moment_absolute_gap'],1e-8)

    def test_single_curve_coupled_engine_includes_lfe_and_checks_all_retained_phase(self):
        p,_,_,settings=fixture(positions=('p0','p1'))
        settings.update(optimizer='native_calibration',receiver_control_engine='coupled',validation_positions=['p1'],
                        receiver_sub_level_min_db=0.,receiver_sub_level_max_db=0.,receiver_max_delay_ms=.1)
        result=design_project(p,'XT32',settings)['receiver_calibration']
        self.assertEqual(result['control_selection']['curves'],['Flat'])
        self.assertIn('LFE',result['evaluation']['group_power_error'])
        target=p.measurements[-1]
        target.ir=None;target.frequency=np.array([20.,2000.]);target.magnitude_db=np.zeros(2);target.phase_deg=None
        with self.assertRaisesRegex(ValueError,'validation response requires phase'):
            design_project(p,'XT32',settings)

    def test_lfe_gain_can_change_without_fabricating_unobservable_program_timing(self):
        p,_,snapshot,settings=fixture()
        snapshot['speaker_types']={'FL':'large','FR':'large'};snapshot['crossovers']={};snapshot['large_channels']=['FL','FR']
        settings.update(receiver_max_delay_ms=.1,bass_db=0,tilt_db_oct=0)
        design=design_project(p,'XT32',{'bass_db':0,'tilt_db_oct':0})
        for item in design['channels']:
            if item['channel']=='SW1':item['taps']*=.5
        result=calibrate_joint_curves(p,{'Flat':design},settings)
        self.assertEqual(result['search']['sub_level_db'],6.)
        self.assertEqual(result['proposal']['distances_m'],snapshot['distances_m'])
        self.assertEqual(result['proposal']['crossovers_hz'],{})
        self.assertFalse(result['search']['time_integration_observable'])


if __name__=='__main__':unittest.main()
