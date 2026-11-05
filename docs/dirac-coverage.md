# Dirac-related capability coverage

audioseyX is an independent Audyssey-aware workbench. It does not contain Dirac algorithms, load a Dirac license, or turn an unsupported receiver into a certified Dirac device. Software calculations, native execution and measured results are separate evidence.

## The three home-audio functions

Dirac describes [Room Correction](https://www.dirac.com/products/room-correction) as magnitude and impulse-response correction across the measured listening area. [Bass Control](https://www.dirac.com/products/bass-control) additionally integrates main speakers and one or more subs, including phase, time, crossover and spatial behavior. [ART's official FAQ](https://helpdesk.dirac.com/en/dirac-bass-control/Dirac-Live-Active-Room-Treatment-ART-FAQ-9be7) specifies cooperative MIMO control from **20 to 150 Hz**. Full-band room correction is a separate function; ART is not arbitrary full-band support on every speaker.

## Development checklist

These ten rows are our engineering breakdown, not Dirac's official feature count or an acoustic-equivalence score. Six have offline implementations, two remain reference/export workflows, and two need controls absent from the observed receiver interface. **There is no defensible percentage of Dirac performance established by these counts.**

| Selected capability | audioseyX software | Current native receiver execution |
|---|---|---|
| Magnitude EQ, target curves and correction bands | Implemented; native tier budgets and measured-band protection | Minimum-phase candidates; physical reconstruction unverified |
| Measured positions, seat priorities, independent validation | Implemented; repeats do not increase a seat's priority | Depends on actual measured coverage; no invented seats |
| Distance and level alignment | Declared native-menu search and auditory-band proposals | X3700H constraints modeled; current distance conversion/gain order unverified |
| Full complex frequency-dependent phase and impulse correction | Full-tap joint FIR and FDW goals | Reference/export only without validated native mixed-phase synthesis |
| Before/after frequency and temporal verification | Original-repeat diagnostics, common windows and quality gates | Normal-playback measurement workflow; no hardware writes |
| Main/sub crossover, shared delay and filter polarity | Conditional exact menu search with routing-model alternatives | One physical measured sub; fixed EQ/trim state, playback path unverified |
| Joint optimization of independent subwoofers | Full-complex multi-sub reference solver | Independent filter control and native multi-sub menu optimization unverified |
| Shared-bass multi-position risk and holdouts | Implemented for explicit measurements and routing | Predicted result, not a guarantee at unmeasured seats |
| Arbitrary main-to-main support and per-input cross filters | Not supplied by the observed native interface | Ordinary per-output EQ cannot create missing convolution routes |
| Cooperative active modal-decay suppression | Decay diagnostics only; no ART support-signal implementation | Not implemented |

The **Verify results** page exposes this checklist and the current project's actual positions, missing seats and configured-but-unmeasured outputs. `/api/capabilities` and the downloadable report expose the same assessment for integrations. `native_hardware_validation_complete` stays false; additional software features do not silently change it.

For example, two repeated scans at the centre are still one measured position; they do not provide left/right seat data. Any configured-but-unmeasured output remains unresolved. A receiver-settings snapshot is not an original-coefficient backup.

## What native Audyssey can still be developed

Minimum-phase EQ, target management, same-model energy balance, documented distances/levels/crossovers, measured spatial objectives and reproducible verification can be improved without adding a playback computer. This still requires validation of microphone compensation, gain/delay order, crossover reconstruction, sample-rate behavior and normal playback. Different target tiers have different writable budgets; expanded FIR size is not the number of independent XT32 hardware parameters.

Arbitrary cooperative support would require accessible per-input/per-output filters, routing, latency and headroom control, together with a verified native synthesis model. Those controls are not exposed by the inspected calibration protocol. Firmware modification is a separate unsupported engineering project, not a capability unlocked by designing more coefficients. audioseyX does not patch firmware.

## Temporal evidence added in 0.5.0a7

The [temporal verification workflow](temporal-verification.md) deliberately separates a smoother magnitude response, a lower level, a changed impulse shape and a demonstrably different decay slope. It helps decide which claimed capability has actually been measured, while leaving the filter designs and native controls unchanged.
