import copy
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
import numpy as np

from audioseyx.app import Workbench
from audioseyx.model import Measurement,Project
from audioseyx.saved_controls import make_saved_controls,load_saved_controls,project_signature


def project():
    x=np.zeros(1024);x[40]=1.
    return Project('original',[Measurement('FL','p0','original',48000,x)],origin='unit_test',
                   metadata={'timing_confirmed':'original provenance'})


def wait(bench):
    deadline=time.monotonic()+10
    while bench.job['status']=='running' and time.monotonic()<deadline:time.sleep(.01)
    if bench.job['status']=='running':raise AssertionError('The processing job did not finish')


class SavedControlsTests(unittest.TestCase):
    def test_controls_are_bound_to_original_data_and_cannot_restore_consent_secrets_or_external_paths(self):
        p=project()
        controls={'tier':'MultEQ','bass_db':4,'timing_confirmed':True,'relative_levels_confirmed':True,
                  'allow_partial_listening_area':True,'apply_receiver_adjustments':True,
                  'receiver_distance_basis':'physical_rescale','receiver_snapshot':{'private':'settings'},
                  'native_synthesis_path':'C:/private/model.npz','token':'do not persist',
                  'curve_profiles':{'Reference':{'bass_db':6,'target_curve':{'frequency':[20,1000],'level_db':[6,0]}}}}
        before=copy.deepcopy(controls);record=make_saved_controls(p,controls)
        self.assertEqual(controls,before)
        saved=record['settings']
        for name in ['receiver_snapshot','native_synthesis_path','token']:self.assertNotIn(name,saved)
        for name in ['timing_confirmed','relative_levels_confirmed','allow_partial_listening_area','apply_receiver_adjustments']:self.assertFalse(saved[name])
        self.assertEqual(saved['receiver_distance_basis'],'preserve')
        self.assertNotIn('target_curve',saved['curve_profiles']['Reference'])
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'saved.json';path.write_text(json.dumps(record))
            self.assertEqual(load_saved_controls(path,p)['bass_db'],4)
            p.measurements[0].ir[40]=2.
            self.assertIsNone(load_saved_controls(path,p))

    def test_invalid_saved_files_do_not_become_active_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'saved.json'
            for raw in ['{"schema":"unknown"}','{"settings":{"gain":NaN}}']:
                path.write_text(raw)
                with self.assertRaises(ValueError):load_saved_controls(path,project())

    def test_successful_processing_keeps_measurement_metadata_and_restores_only_controls_on_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            bench=Workbench(directory);p=project();before=copy.deepcopy(p.metadata);signature=project_signature(p)
            bench.set_project(p)
            bench.start_design({'tier':'MultEQ','bass_db':0,'tilt_db_oct':0,'timing_confirmed':True})
            wait(bench);self.assertEqual(bench.job['status'],'complete',bench.job)
            self.assertEqual(p.metadata,before);self.assertEqual(project_signature(p),signature)
            restored=Workbench(directory)
            self.assertTrue(restored.saved_controls_restored)
            self.assertFalse(restored.saved_design_settings['timing_confirmed'])
            self.assertIsNone(restored.design);self.assertIsNone(restored.receiver);self.assertEqual(restored.job['status'],'idle')

    def test_cancelled_or_failed_replacement_keeps_the_last_result_and_saved_defaults(self):
        with tempfile.TemporaryDirectory() as directory:
            bench=Workbench(directory);bench.set_project(project())
            bench.start_design({'tier':'MultEQ','bass_db':0,'tilt_db_oct':0});wait(bench)
            self.assertEqual(bench.job['status'],'complete',bench.job)
            previous=bench.design;archive=bench.export_path
            path=Path(directory)/'last-design-settings.json';saved=path.read_bytes()
            def cancel_during_export(*args,**kwargs):
                bench.design_cancel.set();return archive
            with patch('audioseyx.app.export_bundle',side_effect=cancel_during_export):
                bench.start_design({'tier':'MultEQ','bass_db':5});wait(bench)
            self.assertEqual(bench.job['status'],'cancelled')
            self.assertIs(bench.design,previous);self.assertEqual(path.read_bytes(),saved)
            self.assertTrue(bench.state()['showing_previous_design'])
            with patch('audioseyx.app.design_project',side_effect=ValueError('deliberate failed replacement')):
                bench.start_design({'tier':'MultEQ','bass_db':6});wait(bench)
            self.assertEqual(bench.job['status'],'failed')
            self.assertIs(bench.design,previous);self.assertEqual(path.read_bytes(),saved)
            self.assertEqual(list(Path(directory).glob('controls-*.tmp.json')),[])


if __name__=='__main__':unittest.main()
