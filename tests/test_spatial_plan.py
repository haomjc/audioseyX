import copy
import hashlib
import json
from pathlib import Path
import tempfile
import time
import threading
import unittest
from unittest.mock import patch

import numpy as np

from audioseyx.app import Workbench
from audioseyx.dsp import design_project
from audioseyx.importers import import_json
from audioseyx.listening_area import resolve_listening_area
from audioseyx.model import Measurement,Project
from audioseyx.saved_controls import project_signature
from audioseyx.spatial_plan import (checked_plan,preset_plan,listening_area,inventory,capture_batches,prepare_design,evaluation_weights)


def fixture(points=8,channels=('FL','FR','SW1'),repeats=1):
    return Project('Spatial synthetic fixture',[Measurement(c,f'p{i}',f'{c}/p{i}',48000,
        np.r_[np.zeros(20),1+i*.01,np.zeros(11)],timing_reference='synthetic_common')
        for i in range(points) for c in channels for _ in range(repeats)],'synthetic')


class SpatialPlanTests(unittest.TestCase):
    def test_eight_and_twenty_positions_keep_all_records_and_seat_priorities(self):
        for count in [8,12,20]:
            p=fixture(count,repeats=2);plan=preset_plan(p,count=count)
            p.metadata.update(spatial_plan=plan,listening_area=listening_area(plan))
            report=inventory(p)
            self.assertTrue(report['complete']);self.assertEqual(report['measured_positions'],count)
            self.assertEqual(report['records'],count*6)
            resolved=resolve_listening_area(p,listening_area(plan))
            for seat in plan['seats']:
                ids={row['id'] for row in plan['positions'] if row['seat']==seat['id']}
                self.assertAlmostEqual(sum(v for k,v in resolved['position_weights'].items() if k in ids),seat['weight'])

    def test_draft_points_do_not_become_measured_positions_or_create_IRs(self):
        p=fixture(1,repeats=2);before=[m.ir.copy() for m in p.measurements]
        plan=preset_plan(p,count=8);p.metadata.update(spatial_plan=plan,listening_area=listening_area(plan))
        report=inventory(p)
        self.assertEqual(report['measured_positions'],1);self.assertEqual(report['planned_positions'],8)
        self.assertEqual(report['pending_training_positions'],[f'p{i}' for i in range(1,8)])
        with self.assertRaisesRegex(ValueError,'explicit measurement plan is incomplete'):
            design_project(p,'MultEQ',{})
        design=design_project(p,'MultEQ',{'allow_partial_listening_area':True})
        self.assertFalse(design['settings']['spatial_coverage']['complete'])
        for a,b in zip(before,p.measurements):np.testing.assert_array_equal(a,b.ir)

    def test_missing_channel_or_repeat_keeps_position_partial(self):
        p=fixture(8,repeats=2);plan=preset_plan(p,count=8,repeats=2)
        p.measurements=[m for i,m in enumerate(p.measurements) if i!=len(p.measurements)-1]
        report=inventory(p,plan)
        row=report['positions'][-1]
        self.assertEqual(row['missing_repeats'],{'SW1':1});self.assertEqual(row['status'],'partial')
        p.measurements.extend(copy.deepcopy(p.measurements[:12]))
        self.assertFalse(inventory(p,plan)['complete'])

    def test_holdout_roles_are_excluded_and_cannot_change_training_filters(self):
        p=fixture();plan=preset_plan(p);plan['positions'][-1]['role']='validation'
        p.metadata.update(spatial_plan=plan,listening_area=listening_area(plan))
        first=design_project(p,'MultEQ',{'bass_db':0,'tilt_db_oct':0})
        self.assertEqual(first['validation_positions'],['p7'])
        for m in p.measurements:
            if m.position=='p7':m.ir*=7
        second=design_project(p,'MultEQ',{'bass_db':0,'tilt_db_oct':0})
        for a,b in zip(first['channels'],second['channels']):np.testing.assert_array_equal(a['taps'],b['taps'])
        self.assertFalse(second['independent_validation']['used_for_design'])
        self.assertNotIn('error',p.summary()['listening_area_coverage'])

    def test_arbitrary_imported_IDs_need_explicit_assignments(self):
        p=fixture(2)
        for m in p.measurements:m.position='audyssey-guid-'+m.position
        plan=preset_plan(p,preset='observed')
        self.assertEqual({r['id'] for r in plan['positions']},{'audyssey-guid-p0','audyssey-guid-p1'})
        checked_plan(plan,p)
        wrong=preset_plan(p,count=8)
        with self.assertRaisesRegex(ValueError,'Assign every retained'):checked_plan(wrong,p)

    def test_validation_weights_preserve_seat_mass_despite_unequal_point_counts(self):
        p=fixture(12);plan=preset_plan(p,count=12)
        held={'p3','p4','p5','p6','p7'}
        for row in plan['positions']:
            if row['id'] in held:row['role']='validation'
        weights=evaluation_weights(plan,held)
        for seat in plan['seats']:
            ids={row['id'] for row in plan['positions'] if row['seat']==seat['id']}
            self.assertAlmostEqual(sum(w for p,w in weights.items() if p in ids),seat['weight'])
        p.metadata.update(spatial_plan=plan,listening_area=listening_area(plan))
        result=design_project(p,'MultEQ',{})
        self.assertEqual(result['settings']['validation_position_weights'],weights)
        actual=result['independent_validation']['channels'][0]['position_weights']
        self.assertEqual(set(actual),set(weights))
        np.testing.assert_allclose([actual[p] for p in weights],list(weights.values()),rtol=1e-14,atol=1e-15)
        equal=design_project(p,'MultEQ',{'listening_area':None})
        self.assertEqual(equal['settings']['validation_position_weights'],{})
        np.testing.assert_allclose(list(equal['independent_validation']['channels'][0]['position_weights'].values()),np.full(len(held),1/len(held)))

    def test_resume_groups_only_missing_valid_output_repeats(self):
        p=fixture(1,repeats=2);p.measurements.pop();p.measurements.pop()
        preview=capture_batches(p,'p0',['FL','FR','SW1'],2,'resume')
        self.assertEqual(preview['batches'],[{'channels':['SW1'],'repeats':2}])
        self.assertEqual(preview['responses_to_capture'],2)
        self.assertTrue(preview['no_hardware_contact'])
        self.assertEqual(capture_batches(p,'p0',['FL','FR'],2,'resume')['responses_to_capture'],0)
        self.assertEqual(capture_batches(p,'p0',['FL','FR'],2,'append')['responses_to_capture'],4)
        before=preview['preview_sha256'];p.measurements[0].ir*=2
        self.assertNotEqual(before,capture_batches(p,'p0',['FL','FR','SW1'],2,'resume')['preview_sha256'])

    def test_unsafe_sub_responses_do_not_satisfy_resume_coverage(self):
        p=fixture(1);p.origin='native_avr';p.measurements[-1].metadata={'receiver_raw_channel_id':'SWMIX1'}
        self.assertEqual(capture_batches(p,'p0',['SW1'],1,'resume')['responses_to_capture'],1)
        report=inventory(p,preset_plan(p,preset='single_area',count=1))
        self.assertEqual(report['unsafe_records'],1);self.assertFalse(report['complete'])

    def test_plan_validation_prevents_ambiguous_roles_duplicates_and_excess_budget(self):
        p=fixture();base=preset_plan(p)
        for mutate in [lambda p:p['positions'].append(copy.deepcopy(p['positions'][0])),
                       lambda p:p['positions'][0].update(role='guess'),
                       lambda p:p['positions'][0].update(repeats=True),
                       lambda p:p['positions'][0].update(seat='unassigned'),
                       lambda p:p.update(expected_channels=['FL'])]:
            invalid=copy.deepcopy(base);mutate(invalid)
            with self.assertRaises(ValueError):checked_plan(invalid,p)
        with self.assertRaisesRegex(ValueError,'1024-response'):
            preset_plan(fixture(1),count=256,repeats=2)

    def test_legacy_dataset_signatures_remain_compatible_and_plan_edits_change_them(self):
        p=fixture(1);before=project_signature(p)
        from audioseyx.receiver_constraints import snapshot_fingerprint
        header={'model':p.model,'tier':p.tier,'groups':None,'listening_area':None,'snapshot':snapshot_fingerprint({})}
        digest=hashlib.sha256();digest.update(json.dumps(header,sort_keys=True,allow_nan=False).encode())
        for m in p.measurements:
            digest.update(json.dumps([m.channel,m.position,m.sample_rate,m.start_time_s,m.timing_reference],allow_nan=False).encode())
            for value in [m.ir,m.frequency,m.magnitude_db,m.phase_deg]:
                if value is None:digest.update(b'none')
                else:
                    array=np.asarray(value,dtype='<f8');digest.update(str(array.shape).encode());digest.update(array.tobytes())
        self.assertEqual(before,digest.hexdigest())
        p.metadata['spatial_plan']=preset_plan(p,count=8)
        first=project_signature(p);self.assertNotEqual(before,first)
        p.metadata['spatial_plan']['positions'][-1]['repeats']=2
        self.assertNotEqual(first,project_signature(p))

    def test_ADY_eight_positions_are_retained_without_fabricated_seat_mapping(self):
        data={'targetModelName':'AVR-X3700H','detectedChannels':[{'commandId':c,'responseData':{str(i):[0,1+i*.01,0] for i in range(8)}} for c in ['FL','FR','SW1']]}
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'test.ady';path.write_text(json.dumps(data))
            p=import_json(path)
        self.assertEqual(len(p.measurements),24)
        self.assertEqual({m.position for m in p.measurements},{f'p{i}' for i in range(8)})
        self.assertNotIn('spatial_plan',p.metadata)

    def test_eight_point_native_controls_retain_seat_balanced_validation(self):
        from test_receiver_calibration import fixture as native_fixture
        p,_,_,settings=native_fixture(positions=tuple(f'p{i}' for i in range(8)))
        plan=preset_plan(p,count=8)
        for row in plan['positions']:
            if row['id'] in {'p3','p4','p5','p6','p7'}:row['role']='validation'
        p.metadata.update(spatial_plan=plan,listening_area=listening_area(plan))
        settings.update(optimizer='native_calibration',receiver_control_engine='coupled',receiver_max_delay_ms=.1,
            receiver_sub_level_min_db=0.,receiver_sub_level_max_db=0.,receiver_crossover_candidates_hz=[80.])
        design=design_project(p,'MultEQ',settings)
        report=design['receiver_calibration']['independent_validation']
        expected=evaluation_weights(plan,{'p3','p4','p5','p6','p7'})
        np.testing.assert_allclose([report['position_weights'][p] for p in expected],list(expected.values()),rtol=1e-14)
        self.assertFalse(report['used_for_design']);self.assertLess(report['power_moment_absolute_gap'],1e-8)


def wait(bench):
    deadline=time.monotonic()+10
    while bench.job['status']=='running' and time.monotonic()<deadline:time.sleep(.01)
    if bench.job['status']=='running':raise AssertionError('Mock capture did not finish')


class CapturePlanningTests(unittest.TestCase):
    def test_capture_stays_running_until_project_and_receipt_are_committed(self):
        from test_native_measure import status
        from audioseyx.archive import save_archive
        with tempfile.TemporaryDirectory() as directory:
            bench=Workbench(Path(directory));p=fixture(1,channels=('FL',));p.origin='native_avr';p.metadata['receiver_host']='127.0.0.1';bench.set_project(p);bench.receiver=status()
            entered=threading.Event();release=threading.Event()
            def capture(receiver,position,repeats,cancel,progress,checkpoint,channels):checkpoint(Measurement('FL','p0','new',ir=[0,1,0]))
            def storage(project,path):
                if Path(path).parent.name=='projects':entered.set();release.wait(3)
                return save_archive(project,path)
            with patch('audioseyx.app.ReceiverClient') as client,patch('audioseyx.app.capture_position',side_effect=capture),patch('audioseyx.app.save_archive',side_effect=storage):
                client.return_value.read_info.return_value=status()
                bench.start_native_measurement({'ready':True,'channels':['FL'],'position':1})
                try:
                    self.assertTrue(entered.wait(3));self.assertEqual(bench.job['status'],'running');self.assertIs(bench.project,p)
                finally:release.set()
                wait(bench)
            self.assertEqual(bench.job['status'],'complete')
            receipt=json.loads(next((Path(directory)/'captures').glob('*.json')).read_text())
            self.assertEqual(receipt['status'],'complete');self.assertEqual(receipt['responses_saved'],1)

    def test_final_save_failure_keeps_previous_project_and_unique_raw_checkpoint(self):
        from test_native_measure import status
        from audioseyx.archive import save_archive
        with tempfile.TemporaryDirectory() as directory:
            bench=Workbench(Path(directory));p=fixture(1,channels=('FL',));p.origin='native_avr';p.metadata['receiver_host']='127.0.0.1';bench.set_project(p);bench.receiver=status()
            def capture(receiver,position,repeats,cancel,progress,checkpoint,channels):checkpoint(Measurement('FL','p0','new',ir=[0,1,0]))
            def storage(project,path):
                if Path(path).parent.name=='projects':raise OSError('Synthetic disk failure')
                return save_archive(project,path)
            with patch('audioseyx.app.ReceiverClient') as client,patch('audioseyx.app.capture_position',side_effect=capture),patch('audioseyx.app.save_archive',side_effect=storage):
                client.return_value.read_info.return_value=status()
                bench.start_native_measurement({'ready':True,'channels':['FL'],'position':1});wait(bench)
            self.assertEqual(bench.job['status'],'failed');self.assertIs(bench.project,p);self.assertEqual(len(p.measurements),1)
            self.assertEqual(len(list((Path(directory)/'captures').glob('*.awb'))),1)
            receipt=json.loads(next((Path(directory)/'captures').glob('*.json')).read_text())
            self.assertEqual(receipt['status'],'save_failed')

    def test_plan_save_and_preview_never_connect_and_do_not_modify_raw_records(self):
        with tempfile.TemporaryDirectory() as directory:
            bench=Workbench(Path(directory));p=fixture(1);bench.set_project(p)
            original=p.measurements[0].ir.copy()
            with patch('audioseyx.app.ReceiverClient',side_effect=AssertionError('No AVR access')):
                result=bench.set_spatial_plan({'preset':'three_seat','count':8})
                preview=bench.preview_native_measurement({'position':8,'mode':'resume','repeats':1})
            self.assertEqual(result['spatial_inventory']['measured_positions'],1)
            self.assertEqual(preview['responses_to_capture'],3)
            np.testing.assert_array_equal(p.measurements[0].ir,original)
            self.assertNotIn('spatial_plan',p.metadata)

    def test_complete_resume_and_changed_preview_stop_before_receiver_refresh(self):
        from test_native_measure import status
        with tempfile.TemporaryDirectory() as directory:
            bench=Workbench(Path(directory));p=fixture(1);p.origin='native_avr';p.metadata['receiver_host']='127.0.0.1';bench.set_project(p);bench.receiver=status()
            with patch('audioseyx.app.ReceiverClient',side_effect=AssertionError('No receiver refresh')):
                with self.assertRaisesRegex(ValueError,'already meets'):
                    bench.start_native_measurement({'ready':True,'mode':'resume','channels':['FL'],'position':1})
                with self.assertRaisesRegex(ValueError,'preview changed'):
                    bench.start_native_measurement({'ready':True,'mode':'append','channels':['FL'],'position':1,'preview_sha256':'stale'})

    def test_partial_run_is_retained_and_resume_measures_only_missing_output(self):
        from test_native_measure import status
        with tempfile.TemporaryDirectory() as directory:
            bench=Workbench(Path(directory));p=fixture(1,channels=('FL',));p.origin='native_avr';p.metadata['receiver_host']='127.0.0.1';bench.set_project(p);bench.receiver=status()
            calls=[]
            def capture(receiver,position,repeats,cancel,progress,checkpoint,channels):
                calls.append((channels,repeats));checkpoint(Measurement('FL','p0','partial',ir=[0,1,0]))
                raise ValueError('Synthetic interrupted run')
            with patch('audioseyx.app.ReceiverClient') as client,patch('audioseyx.app.capture_position',side_effect=capture):
                client.return_value.read_info.return_value=status()
                bench.start_native_measurement({'ready':True,'mode':'append','channels':['FL','SW1'],'position':8});wait(bench)
            self.assertEqual(bench.job['status'],'failed')
            self.assertEqual(len(p.measurements),1)
            self.assertEqual(bench.project.metadata['capture_runs'][-1]['status'],'partial')
            self.assertEqual(bench.project.measurements[-1].position,'p7')
            self.assertTrue(bench.project.measurements[-1].metadata['capture_run_id'])
            preview=bench.preview_native_measurement({'position':8,'channels':['FL','SW1'],'mode':'resume'})
            self.assertEqual(preview['batches'],[{'channels':['SW1'],'repeats':1}])
            saved=list((Path(directory)/'captures').glob('*.awb'));self.assertEqual(len(saved),1)
            def finish(receiver,position,repeats,cancel,progress,checkpoint,channels):
                calls.append((channels,repeats));checkpoint(Measurement('SW1','p0','resumed',ir=[0,.5,0]))
            with patch('audioseyx.app.ReceiverClient') as client,patch('audioseyx.app.capture_position',side_effect=finish):
                client.return_value.read_info.return_value=status()
                bench.start_native_measurement({'ready':True,'mode':'resume','channels':['FL','SW1'],'position':8,'preview_sha256':preview['preview_sha256']});wait(bench)
            self.assertEqual(bench.job['status'],'complete');self.assertEqual(calls[-1],(['SW1'],1))
            self.assertEqual(len(list((Path(directory)/'captures').glob('*.awb'))),2)
            self.assertTrue(saved[0].exists());self.assertEqual(len(bench.project.metadata['capture_runs']),2)


if __name__=='__main__':unittest.main()
