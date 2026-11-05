import base64
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import numpy as np

from audioseyx.app import Workbench, public_design
from audioseyx.calibration_curves import CURVE_SPECS, curve_designs
from audioseyx.coefficient_transport import audit_coefficient_streams
from audioseyx.dsp import design_project
from audioseyx.exporter import export_bundle
from audioseyx.model import Measurement, Project
from audioseyx.native import coefficient_packets
from audioseyx.transfer import prepare_transfer, audit_transfer_plan, transfer_plan_digest, apply_plan
from test_transfer import example, FakeReceiver
from test_receiver_calibration import fixture


def simple_project():
    ir=np.zeros(2048);ir[40]=1.
    return Project('two targets',[Measurement('FL',p,p,48000,ir.copy()) for p in ['p0','p1']],origin='unit_test')


def scalar_curves():
    p,d,t,r=example()
    alternate=copy.deepcopy(d)
    for item in alternate['channels']:item['taps']=item['taps']*2
    d['curve_designs']={'Reference':alternate}
    return p,d,t,r


class CurveDesignTests(unittest.TestCase):
    def test_distinct_targets_keep_the_same_measurements_and_holdouts(self):
        p=simple_project();before=[m.ir.copy() for m in p.measurements]
        settings={'bass_db':0,'tilt_db_oct':0,'validation_positions':['p1'],
                  'curve_profiles':{'Reference':{'bass_db':6,'target_curve':None}}}
        saved=copy.deepcopy(settings)
        design=design_project(p,'MultEQ',settings)
        reference=design['curve_designs']['Reference']
        self.assertFalse(np.allclose(design['channels'][0]['taps'],reference['channels'][0]['taps']))
        self.assertEqual(design['training_positions'],reference['training_positions'])
        self.assertEqual(reference['validation_positions'],['p1'])
        self.assertFalse(reference['independent_validation']['used_for_design'])
        self.assertEqual(settings,saved)
        for a,m in zip(before,p.measurements):np.testing.assert_array_equal(a,m.ir)

    def test_profile_overrides_cannot_change_routing_spatial_weights_or_phase_mode(self):
        for key in ['crossover_hz','receiver_max_delay_ms','position_weights','validation_positions','phase_mode']:
            with self.subTest(key=key),self.assertRaisesRegex(ValueError,'shared'):
                design_project(simple_project(),'MultEQ',{'curve_profiles':{'Reference':{key:0}}})
        with self.assertRaisesRegex(ValueError,'minimum-phase'):
            design_project(simple_project(),'MultEQ',{'phase_mode':'mixed','curve_profiles':{'Reference':{}}})
        with self.assertRaisesRegex(ValueError,'Reference'):
            design_project(simple_project(),'MultEQ',{'curve_profiles':{'Flat':{}}})

    def test_public_report_strips_both_fir_arrays_but_retains_separate_metrics(self):
        design=design_project(simple_project(),'MultEQ',{'curve_profiles':{'Reference':{'bass_db':6}}})
        report=public_design(design)
        self.assertNotIn('taps',report['channels'][0])
        self.assertNotIn('taps',report['curve_designs']['Reference']['channels'][0])
        self.assertIn('metrics',report['curve_designs']['Reference']['channels'][0])
        json.dumps(report,allow_nan=False)

    def test_export_keeps_both_complete_filters_and_does_not_include_stale_files(self):
        p=simple_project()
        dual=design_project(p,'MultEQ',{'curve_profiles':{'Reference':{'bass_db':6}}})
        single=design_project(p,'MultEQ',{})
        with tempfile.TemporaryDirectory() as directory:
            target=Path(directory)/'export'
            archive=export_bundle(p,dual,target)
            with zipfile.ZipFile(archive) as z:
                self.assertIn('FL-filter.wav',z.namelist())
                self.assertIn('curves/Reference/FL-filter.wav',z.namelist())
                self.assertNotEqual(z.read('FL-filter.wav'),z.read('curves/Reference/FL-filter.wav'))
                self.assertEqual(json.loads(z.read('native-candidate.json'))['schema'],'audyssey-native-candidate-2')
                self.assertEqual(sum(n.endswith('measurements.awb.json') for n in z.namelist()),1)
            (target/'unrelated-private.txt').write_text('not a generated export')
            archive=export_bundle(p,single,target)
            with zipfile.ZipFile(archive) as z:
                self.assertNotIn('unrelated-private.txt',z.namelist())
                self.assertFalse(any(n.startswith('curves/') for n in z.namelist()))

    def test_imported_targets_are_independent_persisted_and_invalidate_the_bundle(self):
        with tempfile.TemporaryDirectory() as directory:
            bench=Workbench(directory)
            def upload(name,text):
                return bench.set_curve({'filename':name+'.targetcurve','curve_name':name,
                                        'data':base64.b64encode(text.encode()).decode()})
            upload('Flat','20 3\n1000 0\n20000 -3')
            bench.design={'saved':'candidate'}
            upload('Reference','20 6\n1000 0\n20000 -5')
            self.assertIsNone(bench.design)
            restored=Workbench(directory)
            self.assertEqual(restored.target_curve['level_db'][0],3)
            self.assertEqual(restored.reference_target_curve['level_db'][0],6)
            restored.set_curve({'curve_name':'Reference','reset':True})
            self.assertIsNotNone(restored.target_curve)
            self.assertIsNone(restored.reference_target_curve)

    def test_primary_selects_native_controls_once_and_reference_only_evaluates_them(self):
        from audioseyx.receiver_calibration import search_native_grid
        p,_,_,settings=fixture(positions=('p0','p1'))
        settings.update(optimizer='native_calibration',validation_positions=['p1'],
                        curve_profiles={'Reference':{'bass_db':3}})
        with patch('audioseyx.receiver_calibration.search_native_grid',wraps=search_native_grid) as search:
            design=design_project(p,'XT32',settings)
        self.assertEqual(search.call_count,1)
        reference=design['curve_designs']['Reference']
        fixed=reference['shared_receiver_evaluation']
        self.assertTrue(fixed['available'])
        self.assertFalse(fixed['controls_selected_for_this_curve'])
        self.assertEqual(fixed['proposal'],design['receiver_calibration']['proposal'])
        self.assertFalse(fixed['independent_validation']['used_for_design'])
        self.assertIn('routed_charts',reference)
        self.assertTrue(all(c['metrics']['phase_mode']=='native_receiver_model' for c in reference['routed_charts']))

    def test_reference_holdout_changes_do_not_refit_either_curve_or_shared_controls(self):
        p,_,_,settings=fixture(positions=('p0','p1'))
        settings.update(optimizer='native_calibration',validation_positions=['p1'],
                        curve_profiles={'Reference':{'bass_db':3}})
        a=design_project(p,'XT32',settings)
        for m in p.measurements:
            if m.position=='p1':m.ir=m.ir*4
        b=design_project(p,'XT32',settings)
        self.assertEqual(a['receiver_calibration']['proposal'],b['receiver_calibration']['proposal'])
        for name in ['Flat','Reference']:
            for x,y in zip(curve_designs(a)[name]['channels'],curve_designs(b)[name]['channels']):
                np.testing.assert_array_equal(x['taps'],y['taps'])
        first=a['curve_designs']['Reference']['shared_receiver_evaluation']
        second=b['curve_designs']['Reference']['shared_receiver_evaluation']
        np.testing.assert_allclose(list(first['fixed_training_target_references_db'].values()),
                                   list(second['fixed_training_target_references_db'].values()),rtol=0,atol=1e-12)
        self.assertNotAlmostEqual(first['independent_validation']['evaluation']['objective'],
                                  second['independent_validation']['evaluation']['objective'])


class CurveTransferTests(unittest.TestCase):
    def setUp(self):
        resource=patch('audioseyx.transfer.load_channel_addresses',return_value={
            'FL':{'neq2':101,'eq2':101,'griffin':101},'SW1':{'neq2':102,'eq2':102,'griffin':102}})
        resource.start();self.addCleanup(resource.stop)

    def test_curves_have_distinct_wire_values_and_one_shared_headroom_scale(self):
        for fixed in [False,True]:
            p,d,t,r=scalar_curves()
            r['info']['DType']='Fixed32' if fixed else 'Float32'
            d['settings']['initial_playback_curve']='Reference'
            plan=prepare_transfer(p,d,t,r)
            audit=audit_transfer_plan(plan)
            self.assertEqual(audit['channels_with_distinct_curve_coefficients'],2)
            self.assertEqual(plan['settings']['AudyEqSet'],'Reference')
            self.assertEqual(plan['summary']['initial_curve'],'Reference')
            for channel,adjustment in zip(plan['calibration']['channels'],plan['summary']['adjustments']):
                np.testing.assert_allclose(channel['filter'],np.asarray(channel['filterLV'])*2,rtol=0,atol=0)
                self.assertAlmostEqual(channel['filterLV'][0],adjustment['coefficient_scale'])
                self.assertLessEqual(max(abs(v) for v in channel['filter']),.5+1e-12)
                self.assertAlmostEqual(channel['trimAdjustmentInDbs']-(-6),-20*np.log10(adjustment['coefficient_scale']),places=10)
            self.assertEqual(CURVE_SPECS['Flat'],{'curve_id':0,'calibration_field':'filterLV'})
            self.assertEqual(CURVE_SPECS['Reference'],{'curve_id':1,'calibration_field':'filter'})

    def test_settings_only_uses_one_global_scale_for_both_curves_and_all_outputs(self):
        p,d,_,r=scalar_curves()
        template={'model':'AVR-TEST','eqType':0,'hasGriffinLiteDSP':False,
                  'channels':[{'commandId':c} for c in p.channels],'_source_kind':'native_settings_snapshot'}
        plan=prepare_transfer(p,d,template,r)
        self.assertIsNone(plan['levels'])
        self.assertEqual(len({a['coefficient_scale'] for a in plan['summary']['adjustments']}),1)
        self.assertTrue(all(a['trim_db'] is None for a in plan['summary']['adjustments']))
        self.assertEqual(plan['summary']['coefficient_transport_audit']['streams'],12)

    def test_missing_duplicate_and_mismatched_reference_outputs_are_rejected(self):
        for kind in ['missing','duplicate','tier','project','positions','phase','second_proposal']:
            p,d,t,r=scalar_curves();ref=d['curve_designs']['Reference']
            if kind=='missing':ref['channels'].pop()
            elif kind=='duplicate':ref['channels'].append(copy.deepcopy(ref['channels'][0]))
            elif kind=='tier':ref['tier']='XT'
            elif kind=='project':ref['project_id']='another'
            elif kind=='positions':ref['validation_positions']=['p0']
            elif kind=='phase':ref['settings']['phase_mode']='mixed'
            else:ref['receiver_calibration']={'proposal':'different'}
            with self.subTest(kind=kind),self.assertRaises(ValueError):prepare_transfer(p,d,t,r)

    def test_relabeling_curves_cannot_hide_behind_a_fresh_digest(self):
        plan=prepare_transfer(*scalar_curves())
        changed=copy.deepcopy(plan)
        for c in changed['calibration']['channels']:c['filter'],c['filterLV']=c['filterLV'],c['filter']
        changed['summary']['digest']=transfer_plan_digest(changed)
        with patch('audioseyx.transfer.socket.create_connection') as connect:
            with self.assertRaisesRegex(ValueError,'slot mapping'):apply_plan(changed)
            connect.assert_not_called()
        changed=copy.deepcopy(plan)
        changed['native_manifest'][0]['curves']['0']['curve_name']='Reference'
        changed['summary']['digest']=transfer_plan_digest(changed)
        with self.assertRaisesRegex(ValueError,'slot names'):audit_transfer_plan(changed)

    def test_native_audit_catches_a_curve_that_was_replaced_by_the_other(self):
        plan=prepare_transfer(*scalar_curves())
        manifest=plan['native_manifest']
        packets=[]
        for entry,channel in zip(manifest,plan['calibration']['channels']):
            for curve in [0,1]:
                # MultEQ FL and SW1 vectors are direct, enabling an independent
                # known-value stream fixture without private XT32 resources.
                for rate in [0,1,2]:
                    packets.extend(coefficient_packets(channel['filterLV'],entry['address'],curve,rate))
        with self.assertRaisesRegex(ValueError,'Wire coefficients'):
            audit_coefficient_streams(packets,manifest)

    def test_initial_curve_selection_is_committed_to_review(self):
        p,d,t,r=scalar_curves();plan=prepare_transfer(p,d,t,r)
        changed=copy.deepcopy(plan);changed['settings']['AudyEqSet']='Reference'
        with patch('audioseyx.transfer.socket.create_connection') as connect:
            with self.assertRaisesRegex(ValueError,'changed'):apply_plan(changed)
            connect.assert_not_called()
        changed['summary']['digest']=transfer_plan_digest(changed)
        with self.assertRaisesRegex(ValueError,'Initial curve'):audit_transfer_plan(changed)

    def test_a_design_from_another_project_cannot_be_prepared(self):
        p,d,t,r=scalar_curves();d['project_id']='different measurement project'
        with patch('audioseyx.transfer.socket.create_connection') as connect:
            with self.assertRaisesRegex(ValueError,'another measurement project'):prepare_transfer(p,d,t,r)
            connect.assert_not_called()

    def test_unknown_initial_curve_and_extra_control_commands_fail_even_with_a_fresh_digest(self):
        for kind in ['unknown_curve','extra_control']:
            plan=prepare_transfer(*scalar_curves())
            if kind=='unknown_curve':
                plan['settings']['AudyEqSet']=plan['summary']['initial_curve']='Bypass'
            else:plan['settings']['Power']='On'
            plan['summary']['digest']=transfer_plan_digest(plan)
            with self.subTest(kind=kind),patch('audioseyx.transfer.socket.create_connection') as connect:
                with self.assertRaises(ValueError):apply_plan(plan)
                connect.assert_not_called()

    def test_simulated_transfer_sends_both_targets_without_receiver_control_duplication(self):
        plan=prepare_transfer(*scalar_curves());device=FakeReceiver()
        with patch('audioseyx.transfer.socket.create_connection',return_value=device):apply_plan(plan)
        self.assertEqual(sum(command=='ENTER_AUDY' for command,_ in device.commands),1)
        self.assertEqual(sum(command=='FINZ_COEFS' for command,_ in device.commands),1)
        self.assertEqual(device.commands[-1][0],'EXIT_AUDMD')


if __name__=='__main__':unittest.main()
