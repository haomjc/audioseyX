import copy
import threading
import unittest

import numpy as np

from audioseyx.dsp import design_project
from audioseyx.joint_curve_controls import calibrate_joint_curves
from audioseyx.measurement_robustness import repeat_confidence, uncertainty_cases
from audioseyx.native_power_model import (build_native_delay_table, power_scenarios,
                                        direct_power_evaluation, native_delay_grid)
from audioseyx.native_refinement import refine_native_curves, magnitude_sensitivities
from audioseyx.receiver_calibration import evaluate_settings_table, _group_costs
from audioseyx.risk_derivatives import fixed_choice_row_gradient
from audioseyx.spectral_grid import SpectralGrid
from test_coupled_controls import tables_fixture
from test_receiver_calibration import fixture


class RobustMeasurementTests(unittest.TestCase):
    def test_two_repeats_have_no_invented_preference_and_three_keep_positive_floor(self):
        p,d,_,settings=fixture()
        original=copy.deepcopy(p.measurements)
        p.measurements+=copy.deepcopy(original)
        p.measurements[-1].ir*=10
        weights,report=repeat_confidence(SpectralGrid(p,d),{**settings,'receiver_repeat_weighting':'consistency'})
        self.assertTrue(all(not row['preference_available'] for row in report['groups']))
        np.testing.assert_allclose(weights,.5)
        p.measurements+=copy.deepcopy(original)
        weights,report=repeat_confidence(SpectralGrid(p,d),{**settings,'receiver_repeat_weighting':'consistency'})
        bass=next(row for row in report['groups'] if row['channel']=='SW1')
        self.assertLess(bass['weights'][1],bass['weights'][0])
        self.assertAlmostEqual(bass['weights'][1]/bass['weights'][0],.25)
        for row in report['groups']:self.assertAlmostEqual(sum(row['weights']),1.)
        self.assertTrue(np.all(weights>0));self.assertEqual(report['records_discarded'],0)

    def test_all_weighted_repeat_uncertainty_moments_match_explicit_physical_sums(self):
        p,s,profile,grids,groups,options,weights,_=tables_fixture(True)
        p.measurements+=copy.deepcopy(p.measurements[:3])
        p.measurements[-1].ir*=8
        grids={name:SpectralGrid(p,g.design) for name,g in grids.items()}
        options={name:{**v,'receiver_repeat_weighting':'consistency','receiver_main_uncertainty_db':.2,
            'receiver_sub_uncertainty_db':.3,'receiver_timing_uncertainty_ms':.1} for name,v in options.items()}
        self.assertEqual(len(uncertainty_cases(options['Flat'])),27)
        delays=native_delay_grid(options['Flat'],profile)
        table=build_native_delay_table(grids,s,options,groups,s['levels_db'],{'p0':1.},weights,delays,budget=512)
        crossovers={g['id']:g['current_hz'] for g in groups}
        for delay in delays:
            moment=evaluate_settings_table(table,delay,True,options['Flat'],crossovers,sub_gain=.8)
            distances=dict(s['distances_m']);distances['SW1']-=float(delay)*options['Flat']['receiver_delay_speed_m_s']
            levels={**s['levels_db'],'SW1':20*np.log10(.8)}
            proposal=dict(distances_m=distances,levels_db=levels,inverted_outputs=['SW1'])
            direct=direct_power_evaluation(grids,s,options,groups,proposal,{'p0':1.},weights,crossovers)
            self.assertAlmostEqual(moment['objective'],direct['objective'],places=9)
        expected=27*sum(len(g['choices_hz'])*len(g['channels']) for g in groups)*2*2
        self.assertEqual(len(table['rows']),expected)

    def test_uncertainty_is_budgeted_before_allocating_and_bad_radii_fail(self):
        _,s,_,grids,groups,options,weights,_=tables_fixture()
        for settings in options.values():settings.update(receiver_main_uncertainty_db=.3,receiver_sub_uncertainty_db=.3,receiver_timing_uncertainty_ms=.3)
        with self.assertRaisesRegex(ValueError,'memory budget'):
            build_native_delay_table(grids,s,options,groups,s['levels_db'],{'p0':1.},weights,np.linspace(-.01,.01,10001),budget=150)
        for value in [float('nan'),-1,True,6]:
            with self.assertRaises(ValueError):uncertainty_cases({'receiver_timing_uncertainty_ms':value})

    def test_nested_risk_row_gradient_includes_all_levels_and_fixed_crossover_penalties(self):
        _,_,_,_,groups,options,weights,tables=tables_fixture(True)
        from audioseyx.joint_curve_controls import combine_curve_tables
        table=combine_curve_tables(tables,weights)
        rng=np.random.default_rng(773)
        losses=rng.uniform(.02,1.,len(table['rows']))
        chosen={g['id']:g['choices_hz'][-1] for g in groups}
        settings={**options['Flat'],'receiver_crossover_change_penalty':.3}
        value,gradient=fixed_choice_row_gradient(table,losses,settings,chosen)
        direct=_group_costs(table,losses[:,None],settings,False,chosen)[0][0]
        self.assertAlmostEqual(value,direct,places=13)
        self.assertAlmostEqual(float(gradient.sum()),1.)
        for index in range(len(losses)):
            a=losses.copy();b=losses.copy();a[index]+=1e-6;b[index]-=1e-6
            va=_group_costs(table,a[:,None],settings,False,chosen)[0][0]
            vb=_group_costs(table,b[:,None],settings,False,chosen)[0][0]
            self.assertAlmostEqual(gradient[index],(va-vb)/2e-6,places=8)

    def test_full_response_magnitude_sensitivity_matches_independent_filter_perturbation(self):
        _,s,_,grids,groups,options,weights,_=tables_fixture(True)
        proposal=dict(distances_m=s['distances_m'],levels_db=s['levels_db'],inverted_outputs=[])
        chosen={g['id']:g['current_hz'] for g in groups}
        value,_,grad=magnitude_sensitivities(grids,s,groups,options,proposal,{'p0':1.},weights,chosen)
        for name,grid in grids.items():
            for channel in grid.filters:
                original=grid.filters[channel].copy()
                grid.filters[channel]=original*np.exp(1e-6)
                a=direct_power_evaluation(grids,s,options,groups,proposal,{'p0':1.},weights,chosen)['objective']
                grid.filters[channel]=original*np.exp(-1e-6)
                b=direct_power_evaluation(grids,s,options,groups,proposal,{'p0':1.},weights,chosen)['objective']
                grid.filters[channel]=original
                self.assertAlmostEqual(float(grad[name][channel].sum()),(a-b)/2e-6,places=7)
        self.assertTrue(np.isfinite(value))


def refinement_fixture(held=False):
    p,_,s,settings=fixture(positions=('p0','p1') if held else ('p0',))
    # Short reflection interference cannot be solved by native scalar controls.
    for m in p.measurements:
        if not m.channel.startswith('SW'):m.ir[38]=.55 if m.channel=='FL' else .35
    settings.update(optimizer='native_calibration',receiver_control_engine='coupled',
        receiver_eq_refinement=True,receiver_refinement_rounds=2,receiver_max_delay_ms=.1,
        receiver_sub_level_min_db=-.5,receiver_sub_level_max_db=.5,
        receiver_crossover_candidates_hz=[80.],high_hz=1000.,sub_high_hz=400.,
        max_boost_db=3.,receiver_auditory_balance=False,validation_positions=['p1'] if held else [])
    return p,s,settings


class NativeRefinementTests(unittest.TestCase):
    def test_complete_native_pipeline_accepts_only_monotone_feasible_steps(self):
        p,_,settings=refinement_fixture()
        result=design_project(p,'MultEQ',settings)
        calibration=result['receiver_calibration'];report=calibration['eq_refinement']
        scores=[row['objective'] for row in report['trace'] if row['accepted']]
        self.assertTrue(all(b<a for a,b in zip(scores,scores[1:])))
        self.assertLessEqual(report['final_objective'],report['initial_objective'])
        self.assertGreater(report['accepted_EQ_steps'],0)
        self.assertEqual(report['frozen_training_target_references_db'],calibration['curve_target_references_db'])
        self.assertFalse(report['global_minimum_claimed']);self.assertFalse(report['held_out_used_for_selection'])
        self.assertEqual([len(i['taps']) for i in result['channels']],[128,128,512])
        self.assertLess(calibration['direct_forward_check']['power_moment_absolute_gap'],1e-8)

    def test_excluded_seat_cannot_select_EQ_steps_or_native_controls(self):
        p,_,settings=refinement_fixture(True)
        settings.update(curve_profiles={'Reference':{'bass_db':2.,'high_hz':1200.}},receiver_curve_control_mode='joint',receiver_refinement_rounds=1)
        a=design_project(p,'MultEQ',settings)
        for m in p.measurements:
            if m.position=='p1':m.ir*=5
        b=design_project(p,'MultEQ',settings)
        self.assertEqual(a['receiver_calibration']['proposal'],b['receiver_calibration']['proposal'])
        self.assertEqual(a['receiver_calibration']['eq_refinement']['output_FIR_sha256'],b['receiver_calibration']['eq_refinement']['output_FIR_sha256'])
        self.assertGreater(b['receiver_calibration']['independent_validation']['after']['objective'],a['receiver_calibration']['independent_validation']['after']['objective'])
        self.assertFalse(b['receiver_calibration']['independent_validation']['used_for_design'])

    def test_cancelled_refinement_never_mutates_the_seed_design(self):
        p,_,settings=refinement_fixture()
        seed=design_project(p,'MultEQ',{**settings,'optimizer':'independent'})
        before=[i['taps'].copy() for i in seed['channels']]
        cancel=threading.Event();cancel.set()
        with self.assertRaises(InterruptedError):refine_native_curves(p,{'Flat':seed},settings,cancel=cancel)
        for item,original in zip(seed['channels'],before):np.testing.assert_array_equal(item['taps'],original)

    def test_disabled_refinement_preserves_existing_coupled_result(self):
        p,_,settings=refinement_fixture()
        design=design_project(p,'MultEQ',{**settings,'optimizer':'independent'})
        plain=calibrate_joint_curves(p,{'Flat':design},settings)
        result=refine_native_curves(p,{'Flat':design},{**settings,'receiver_eq_refinement':False})
        self.assertEqual(result['proposal'],plain['proposal'])
        self.assertNotIn('eq_refinement',result)


if __name__=='__main__':unittest.main()
