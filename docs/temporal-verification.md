# Temporal prediction and playback verification

## Before/after recordings

Import verification measurements on **Verify results**. Matching is by output and actual position. Magnitude comparison retains its common-band shape and measurement-chain rules. Temporal comparison additionally requires original impulse responses for **every** retained repeat; one magnitude-only repeat makes temporal evidence unavailable for that entry.

All original repeats are evaluated individually. The report gives their minimum, median and maximum recorded-energy fractions and all decay-fit results/rejection reasons. It does not arbitrarily pair recordings or average their complex responses into a new room.

The temporal reference is an explicit measurement metadata `temporal_anchor_time_s` when provided, otherwise that original record's broadband peak. Neither is automatically a verified direct arrival. Original sample values and start times remain unchanged. Common relative start/end times are intersected across every before/after repeat, so a shorter after-recording does not appear to have a faster decay just because its tail is missing.

Reported diagnostics include:

- Energy before the reference, as a fraction of energy in the common observed window.
- Energy after 50, 80 and 150 ms, as a fraction of observed post-reference energy. These ratios are independent of an overall gain change. A cutoff beyond the common recording support is unavailable.
- Early/late energy ratios and descriptive EDT/T20/T30 extrapolated slopes with fit quality, tail-noise and truncated-record guards.
- The explicit 20–150 Hz region and octave bands from 31.5 Hz through 16 kHz, including unavailable bands above the sample-rate margin.
- An impulse-only reference through the identical analysis filter and window, plus the known slowest-pole decay time. A measured band slope no longer than 1.5 times that pole's equivalent 60 dB decay time is rejected as insufficiently separable from analysis-filter ringing. This conservative threshold is a diagnostic guard, not proof of a room pole.

The diagnostic bandpass is a causal third-order Butterworth prototype, sixth order overall. It can add substantial ringing at low frequencies. The report is not an ISO-certified clarity/RT measurement, and low-frequency modal behavior must not be interpreted as diffuse-field reverberation time. No noise subtraction is applied. Gain-invariant energy ratios still include recorded noise.

An absolute peak-time change is available only when all retained records declare the same non-empty `timebase_id` and `timing_reference_verified=true`. The text in `timing_reference` by itself is not proof. A peak shift is not necessarily a direct-arrival shift.

Comparable corrected playback also requires both project metadata entries to declare `normal_playback_verified=true` and the same non-empty `measurement_path_id`. Missing declarations leave diagnostics available but explicitly unverified as a comparison of the same playback path. A native calibration-mode after-recording is rejected unless normal playback is explicitly established. Another AVR stock calibration sweep may bypass active correction.

## EQ-stage prediction

The design UI enables **Evaluate the complete EQ-stage impulse response and band decay**. Python clients opt in with `temporal_diagnostics=true`.

Prediction linearly convolves the **complete** original IR with the complete returned FIR. The temporal reference is frozen from the original record and is not moved to a new, stronger filtered peak. Requested mixed-phase design delay is not used to realign the result. All records, including independent validation positions, retain their identity and training/holdout label.

Metrics stop at the original measured support. A longer convolution does not invent an unrecorded room tail. The full convolution sample count is reported. If filter/measurement sample rates differ, that entry is unavailable until a temporal multirate reconstruction model is supplied; an interpolated filter is not silently declared to be the receiver's implementation.

This diagnostic includes **individual-output EQ FIR only**. It does not include crossover routing, speaker distances, trims, main/sub acoustic sums, native coefficient synthesis, or ART support signals. Full native-routing frequency predictions remain a separate report. Energy before the reference can be source energy already present in the original IR; it is not by itself a pre-ringing diagnosis.

The memory budget is checked before full convolution and profile allocation. It refuses an insufficient budget instead of shortening responses or removing repeats. `temporal_memory_mib` defaults to 128 for both the design setting and the verification API request. Pure analysis uses a working copy scaled once per complete IR for numerical stability; original amplitudes are unchanged, and this is not level calibration.

## Reports and repeatability

The design bundle retains `temporal_prediction` in `design-report.json`. Before/after verification has a separate downloadable JSON report, invalidated when the current project changes. The original before/after files remain separate.

Numerical tests cover gain invariance, known faster decay, unknown/verified timebases, a truncated after-recording, unequal repeat counts, magnitude-only data, analysis-filter-only ringing, fixed-reference complete convolution and unavailable multirate reconstruction. These tests do not establish reduced room decay on an actual receiver.
