import copy
import json
from pathlib import Path
import tempfile
import unittest

from audioseyx.design_history import write_receipt,read_receipt,history,restore_recipe,verified_archive
from audioseyx.dsp import design_project
from test_saved_controls import project


class DesignHistoryTests(unittest.TestCase):
    def fixture(self,home):
        p=project();design=design_project(p,'MultEQ',{})
        archive=home/'exports'/'test.zip';archive.parent.mkdir();archive.write_bytes(b'local export fixture')
        settings=dict(timing_confirmed=True,relative_levels_confirmed=True,apply_receiver_adjustments=True,
            receiver_eq_refinement=True,receiver_repeat_weighting='consistency',receiver_main_uncertainty_db=.2,
            receiver_snapshot={'secret':'never preserve in recipe'},token='never preserve')
        receipt=write_receipt(home,p,settings,'complete',design=design,archive=archive)
        return p,design,archive,receipt

    def test_recipe_checks_dataset_export_integrity_and_resets_all_consents(self):
        with tempfile.TemporaryDirectory() as directory:
            home=Path(directory);p,_,archive,receipt=self.fixture(home)
            restored=restore_recipe(home,p,receipt['id'])
            self.assertTrue(restored['settings']['receiver_eq_refinement'])
            for key in ['timing_confirmed','relative_levels_confirmed','apply_receiver_adjustments']:
                self.assertFalse(restored['settings'][key])
            self.assertNotIn('token',restored['settings']);self.assertNotIn('receiver_snapshot',restored['settings'])
            self.assertEqual(verified_archive(home,receipt['id']),archive)
            foreign=copy.deepcopy(p);foreign.measurements[0].ir[40]*=2
            with self.assertRaisesRegex(ValueError,'different measurements'):restore_recipe(home,foreign,receipt['id'])
            archive.write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError,'changed'):restore_recipe(home,p,receipt['id'])

    def test_receipts_survive_failures_and_detect_tampering(self):
        with tempfile.TemporaryDirectory() as directory:
            home=Path(directory);p,_,_,complete=self.fixture(home)
            write_receipt(home,p,{},'cancelled',error='cancelled locally')
            failed=write_receipt(home,p,{},'failed',error='constraint failed')
            records=history(home,p)
            self.assertEqual(records['total_runs'],3)
            self.assertEqual(records['runs'][0]['status'],'failed')
            with self.assertRaisesRegex(ValueError,'completed design'):restore_recipe(home,p,failed['id'])
            path=home/'design-history'/(complete['id']+'.json')
            body=json.loads(path.read_text());body['status']='failed';path.write_text(json.dumps(body))
            with self.assertRaisesRegex(ValueError,'integrity'):read_receipt(home,complete['id'])
            self.assertEqual(history(home,p)['invalid_receipts'],1)
            self.assertEqual(list((home/'design-history').glob('*.tmp')),[])

    def test_path_traversal_and_foreign_export_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            home=Path(directory);p,design,_,_=self.fixture(home)
            with self.assertRaises(ValueError):read_receipt(home,'../outside')
            outside=home/'outside.zip';outside.write_bytes(b'outside')
            with self.assertRaisesRegex(ValueError,'local export'):write_receipt(home,p,{},'complete',design=design,archive=outside)

    def test_imported_target_must_match_before_restoring_recipe(self):
        with tempfile.TemporaryDirectory() as directory:
            home=Path(directory);p,design,archive,_=self.fixture(home)
            target={'frequency':[20.,1000.,20000.],'level_db':[3.,0.,-2.]}
            design['settings']['target_curve']=target
            receipt=write_receipt(home,p,{},'complete',design=design,archive=archive)
            with self.assertRaisesRegex(ValueError,'active imported target'):restore_recipe(home,p,receipt['id'])
            restored=restore_recipe(home,p,receipt['id'],{'Flat':target})
            self.assertFalse(restored['automatic_calculation_started'])


if __name__=='__main__':unittest.main()
