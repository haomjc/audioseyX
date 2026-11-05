# OCA replacement status

**audioseyX 0.5.0a4 is not yet a verified replacement for the complete OCA workflow.** Several measurement, analysis and optimization components are implemented; a complete native calibration and normal-playback acoustic result have not been established.

This assessment distinguishes the user's A1 Evo AcoustiX 2026-06-09 package from GSonic Reference. It describes inspected behavior and audioseyX's evidence, rather than an independently measured product comparison.

| Capability | Current audioseyX state | Evidence and remaining work |
|---|---|---|
| Receiver stock-microphone acquisition | Checked on X3700H Float transport | Individual main/sub responses and repeated scans captured; microphone compensation and other hardware remain unverified |
| Minimum-phase EQ and target curves | Implemented | Native tier budgets, measured-band limits and offline numerical tests; no same-data acoustic superiority claim |
| XT32 native encoding | Implemented with optional personal compatibility data | A private differential experiment compared 34 main/sub/recorded-filter cases against the supplied package; serialized Float32 bytes matched in those cases |
| Both calibration curves and all observed rate slots | Offline audited | Reconstructs every channel/curve/rate stream; verifies sequence, size, checksum, routing, count and serialized values before connecting |
| Independent calibration-curve designs | Incomplete | Current preparation writes the same designed filter into both curve identifiers; independent curve targets are not yet exposed |
| Review and transfer transaction | Implemented; simulated validation | The review digest commits to the receiver, calibration, levels, settings, control frames and coefficient frames; real transfer is unverified |
| Speaker distance, level and crossover calibration | Incomplete native integration | Existing settings are retained, coefficient gain compensation is planned, and crossover/alignment models exist; there is no complete joint settings-calibration replacement |
| Multiple positions and seat priorities | Implemented; synthetic spatial validation | Actual three-seat correction still requires left/right measurements; repeats at the centre do not fill those seats |
| Full complex joint FIR and FDW goals | Implemented as reference/export workflow | Expanded taps are distinct from writable native vectors; native synthesis and phase reconstruction require verification |
| Original-filter recovery | Imported full OCA copy retained | A device-settings snapshot has no original filters; automatic recovery from AVR-resident coefficients is not implemented |
| Normal-playback verification | File comparison available; physical validation pending | Verification must measure the active playback chain; a stock calibration sweep may bypass its current correction |
| ART-style arbitrary main-to-main support | Not provided by the current interface | Existing bass-management paths are modeled; new DSP routing is not created |

## What the encoder experiment establishes

The reference was the user-supplied package's XT32 encoder. The independently written Python implementation was compared locally using impulse positions, silence, negative zero, decaying random vectors and twelve previously generated filters. The Float32 comparison used serialized bytes, preserving signed zero rather than comparing JSON numbers alone.

The experiment is evidence for the tested encoding cases. It does not establish identical filter-design choices, actual DSP synthesis, all input vectors, microphone calibration or audible results. Upstream code, numerical tables and private response/filter vectors are excluded from the public repository and releases. Public transport tests instead use artificial values and addresses.

## Native transaction checks

The observed coefficient protocol uses two curve identifiers and three sampling-rate slots: 32, 44.1 and 48 kHz. Coefficient precision and coverage are checked separately from the FIR design sample rate. Repeating an encoded vector in those observed slots does not certify the receiver's actual sample-rate-dependent behavior.

Preparation retains a manifest of native coefficient counts and quantized-value hashes. Execution takes a private snapshot, reconstructs and checks every stream before opening a socket. Altering the target address, gains, settings, calibration, control payload order, coefficient bytes or manifest invalidates the reviewed commitment. A complete byte hash alone is insufficient: missing/duplicated/reordered streams and unexpected routing still fail structural validation. Command acknowledgements require an explicit ACK status, rather than an arbitrary matching substring.

Offline native-candidate exports also record Float32 coefficient precision and a serialized-value hash when encoding data are available. This can be inspected without a connected AVR. The selected receiver may require a different numeric format, and the export remains a candidate rather than an executable transfer plan.

## Comparison with GSonic

GSonic Reference's [official README](https://github.com/ObsessiveCompulsiveAudiophile/GSonic/blob/master/README.md) describes stereo measurement, spatial averaging, drift compensation and WAV filters loaded into a convolution engine. Its feature list is a research reference. It is not evidence that those capabilities run through an Audyssey AVR's native coefficient interface. audioseyX has not implemented automatic clock-drift recovery from already-deconvolved IRs or established GSonic-equivalent perceptual balancing.

The practical priority is a validated minimum-phase native calibration workflow, then complete receiver-settings integration and physical playback verification. Adding more reference optimizers does not itself complete that path.
