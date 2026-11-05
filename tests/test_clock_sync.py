import unittest
import numpy as np
from scipy import signal
from audioseyx.clock_sync import pilot_signal, estimate_clock, correct_recording


def original_recording(ppm=350., invert=False, nonlinear=0., noise=1e-7):
    rate=8000;duration=.04;times=np.array([.4,1.3,2.2,3.1])
    time=np.arange(32000)/rate
    reference=(time-.035)/(1+ppm*1e-6)+nonlinear*time**2
    value=np.zeros(len(time))
    for marker in times:
        local=reference-marker
        selected=(local>=0)&(local<=(round(rate*duration)-1)/rate)
        window=.5-.5*np.cos(2*np.pi*local[selected]*rate/(round(rate*duration)-1))
        value[selected]+=signal.chirp(local[selected],rate*.06,duration,rate*.3,method='logarithmic')*window
    if invert:value*=-.7
    value+=np.random.default_rng(9).normal(0,noise,len(value))
    return value,pilot_signal(rate,duration),rate,times


class RawClockTests(unittest.TestCase):
    def test_exact_digital_silence_does_not_create_false_normalized_pilot_peaks(self):
        value,pilot,rate,times=original_recording(noise=0)
        result=estimate_clock(value,pilot,rate,times,latency_guess_s=.035,search_radius_s=.08)
        self.assertAlmostEqual(result['estimated_drift_ppm'],350,delta=1.5)
    def test_known_drift_and_inverted_polarity_are_recovered(self):
        for ppm,invert in [(350.,False),(-220.,False),(180.,True)]:
            value,pilot,rate,times=original_recording(ppm,invert)
            result=estimate_clock(value,pilot,rate,times,latency_guess_s=.035,search_radius_s=.08)
            self.assertAlmostEqual(result['estimated_drift_ppm'],ppm,delta=1.5)
            self.assertLess(result['maximum_fit_residual_samples'],.05)
            self.assertFalse(result['direct_arrival_identified'])

    def test_complete_resampling_preserves_the_recording_and_time_anchor(self):
        value,pilot,rate,times=original_recording();saved=value.copy()
        analysis=estimate_clock(value,pilot,rate,times,latency_guess_s=.035,search_radius_s=.08)
        corrected,info=correct_recording(value,analysis)
        after=estimate_clock(corrected,pilot,rate,times,latency_guess_s=.035,search_radius_s=.08)
        self.assertAlmostEqual(after['estimated_drift_ppm'],0,delta=1.5)
        old_anchor=analysis['pilots'][0]['observed_time_s']
        new_anchor=after['pilots'][0]['observed_time_s']+info['output_start_time_s']
        # The affine sample coordinate is preserved exactly. Re-detecting a
        # formerly stretched pilot can move its correlation maximum slightly
        # because its waveform now matches the unstretched reference.
        mapped_anchor=old_anchor*info['resampling_ratio']+info['output_start_time_s']
        self.assertAlmostEqual(old_anchor,mapped_anchor,places=12)
        self.assertAlmostEqual(old_anchor,new_anchor,delta=1/rate/10)
        np.testing.assert_array_equal(value,saved)
        self.assertFalse(info['deconvolution_performed'])

    def test_ir_or_missing_pilots_cannot_be_used_to_invent_clock_drift(self):
        impulse=np.zeros(32000);impulse[100]=1
        with self.assertRaises(ValueError):
            estimate_clock(impulse,pilot_signal(8000),8000,[.4,1.3,2.2],search_radius_s=.08)
        value,pilot,rate,times=original_recording()
        with self.assertRaises(ValueError):estimate_clock(value,pilot,rate,times[:2])
        analysis=estimate_clock(value,pilot,rate,times,latency_guess_s=.035,search_radius_s=.08)
        changed=value.copy();changed[5]+=.1
        with self.assertRaisesRegex(ValueError,'differs'):correct_recording(changed,analysis)

    def test_non_affine_marker_timing_is_rejected(self):
        value,pilot,rate,times=original_recording(nonlinear=.0005)
        with self.assertRaisesRegex(ValueError,'stable affine'):
            estimate_clock(value,pilot,rate,times,latency_guess_s=.035,search_radius_s=.08,
                           maximum_fit_residual_samples=.1)


if __name__=='__main__':unittest.main()
