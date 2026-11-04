# Filter design

## Capability budgets

These budgets describe the observed research formats. They are not manufacturer certification or proof of hardware execution.

| Tier | Main design | Subwoofer design | Native candidate format |
|---|---|---|---|
| MultEQ | 128 taps, 48 kHz | 512 taps, 6 kHz | Direct observed vector |
| XT | 512 taps, 48 kHz | 512 taps, 6 kHz | Direct observed vector |
| XT32 | 16,321 taps, 48 kHz | 16,055 taps, 48 kHz | 1,024 / 704 values; requires optional local tables |

Filter WAVs contain the designed time-domain taps. A native encoded vector is a different representation; a WAV must not be uploaded as if it were that vector.

## Targets and boost protection

Use bass lift and high-frequency tilt, or import a two-column `frequency_Hz gain_dB` curve. A custom target is normalized at 1 kHz and replaces the simple lift and tilt controls.

The design uses robust magnitude statistics across positions, logarithmic smoothing, maximum boost/cut limits and separate speaker/subwoofer upper limits. Spatial variability reduces boost trust. Deep nulls and substantial roll-off also reduce boost through a conservative depth gate; this does not establish the physical cause of a dip.

Correction is limited to the common measured band. Data outside a frequency-only file are not treated as measured extension. If the usual reference band is missing, the effective design band is used for normalization and the report records the change.

## Minimum and limited mixed phase

Minimum phase is the default. The experimental limited mixed-phase design requires original impulses and confirmed timing. Trust is reduced where positions disagree or the response has deep nulls. The design delay is limited by the selected tier's capacity.

Mixed-phase output remains export-only. It has not been validated against native receiver reconstruction and is not enabled for real transfer.

## What a prediction means

The report evaluates the actual truncated and faded FIR taps, not just an ideal frequency-domain correction. It includes target error, lost tail energy and numerical gain bounds. Prediction is evaluated against the imported data and is not a new acoustic measurement.

The current workflow redesigns the final correction. It does not automatically append a new stage after the stock Audyssey filter or retain its out-of-band correction. Incremental enhancement requires the original coefficients and normal-playback measurements and remains separate work.

## Shared subwoofer

The independent designer can suggest common relative delay and polarity for one independent subwoofer across matching positions. These suggestions are not automatically written to receiver settings.

The experimental [full complex joint designer](joint-design.md) includes all program speakers, shared independently controllable subwoofers and LFE in one full-FIR objective, with optional full re-solving across crossover candidates. Receiver paths and crossover models remain assumptions to verify. Arbitrary ART-style main-to-main support paths cannot be added by this calibration interface.
