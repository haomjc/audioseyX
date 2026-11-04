# Acoustic analysis

## Available data

| Input | Magnitude | Group delay | Impulse, ETC, decay, spectrogram |
|---|---|---|---|
| Original IR | Supported | From FFT phase | Supported with quality checks |
| Frequency response with measured phase | Supported | Estimated from supplied phase | Not reconstructed |
| Magnitude only | Supported | Not invented | Not reconstructed |

Plots use relative levels unless reliable absolute calibration is explicitly recorded. Receiver calibration-mode data are not automatically corrected-playback measurements.

## Frequency response and group delay

Group delay is the negative derivative of phase with respect to angular frequency. For IR data, the derivative is calculated on the linear FFT grid before interpolation for display and includes the original measurement start time. Display points more than 60 dB below the response peak are suppressed; phase slopes near deep nulls still require caution.

The minimum-phase component is estimated from the finite IR magnitude spectrum using the real cepstrum. Windowing, finite duration, amplitude floors and band edges affect the estimate. The displayed excess group delay subtracts this estimate and the original impulse peak time. This reference does not identify the peak as the direct arrival and does not shift the measurement.

Background: [REW group-delay documentation](https://www.roomeqwizard.com/help/help/html/graph_groupdelay.html).

## Impulse and ETC

Impulse plots retain the original measurement time. Display decimation retains the largest absolute sample in each segment. ETC uses the analytic-signal envelope and retains its segment peaks. Raw arrays remain unchanged in the saved project.

## Decay and quality thresholds

Analysis starts at the first sample reaching 10% of the original impulse peak amplitude; its original time is recorded. The Schroeder curve is the normalized reverse integral of squared impulse energy. Tail-noise compensation is not applied.

EDT, T20 and T30 fit 0 to -10 dB, -5 to -25 dB and -5 to -35 dB respectively, with a 60 dB slope extrapolation. A value is reported only when the current checks pass:

- at least 10 ms and 20 samples in the fitting interval;
- the endpoint is no later than 75% of the available record;
- the endpoint has at least 10 dB margin over estimated remaining tail noise;
- the linear fit has R-squared of at least 0.98 and a valid negative slope.

The final 10% of samples provides a conservative mean-square tail estimate, which can include late decay. Inadequate fits are left unavailable with a reason.

Octave analysis uses a sixth-order forward Butterworth bandpass and rejects excessively short estimates that may be dominated by filter ringing. Diffuse-field RT60 is not provided for the 125 Hz band. Higher bands still require an appropriate acoustic field; these checks are not ISO certification or a guarantee that all measurement errors have been removed.

In small rooms, bass is generally modal. Inspect frequency-dependent decay rather than treating every low-frequency decay as diffuse-field reverberation time. Definitions and context: [REW RT60 documentation](https://www.roomeqwizard.com/help/help_en-GB/html/graph_rt60.html).

## Low-frequency spectrogram

The 20-500 Hz view uses an approximately 128 ms Hann-window STFT, typically about 7.8 Hz frequency spacing and 16 ms steps. All frames share the whole plot's peak reference, from 0 to -70 dB. Time is relative to the original impulse peak.

Fixed-window analysis trades time resolution for frequency resolution and has boundary effects. This implementation does not provide REW's CWT or Burst Decay modes. Method context: [REW spectrogram documentation](https://www.roomeqwizard.com/help/help_en-GB/html/graph_spectrogram.html).

## Room modes

Mode prediction assumes a rectangular room with rigid boundaries and a stated sound speed. Axial, tangential and oblique modes are listed by frequency and integer order. Openings, absorption, placement and EQ are not modeled. A predicted mode is a geometric hypothesis to compare with measurements.
