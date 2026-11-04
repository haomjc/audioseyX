import copy
import unittest
import numpy as np
from audioseyx.listening_area import resolve_listening_area,three_seat_row_plan,weighted_median
from audioseyx.model import Measurement,Project
from audioseyx.dsp import design_project


def fixture(positions):
    return Project('synthetic seats',[Measurement('FL',p,p,ir=[0,1,0,0]) for p in positions],'synthetic')


class ListeningAreaTests(unittest.TestCase):
    def test_additional_points_do_not_multiply_a_seats_total_priority(self):
        plan=three_seat_row_plan();project=fixture(['p0','p1','p2','p3','p4','p5','p6'])
        result=resolve_listening_area(project,plan)
        self.assertTrue(result['required_seats_measured'])
        for seat in plan['seats']:
            self.assertAlmostEqual(sum(result['position_weights'][p] for p in seat['positions']),seat['weight'])
        project.measurements.extend(copy.deepcopy(project.measurements[:3]))
        self.assertEqual(result,resolve_listening_area(project,plan))

    def test_missing_seats_are_not_synthesized_or_silently_finalized(self):
        project=fixture(['p0']);plan=three_seat_row_plan()
        result=resolve_listening_area(project,plan)
        self.assertEqual(result['missing_training_seats'],['left','right'])
        self.assertEqual(result['position_weights'],{'p0':1.})
        with self.assertRaisesRegex(ValueError,'missing measured seats'):
            design_project(project,'XT',{'listening_area':plan})
        result=design_project(project,'XT',{'listening_area':plan,'allow_partial_listening_area':True})
        self.assertTrue(result['settings']['listening_area_resolution']['provisional_normalization'])

    def test_unknown_or_multiply_assigned_positions_are_rejected(self):
        plan=three_seat_row_plan()
        with self.assertRaisesRegex(ValueError,'not assigned'):resolve_listening_area(fixture(['p9']),plan)
        plan['seats'][1]['positions']['p0']=1
        with self.assertRaisesRegex(ValueError,'exactly one'):resolve_listening_area(fixture(['p0']),plan)

    def test_weighted_median_uniform_ties_match_the_regular_median(self):
        rng=np.random.default_rng(98)
        for n in [2,3,4,7]:
            values=rng.normal(size=(n,20))
            np.testing.assert_allclose(weighted_median(values,np.ones(n)),np.median(values,axis=0))

    def test_position_balanced_native_candidate_is_invariant_to_duplicate_scans(self):
        project=fixture(['p0','p1']);project.measurements[1].ir*=2
        settings={'position_weights':{'p0':.5,'p1':.5},'bass_db':0,'tilt_db_oct':0}
        first=design_project(project,'XT32',settings)
        project.measurements.extend(copy.deepcopy([project.measurements[0]])*3)
        second=design_project(project,'XT32',settings)
        np.testing.assert_allclose(first['channels'][0]['taps'],second['channels'][0]['taps'],atol=1e-12)

    def test_independent_holdout_does_not_refit_filters_or_renormalize_its_error(self):
        project=fixture(['p0','p1']);settings={'validation_positions':['p1'],'bass_db':0,'tilt_db_oct':0}
        first=design_project(project,'XT32',settings)
        project.measurements[1].ir*=4
        second=design_project(project,'XT32',settings)
        np.testing.assert_array_equal(first['channels'][0]['taps'],second['channels'][0]['taps'])
        result=second['independent_validation']['channels'][0]
        self.assertFalse(result['used_for_design']);self.assertFalse(result['renormalized_after_design'])
        self.assertGreater(result['rms_after_db'],11.9)


if __name__=='__main__':unittest.main()
