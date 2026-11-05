# Independent Flat and Reference targets

audioseyX can design two complete static minimum-phase EQ sets for the receiver's existing **Flat** and **Reference** slots. The names identify calibration slots; they do not require either uploaded target to be flat or to reproduce an Audyssey factory curve. Slot mapping and actual playback remain hardware-unverified.

## Design and compare

Enable **Design separate Flat and Reference calibration targets** on Design. The ordinary controls and imported target define Flat. Reference has its own target import, bass lift/tilt, correction bands, smoothing and boost/cut limits. Imported targets are normalized at 1 kHz. The imported target files persist separately; changing either invalidates the design and transfer review.

Each curve retains the complete selected tier's FIR budget. Training positions, independent validation positions, recorded repeats and timing references are shared. Two targets are calculated independently; copying a filter does not count as optimizing a second target. Mixed-phase and full-complex reference workflows remain separate and cannot be silently converted to minimum phase by enabling this feature.

Use **Select calibration curve** on Results to compare targets and actual truncated-FIR predictions. Temporal diagnostics and held-out results belong to the selected curve. Different targets need not have equal RMS errors, and a lower error does not by itself establish a preferable listening result.

These are static alternatives. They do not provide Dynamic EQ, automatic playback-level compensation or Dynamic Volume. The existing native transfer explicitly disables Dynamic EQ and Dynamic Volume.

## Common receiver controls

Speaker distances, channel trims, crossovers, sub level and filter polarity are common to both curves. The UI's [coupled native engine](coupled-native.md) can select controls from both weighted targets, including LFE, or prioritize Flat and evaluate Reference at that state. Main ERB trims and complete FIRs remain fixed during the gain/delay/crossover search; this is not a global joint optimum across all parameters. Python clients without the new engine/mode settings retain primary-target compatibility.

The Reference evaluation retains all repeats, routing-model alternatives and validation positions. Held-out data never select controls or refit either curve. If the primary proposal is level-only, routed timing evaluation is explicitly unavailable and Reference plots show EQ only. Two targets do not resolve missing seats, unmeasured outputs or unverified native reconstruction.

## Shared headroom and review

Both curves use **one coefficient scale per output**, determined by the worst FIR/native coefficient peak across the pair. A common channel-trim compensation preserves both curves' relative digital gain. With unknown baseline trims, settings-only mode instead reserves one attenuation across all outputs and curves and retains current trims.

Compensation uses the observed tenth-dB control precision. Gain, trim and numeric limits are checked before any connection. These are digital-data guarantees; equal perceived level or acoustic gain across AVR modes is not certified.

Choose **Initially selected receiver curve** on Receiver transfer before preparing review. Changing that selection, targets or optional receiver adjustments requires a new review. The byte transaction commits to both curves, their calibration FIRs, the initial selection and common control frames. Application still requires a separate explicit review/action.

## Encoding and export

The inspected reference package associates native slot **0** with **Flat** / OCA `filterLV`, and slot **1** with **Reference** / OCA `filter`. audioseyX independently constructs both streams; reference code, private tables and recorded filters are not redistributed. This is an observed protocol association, not hardware certification.

Each configured output needs both complete curves at all three observed rate slots. Each curve has its own count/hash and serialized-precision report. Missing, duplicated, swapped or relabeled streams fail the audit. Transfer schema `audioseyx-native-transfer-4` requires older reviews to be regenerated.

The bundle preserves root Flat WAV/report paths and adds `curves/Reference/`. Original measurements are included once. Root `design-report.json` includes a report form of Reference; the root native candidate links its separate candidate file. WAV export works without optional private encoding data. Only files generated for the current export enter the archive; stale files in reused directories are excluded.

## Python example

```python
design = design_project(project, "XT32", {
    "phase_mode": "minimum",
    "bass_db": 3,
    "tilt_db_oct": -0.5,
    "validation_positions": ["p2"],
    "curve_profiles": {
        "Reference": {"bass_db": 5, "high_hz": 800, "target_curve": None},
    },
})
```

`curve_profiles` requires Reference and optionally accepts Flat overrides. Only `low_hz`, `high_hz`, `sub_high_hz`, `max_boost_db`, `max_cut_db`, `bass_db`, `tilt_db_oct`, `smoothing_octave` and `target_curve` may vary. `target_curve=None` uses bass/tilt instead of inheriting a common imported target. Native geometry, phase mode and spatial references cannot be overridden per curve.
