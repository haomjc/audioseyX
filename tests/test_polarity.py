import unittest
import numpy as np
from audioseyx.model import Measurement,Project
from audioseyx.polarity import analyze_polarity,early_reference


class PolarityDiagnosticTests(unittest.TestCase):
    def project(self,invert=False,echo_gain=0):
        shape=np.array([.08,.25,1.,-.38,-.22,.1,.04])
        rows=[]
        for channel,delay,gain in [('FL',240,1),('FR',510,-.4 if invert else .4)]:
            for repeat in range(2):
                ir=np.zeros(4096);ir[delay:delay+len(shape)]=gain*shape
                if echo_gain:ir[delay+700:delay+700+len(shape)]+=gain*shape*echo_gain
                rows.append(Measurement(channel,'p0',f'{channel}/{repeat}',48000,ir))
        return Project('artificial polarity fixture',rows,'synthetic')

    def test_whole_response_reversal_is_detected_despite_gain_and_propagation_delay(self):
        report=analyze_polarity(self.project(True),[['FL','FR']])
        self.assertEqual(report['pairs'][0]['classification'],'possible_relative_inversion')
        self.assertFalse(report['terminal_wiring_verified'])

    def test_same_polarity_is_not_reversed_and_original_arrays_are_unchanged(self):
        project=self.project();before=[m.ir.copy() for m in project.measurements]
        report=analyze_polarity(project,[['FL','FR']])
        self.assertEqual(report['pairs'][0]['classification'],'no_consistent_broadband_inversion')
        self.assertFalse(report['raw_ir_modified'])
        for a,b in zip(before,project.measurements):np.testing.assert_array_equal(a,b.ir)

    def test_late_large_reflection_is_not_used_as_the_first_response_reference(self):
        project=self.project(echo_gain=3)
        first=project.measurements[0]
        reference,spread=early_reference(first)
        self.assertLess(reference,300)
        self.assertLess(spread,.1)
        self.assertGreater(int(np.argmax(abs(first.ir))),900)

    def test_centre_without_a_same_model_reference_and_subwoofer_hints_are_reported_separately(self):
        project=self.project()
        for channel,code in [('C','N'),('SW1','R')]:
            project.measurements.append(Measurement(channel,'p0',channel,ir=[0,1,0],metadata={'report':{'Polarity':code}}))
        report=analyze_polarity(project,[['FL','FR']])
        self.assertEqual(report['native_reverse_hint_channels'],['SW1'])
        self.assertIn('C',report['unpaired_channels'])
        self.assertEqual(report['possible_relative_inversions'],[])

    def test_unknown_groups_frequency_only_data_and_subwoofer_comparisons_do_not_get_wiring_verdicts(self):
        with self.assertRaises(ValueError):analyze_polarity(self.project(),[['FL','UNKNOWN']])
        with self.assertRaises(ValueError):analyze_polarity(self.project(),[])
        project=Project('magnitude only',[Measurement(c,'p0',c,frequency=[20,100,1000],magnitude_db=[0,0,0]) for c in ['FL','FR']])
        with self.assertRaisesRegex(ValueError,'IRs'):analyze_polarity(project,[['FL','FR']])


if __name__=='__main__':unittest.main()
